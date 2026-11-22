"""Mechanical geometry and exact command-operand safety checks, without GT6."""
from __future__ import annotations

import copy
import struct
import unittest
import numpy as np

from .errors import GarageError
from .native import MDL3, pack_normal
from .util import sha256
from .mechanical_native import append_dynamic_components, prepare_component, CORNERS, ROLES


class RecordingEditor:
    """Real original MDL3 header/program bytes with recorded authoring writes."""
    def __init__(self):
        b = bytearray(4096)
        b[:4] = b'MDL3'
        struct.pack_into('>IIH', b, 4, len(b), 0, 14)
        struct.pack_into('>I', b, 0x6c, len(b))
        struct.pack_into('>H', b, 0x10, 2)
        struct.pack_into('>H', b, 0x14, 8)
        struct.pack_into('>H', b, 0x18, 1)
        for offset, pointer in ((0x30, 0x100), (0x38, 0x200), (0x40, 0x400)):
            struct.pack_into('>I', b, offset, pointer)
        for sid in range(8):
            struct.pack_into('>Hhh', b, 0x200+48*sid, 0x1001, -1, 0)
            struct.pack_into('>I', b, 0x200+48*sid+44, 0x900+sid*16)
        for at, name in ((0x740, 'position'), (0x750, 'normal'), (0x760, 'map12'), (0x770, 'Flex')):
            b[at:at+len(name)+1] = name.encode()+b'\0'
        struct.pack_into('>III', b, 0x400, 0x770, 0, 0x700)
        b[0x418:0x41a] = bytes((3, 24))
        for at, name, fields in ((0x700, 0x740, (0, 3, 2, 0)),
                                  (0x708, 0x750, (12, 1, 6, 0)),
                                  (0x710, 0x760, (16, 2, 2, 0))):
            struct.pack_into('>I4B', b, at, name, *fields)
        self.calls = {}
        for mid in (0, 1):
            # The 0x3b within an opaque command operand is intentionally not a
            # draw instruction. A broad byte-pattern search would corrupt it.
            program = bytearray(b'\x2b\x3b\x11\x22\x33')
            for ci, corner in enumerate(CORNERS):
                program += bytes((14, 6, 11+ci))
                for role, delta in (('brake_caliper', 0), ('brake_rotor', 1)):
                    if delta:
                        program += bytes((14, 6, 15+ci))
                    offset = len(program)
                    context = 1+2*ci+delta+17*mid
                    program += bytes((59, 2*ci+delta, context))
                    self.calls.setdefault((role, corner), []).append({'model': mid, 'offset': offset, 'context': context})
                program += bytes((15, 15))
            program += b'\0'
            pointer = 0xa00+mid*0x100
            b[pointer:pointer+len(program)] = program
            struct.pack_into('>II', b, 0x100+48*mid+24, pointer, len(program))
        self.raw = bytes(b)
        self.b = b
        self.m = MDL3(self.raw)
        self.m.validate()
        self.finished = False
        self.fvf_rows = [bytearray(b[0x400:0x478])]
        self.shape_rows = [bytearray(b[0x200+i*48:0x200+(i+1)*48]) for i in range(8)]
        self.bank_size = 3
        self.new_materials = []
        self.emitted, self.changed = [], {}

    def shape(self, vb, ib, points, fvf, material, label, **kwargs):
        sid = len(self.shape_rows)
        self.shape_rows.append(bytearray(48))
        self.emitted.append(dict(sid=sid, vb=vb, ib=ib, points=points.copy(), fvf=fvf,
                                 material=material, label=label, kwargs=kwargs))
        return sid

    def model(self, mid, program, materials, points):
        self.changed[mid] = dict(program=program, materials=set(materials), points=points.copy())
        pointer = len(self.b)
        self.b.extend(program)
        struct.pack_into('>II', self.b, 0x100+48*mid+24, pointer, len(program))

    def routes(self):
        programs = {}
        for mid in (0, 1):
            at = 0x100+48*mid
            programs[str(mid)] = sha256(self.m.span(self.m.u32(at+24), self.m.u32(at+28)))
        return {'asset_sha256': sha256(self.raw), 'programs': programs, 'evidence': {'fixture': 'serialized commands'},
                'components': [{'role': role, 'corner': corner, 'original_shape': 2*ci+delta,
                                'calls': copy.deepcopy(self.calls[role, corner])}
                               for ci, corner in enumerate(CORNERS)
                               for role, delta in (('brake_caliper', 0), ('brake_rotor', 1))]}


def components():
    parts, frames = [], {}
    for ci, corner in enumerate(CORNERS):
        center = np.array([.8 if corner[1] == 'L' else -.8, .35, 1.3 if corner[0] == 'F' else -1.3])
        for role in ROLES:
            # Offset caliper geometry is legitimate; its own AABB centre is
            # deliberately nowhere near the selected tyre/hub centre.
            local = np.array([[.04, .16, .02], [.05, .25, .02], [.05, .25, .08], [.04, .16, .08]])
            rotation = np.diag([-1., 1., -1.]) if corner[1] == 'R' else np.eye(3)
            name = role+'_'+corner
            parts.append({'id': name, 'role': role, 'corner': corner,
                          'positions': local+center, 'normals': np.tile([1., 0., 0.], (4, 1)),
                          'uv': np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]]),
                          'faces': np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32),
                          'native_material': 0, 'native_fvf': 0})
            frames[role+':'+corner] = {'units': 'metres', 'pivot_kind': 'tyre_hub_center',
                                      'motion_intent': ROLES[role], 'source_center_metres': center.tolist(),
                                      'source_to_local_rotation': rotation.tolist(), 'axial_offset_metres': .015,
                                      'radial_offset_metres': [0., 0.],
                                      'reference_local_to_body': np.column_stack((rotation, center)).tolist(),
                                      'evidence': {'fixture': 'explicit measured frame'}}
    return parts, frames


class MechanicalNativeTests(unittest.TestCase):
    def assert_preflight_rejected(self, editor, parts, routes, frames):
        before = bytes(editor.b), [bytes(row) for row in editor.shape_rows]
        with self.assertRaises(GarageError):
            append_dynamic_components(editor, parts, routes, frames)
        self.assertEqual((bytes(editor.b), [bytes(row) for row in editor.shape_rows]), before)
        self.assertEqual(editor.emitted, [])
        self.assertEqual(editor.changed, {})

    def test_rigid_component_preserves_authored_geometry_uv_normals_and_winding(self):
        parts, frames = components()
        before = copy.deepcopy(parts)
        for part in parts:
            frame = frames[part['role']+':'+part['corner']]
            actual = prepare_component(part, frame)
            distances = lambda a: np.linalg.norm(a[:, None]-a[None, :], axis=2)
            np.testing.assert_allclose(distances(actual['positions']), distances(part['positions']), rtol=0, atol=2e-16)
            np.testing.assert_array_equal(actual['faces'], part['faces'])
            np.testing.assert_array_equal(actual['uv'], part['uv'])
            np.testing.assert_allclose(np.linalg.norm(actual['normals'], axis=1), 1., rtol=0, atol=1e-14)
        for old, part in zip(before, parts):
            for key in ('positions', 'normals', 'uv', 'faces'):
                np.testing.assert_array_equal(old[key], part[key])

    def test_off_axis_caliper_uses_tyre_hub_and_retains_its_authored_offset(self):
        parts, frames = components()
        part = next(p for p in parts if p['role'] == 'brake_caliper' and p['corner'] == 'FL')
        actual = prepare_component(part, frames['brake_caliper:FL'])
        np.testing.assert_allclose(actual['positions'][:, 1:].mean(0), [.205, .05], rtol=0, atol=1e-15)
        self.assertFalse(np.allclose(actual['positions'].mean(0), [.015, 0., 0.]))

    def test_all_four_corner_routes_change_only_draw_shape_operands(self):
        editor = RecordingEditor()
        parts, frames = components()
        routes = editor.routes()
        original_shapes = [bytes(row) for row in editor.shape_rows]
        receipt = append_dynamic_components(editor, parts, routes, frames)
        self.assertEqual(receipt['source_triangles'], receipt['native_triangles'])
        self.assertEqual(receipt['native_triangles'], 16)
        self.assertEqual(len(editor.emitted), 8)
        self.assertEqual([bytes(row) for row in editor.shape_rows[:8]], original_shapes)
        for report in receipt['programs']:
            mid = report['model']
            at = editor.m.u32(0x30)+48*mid
            old = editor.m.span(editor.m.u32(at+24), editor.m.u32(at+28))
            new = editor.changed[mid]['program']
            expected = {row['offset'] for row in report['shape_operands']}
            self.assertEqual({i for i, (a, b) in enumerate(zip(old, new)) if a != b}, expected)
            self.assertEqual(new[:5], b'\x2b\x3b\x11\x22\x33')
            self.assertEqual(len(new), len(old))
            for row in report['shape_operands']:
                self.assertEqual(new[row['offset']-1], 59)
                self.assertEqual(new[row['offset']+1], row['context_preserved'])
        self.assertFalse(receipt['installable'])
        self.assertFalse(receipt['runtime_verified'])
        self.assertFalse(receipt['physics_or_mounts_changed'])

    def test_single_selected_rotor_preserves_calipers_and_other_corners(self):
        editor = RecordingEditor()
        parts, frames = components()
        chosen = [p for p in parts if p['role'] == 'brake_rotor' and p['corner'] == 'RR']
        receipt = append_dynamic_components(editor, chosen, editor.routes(), frames)
        self.assertEqual(len(receipt['shapes']), 1)
        self.assertEqual([len(row['shape_operands']) for row in receipt['programs']], [1, 1])

    def test_serialized_vertex_attributes_match_transformed_source_triangles(self):
        editor = RecordingEditor()
        parts, frames = components()
        append_dynamic_components(editor, parts[:1], editor.routes(), frames)
        item = editor.emitted[0]
        prepared = prepare_component(parts[0], frames[parts[0]['role']+':'+parts[0]['corner']])
        indices = np.frombuffer(item['ib'], dtype='>u2').reshape(-1, 3)
        points = np.ndarray((4, 3), dtype='>f4', buffer=item['vb'], strides=(24, 4))
        uv = np.ndarray((4, 2), dtype='>f4', buffer=item['vb'], offset=16, strides=(24, 4))
        np.testing.assert_allclose(points[indices], prepared['positions'][prepared['faces']], rtol=0, atol=2e-8)
        np.testing.assert_array_equal(uv[indices], prepared['uv'][prepared['faces']])
        for i, n in enumerate(prepared['normals']):
            self.assertEqual(item['vb'][i*24+12:i*24+16], pack_normal(n))

    def test_source_parts_with_same_recipe_share_one_route_without_losing_faces(self):
        editor = RecordingEditor()
        parts, frames = components()
        second = copy.deepcopy(parts[0])
        second['id'] += '_separate'
        second['positions'][:, 0] += .01
        receipt = append_dynamic_components(editor, [parts[0], second], editor.routes(), frames)
        self.assertEqual(receipt['native_triangles'], 4)
        self.assertEqual(receipt['shapes'][0]['vertices'], 8)
        self.assertEqual(len(receipt['shapes'][0]['source_parts']), 2)

    def test_rotor_bounds_include_complete_spin_sweep(self):
        parts, frames = components()
        rotor = parts[0]
        prepared = prepare_component(rotor, frames[rotor['role']+':'+rotor['corner']])
        center = np.array(frames[rotor['role']+':'+rotor['corner']]['source_center_metres'])
        relative_bounds = prepared['body_reference_points']-center
        self.assertLess(relative_bounds[:, 1].min(), -.25)
        self.assertGreater(relative_bounds[:, 2].max(), .25)

    def test_rejects_scale_shear_reflection_and_added_radial_alignment(self):
        for matrix in (np.diag([.8, 1., 1.]), np.diag([-1., 1., 1.]), [[1., .1, 0.], [0., 1., 0.], [0., 0., 1.]]):
            editor = RecordingEditor()
            parts, frames = components()
            frames['brake_rotor:FL']['source_to_local_rotation'] = np.array(matrix).tolist()
            self.assert_preflight_rejected(editor, parts, editor.routes(), frames)
        editor = RecordingEditor()
        parts, frames = components()
        frames['brake_rotor:FL']['radial_offset_metres'] = [.01, 0.]
        self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_rejects_component_bbox_pivot_and_swapped_motion_intent(self):
        for key, value in (('pivot_kind', 'component_aabb_center'), ('motion_intent', 'upright_nonspin'), ('units', 'millimetres')):
            editor = RecordingEditor()
            parts, frames = components()
            frames['brake_rotor:FL'][key] = value
            self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_rejects_stale_asset_program_shape_context_and_duplicate_operand(self):
        for mutation in ('asset', 'program', 'shape', 'context', 'duplicate'):
            editor = RecordingEditor()
            parts, frames = components()
            routes = editor.routes()
            if mutation == 'asset': routes['asset_sha256'] = '0'*64
            elif mutation == 'program': routes['programs']['0'] = '0'*64
            elif mutation == 'shape': routes['components'][0]['original_shape'] = 7
            elif mutation == 'context': routes['components'][0]['calls'][0]['context'] = 90
            else: routes['components'][0]['calls'].append(copy.deepcopy(routes['components'][0]['calls'][0]))
            self.assert_preflight_rejected(editor, parts, routes, frames)

    def test_preexisting_program_authoring_is_not_overwritten(self):
        editor = RecordingEditor()
        parts, frames = components()
        pointer = editor.m.u32(0x100+24)
        editor.b[pointer] ^= 1
        self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_missing_unknown_or_duplicate_routes_fail_before_native_writes(self):
        for mutation in ('missing', 'unknown', 'duplicate', 'frame'):
            editor = RecordingEditor()
            parts, frames = components()
            routes = editor.routes()
            if mutation == 'missing': routes['components'] = routes['components'][1:]
            elif mutation == 'unknown': routes['components'][0]['role'] = 'suspension'
            elif mutation == 'duplicate': routes['components'].append(copy.deepcopy(routes['components'][0]))
            else: del frames['brake_rotor:FL']
            self.assert_preflight_rejected(editor, parts, routes, frames)

    def test_incompatible_materials_and_missing_material_are_not_merged(self):
        editor = RecordingEditor()
        parts, frames = components()
        second = copy.deepcopy(parts[0])
        second.update(id='different_recipe', native_material=1)
        self.assert_preflight_rejected(editor, [parts[0], second], editor.routes(), frames)
        parts[0]['native_material'] = 3
        self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_matching_stride_cannot_hide_wrong_position_stream_or_layout(self):
        for at, value in ((0x707, 1), (0x704, 4), (0x70e, 2), (0x715, 3)):
            editor = RecordingEditor()
            parts, frames = components()
            editor.b[at] = value
            self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_invalid_last_group_cannot_leave_earlier_native_writes(self):
        editor = RecordingEditor()
        parts, frames = components()
        parts[-1]['faces'][0, 0] = 400
        self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_nonfinite_noninteger_or_incomplete_attributes_fail_closed(self):
        for mutation in ('nan', 'float_faces', 'missing_uv', 'zero_normal', 'float32_overflow'):
            editor = RecordingEditor()
            parts, frames = components()
            if mutation == 'nan': parts[0]['positions'][0, 0] = float('nan')
            elif mutation == 'float_faces': parts[0]['faces'] = parts[0]['faces'].astype(float)
            elif mutation == 'missing_uv': parts[0]['uv'] = parts[0]['uv'][:-1]
            elif mutation == 'zero_normal': parts[0]['normals'][0] = 0.
            else: parts[0]['positions'][0, 0] = 1e100
            self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_duplicate_source_and_shape_operand_capacity_are_rejected(self):
        editor = RecordingEditor()
        parts, frames = components()
        self.assert_preflight_rejected(editor, [parts[0], parts[0]], editor.routes(), frames)
        editor.shape_rows.extend(bytearray(48) for _ in range(249))
        self.assert_preflight_rejected(editor, parts, editor.routes(), frames)

    def test_receipt_does_not_alias_source_frame_provenance(self):
        editor = RecordingEditor()
        parts, frames = components()
        routes = editor.routes()
        receipt = append_dynamic_components(editor, parts, routes, frames)
        receipt['shapes'][0]['frame']['evidence']['fixture'] = 'changed'
        receipt['route_evidence']['fixture'] = 'changed'
        self.assertEqual(frames['brake_rotor:FL']['evidence']['fixture'], 'explicit measured frame')
        self.assertEqual(routes['evidence']['fixture'], 'serialized commands')
