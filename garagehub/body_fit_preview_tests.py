"""Preview geometry checks use synthetic source assets and fixed references."""
from __future__ import annotations

import copy
import json
import struct
import unittest

import numpy as np

from .body_fit_preview import build_body_fit_preview
from .errors import GarageError
from .glb import Part, Scene


def fixture(all_roles=False):
    positions = np.array([[0., 0., 0.], [1., 0., 1.], [0., 1., 1.]], dtype=np.float32)
    normal = np.array([-1., -1., 1.], dtype=np.float32) / np.sqrt(3.)
    roles = ('body', 'paint', 'glass', 'headlight', 'taillight', 'brakelight', 'interior',
             'rim', 'tyre', 'valve', 'brake_rotor', 'brake_caliper', 'ignore') if all_roles else ('body',)
    parts, records = [], []
    for index, role in enumerate(roles):
        parts.append(Part(role, 'Source ' + role, index, 0, 0,
                          positions.copy() + [index * .01, 0., 0.],
                          np.tile(normal, (3, 1)), np.array([[0., 0.], [1., 0.], [0., 1.]], dtype=np.float32),
                          np.array([[0, 1, 2]], dtype=np.uint32),
                          extras={'authored': [1, 2]}, warnings=['Original warning']))
        records.append({'id': role, 'role': role, 'corner': 'FL' if role in
                        ('rim', 'tyre', 'valve', 'brake_rotor', 'brake_caliper') else None})
    scene = Scene(parts, [{'id': 0, 'name': 'Source paint', 'color': [.7, .1, .1, 1.],
                          'metallic': .2, 'roughness': .5, 'base_texture': None}],
                  {'asset': {'version': '2.0', 'extras': {'preserve': True}}}, b'original source bytes', 'a' * 64,
                  ['Source warning'])
    reference = {'profile_id': 'synthetic-fixed-reference', 'reference_digest': 'b' * 64, 'forward': '-Z',
        'wheel_geometry': {corner: {'center_metres': [x, y, z], 'tyre_radius_metres': radius,
                                    'tyre_width_metres': width}
            for corner, x, y, z, radius, width in (
                ('FL', -.75, .2, -1.3, .32, .22), ('FR', .75, .2, -1.3, .32, .22),
                ('RL', -.8, .21, 1.3, .34, .28), ('RR', .8, .21, 1.3, .34, .28))}}
    review = {'available': True, 'within_profile_limits': True, 'runtime_verified': False,
              'review_digest': 'c' * 64, 'reference': reference,
              'matrix': [[0., -3., 0.], [2., 0., 0.], [0., 0., .5]],
              'translation_metres': [.1, .2, -.3]}
    return scene, {'parts': records}, review


def decode(raw):
    if raw[:4] != b'GGV1':
        raise AssertionError('Wrong preview magic')
    size, vertex_count, index_count = struct.unpack_from('<III', raw, 4)
    if size % 4 or len(raw) != 16 + size + vertex_count * 24 + index_count * 4:
        raise AssertionError('Preview buffer/header lengths disagree')
    header = json.loads(raw[16:16 + size])
    vertices = np.frombuffer(raw, dtype='<f4', offset=16 + size, count=vertex_count * 6).reshape(-1, 6)
    indices = np.frombuffer(raw, dtype='<u4', offset=16 + size + vertex_count * 24, count=index_count)
    return header, vertices, indices


def mesh_vertices(raw, identifier):
    header, vertices, indices = decode(raw)
    mesh = next(item for item in header['meshes'] if item['id'] == identifier)
    faces = indices[mesh['index_start']:mesh['index_start'] + mesh['index_count']].reshape(-1, 3)
    return vertices[np.unique(faces)], vertices, faces


class BodyFitPreviewTests(unittest.TestCase):
    def test_nonuniform_body_transform_preserves_oblique_normal_direction(self):
        scene, inspection, review = fixture()
        raw, _ = build_body_fit_preview(scene, inspection, review)
        selected, vertices, faces = mesh_vertices(raw, 'body')
        matrix = np.asarray(review['matrix'])
        expected_positions = scene.parts[0].positions @ matrix.T + review['translation_metres']
        expected_normals = scene.parts[0].normals @ np.linalg.inv(matrix)
        expected_normals /= np.linalg.norm(expected_normals, axis=1, keepdims=True)
        np.testing.assert_allclose(selected[:, :3], expected_positions, rtol=0, atol=2e-7)
        np.testing.assert_allclose(selected[:, 3:], expected_normals, rtol=0, atol=1e-7)
        face = vertices[faces[0]]
        for edge in (face[1, :3] - face[0, :3], face[2, :3] - face[0, :3]):
            self.assertAlmostEqual(float(np.dot(face[0, 3:], edge)), 0., places=6)
        wrong = scene.parts[0].normals @ matrix.T
        wrong /= np.linalg.norm(wrong, axis=1, keepdims=True)
        self.assertGreater(float(np.max(np.abs(selected[:, 3:] - wrong))), .3)

    def test_exact_source_geometry_and_all_review_inputs_remain_unchanged(self):
        scene, inspection, review = fixture(all_roles=True)
        before = copy.deepcopy((scene, inspection, review))
        raw, metadata = build_body_fit_preview(scene, inspection, review)
        self.assertEqual(inspection, before[1])
        self.assertEqual(review, before[2])
        for field in ('materials', 'document', 'raw', 'digest', 'warnings'):
            self.assertEqual(getattr(scene, field), getattr(before[0], field))
        for old, current in zip(before[0].parts, scene.parts):
            for field in ('positions', 'normals', 'uv', 'indices'):
                self.assertEqual(getattr(current, field).dtype, getattr(old, field).dtype)
                self.assertEqual(getattr(current, field).tobytes(), getattr(old, field).tobytes())
            for field in ('id', 'name', 'node', 'primitive', 'material', 'extras', 'warnings'):
                self.assertEqual(getattr(current, field), getattr(old, field))
        metadata[0]['center'][0] = 999.
        header, _, _ = decode(raw)
        header['materials'][0]['color'][0] = 999.
        self.assertEqual(scene.materials[0]['color'][0], .7)

    def test_fixed_envelopes_do_not_follow_source_dimensions_or_body_transform(self):
        scene, inspection, review = fixture()
        raw, records = build_body_fit_preview(scene, inspection, review)
        altered = copy.deepcopy(scene)
        altered.parts[0].positions *= 3
        altered_review = copy.deepcopy(review)
        altered_review['matrix'] = [[1., 0., 0.], [0., 1., 0.], [0., 0., 1.]]
        altered_review['translation_metres'] = [4., 5., 6.]
        changed, changed_records = build_body_fit_preview(altered, inspection, altered_review)
        guides = [row for row in records if row['is_reference_guide']]
        self.assertEqual(len(guides), 4)
        self.assertEqual(guides, [row for row in changed_records if row['is_reference_guide']])
        for row in guides:
            first, _, _ = mesh_vertices(raw, row['id'])
            second, _, _ = mesh_vertices(changed, row['id'])
            np.testing.assert_array_equal(first, second)
            reference = review['reference']['wheel_geometry'][row['corner']]
            center = np.array(reference['center_metres'])
            radius, width = reference['tyre_radius_metres'], reference['tyre_width_metres']
            half = np.array([width / 2, radius, radius])
            np.testing.assert_allclose(row['bounds'], [center - half, center + half], rtol=0, atol=1e-7)
            np.testing.assert_allclose(row['center'], center, rtol=0, atol=1e-7)
            self.assertLessEqual(float(np.max(np.linalg.norm(first[:, 1:3] - center[1:], axis=1))), radius + 1e-7)
            self.assertEqual(row['role'], 'body')
            self.assertFalse(row['native_tyre_rendered'])
            self.assertFalse(row['current_load_state_verified'])

    def test_static_roles_are_shown_and_source_mechanics_are_excluded(self):
        scene, inspection, review = fixture(all_roles=True)
        raw, records = build_body_fit_preview(scene, inspection, review)
        header, _, _ = decode(raw)
        expected = {'body', 'paint', 'glass', 'headlight', 'taillight', 'brakelight', 'interior'}
        self.assertEqual({row['id'] for row in records if not row['is_reference_guide']}, expected)
        self.assertEqual(set(header['excluded_source_part_ids']),
                         {'rim', 'tyre', 'valve', 'brake_rotor', 'brake_caliper', 'ignore'})
        self.assertEqual(header['source_static_triangles'], len(expected))
        self.assertEqual(header['original_source_triangles'], scene.triangles)
        guide = header['materials'][-1]
        self.assertGreater(guide['color'][1], guide['color'][0] * 5)
        self.assertGreater(guide['color'][1], guide['color'][2] * 2)

    def test_binary_metadata_ranges_centres_bounds_and_counts_agree(self):
        scene, inspection, review = fixture(all_roles=True)
        raw, records = build_body_fit_preview(scene, inspection, review)
        header, vertices, indices = decode(raw)
        self.assertEqual([row['id'] for row in records], [mesh['id'] for mesh in header['meshes']])
        self.assertEqual(header['vertices'], len(vertices))
        self.assertEqual(header['indices'], len(indices))
        self.assertEqual(header['preview_triangles'] * 3, len(indices))
        self.assertEqual(header['body_preview_triangles'] + header['reference_guide_triangles'], len(indices) // 3)
        self.assertTrue(np.isfinite(vertices).all())
        self.assertLess(int(indices.max()), len(vertices))
        cursor = 0
        for row, mesh in zip(records, header['meshes']):
            self.assertEqual(mesh['index_start'], cursor)
            cursor += mesh['index_count']
            self.assertEqual(row['material'], mesh['material'])
            self.assertEqual(row['triangles'] * 3, mesh['index_count'])
            points, _, _ = mesh_vertices(raw, row['id'])
            bounds = np.array([points[:, :3].min(0), points[:, :3].max(0)], dtype=float)
            np.testing.assert_array_equal(row['bounds'], bounds)
            np.testing.assert_array_equal(row['center'], bounds.mean(0))
        self.assertEqual(cursor, len(indices))
        json.dumps(records, allow_nan=False)

    def test_outside_profile_limits_is_previewable_without_approval(self):
        scene, inspection, review = fixture()
        review['within_profile_limits'] = False
        raw, _ = build_body_fit_preview(scene, inspection, review)
        header, _, _ = decode(raw)
        self.assertTrue(header['preview_only'])
        self.assertFalse(header['runtime_verified'])
        self.assertFalse(header['build_approved'])
        self.assertFalse(header['source_inputs_changed'])
        self.assertEqual(header['body_fit_digest'], review['review_digest'])
        self.assertEqual(header['reference_digest'], review['reference']['reference_digest'])
        self.assertEqual(header['scope'], 'body_fit_preview_with_reference_envelopes')

    def test_guide_ids_do_not_collide_with_source_ids(self):
        scene, inspection, review = fixture()
        scene.parts[0].id = '__gt6_body_fit_reference_FL'
        inspection['parts'][0]['id'] = scene.parts[0].id
        raw, records = build_body_fit_preview(scene, inspection, review)
        identifiers = [row['id'] for row in records]
        self.assertEqual(len(set(identifiers)), len(identifiers))
        self.assertEqual(records[0]['id'], scene.parts[0].id)
        guide = next(row for row in records if row['is_reference_guide'] and row['corner'] == 'FL')
        self.assertEqual(guide['id'], '__gt6_body_fit_reference_FL_1')
        self.assertEqual([row['id'] for row in decode(raw)[0]['meshes']], identifiers)

    def test_reflected_preview_keeps_authored_front_surface_and_source_faces(self):
        scene, inspection, review = fixture()
        review['matrix'] = [[-1., 0., 0.], [0., 2., 0.], [0., 0., 3.]]
        old_faces = scene.parts[0].indices.copy()
        raw, _ = build_body_fit_preview(scene, inspection, review)
        _, vertices, faces = mesh_vertices(raw, 'body')
        triangle = vertices[faces[0]]
        geometric = np.cross(triangle[1, :3] - triangle[0, :3], triangle[2, :3] - triangle[0, :3])
        self.assertGreater(float(np.dot(geometric, triangle[:, 3:].mean(0))), 0.)
        np.testing.assert_array_equal(scene.parts[0].indices, old_faces)

    def test_invalid_fit_matrices_translations_and_review_proof_are_rejected(self):
        _, _, original = fixture()
        changes = [({'available': False}), ({'matrix': [[1., 0.], [0., 1.]]}),
                   ({'matrix': np.zeros((3, 3)).tolist()}),
                   ({'matrix': np.diag([1., 1., 1e-20]).tolist()}),
                   ({'matrix': [[float('nan'), 0., 0.], [0., 1., 0.], [0., 0., 1.]]}),
                   ({'matrix': np.eye(3, dtype=bool).tolist()}),
                   ({'translation_metres': [0., float('inf'), 0.]}),
                   ({'review_digest': None})]
        for change in changes:
            with self.subTest(change=change):
                scene, inspection, _ = fixture()
                review = copy.deepcopy(original)
                review.update(change)
                with self.assertRaises(GarageError) as caught:
                    build_body_fit_preview(scene, inspection, review)
                self.assertEqual(caught.exception.code, 'body_fit_preview')

    def test_missing_static_body_invalid_geometry_and_stale_assignments_are_rejected(self):
        for failure in ('all_mechanical', 'empty_vertices', 'bad_indices', 'nan_position', 'zero_normal',
                        'missing_assignment', 'duplicate_assignment', 'unknown_role'):
            scene, inspection, review = fixture()
            if failure == 'all_mechanical':
                inspection['parts'][0]['role'] = 'tyre'
            elif failure == 'empty_vertices':
                scene.parts[0].positions = np.empty((0, 3))
            elif failure == 'bad_indices':
                scene.parts[0].indices[0, 2] = 100
            elif failure == 'nan_position':
                scene.parts[0].positions[0, 0] = float('nan')
            elif failure == 'zero_normal':
                scene.parts[0].normals[0] = 0
            elif failure == 'missing_assignment':
                inspection['parts'] = []
            elif failure == 'duplicate_assignment':
                inspection['parts'].append(copy.deepcopy(inspection['parts'][0]))
            elif failure == 'unknown_role':
                inspection['parts'][0]['role'] = 'unreviewed'
            with self.subTest(failure=failure), self.assertRaises(GarageError) as caught:
                build_body_fit_preview(scene, inspection, review)
            self.assertEqual(caught.exception.code, 'body_fit_preview')

    def test_invalid_reference_envelopes_fail_without_returning_geometry(self):
        for failure in ('missing_corner', 'unknown_axis', 'missing_digest', 'negative_radius',
                        'boolean_width', 'nonfinite_center', 'overflow_radius'):
            scene, inspection, review = fixture()
            reference = review['reference']
            if failure == 'missing_corner':
                reference['wheel_geometry'].pop('RR')
            elif failure == 'unknown_axis':
                reference['forward'] = '+Z'
            elif failure == 'missing_digest':
                reference.pop('reference_digest')
            elif failure == 'negative_radius':
                reference['wheel_geometry']['FL']['tyre_radius_metres'] = -1
            elif failure == 'boolean_width':
                reference['wheel_geometry']['FL']['tyre_width_metres'] = True
            elif failure == 'nonfinite_center':
                reference['wheel_geometry']['FL']['center_metres'][0] = float('inf')
            elif failure == 'overflow_radius':
                reference['wheel_geometry']['FL']['tyre_radius_metres'] = 10 ** 1000
            with self.subTest(failure=failure), self.assertRaises(GarageError) as caught:
                build_body_fit_preview(scene, inspection, review)
            self.assertEqual(caught.exception.code, 'body_fit_preview')

    def test_small_preview_target_keeps_fixed_guides_and_complete_small_surfaces(self):
        scene, inspection, review = fixture()
        full, full_rows = build_body_fit_preview(scene, inspection, review)
        small, small_rows = build_body_fit_preview(scene, inspection, review, limit=128)
        first, second = decode(full), decode(small)
        np.testing.assert_array_equal(first[1], second[1])
        np.testing.assert_array_equal(first[2], second[2])
        self.assertEqual(full_rows, small_rows)
        self.assertEqual(second[0]['triangle_target'], 128)
        self.assertEqual(second[0]['reference_guide_triangles'], first[0]['reference_guide_triangles'])
        for limit in (0, -1, True, 1.5):
            with self.subTest(limit=limit), self.assertRaises(GarageError):
                build_body_fit_preview(scene, inspection, review, limit=limit)


if __name__ == '__main__':
    unittest.main()
