"""Pinned native-command and source-independent RX8 mechanical frame checks."""
from __future__ import annotations

import copy
import unittest
from unittest.mock import patch
import numpy as np

from .errors import GarageError
from .glb import Part, Scene
from .recognition import CORNERS, axes
from .util import sha256
from .rx8_mechanical_lab import (historical_frame_contract, reviewed_rx8_routes,
    rotor_to_caliper_rest_basis, _reviewed_parts, PROGRAM_HASHES, build_mechanical_lod)

# Original program-only bytes from the read-only, hash-pinned RX8 native probe.
# No model geometry, textures, game executables or proprietary runtime are needed.
ORIGINAL_PROGRAMS = {
    'hq_0': bytes.fromhex(
        '3b27003b01003b02003b0000050007020004000a090e3b04003b030009063b05003b0600050007020004001f093b05000002'
        '0004000a09113b08003b070009093b0a003b0b003b0900091e050000020004000d09143b0d003b0e003b0c0009093b10003b'
        '11003b0f003b15003b16003b13003b12003b14003b18003b17003b1b003b19003b1c003b1a003b1d003b1e003b1f00050007'
        '020004001309230500000200040004090509033b20000912050000020004000709083b210009033b22003b24003b23003b25'
        '003b26000e05000b0200040020091e2c36000901000209033b28010e060f2c36000701000209033b29020f09000f0e05000c'
        '0200040020091e2c36000901000209033b2a030e06102c36000701000209033b2b040f09000f0e05000d0200040020091e2c'
        '36000901000209033b2c050e06112c36000701000209033b2d060f09000f0e05000e0200040020091e2c3600090100020903'
        '3b2e070e06122c36000701000209033b2f080f09000f00'),
    'hq_5': bytes.fromhex(
        '3b60113b27113b01113b02113b0011050007020004000a090e3b04113b031109063b05113b0611050007020004001f093b05'
        '0000020004000a09113b08113b071109093b0a113b0b113b0911091e050000020004000d09143b0d113b0e113b0c1109093b'
        '10113b11113b0f113b15113b16113b13113b12113b14113b18113b17113b1b113b19113b1c113b1a113b1d113b1e113b1f11'
        '050007020004001309230500000200040004090509033b20110912050000020004000709083b211109033b22113b24113b23'
        '113b25113b26110e05000b0200040020091e2c36000901000209033b28120e060f2c36000701000209033b29130f09000f0e'
        '05000c0200040020091e2c36000901000209033b2a140e06102c36000701000209033b2b150f09000f0e05000d0200040020'
        '091e2c36000901000209033b2c160e06112c36000701000209033b2d170f09000f0e05000e0200040020091e2c3600090100'
        '0209033b2e180e06122c36000701000209033b2f190f09000f00'),
    'race_0': bytes.fromhex(
        '05007202000501790a01763b27003b01003b02003b0000050007020004000a090e3b04003b030009063b05003b0600050007'
        '020004001f093b050000020004000a09113b08003b070009093b0a003b0b003b0900091e050000020004000d09143b0d003b'
        '0e003b0c0009093b10003b11003b0f003b15003b16003b13003b12003b14003b18003b17003b1b003b19003b1c003b1a003b'
        '1d003b1e003b1f00050007020004001309230500000200040004090509033b20000912050000020004000709083b21000903'
        '3b22003b24003b23003b25003b26000e05000b0200040020091e2c36000901000209033b28010e060f2c3600070100020903'
        '3b29020f09000f0e05000c0200040020091e2c36000901000209033b2a030e06102c36000701000209033b2b040f09000f0e'
        '05000d0200040020091e2c36000901000209033b2c050e06112c36000701000209033b2d060f09000f0e05000e0200040020'
        '091e2c36000901000209033b2e070e06122c36000701000209033b2f080f09000f090000'),
}
ORIGINAL_PROGRAMS['race_5'] = ORIGINAL_PROGRAMS['hq_5']


def source_fixture(forward='+Z'):
    left, up, front = axes(forward)
    orientation = np.array([left, up, front])
    parts, rows, centers = [], [], {}
    for ci, corner in enumerate(CORNERS):
        center = np.array([.83 if corner[1] == 'L' else -.83, .34,
                           1.3 if corner[0] == 'F' else -1.3])@orientation
        centers[corner] = center.tolist()
        tyre = np.array([[x, y, z] for x in (-.15, .15) for y in (-.34, .34) for z in (-.34, .34)])@orientation+center
        rotor = np.array([[.04, 0., 0.], [.04, .18, 0.], [.04, .18, .12], [.04, 0., .12]])@orientation+center
        for role, points in (('tyre', tyre), ('brake_rotor', rotor), ('brake_caliper', rotor+[0., .1, 0.])):
            pid = role+'_'+corner
            count = len(points)
            parts.append(Part(pid, pid, ci, 0, 0, points, np.tile(left, (count, 1)),
                              np.zeros((count, 2)), np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)))
            rows.append({'id': pid, 'role': role, 'corner': corner, 'role_review_required': False})
    scene = Scene(parts, [{'id': 0, 'name': 'fixture', 'color': [.1, .1, .1, 1.],
                           'metallic': 0., 'roughness': .5, 'base_texture': None, 'normal_map': None,
                           'alpha_mode': 'OPAQUE', 'transmission': 0.}], {}, b'different source', 'b'*64, [])
    return scene, {'forward': forward, 'wheel_centers': centers, 'parts': rows}


class RX8MechanicalTests(unittest.TestCase):
    def test_reviewed_routes_match_original_program_bytes_in_both_models_and_lods(self):
        raw = b'isolated route test identity'
        for lod in ('hq', 'race'):
            with patch.dict('garagehub.rx8_mechanical_lab.RX8_ASSETS', {lod+'/body': sha256(raw)}):
                routes = reviewed_rx8_routes(raw, lod)
            self.assertEqual(len(routes['components']), 8)
            for mid in (0, 5):
                self.assertEqual(sha256(ORIGINAL_PROGRAMS[lod+'_'+str(mid)]), PROGRAM_HASHES[lod][str(mid)])
            for row in routes['components']:
                ci = CORNERS.index(row['corner'])
                expected_shape = 40+2*ci+(row['role'] == 'brake_rotor')
                self.assertEqual(row['original_shape'], expected_shape)
                for call in row['calls']:
                    program = ORIGINAL_PROGRAMS[lod+'_'+str(call['model'])]
                    self.assertEqual(program[call['offset']:call['offset']+3],
                                     bytes((59, expected_shape, call['context'])))
                    if row['role'] == 'brake_rotor':
                        # This original nesting selects the rotor's motion
                        # route; it is retained, not recomputed by the exporter.
                        self.assertEqual(program[call['offset']-12:call['offset']-9], bytes((14, 6, 15+ci)))

    def test_route_identity_and_lod_are_never_guessed(self):
        for lod in ('hq', 'race', 'unknown'):
            with self.assertRaises(GarageError):
                reviewed_rx8_routes(b'wrong donor', lod)

    def test_right_caliper_uses_observed_rest_basis_separate_from_rotating_rim(self):
        _, inspection = source_fixture()
        frames = historical_frame_contract(inspection)
        for corner in CORNERS:
            rotor = frames['brake_rotor:'+corner]
            caliper = frames['brake_caliper:'+corner]
            rest = rotor_to_caliper_rest_basis(corner)
            self.assertAlmostEqual(np.linalg.det(rest), 1.)
            np.testing.assert_array_equal(np.array(caliper['source_to_local_rotation']),
                                          rest@np.array(rotor['source_to_local_rotation']))
            if corner[1] == 'R':
                self.assertFalse(np.array_equal(caliper['source_to_local_rotation'], rotor['source_to_local_rotation']))
            np.testing.assert_array_equal(caliper['source_to_local_rotation'], np.diag([-1., 1., -1.]))

    def test_rest_rotor_and_caliper_share_origin_with_proper_rigid_relative_basis(self):
        _, inspection = source_fixture()
        frames = historical_frame_contract(inspection)
        for corner in CORNERS:
            rotor = np.array(frames['brake_rotor:'+corner]['reference_local_to_body'])
            caliper = np.array(frames['brake_caliper:'+corner]['reference_local_to_body'])
            np.testing.assert_array_equal(rotor[:, 3], caliper[:, 3])
            np.testing.assert_allclose(caliper[:, :3]@rotor_to_caliper_rest_basis(corner), rotor[:, :3], rtol=0, atol=1e-15)

    def test_source_axes_do_not_bake_historical_camber_steering_or_scale(self):
        for forward in ('+Z', '-Z', '+X', '-X'):
            _, inspection = source_fixture(forward)
            frames = historical_frame_contract(inspection)
            for value in frames.values():
                basis = np.array(value['source_to_local_rotation'])
                self.assertTrue(set(basis.ravel()) <= {-1., 0., 1.})
                np.testing.assert_array_equal(basis.T@basis, np.eye(3))
                self.assertAlmostEqual(np.linalg.det(basis), 1.)
                self.assertFalse(value['captured_camber_or_steering_baked_into_source'])
                self.assertFalse(value['native_mount_or_physics_changed'])

    def test_axial_reference_matches_source_lateral_datum_and_discloses_yz_residual(self):
        _, inspection = source_fixture()
        frames = historical_frame_contract(inspection)
        for value in frames.values():
            self.assertEqual(value['radial_offset_metres'], [0., 0.])
            target = np.array(value['source_center_in_body_frame'])
            fitted = np.array(value['fitted_center_in_reference_pose'])
            self.assertAlmostEqual(target[0], fitted[0], places=13)
            np.testing.assert_allclose(target-fitted, value['source_body_minus_fitted_center_metres'], rtol=0, atol=1e-15)
            self.assertGreater(abs(target[1]-fitted[1]), .1)
            self.assertGreater(abs(target[2]-fitted[2]), .049)

    def test_source_inventory_accepts_other_model_hashes_and_uses_all_assigned_mechanical_parts(self):
        scene, inspection = source_fixture()
        selected = _reviewed_parts(scene, inspection)
        self.assertEqual(scene.digest, 'b'*64)
        self.assertEqual(len(selected), 8)
        self.assertEqual({row['corner'] for _, row in selected}, set(CORNERS))

    def test_hub_centres_come_from_tyre_geometry_not_caliper_boxes(self):
        scene, inspection = source_fixture()
        caliper = next(p for p in scene.parts if p.id == 'brake_caliper_FL')
        self.assertGreater(np.linalg.norm(caliper.center-np.array(inspection['wheel_centers']['FL'])), .1)
        self.assertEqual(len(_reviewed_parts(scene, inspection)), 8)
        inspection['wheel_centers']['FL'] = caliper.center.tolist()
        with self.assertRaises(GarageError):
            _reviewed_parts(scene, inspection)

    def test_missing_tyres_or_stale_source_centres_cannot_drive_native_geometry(self):
        for mutation in ('tyre_role', 'centre', 'inventory'):
            scene, inspection = source_fixture()
            if mutation == 'tyre_role':
                inspection['parts'][0]['role'] = 'body'
            elif mutation == 'centre':
                inspection['wheel_centers']['RR'][2] += .01
            else:
                inspection['parts'] = inspection['parts'][:-1]
            with self.assertRaises(GarageError):
                _reviewed_parts(scene, inspection)

    def test_float32_tyre_centres_match_actual_recognition_mean_arithmetic(self):
        from .recognition import infer
        scene, inspection = source_fixture()
        for part in scene.parts:
            part.positions = part.positions.astype(np.float32)
        # Make the extrema's midpoint nonrepresentable in float32 so computing
        # it after an early float64 promotion would fail a false stale check.
        tyre = next(part for part in scene.parts if part.id == 'tyre_FL')
        tyre.positions[tyre.positions[:, 0] > .83, 0] += np.float32(.000013)
        overrides = {row['id']: {'role': row['role'], 'corner': row['corner']} for row in inspection['parts']}
        measured = infer(scene, forward='+Z', overrides=overrides)
        self.assertEqual(len(_reviewed_parts(scene, measured)), 8)
        bounds = tyre.bounds
        self.assertFalse(np.array_equal(bounds.astype(float).mean(0), np.array(measured['wheel_centers']['FL'])))

    def test_name_only_proposal_and_unpartitioned_corner_remain_blocked(self):
        for mutation in ('proposal', 'corner'):
            scene, inspection = source_fixture()
            row = next(row for row in inspection['parts'] if row['role'] == 'brake_rotor')
            if mutation == 'proposal':
                row['role_review_required'] = True
            else:
                row['corner'] = None
            with self.assertRaises(GarageError):
                _reviewed_parts(scene, inspection)

    def test_ambiguous_body_group_cannot_hide_unresolved_mechanical_proposal(self):
        scene, inspection = source_fixture()
        inspection['parts'][2].update(role='body', corner=None, role_review_required=True,
                                      mechanical_role_proposals=['brake_rotor', 'brake_caliper'])
        with self.assertRaises(GarageError):
            _reviewed_parts(scene, inspection)

    def test_crossed_corner_labels_are_rejected_before_source_routing(self):
        scene, inspection = source_fixture()
        inspection['wheel_centers']['FL'], inspection['wheel_centers']['FR'] = (
            inspection['wheel_centers']['FR'], inspection['wheel_centers']['FL'])
        with self.assertRaises(GarageError):
            _reviewed_parts(scene, inspection)

    def test_frame_receipts_are_independent_copies_and_json_compatible(self):
        import json
        _, inspection = source_fixture()
        before = copy.deepcopy(inspection)
        frames = historical_frame_contract(inspection)
        json.dumps(frames, allow_nan=False)
        self.assertEqual(inspection, before)
        frames['brake_rotor:FL']['evidence']['scope'] = 'changed'
        frames['brake_rotor:FL']['source_center_metres'][0] += 8.
        fresh = historical_frame_contract(inspection)
        self.assertNotEqual(fresh['brake_rotor:FL']['evidence']['scope'], 'changed')
        self.assertEqual(fresh['brake_rotor:FL']['source_center_metres'], inspection['wheel_centers']['FL'])

    def test_build_rejects_unknown_donor_before_editor_or_filesystem_access(self):
        scene, inspection = source_fixture()
        with patch('garagehub.rx8_mechanical_lab.Editor') as editor:
            with self.assertRaises(GarageError):
                build_mechanical_lod(scene, inspection, b'unknown asset', 'hq')
            editor.assert_not_called()

