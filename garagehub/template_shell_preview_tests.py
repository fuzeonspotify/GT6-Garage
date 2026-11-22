"""Assembled-preview contracts with independently serialized synthetic MDL3.

The synthetic wheel file exercises ordinary native parsing and complete draw
coverage. Authentication is explicitly substituted only for this fixture; real
owned-template/native/GUI checks are separate run receipts, not these tests.
"""
from __future__ import annotations

import copy
import json
import struct
import tempfile
import types
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .body_fit_preview_tests import decode, mesh_vertices
from .car_profiles import ASSETS, PROFILE
from .errors import GarageError
from .glb import GLB
from .native import MDL3
from .recognition import CORNERS, infer
from .template_shell import review_shell
from .template_shell_independent_tests import shell_fixture, wheel_free_glb
from .surface_realization_tests import synthetic_opaque_body
from .template_shell_preview import (build_template_shell_preview,
                                     decode_template_wheels, _direct_shape)
from .util import sha256


def synthetic_wheel():
    """Minimal ordinary MDL3 writer independent of the product decoder/editor."""
    data = bytearray(0x100)
    def put16(at, value): struct.pack_into('>H', data, at, value)
    def put32(at, value): struct.pack_into('>I', data, at, value)
    def alloc(raw, align=4):
        data.extend(bytes(-len(data) % align))
        at = len(data)
        data.extend(raw)
        return at
    models = alloc(bytes(8 * 48))
    shapes = alloc(bytes(16 * 48))
    layouts = alloc(bytes(120))
    layout_name = alloc(b'Flex\0', 1)
    fields = bytearray()
    for name, offset, count, fmt in [('position', 0, 3, 2), ('normal', 12, 1, 6),
                                    ('map12', 16, 2, 2), ('colorSet1', 24, 4, 4)]:
        pointer = alloc(name.encode() + b'\0', 1)
        fields.extend(struct.pack('>I4B', pointer, offset, count, fmt, 0))
    field_pointer = alloc(fields)
    put32(layouts, layout_name)
    put32(layouts + 8, field_pointer)
    data[layouts + 24:layouts + 26] = bytes((4, 28))
    for sid in range(4, 8):
        struct.pack_into('>h', data, shapes + sid * 48 + 2, -1)
    for ci, corner in enumerate(CORNERS):
        reference = PROFILE['wheel_geometry'][corner]
        width, radius = reference['tyre_width_metres'], reference['tyre_radius_metres']
        ids = (ci, 8 + 2 * ci, 9 + 2 * ci)
        program = b''.join(bytes((59, sid, ci)) for sid in ids) + b'\0'
        cp = alloc(program)
        put32(models + 48 * ci + 24, cp)
        put32(models + 48 * ci + 28, len(program))
        for role, sid in enumerate(ids):
            r = radius / (role + 1)
            points = np.array([[width / 2, -r, 0], [width / 2, r, 0],
                               [width / 2, 0, r]], dtype=float)
            vertex = b''.join(struct.pack('>3fI2f4B', *point, 1023, .25, .75,
                                         128, 128, 128, 255) for point in points)
            vp = alloc(vertex)
            ip = alloc(struct.pack('>3H', 0, 1, 2))
            at = shapes + 48 * sid
            struct.pack_into('>Hhh', data, at, 0x1809, 0, role)
            struct.pack_into('>II', data, at + 8, 3, vp)
            struct.pack_into('>II', data, at + 20, 3, ip)
            put16(at + 38, 1)
    data[:4] = b'MDL3'
    put32(4, len(data))
    put16(12, 14)
    put16(0x10, 8)
    put16(0x14, 16)
    put16(0x18, 1)
    put32(0x30, models)
    put32(0x38, shapes)
    put32(0x40, layouts)
    put32(0x6c, len(data))
    return bytes(data)


def body_only_fixture():
    """Keep only the original shell node in an authentic serialized GLB."""
    raw = wheel_free_glb()
    length = struct.unpack_from('<I', raw, 12)[0]
    doc = json.loads(raw[20:20 + length])
    doc['nodes'] = [doc['nodes'][0]]
    doc['scenes'][0]['nodes'] = [0]
    metadata = json.dumps(doc, separators=(',', ':')).encode()
    metadata += b' ' * (-len(metadata) % 4)
    tail = raw[20 + length:]
    serialized = struct.pack('<4sII', b'glTF', 2, 20 + len(metadata) + len(tail))
    serialized += struct.pack('<II', len(metadata), 0x4e4f534a) + metadata + tail
    scene = GLB(serialized).scene()
    _, project = shell_fixture()
    project['source_sha256'] = scene.digest
    project['inspection'] = infer(scene, project['forward'])
    return scene, project


@contextmanager
def preview_fixture(raw=None, *, native_surfaces=False):
    raw = synthetic_wheel() if raw is None else raw
    with tempfile.TemporaryDirectory() as temp:
        folder = Path(temp)
        (folder / 'hq').mkdir()
        (folder / 'hq/wheel').write_bytes(raw)
        hashes = {'hq/wheel': sha256(raw)}
        if native_surfaces:
            body, _ = synthetic_opaque_body()
            (folder / 'race').mkdir()
            for lod in ('hq', 'race'):
                (folder / lod / 'body').write_bytes(body)
                hashes[lod+'/body'] = sha256(body)
        with patch.dict(ASSETS, hashes), patch(
                'garagehub.template_shell_preview.validate_template',
                return_value={'root': str(folder), 'synthetic_authentication_boundary': True}) as auth:
            yield folder, auth


def current_review(scene, project, **changes):
    placement = changes.pop('placement', {'scale': 1., 'translation_metres': [0., .35, 0.]})
    report = review_shell(scene, project, placement, **changes)
    if not report['ready_for_candidate']:
        raise AssertionError(report['blockers'])
    return report


class TemplateShellPreviewTests(unittest.TestCase):
    def test_two_sided_low_target_preserves_split_normals_and_native_state_receipts(self):
        scene, project = shell_fixture(extra_material={'doubleSided': True})
        with preview_fixture(native_surfaces=True) as (folder, _):
            review = current_review(scene, project)
            # No second simplifier call may see the coincident opposite faces.
            def forbidden_simplifier(*args, **kwargs):
                raise AssertionError('Preview QEM must not see coincident front/back faces')
            with patch.dict('sys.modules', {'fast_simplification': types.SimpleNamespace(
                    simplify=forbidden_simplifier)}):
                raw, records = build_template_shell_preview(scene, project, review, folder, limit=1)
        header, _, _ = decode(raw)
        self.assertEqual(header['triangle_target'], 1)
        self.assertEqual(header['effective_triangle_target'], 42)
        self.assertFalse(header['additional_preview_simplification_allowed'])
        self.assertEqual(header['source_original_triangles'], 18)
        self.assertEqual(header['source_static_triangles'], 18)
        self.assertEqual(header['native_prepared_static_triangles'], 30)
        self.assertEqual(header['generated_backface_triangles'], 12)
        self.assertTrue(header['native_surface_state_validation'])
        record = next(row for row in records if row['id'] == scene.parts[0].id)
        self.assertEqual(record['surface_realization']['generated_backface_triangles'], 12)
        self.assertFalse(record['surface_simplified'])
        points, _, faces = mesh_vertices(raw, scene.parts[0].id)
        self.assertEqual(len(points), 16)
        np.testing.assert_array_equal(points[:8, :3], points[8:, :3])
        np.testing.assert_array_equal(points[:8, 3:], -points[8:, 3:])
        self.assertEqual(len(faces), 24)

    def test_authentic_source_transform_and_all_original_arrays_are_preserved(self):
        scene, project = shell_fixture()
        before = copy.deepcopy((scene, project))
        with preview_fixture() as (folder, auth):
            review = current_review(scene, project)
            raw, records = build_template_shell_preview(scene, project, review, folder)
            auth.assert_called_once_with(folder)
            matrix, shift = np.asarray(review['matrix']), np.asarray(review['translation_metres'])
            for part in scene.parts:
                points, _, _ = mesh_vertices(raw, part.id)
                np.testing.assert_allclose(points[:, :3], part.positions @ matrix.T + shift,
                                           rtol=0, atol=2e-7)
                expected = part.normals @ np.linalg.inv(matrix)
                expected /= np.linalg.norm(expected, axis=1)[:, None]
                np.testing.assert_allclose(points[:, 3:], expected, rtol=0, atol=2e-7)
        self.assertEqual(project, before[1])
        for old, new in zip(before[0].parts, scene.parts):
            for field in ('positions', 'normals', 'uv', 'indices'):
                np.testing.assert_array_equal(getattr(old, field), getattr(new, field))
        for field in ('raw', 'digest', 'document', 'materials', 'warnings'):
            self.assertEqual(getattr(before[0], field), getattr(scene, field))
        self.assertEqual(sum(row['component_origin'] == 'source' for row in records), 4)

    def test_complete_native_draws_keep_fixed_pose_when_body_moves_and_scales(self):
        scene, project = shell_fixture()
        with preview_fixture() as (folder, _):
            first, records = build_template_shell_preview(scene, project,
                current_review(scene, project), folder)
            moved = current_review(scene, project,
                placement={'scale': 1.4, 'translation_metres': [0., .6, .4]})
            second, changed = build_template_shell_preview(scene, project, moved, folder)
        wheels = [row for row in records if row['component_origin'] == 'native_template']
        self.assertEqual(len(wheels), 12)
        self.assertEqual(wheels, [row for row in changed if row['component_origin'] == 'native_template'])
        for row in wheels:
            np.testing.assert_array_equal(mesh_vertices(first, row['id'])[0],
                                          mesh_vertices(second, row['id'])[0])
            self.assertFalse(row['moves_with_body'])
        self.assertNotEqual(mesh_vertices(first, scene.parts[0].id)[0].tobytes(),
                            mesh_vertices(second, scene.parts[0].id)[0].tobytes())

    def test_native_corner_rotation_preserves_winding_and_scale(self):
        raw = synthetic_wheel()
        with patch.dict(ASSETS, {'hq/wheel': sha256(raw)}):
            rows, proof = decode_template_wheels(raw)
        self.assertEqual(len(proof['draws']), 12)
        for row in rows:
            reference = PROFILE['wheel_geometry'][row['corner']]
            # Every synthetic vertex lies on its wheel's exact axial centre.
            np.testing.assert_allclose(row['positions'][:, 0], reference['center_metres'][0],
                                       rtol=0, atol=2e-8)
            p = row['positions'][row['faces'][0]]
            cross = np.cross(p[1] - p[0], p[2] - p[0])
            self.assertGreater(float(np.dot(cross, row['normals'].mean(0))), 0.)
            rotation = np.asarray(row['local_to_reference_rotation'])
            self.assertAlmostEqual(float(np.linalg.det(rotation)), 1.)
            np.testing.assert_array_equal(rotation.T @ rotation, np.eye(3))

    def test_forged_report_geometry_cannot_override_fresh_review(self):
        scene, project = shell_fixture()
        with preview_fixture() as (folder, _):
            review = current_review(scene, project)
            expected, records = build_template_shell_preview(scene, project, review, folder)
            forged = copy.deepcopy(review)
            forged.update(matrix=np.diag([9., 9., 9.]).tolist(), translation_metres=[9., 9., 9.],
                          coverage=[], source_triangles=0)
            actual, changed = build_template_shell_preview(scene, project, forged, folder)
        self.assertEqual(actual, expected)
        self.assertEqual(changed, records)

    def test_stale_inputs_or_digest_fail_before_native_template_access(self):
        for condition in ('digest', 'placement', 'destination', 'source'):
            with self.subTest(condition=condition), preview_fixture() as (folder, auth):
                scene, project = shell_fixture()
                review = current_review(scene, project)
                if condition == 'digest': review['review_digest'] = '0' * 64
                elif condition == 'placement': review['placement']['translation_metres'][1] += .1
                elif condition == 'destination': project['installation'] = {'mode': 'replace', 'replace_car_id': 82}
                else: scene.parts[0].positions[0, 1] += .1
                with self.assertRaises(GarageError):
                    build_template_shell_preview(scene, project, review, folder)
                auth.assert_not_called()

    def test_unknown_asset_and_incomplete_draw_programs_are_rejected(self):
        raw = synthetic_wheel()
        with self.assertRaises(GarageError):
            decode_template_wheels(raw)
        model = MDL3(raw)
        at = model.u32(0x30)
        cp = model.u32(at + 24)
        for changes in ((cp, 0), (cp + 1, 4), (cp + 2, 1)):
            altered = bytearray(raw)
            altered[changes[0]] = changes[1]
            with self.subTest(changes=changes), patch.dict(ASSETS, {'hq/wheel': sha256(altered)}):
                with self.assertRaises(GarageError):
                    decode_template_wheels(bytes(altered))

    def test_unsupported_layout_payloads_and_invalid_native_geometry_are_rejected(self):
        raw = synthetic_wheel()
        model = MDL3(raw)
        at = model.u32(0x38)
        vp, ip = model.u32(at + 12), model.u32(at + 24)
        cases = [('position_nan', vp, struct.pack('>f', float('nan'))),
                 ('zero_normal', vp + 12, bytes(4)),
                 ('bad_index', ip, struct.pack('>H', 999)),
                 ('shape_extension', at + 44, struct.pack('>I', 1)),
                 ('wrong_stride', model.u32(0x40) + 25, bytes((24,))),
                 ('streamed_pointer', at + 12, bytes(4))]
        for name, offset, value in cases:
            altered = bytearray(raw)
            altered[offset:offset + len(value)] = value
            with self.subTest(name=name), self.assertRaises(GarageError):
                _direct_shape(MDL3(bytes(altered)), 0)

    def test_generated_lamps_are_from_same_assembly_and_distinct_from_original_faces(self):
        from .template_shell import assemble_shell
        scene, project = body_only_fixture()
        with preview_fixture() as (folder, _):
            review = current_review(scene, project, lighting={'mode': 'supplied'})
            raw, records = build_template_shell_preview(scene, project, review, folder)
            assembled, _, generated = assemble_shell(scene, project, review)
        sources = [row for row in records if row['component_origin'] == 'source']
        lights = [row for row in records if row['component_origin'] == 'supplied_lamp_kit']
        self.assertEqual(len(sources), 1)
        self.assertEqual({row['id'] for row in lights}, set(generated['generated_part_ids']))
        self.assertGreater(len(lights), 0)
        lookup = {p.id: p for p in assembled.parts}
        for row in lights:
            self.assertTrue(row['moves_with_body'])
            self.assertIsNone(row['source_part_id'])
            part = lookup[row['id']]
            expected = part.positions @ np.asarray(review['matrix']).T + review['translation_metres']
            np.testing.assert_allclose(mesh_vertices(raw, row['id'])[0][:, :3], expected,
                                       rtol=0, atol=2e-7)
        header, _, _ = decode(raw)
        self.assertEqual(header['source_original_triangles'], 12)
        self.assertEqual(header['source_static_triangles'], 12)
        self.assertGreater(header['native_prepared_static_triangles'], 12)

    def test_explicit_excluded_source_is_accounted_but_not_drawn(self):
        scene, project = shell_fixture(with_badge=True)
        badge = scene.parts[-1].id
        project['overrides'][badge] = {'role': 'ignore', 'corner': None}
        project['inspection'] = infer(scene, project['forward'], project['overrides'])
        dispositions = {badge: {'disposition': 'excluded', 'reason': 'Optional roof badge removed by player.'}}
        with preview_fixture() as (folder, _):
            review = current_review(scene, project, dispositions=dispositions)
            raw, records = build_template_shell_preview(scene, project, review, folder)
        self.assertNotIn(badge, [row['id'] for row in records])
        header, _, _ = decode(raw)
        self.assertEqual(header['source_original_triangles'], 20)
        self.assertEqual(header['source_static_triangles'], 18)

    def test_binary_parts_and_scope_metadata_describe_the_actual_buffer(self):
        scene, project = shell_fixture()
        with preview_fixture() as (folder, _):
            review = current_review(scene, project)
            raw, records = build_template_shell_preview(scene, project, review, folder)
        header, vertices, indices = decode(raw)
        self.assertEqual(header['template_shell_digest'], review['review_digest'])
        self.assertTrue(header['actual_native_wheel_geometry'])
        self.assertTrue(header['native_wheel_geometry']['complete_stationary_draw_coverage'])
        self.assertFalse(header['runtime_verified'])
        self.assertFalse(header['current_mount_verified'])
        self.assertFalse(header['build_approved'])
        self.assertIn('native game shaders and textures are not rendered', header['material_scope'])
        self.assertEqual(header['native_wheel_preview_triangles'], 12)
        self.assertFalse(header['native_wheel_geometry_simplified'])
        self.assertEqual(header['preview_triangles'], 30)
        self.assertEqual(len(records), len(header['meshes']))
        cursor = 0
        for row, mesh in zip(records, header['meshes']):
            self.assertEqual(row['id'], mesh['id'])
            self.assertEqual(cursor, mesh['index_start'])
            cursor += mesh['index_count']
            self.assertEqual(row['triangles'] * 3, mesh['index_count'])
            points = vertices[np.unique(indices[mesh['index_start']:cursor]), :3].astype(float)
            np.testing.assert_array_equal(row['bounds'], [points.min(0), points.max(0)])
            self.assertEqual(row['moves_with_body'], mesh['moves_with_body'])
        self.assertEqual(cursor, len(indices))
        json.dumps(records, allow_nan=False)

    def test_invalid_preview_limits_and_missing_review_fail_before_native_access(self):
        scene, project = shell_fixture()
        with preview_fixture() as (folder, auth):
            review = current_review(scene, project)
            for limit in (0, -1, True, '10', 1.2):
                with self.subTest(limit=limit), self.assertRaises(GarageError):
                    build_template_shell_preview(scene, project, review, folder, limit)
            for value in (None, {}, {'review_digest': None}):
                with self.subTest(review=value), self.assertRaises(GarageError):
                    build_template_shell_preview(scene, project, value, folder)
            auth.assert_not_called()
