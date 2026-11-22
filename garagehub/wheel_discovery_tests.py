"""Geometry/provenance cases for reviewed combined-tyre bootstrapping."""
from __future__ import annotations

import copy
import hashlib
import json
import unittest
from dataclasses import replace

import numpy as np

from .errors import GarageError
from .glb import MAX_PARTS, Part, Scene
from .mechanical_parts import partition_wheel_corners, replay_wheel_partition
from .recognition import CORNERS, axes, infer
from .recognition_tests import CENTRES, circular_part, scene_case
from .scene_coordinates import UP_AXES, coordinate_description, prepare_scene
from .wheel_discovery import discover_tyre_corners


def merged(parts, pid='combined_surface'):
    offsets = np.cumsum([0] + [len(part.positions) for part in parts[:-1]])
    return Part(pid, 'Unlabelled source surface', 0, 0, 0,
                np.concatenate([part.positions for part in parts]),
                np.concatenate([part.normals for part in parts]),
                np.concatenate([part.uv for part in parts]),
                np.concatenate([part.indices + offset for part, offset in zip(parts, offsets)]),
                {'source_note': {'keep': [1, 2, 3]}}, ['Authored source warning'])


def four_parts():
    result = [circular_part('surface_' + corner, CENTRES[corner]) for corner in CORNERS]
    for i, part in enumerate(result):
        # Distinct UV values make accidental reordering/zeroing observable.
        part.uv = np.arange(len(part.positions) * 2, dtype=np.float64).reshape(-1, 2) / 71 + i
    return result


def scene_with(part):
    scene = scene_case()
    return replace(scene, parts=[part, scene.parts[-1]])


def face_map(decision):
    return {group['corner']: group['source_face_indices'] for group in decision['groups']}


def json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


class WheelDiscoveryTests(unittest.TestCase):
    def assertLossless(self, source, children, decision):
        self.assertEqual([child.extras['gt6_corner'] for child in children], list(CORNERS))
        covered = []
        for child, group in zip(children, decision['groups']):
            covered.extend(group['source_face_indices'])
            selected = source.indices[group['source_face_indices']]
            for name in ('positions', 'normals', 'uv'):
                np.testing.assert_array_equal(getattr(child, name)[child.indices],
                                              getattr(source, name)[selected])
            self.assertEqual(child.material, source.material)
            self.assertEqual(child.extras['gt6_source_part'], source.id)
        self.assertEqual(sorted(covered), list(range(len(source.indices))))
        self.assertEqual(len(set(covered)), len(source.indices))

    def test_combined_group_bootstraps_existing_four_centre_prerequisite(self):
        separate = scene_case()
        self.assertEqual(set(infer(separate, forward='+Z')['wheel_centers']), set(CORNERS))
        source = merged(separate.parts[:4])
        scene = replace(separate, parts=[source, separate.parts[-1]])
        inspection = infer(scene, forward='+Z', overrides={source.id: {'role': 'tyre', 'corner': None}})
        self.assertEqual(inspection['wheel_centers'], {})
        with self.assertRaisesRegex(GarageError, 'exactly FL, FR, RL and RR'):
            partition_wheel_corners(source, inspection, 'tyre')
        children, decision, report = discover_tyre_corners(source, forward='+Z')
        prepared = replace(scene, parts=children + scene.parts[1:])
        after = infer(prepared, forward='+Z')
        self.assertEqual(set(after['wheel_centers']), set(CORNERS))
        self.assertAlmostEqual(after['wheelbase'], 2.6)
        for corner in CORNERS:
            np.testing.assert_allclose(after['wheel_centers'][corner], CENTRES[corner], atol=1e-14)
        self.assertEqual(report['status'], 'review_required')
        self.assertLossless(source, children, decision)

    def test_all_original_triangle_attributes_and_winding_are_exact(self):
        parts = four_parts()
        source = merged(parts)
        children, decision, _ = discover_tyre_corners(source, forward='+Z')
        offset = 0
        for corner, original in zip(CORNERS, parts):
            self.assertEqual(face_map(decision)[corner], list(range(offset, offset + len(original.indices))))
            offset += len(original.indices)
        self.assertLossless(source, children, decision)

    def test_discovery_does_not_mutate_source_arrays_metadata_or_known_ids(self):
        source = merged(four_parts())
        source.extras['gt6_role'] = 'ignore'
        before = copy.deepcopy(source)
        ids = ['shell', source.id]
        children, decision, report = discover_tyre_corners(source, forward='+Z', known_ids=ids)
        for name in ('positions', 'normals', 'uv', 'indices'):
            np.testing.assert_array_equal(getattr(source, name), getattr(before, name))
        self.assertEqual(source.extras, before.extras)
        self.assertEqual(source.warnings, before.warnings)
        self.assertEqual(ids, ['shell', source.id])
        children[0].positions[:] = 0
        children[0].normals[:] = 0
        children[0].uv[:] = 0
        children[0].extras['source_note']['keep'].append(7)
        decision['source_part']['name'] = 'changed returned decision'
        report['input_proof']['canonical_source']['name'] = 'changed returned report'
        np.testing.assert_array_equal(source.positions, before.positions)
        np.testing.assert_array_equal(source.normals, before.normals)
        np.testing.assert_array_equal(source.uv, before.uv)
        self.assertEqual(source.extras, before.extras)
        self.assertEqual(source.name, before.name)

    def test_all_declared_front_axes_preserve_physical_corner_membership(self):
        original = merged(four_parts())
        expected = face_map(discover_tyre_corners(original, forward='+Z')[1])
        for forward in ('+Z', '-Z', '+X', '-X'):
            with self.subTest(forward=forward):
                left, up, front = axes(forward)
                rotation = np.column_stack((left, up, front))
                part = replace(original, positions=original.positions @ rotation.T,
                               normals=original.normals @ rotation.T)
                children, decision, report = discover_tyre_corners(part, forward=forward)
                self.assertEqual(face_map(decision), expected)
                self.assertEqual(report['input_proof']['forward'], forward)
                for corner in CORNERS:
                    np.testing.assert_allclose(report['wheel_centers_metres'][corner],
                                               CENTRES[corner] @ rotation.T, atol=1e-14)
                self.assertLossless(part, children, decision)

    def test_source_up_axes_and_uniform_units_match_exact_prepared_scene(self):
        original = merged(four_parts())
        expected = face_map(discover_tyre_corners(original, forward='+Z')[1])
        for source_up in UP_AXES:
            with self.subTest(source_up=source_up):
                rotation = np.asarray(coordinate_description(source_up)['source_to_y_up_rotation'])
                source = replace(original, positions=(original.positions @ rotation * 100).astype(np.float32),
                                 normals=(original.normals @ rotation).astype(np.float32))
                scene = scene_with(source)
                prepared = prepare_scene(scene, source_up, .01).parts[0]
                children, decision, report = discover_tyre_corners(
                    prepared, forward='+Z', source_part=source,
                    source_up_axis=source_up, metres_per_unit=.01)
                self.assertEqual(face_map(decision), expected)
                self.assertEqual(report['input_proof']['preparation']['source_up_axis'], source_up)
                self.assertEqual(report['input_proof']['preparation']['metres_per_unit'], .01)
                self.assertLossless(prepared, children, decision)

    def test_saved_original_faces_replay_after_unit_preparation_changes(self):
        source = merged(four_parts())
        prepared = prepare_scene(scene_with(source), '+Y', .01).parts[0]
        _, decision, _ = discover_tyre_corners(prepared, forward='+Z', source_part=source,
                                                metres_per_unit=.01)
        saved = json.loads(json.dumps(decision))
        later = prepare_scene(scene_with(source), '+Y', .001).parts[0]
        children = replay_wheel_partition(later, saved, source_part=source)
        self.assertLossless(later, children, saved)
        changed = replace(source, uv=source.uv.copy())
        changed.uv[0, 0] += .1
        with self.assertRaisesRegex(GarageError, 'geometry hash'):
            replay_wheel_partition(changed, saved, source_part=changed)

    def test_translation_and_source_size_do_not_use_world_origin_quadrants(self):
        original = merged(four_parts())
        expected = face_map(discover_tyre_corners(original, forward='+Z')[1])
        for scale in (1e-4, 1, 1e4):
            with self.subTest(scale=scale):
                part = replace(original, positions=(original.positions + [17, -6, 21]) * scale)
                children, decision, report = discover_tyre_corners(part, forward='+Z')
                self.assertEqual(face_map(decision), expected)
                self.assertGreater(report['projections']['left']['separator_metres'], 0)
                self.assertGreater(report['projections']['forward']['separator_metres'], 0)
                self.assertLossless(part, children, decision)

    def test_unequal_front_rear_tracks_radii_widths_and_heights_are_not_forced_equal(self):
        centres = {'FL': [.75, .31, 1.45], 'FR': [-.75, .31, 1.45],
                   'RL': [1., .42, -1.2], 'RR': [-1., .42, -1.2]}
        parts = [circular_part(corner, np.array(centres[corner]),
                               radius=.31 if corner[0] == 'F' else .42,
                               width=.22 if corner[0] == 'F' else .4) for corner in CORNERS]
        source = merged(parts)
        children, decision, report = discover_tyre_corners(source, forward='+Z')
        for corner in CORNERS:
            np.testing.assert_allclose(report['wheel_centers_metres'][corner], centres[corner], atol=1e-14)
        self.assertLossless(source, children, decision)

    def test_duplicated_seam_vertices_and_disconnected_triangles_need_no_welding(self):
        source = merged(four_parts())
        selected = source.indices.reshape(-1)
        source = replace(source, positions=source.positions[selected].copy(),
                         normals=source.normals[selected].copy(), uv=source.uv[selected].copy(),
                         indices=np.arange(len(selected), dtype=np.int64).reshape(-1, 3))
        # Every triangle is a distinct topological component, with coincident
        # seam positions. Requiring exactly four connected components fails.
        children, decision, _ = discover_tyre_corners(source, forward='+Z')
        self.assertEqual(len(np.unique(source.indices)), 3 * len(source.indices))
        self.assertLossless(source, children, decision)

    def test_unused_outlier_vertices_do_not_invent_bands_or_shift_centres(self):
        original = merged(four_parts())
        part = replace(original, positions=np.vstack((original.positions, [1000, -8000, 9000])),
                       normals=np.vstack((original.normals, [1, 0, 0])),
                       uv=np.vstack((original.uv, [123, 456])))
        children, decision, report = discover_tyre_corners(part, forward='+Z')
        for corner in CORNERS:
            np.testing.assert_allclose(report['wheel_centers_metres'][corner], CENTRES[corner], atol=1e-14)
        self.assertLossless(part, children, decision)

    def test_shuffled_faces_vertices_and_unfamiliar_names_keep_geometric_labels(self):
        source = merged(four_parts(), pid='random_asset_712')
        rng = np.random.default_rng(420)
        vertices = rng.permutation(len(source.positions))
        inverse = np.argsort(vertices)
        face_order = rng.permutation(len(source.indices))
        part = replace(source, name='unrelated_authored_name',
                       positions=source.positions[vertices], normals=source.normals[vertices],
                       uv=source.uv[vertices], indices=inverse[source.indices[face_order]])
        children, decision, report = discover_tyre_corners(part, forward='+Z')
        for index, group in enumerate(decision['groups']):
            expected = np.flatnonzero(face_order // 128 == index).tolist()
            self.assertEqual(group['source_face_indices'], expected)
        for corner in CORNERS:
            np.testing.assert_allclose(report['wheel_centers_metres'][corner], CENTRES[corner], atol=1e-14)
        self.assertLossless(part, children, decision)

    def test_proof_is_deterministic_and_known_id_order_is_irrelevant(self):
        source = merged(four_parts())
        first = discover_tyre_corners(source, forward='+Z', known_ids=[source.id, 'body'])[2]
        second = discover_tyre_corners(source, forward='+Z', known_ids=iter(['body', source.id]))[2]
        self.assertEqual(first, second)
        self.assertEqual(first['input_sha256'], json_hash(first['input_proof']))
        _, decision, report = discover_tyre_corners(source, forward='+Z', known_ids=[source.id, 'body'])
        self.assertEqual(report['decision_sha256'], json_hash(decision))
        self.assertEqual(report, json.loads(json.dumps(report, allow_nan=False)))

    def test_proof_changes_for_prepared_units_forward_geometry_and_scene_membership(self):
        source = merged(four_parts())
        base = discover_tyre_corners(source, forward='+Z', source_part=source)[2]
        prepared = prepare_scene(scene_with(source), '+Y', .01).parts[0]
        scaled = discover_tyre_corners(prepared, forward='+Z', source_part=source, metres_per_unit=.01)[2]
        reversed_front = discover_tyre_corners(source, forward='-Z', source_part=source)[2]
        changed = replace(source, uv=source.uv.copy())
        changed.uv[0, 0] += .01
        geometry = discover_tyre_corners(changed, forward='+Z', source_part=changed)[2]
        membership = discover_tyre_corners(source, forward='+Z', source_part=source, known_ids=['body'])[2]
        hashes = {x['input_sha256'] for x in (base, scaled, reversed_front, geometry, membership)}
        self.assertEqual(len(hashes), 5)
        self.assertEqual(base['input_proof']['canonical_source'], scaled['input_proof']['canonical_source'])
        self.assertNotEqual(base['input_proof']['prepared_source'], scaled['input_proof']['prepared_source'])

    def test_nondefault_preparation_requires_canonical_source(self):
        source = merged(four_parts())
        for values in ({'metres_per_unit': .01}, {'source_up_axis': '+Z'}):
            with self.subTest(values=values), self.assertRaisesRegex(GarageError, 'canonical source'):
                discover_tyre_corners(source, forward='+Z', **values)

    def test_declared_preparation_rejects_wrong_units_axes_and_local_changes(self):
        source = merged(four_parts())
        prepared = prepare_scene(scene_with(source), '+Y', .01).parts[0]
        for part, values in (
                (prepared, {}),
                (prepared, {'metres_per_unit': .02}),
                (prepared, {'source_up_axis': '+Z', 'metres_per_unit': .01}),
                (replace(prepared, positions=prepared.positions + np.float32(.0001)), {'metres_per_unit': .01}),
                (replace(prepared, normals=-prepared.normals), {'metres_per_unit': .01})):
            with self.subTest(values=values), self.assertRaisesRegex(GarageError, 'declared source axes and units'):
                discover_tyre_corners(part, forward='+Z', source_part=source, **values)

    def test_declared_preparation_rejects_changed_topology_uvs_or_identity(self):
        source = merged(four_parts())
        for part in (replace(source, indices=source.indices[:, ::-1]),
                     replace(source, uv=source.uv + .01),
                     replace(source, name='changed identity')):
            with self.subTest(name=part.name), self.assertRaises(GarageError):
                discover_tyre_corners(part, forward='+Z', source_part=source)

    def test_invalid_forward_and_preparation_values_have_actionable_errors(self):
        source = merged(four_parts())
        for forward in (None, '+Y', '-Y', True, 3, ['+Z']):
            with self.subTest(forward=forward), self.assertRaisesRegex(GarageError, 'front direction') as caught:
                discover_tyre_corners(source, forward=forward)
            self.assertEqual(caught.exception.code, 'wheel_discovery')
        for kwargs in ({'source_up_axis': 'auto'}, {'metres_per_unit': True},
                       {'metres_per_unit': float('nan')}, {'metres_per_unit': float('inf')},
                       {'metres_per_unit': 0}, {'metres_per_unit': 1e7}):
            with self.subTest(kwargs=kwargs), self.assertRaises(GarageError):
                discover_tyre_corners(source, forward='+Z', source_part=source, **kwargs)

    def test_nonfinite_or_malformed_source_attributes_are_rejected_before_indexing(self):
        source = merged(four_parts())
        bad = []
        for name in ('positions', 'normals', 'uv'):
            for value in (np.nan, np.inf):
                array = getattr(source, name).copy()
                array[0, 0] = value
                bad.append(replace(source, **{name: array}))
        bad.extend((replace(source, positions=source.positions.astype(str)),
                    replace(source, normals=source.normals[:-1]),
                    replace(source, uv=source.uv[:, :1]),
                    replace(source, indices=source.indices.astype(float)),
                    replace(source, indices=np.array([[-1, 2, 3]])),
                    replace(source, indices=np.array([[0, 1, len(source.positions)]])),
                    replace(source, indices=np.empty((0, 3), dtype=int))))
        for part in bad:
            with self.subTest(dtype=part.positions.dtype), self.assertRaises(GarageError) as caught:
                discover_tyre_corners(part, forward='+Z')
            self.assertEqual(caught.exception.code, 'wheel_discovery')

    def test_bridge_triangle_is_rejected_even_with_a_clear_vertex_coordinate_gap(self):
        source = merged(four_parts())
        self.assertGreater(np.min(source.positions[source.positions[:, 0] > 0, 0]),
                           np.max(source.positions[source.positions[:, 0] < 0, 0]))
        bridge = np.array([[0, 1, len(four_parts()[0].positions)]])
        source = replace(source, indices=np.vstack((source.indices, bridge)))
        before = source.indices.copy()
        with self.assertRaisesRegex(GarageError, '1 occupied left/right bands'):
            discover_tyre_corners(source, forward='+Z')
        np.testing.assert_array_equal(source.indices, before)

    def test_longitudinal_bridge_is_rejected_without_cutting_triangles(self):
        parts = four_parts()
        source = merged(parts)
        rear_start = len(parts[0].positions) + len(parts[1].positions)
        source = replace(source, indices=np.vstack((source.indices, [[0, 1, rear_start]])))
        with self.assertRaisesRegex(GarageError, '1 occupied front/rear bands'):
            discover_tyre_corners(source, forward='+Z')

    def test_sidewall_only_multiple_lateral_bands_need_existing_centres(self):
        parts = four_parts()
        sidewalls = []
        for part in parts:
            vertices = part.positions[part.indices, 0]
            mask = np.ptp(vertices, axis=1) == 0
            sidewalls.append(replace(part, indices=part.indices[mask]))
        source = merged(sidewalls)
        # Matching front/rear tracks share the same four sidewall planes.
        with self.assertRaisesRegex(GarageError, '4 occupied left/right bands'):
            discover_tyre_corners(source, forward='+Z')
        # The existing follow-up path remains usable once a full tyre group
        # has established centres; bootstrap need not guess among extra bands.
        children, decision = partition_wheel_corners(source, {'forward': '+Z', 'wheel_centers': CENTRES}, 'tyre')
        self.assertLossless(source, children, decision)

    def test_three_corner_group_reports_the_missing_corner(self):
        source = merged(four_parts()[:3])
        with self.assertRaisesRegex(GarageError, 'no complete triangles at RR'):
            discover_tyre_corners(source, forward='+Z')

    def test_only_one_axle_cannot_be_forced_into_four_wheels(self):
        source = merged(four_parts()[:2])
        with self.assertRaisesRegex(GarageError, '1 occupied front/rear bands'):
            discover_tyre_corners(source, forward='+Z')

    def test_touching_projection_bands_are_ambiguous(self):
        parts = [circular_part(corner, CENTRES[corner], width=1.6) for corner in CORNERS]
        with self.assertRaisesRegex(GarageError, '1 occupied left/right bands'):
            discover_tyre_corners(merged(parts), forward='+Z')

    def test_extra_spare_surface_is_not_silently_dropped(self):
        parts = four_parts() + [circular_part('spare', np.array([.8, .34, 0]), radius=.2)]
        with self.assertRaisesRegex(GarageError, '3 occupied front/rear bands'):
            discover_tyre_corners(merged(parts), forward='+Z')

    def test_nearest_centre_disagreement_rejects_an_otherwise_separated_proposal(self):
        parts = four_parts()
        parts[0].positions[:, 1] = (parts[0].positions[:, 1] - .34) * 30 + 10
        source = merged(parts)
        with self.assertRaisesRegex(GarageError, 'ownership boundaries|disagree|equally close'):
            discover_tyre_corners(source, forward='+Z')

    def test_child_id_collisions_and_component_budget_are_enforced(self):
        source = merged(four_parts())
        cases = ([source.id + '__wheel_FL'], ['duplicate', 'duplicate'],
                 ['id_' + str(i) for i in range(MAX_PARTS - 3)], 'not_an_id_collection', None)
        for ids in cases:
            with self.subTest(ids=str(ids)[:60]), self.assertRaises(GarageError):
                discover_tyre_corners(source, forward='+Z', known_ids=ids)

    def test_envelope_centres_do_not_claim_native_semantics_or_runtime_acceptance(self):
        _, decision, report = discover_tyre_corners(merged(four_parts()), forward='+Z')
        self.assertEqual(report['status'], 'review_required')
        self.assertTrue(report['scope']['role_confirmation_required'])
        self.assertIn('not proven rotation hubs', report['scope']['centres'])
        self.assertFalse(report['scope']['native_output_verified'])
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(decision['runtime_verified'])
        self.assertIn('original upload bytes', report['scope']['input_proof'])


if __name__ == '__main__':
    unittest.main()
