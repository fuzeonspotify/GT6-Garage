"""Geometry-fit and authored-winding regressions without proprietary assets."""
from __future__ import annotations

import copy
import hashlib
import itertools
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .car_builder import plan
from .car_geometry import alignment
from .car_profiles import PROFILE
from .glb import Part, Scene
from .recognition import CORNERS, axes
from .wheel_fit import native_wheel_geometry, normal_winding_report, tyre_fit_report, wheel_source_orientation_report
from .wheels import geometry


def wheel_scene(forward='+Z', radius=.32, width=.24, scale=1., normals='aligned'):
    left, up, front = axes(forward)
    basis = np.array([left, up, front])
    parts, records, centers, radii = [], [], {}, {}
    for corner in CORNERS:
        center = np.array([.7682615 if corner[1] == 'L' else -.7682615,
                           0., 1.275 if corner[0] == 'F' else -1.275])
        centers[corner] = (center@basis*scale).tolist()
        radii[corner] = radius*scale
        tyre = np.array(list(itertools.product((-.5*width, .5*width),
                                                (-radius, radius), (-radius, radius))))
        positions = (tyre+center)@basis*scale
        parts.append(Part('tyre_'+corner, 'Tyre', len(parts), 0, 0, positions,
                          np.tile([0., 1, 0], (len(positions), 1)), np.zeros((len(positions), 2)),
                          np.array([[0, 1, 2], [1, 3, 2]], dtype=np.int64)))
        records.append({'id': 'tyre_'+corner, 'role': 'tyre', 'corner': corner, 'material': 0})
        rim = np.array([[0., 0., 0.], [0., .07, 0.], [0., 0., .07],
                        [0., .1, 0.], [0., .17, 0.], [0., .1, .07]])
        positions = (rim+center)@basis*scale
        authored = np.tile([1., 0, 0], (len(positions), 1))
        if normals == 'opposed':
            authored *= -1
        elif normals == 'mixed':
            authored[3:] *= -1
        parts.append(Part('rim_'+corner, 'Rim', len(parts), 0, 0, positions,
                          authored@basis, np.zeros((len(positions), 2)),
                          np.array([[0, 1, 2], [3, 4, 5]], dtype=np.int64)))
        records.append({'id': 'rim_'+corner, 'role': 'rim', 'corner': corner, 'material': 0})
    # Keep plan role checks realistic without changing the frozen rim fixtures.
    for role, z in (('headlight', 1.5), ('taillight', -1.5)):
        positions = np.array([[0., .5, z], [.1, .5, z], [0., .6, z]])@basis*scale
        parts.append(Part(role, role, len(parts), 0, 0, positions,
                          np.tile([0., 0, 1], (3, 1))@basis, np.zeros((3, 2)),
                          np.array([[0, 1, 2]], dtype=np.int64)))
        records.append({'id': role, 'role': role, 'corner': None, 'material': 0})
    scene = Scene(parts, [{'id': 0, 'base_texture': None}], {}, b'synthetic', 'a'*64, [])
    inspection = {'forward': forward, 'wheel_centers': centers, 'wheel_radii': radii,
                  'wheelbase': 2.55*scale, 'track': 1.536523*scale, 'parts': records,
                  'role_counts': {'rim': 4, 'tyre': 4, 'headlight': 1, 'taillight': 1}}
    return scene, inspection


class WheelFitTests(unittest.TestCase):
    def test_source_body_native_measurements_have_signed_differences(self):
        scene, inspection = wheel_scene()
        report = tyre_fit_report(scene, inspection)
        self.assertEqual(report['status'], 'measured')
        for row in report['corners']:
            with self.subTest(corner=row['corner']):
                self.assertAlmostEqual(row['source']['radius_metres'], .32)
                self.assertAlmostEqual(row['body']['radius_metres'], .32)
                self.assertAlmostEqual(row['body']['width_metres'], .24)
                self.assertLess(row['differences']['center_error_metres'], 1e-14)
                if row['corner'][0] == 'F':
                    self.assertAlmostEqual(row['differences']['radius_metres'], -.00165)
                    self.assertAlmostEqual(row['differences']['width_metres'], .005)
                    self.assertAlmostEqual(row['rim_transform']['radial_scale'], 1.00515625)
                else:
                    self.assertAlmostEqual(row['differences']['radius_metres'], .0021)
                    self.assertAlmostEqual(row['differences']['width_metres'], -.015)

    def test_equal_centres_do_not_hide_large_tyre_differences(self):
        reports = []
        for radius in (.1, 1.):
            scene, inspection = wheel_scene(radius=radius)
            project = {'inspection': inspection, 'approved_roles': True,
                       'template_inheritance_approved': True}
            from .body_fit import body_fit_review,approve_body_fit
            fit_review=body_fit_review(scene,project,'adapt_body')
            project['body_fit']=approve_body_fit(scene,project,'adapt_body',fit_review['review_digest'],True)
            with patch('garagehub.car_builder.validate_template', return_value={'root': 'synthetic'}):
                result = plan(scene, project, Path('/synthetic'))
            self.assertTrue(result['ready'])  # Structural candidate checks stay unchanged.
            self.assertEqual(result['fit']['fit_scope'], 'wheel_centres_only')
            self.assertTrue(result['fit']['dimensional_review_required'])
            fit = result['fit']['tyre_fit']
            self.assertEqual(fit['dimensional_compatibility'], 'unreviewed')
            self.assertTrue(fit['dimensional_review_required'])
            reports.append(fit['corners'][0])
        self.assertAlmostEqual(reports[0]['differences']['radius_metres'], -.22165)
        self.assertAlmostEqual(reports[1]['differences']['radius_metres'], .67835)
        self.assertAlmostEqual(reports[0]['differences']['center_error_metres'],
                               reports[1]['differences']['center_error_metres'])

    def test_directions_and_units_preserve_body_envelope_measurements(self):
        for forward, scale in itertools.product(('+Z', '-Z', '+X', '-X'), (.01, 1., 100.)):
            scene, inspection = wheel_scene(forward=forward, scale=scale)
            report = tyre_fit_report(scene, inspection)
            for row in report['corners']:
                with self.subTest(forward=forward, scale=scale, corner=row['corner']):
                    self.assertAlmostEqual(row['body']['radius_metres'], .32)
                    self.assertAlmostEqual(row['body']['width_metres'], .24)
                    self.assertLess(row['differences']['center_error_metres'], 1e-13)
                    self.assertAlmostEqual(row['source']['radius_metres'], .32*scale)

    def test_shared_profile_drives_body_targets_and_native_dimensions(self):
        scene, inspection = wheel_scene()
        profile = copy.deepcopy(PROFILE)
        profile['id'] = 'synthetic-shared-profile'
        profile['wheelbase'] = 3.
        profile['front_track'] = profile['rear_track'] = 1.8
        for corner in CORNERS:
            profile['wheel_geometry'][corner] = {
                'center_metres': [-.9 if corner[1] == 'L' else .9, .25,
                                  -1.5 if corner[0] == 'F' else 1.5],
                'tyre_radius_metres': .42, 'tyre_width_metres': .3}
        report = tyre_fit_report(scene, inspection, profile=profile)
        self.assertEqual(report['profile_id'], profile['id'])
        for row in report['corners']:
            self.assertLess(row['differences']['center_error_metres'], 1e-13)
            self.assertEqual(row['native']['radius_metres'], .42)
            self.assertEqual(row['native']['width_metres'], .3)
        matrix, translation, fit = alignment(inspection, profile)
        self.assertEqual(fit['target_wheelbase_metres'], 3.)
        actual = np.array([inspection['wheel_centers'][corner] for corner in CORNERS])@matrix.T+translation
        target = np.array([profile['wheel_geometry'][corner]['center_metres'] for corner in CORNERS])
        np.testing.assert_allclose(actual, target, atol=1e-14)

    def test_native_contract_is_a_private_copy(self):
        row = native_wheel_geometry('FL')
        row['center_metres'][0] = 100.
        self.assertEqual(native_wheel_geometry('FL')['center_metres'][0], -.7682615)

    def test_missing_tyre_geometry_is_explicitly_incomplete(self):
        scene, inspection = wheel_scene()
        scene.parts = [part for part in scene.parts if part.id != 'tyre_RR']
        report = tyre_fit_report(scene, inspection)
        self.assertEqual(report['status'], 'incomplete')
        self.assertFalse(report['corners'][-1]['available'])
        self.assertIsNone(report['corners'][-1]['source'])
        self.assertIn('RR tyre geometry', report['corners'][-1]['issues'][0])
        self.assertTrue(report['dimensional_review_required'])

    def test_incompatible_body_fit_remains_visible_to_standalone_rim_export(self):
        scene, inspection = wheel_scene()
        inspection['track'] = 3.
        report = tyre_fit_report(scene, inspection)
        self.assertEqual(report['status'], 'incomplete')
        self.assertFalse(report['body_transform_available'])
        self.assertTrue(report['body_transform_error'])
        self.assertIsNotNone(report['corners'][0]['source'])
        self.assertIsNotNone(report['corners'][0]['rim_transform'])
        self.assertIsNone(report['corners'][0]['body'])

    def test_stale_inspection_radius_does_not_replace_geometry_measurement(self):
        scene, inspection = wheel_scene(radius=.8)
        inspection['wheel_radii']['FL'] = .32
        row = tyre_fit_report(scene, inspection)['corners'][0]
        self.assertAlmostEqual(row['source']['radius_metres'], .8)
        self.assertAlmostEqual(row['body']['radius_metres'], .8)
        self.assertAlmostEqual(row['differences']['radius_metres'], .47835)
        self.assertAlmostEqual(row['rim_transform']['inspection_radius_difference_metres'], -.48)
        self.assertEqual(row['rim_transform']['inspection_radius_metres'], .32)

    def test_missing_inspection_radius_reports_incomplete_measurements(self):
        scene, inspection = wheel_scene()
        del inspection['wheel_radii']['FL']
        report = tyre_fit_report(scene, inspection)
        self.assertEqual(report['status'], 'incomplete')
        self.assertIsNotNone(report['corners'][0]['source'])
        self.assertIsNone(report['corners'][0]['rim_transform'])

    def test_invalid_body_transform_is_reported_without_nonfinite_ratios(self):
        scene, inspection = wheel_scene()
        report = tyre_fit_report(scene, inspection, np.zeros((3, 3)), np.zeros(3))
        self.assertFalse(report['body_transform_available'])
        self.assertEqual(report['status'], 'incomplete')
        self.assertTrue(all(row['differences'] is None for row in report['corners']))

    def test_measurements_and_conversion_leave_source_and_inspection_unchanged(self):
        scene, inspection = wheel_scene(forward='-X', scale=2.4, normals='mixed')
        saved_inspection = copy.deepcopy(inspection)
        saved_materials = copy.deepcopy(scene.materials)
        saved_document = copy.deepcopy(scene.document)
        saved = [(part.positions.copy(), part.normals.copy(), part.uv.copy(), part.indices.copy())
                 for part in scene.parts]
        tyre_fit_report(scene, inspection)
        wheel_source_orientation_report(scene, inspection)
        for corner in CORNERS:
            geometry(scene, inspection, corner)
        self.assertEqual(inspection, saved_inspection)
        self.assertEqual(scene.raw, b'synthetic')
        self.assertEqual(scene.digest, 'a'*64)
        self.assertEqual(scene.materials, saved_materials)
        self.assertEqual(scene.document, saved_document)
        for part, arrays in zip(scene.parts, saved):
            for actual, original in zip((part.positions, part.normals, part.uv, part.indices), arrays):
                np.testing.assert_array_equal(actual, original)

    def test_source_orientation_discloses_opposed_normals_and_two_sided_valve(self):
        scene, inspection = wheel_scene(normals='opposed')
        rim = next(part for part in scene.parts if part.id == 'rim_FR')
        scene.materials.append({'id': 1, 'base_texture': None, 'double_sided': True})
        valve = replace(rim, id='valve_FR', name='Authored valve', material=1,
                        normals=-rim.normals.copy())
        scene.parts.append(valve)
        inspection['parts'].append({'id': valve.id, 'role': 'valve', 'corner': 'FR', 'material': 1})
        report = wheel_source_orientation_report(scene, inspection)
        self.assertEqual(len(report['parts']), 5)
        self.assertEqual(report['normal_disagreement_parts'], 4)
        self.assertEqual(report['double_sided_parts'], 1)
        self.assertTrue(report['review_required'])
        self.assertFalse(report['geometry_changed'])
        self.assertFalse(report['normals_changed'])
        self.assertFalse(report['native_double_sided_verified'])
        source_valve = report['parts'][-1]
        self.assertEqual(source_valve['part'], 'valve_FR')
        self.assertEqual(source_valve['name'], 'Authored valve')
        self.assertEqual(source_valve['role'], 'valve')
        self.assertEqual(source_valve['corner'], 'FR')
        self.assertFalse(source_valve['normal_disagreement'])
        self.assertTrue(source_valve['source_double_sided'])
        self.assertTrue(source_valve['review_required'])
        self.assertTrue(all(row['source_normal_winding']['opposed_triangles'] == 2
                            for row in report['parts'][:-1]))

    def test_source_orientation_ignores_nonwheel_materials_and_clean_surfaces(self):
        scene, inspection = wheel_scene()
        scene.materials.append({'id': 1, 'base_texture': None, 'double_sided': True})
        next(part for part in scene.parts if part.id == 'headlight').material = 1
        report = wheel_source_orientation_report(scene, inspection)
        self.assertEqual(len(report['parts']), 4)
        self.assertEqual(report['normal_disagreement_parts'], 0)
        self.assertEqual(report['double_sided_parts'], 0)
        self.assertFalse(report['review_required'])
        self.assertTrue(all(row['role'] == 'rim' for row in report['parts']))

    def test_plan_orientation_disclosure_preserves_candidate_readiness_and_approval(self):
        scene, inspection = wheel_scene(normals='opposed')
        scene.materials[0]['double_sided'] = True
        project = {'inspection': inspection, 'approved_roles': True,
                   'template_inheritance_approved': True,
                   'installation': {'mode': 'new_entry', 'replace_car_id': None}}
        from .body_fit import body_fit_review,approve_body_fit
        fit_review=body_fit_review(scene,project,'adapt_body')
        project['body_fit']=approve_body_fit(scene,project,'adapt_body',fit_review['review_digest'],True)
        before = copy.deepcopy(project)
        with patch('garagehub.car_builder.validate_template', return_value={'root': 'synthetic'}):
            result = plan(scene, project, Path('/synthetic'))
        self.assertTrue(result['ready'])
        self.assertEqual(result['actions'], [])
        self.assertEqual(project, before)
        self.assertEqual(result['source_orientation']['normal_disagreement_parts'], 4)
        self.assertEqual(result['source_orientation']['double_sided_parts'], 4)
        self.assertTrue(result['source_orientation']['review_required'])


class RimWindingTests(unittest.TestCase):
    def test_aligned_native_geometry_matches_frozen_prechange_bytes(self):
        # Captured with v0.4.0 geometry() before this change, for all 16 cases.
        expected = {
            'FL': '82dec4f14216bb287c888225c70321c255f52103d840303412b7b5cf3a7903e0',
            'FR': '4338ae4618fe9194f9c290532696b592acdaaded79ca4a25f5b9b147720ba437',
            'RL': '902291be7505cb32401a48150d75c7aadaa00ee665e7f21c0d69eb31ee3b261a',
            'RR': 'adec737963d5a97afba8822192d49374a1975fa3f72f1418caf0f8758844f5f3'}
        for forward in ('+Z', '-Z', '+X', '-X'):
            scene, inspection = wheel_scene(forward=forward)
            for corner in CORNERS:
                with self.subTest(forward=forward, corner=corner):
                    vb, ib, _, _, reports = geometry(scene, inspection, corner)
                    self.assertEqual(hashlib.sha256(vb+ib).hexdigest(), expected[corner])
                    self.assertFalse(reports[0]['winding_corrected'])
                    self.assertFalse(reports[0]['source_normal_disagreement'])

    def test_opposed_normals_do_not_reverse_authored_faces(self):
        for forward in ('+Z', '-Z', '+X', '-X'):
            scene, inspection = wheel_scene(forward=forward, normals='opposed')
            for corner in CORNERS:
                with self.subTest(forward=forward, corner=corner):
                    _, _, _, faces, reports = geometry(scene, inspection, corner)
                    np.testing.assert_array_equal(faces, [[0, 1, 2], [3, 4, 5]])
                    row = reports[0]
                    self.assertGreater(row['transform_determinant'], 0)
                    self.assertEqual(row['winding_policy'], 'transform_determinant')
                    self.assertFalse(row['winding_corrected'])
                    self.assertTrue(row['source_normal_disagreement'])
                    self.assertEqual(row['source_normal_winding']['opposed_triangles'], 2)
                    self.assertEqual(row['output_normal_winding']['opposed_triangles'], 2)

    def test_mixed_normals_are_reported_per_triangle_without_global_repair(self):
        for forward in ('+Z', '-Z', '+X', '-X'):
            scene, inspection = wheel_scene(forward=forward, normals='mixed')
            for corner in CORNERS:
                with self.subTest(forward=forward, corner=corner):
                    _, _, _, faces, reports = geometry(scene, inspection, corner)
                    np.testing.assert_array_equal(faces, [[0, 1, 2], [3, 4, 5]])
                    for key in ('source_normal_winding', 'output_normal_winding'):
                        report = reports[0][key]
                        self.assertEqual(report['aligned_triangles'], 1)
                        self.assertEqual(report['opposed_triangles'], 1)
                        self.assertEqual(report['opposed_fraction'], .5)
                        self.assertFalse(report['repair_applied'])

    def test_reflected_frame_changes_winding_to_preserve_front_face(self):
        scene, inspection = wheel_scene()
        with patch('garagehub.wheels.rim_frame', return_value=(np.zeros(3), np.diag([-1., 1., 1.]), np.zeros(3), {})):
            _, _, _, faces, reports = geometry(scene, inspection, 'FL')
        np.testing.assert_array_equal(faces, [[0, 2, 1], [3, 5, 4]])
        self.assertTrue(reports[0]['winding_corrected'])
        self.assertEqual(reports[0]['transform_determinant'], -1.)
        self.assertEqual(reports[0]['output_normal_winding']['aligned_triangles'], 2)

    def test_degenerate_faces_and_zero_normals_are_unassessable(self):
        p = np.array([[0., 0., 0.], [0., 1., 0.], [0., 0., 1.]])
        report = normal_winding_report(p, np.zeros_like(p), np.array([[0, 1, 2], [0, 0, 0]]))
        self.assertEqual(report['unassessable_triangles'], 2)
        self.assertEqual(report['opposed_triangles'], 0)
        self.assertIsNone(report['opposed_fraction'])


if __name__ == '__main__':
    unittest.main()
