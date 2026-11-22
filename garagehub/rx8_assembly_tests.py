"""Focused source-accounting and serialized combined-command regressions.

The structural fixture contains synthetic metadata/geometry and the already
recorded original command-only contracts. No game data or PC access is needed.
Actual donor material/resource conversion is an independent original-asset
audit, not a claim made by this fixture.
"""
from __future__ import annotations

import copy
import struct
import unittest
from contextlib import ExitStack
from unittest.mock import patch

import numpy as np
from PIL import Image

from .errors import GarageError
from .glb import Part
from .native import MDL3, corners
from .native_editor import Editor
from .util import sha256
from .rx8_mechanical_tests import source_fixture, ORIGINAL_PROGRAMS
from .rx8_assembly import (POLICY, assembly_plan, build_assembly, build_assembly_lod,
    compose_body_program, compose_wheel_program, wheel_state_calls, _native_check)


FIT = {'policy': POLICY, 'review_note': 'Explicit historical diagnostic fixture; not a solved mount.'}


def scene_fixture(count=None):
    scene, inspection = source_fixture()
    base = copy.deepcopy(scene.materials[0])
    for index in range(1, 5):
        material = dict(base, id=index, name='material_'+str(index), double_sided=False)
        scene.materials.append(material)
    scene.materials[2]['alpha_mode'] = 'BLEND'
    scene.materials[3].update(alpha_mode='BLEND', base_texture=0)
    face = np.array([[0, 1, 2], [0, 2, 3]], dtype=np.uint32)

    def add(pid, role, position, material=1, corner=None):
        points = np.asarray(position, float)+np.array([[0., 0., 0.], [0., .1, 0.], [0., .1, .1], [0., 0., .1]])
        part = Part(pid, 'Arbitrary_'+pid, len(scene.parts), 0, material, points,
            np.tile([1., 0., 0.], (4, 1)), np.array([[0., 0.], [1., 0.], [1., 1.], [0., 1.]]), face.copy())
        scene.parts.append(part)
        inspection['parts'].append({'id': pid, 'role': role, 'corner': corner,
                                    'role_review_required': False})
        return part
    for corner, center in inspection['wheel_centers'].items():
        add('arbitrary_rim_'+corner, 'rim', center, corner=corner)
        add('alpha_decal_'+corner, 'brake_caliper', center, 3, corner)
    add('shell', 'paint', [0., .5, 0.])
    add('window', 'glass', [0., .7, 0.], 2)
    add('front_lenses', 'headlight', [0., .4, 1.6])
    add('rear_lenses', 'taillight', [0., .4, -1.6])
    add('motion_unresolved', 'body', [0., .3, 0.])
    if count is not None:
        while len(scene.parts) < count:
            add('body_piece_'+str(len(scene.parts)), 'body', [0., .6, 0.])
    return scene, inspection


DISPOSITIONS = {'motion_unresolved': {'disposition': 'blocked',
    'reason': 'Caller-reviewed suspension articulation has no native route.',
    'provenance': {'review': 'synthetic distinct motion group'}}}


def native_fixture(lod, wheel=False):
    """Synthetic MDL3 envelope sufficient for real shape/model serialization."""
    cut = 0x6000
    b = bytearray(cut)
    b[:4] = b'MDL3'
    struct.pack_into('>H', b, 12, 14)
    models, shapes = (8, 8 if lod == 'hq' else 16) if wheel else (11, 102)
    struct.pack_into('>H', b, 0x10, models)
    struct.pack_into('>H', b, 0x14, shapes)
    struct.pack_into('>H', b, 0x18, 1)
    for cell, pointer in ((0x30, 0x100), (0x38, 0x500), (0x40, 0x2000),
                          (0xb0, 0x2200), (0x64, 0x4a00)):
        struct.pack_into('>I', b, cell, pointer)
    struct.pack_into('>I', b, 0x60, 4)
    b[0x4a00:0x4a04] = b'VM\0\0'
    for sid in range(shapes):
        struct.pack_into('>Hhh', b, 0x500+48*sid, 0x1001, -1, 0)
        struct.pack_into('>I', b, 0x500+48*sid+44, 0x4900)
    struct.pack_into('>III', b, 0x2000, 0x2180, 0, 0x2100)
    b[0x2018:0x201a] = bytes((3, 24))
    for at, pointer, name, fields in (
            (0x2100, 0x2140, 'position', (0, 3, 2, 0)),
            (0x2108, 0x2150, 'normal', (12, 1, 6, 0)),
            (0x2110, 0x2160, 'map12', (16, 2, 2, 0))):
        struct.pack_into('>I4B', b, at, pointer, *fields)
        b[pointer:pointer+len(name)+1] = name.encode()+b'\0'
    b[0x2180:0x2185] = b'Flex\0'
    for mid in range(models):
        if wheel and mid < 4:
            if lod == 'hq':
                program = bytes((59, mid, mid, 0))
            else:
                context, main = mid*2+1, mid*3
                program = bytes((5, 0, 0, 2, 0, 4, 0, 7, 9, 11, 59, main, context,
                                 9, 6, 59, main+1, mid*2, 59, main+2, mid*2, 0))
        elif not wheel and mid in (0, 5):
            program = ORIGINAL_PROGRAMS[lod+'_'+str(mid)]
        else:
            program = bytes((59, min(shapes-1, 96), 0, 0))
        pointer = 0x3000+mid*0x200
        b[pointer:pointer+len(program)] = program
        at = 0x100+48*mid
        struct.pack_into('>H', b, at+18, 8)
        struct.pack_into('>III', b, at+20, 0x2800+96*mid, pointer, len(program))
        b[0x2800+96*mid:0x2800+96*(mid+1)] = corners([np.array([-1., -1., -1.]), np.array([1., 1., 1.])])
        struct.pack_into('>I', b, 0x2200+64*mid, 0x2600+16*mid)
        struct.pack_into('>H', b, 0x2200+64*mid+48, 1)
        struct.pack_into('>H', b, 0x2600+16*mid, 0)
    stream = b'Synthetic original stream; retained byte for byte.'
    if not wheel:
        struct.pack_into('>I', b, 0xdc, 0x4b00)
        struct.pack_into('>3I', b, 0x4b00, 1, 0, 0x4b10)
        struct.pack_into('>3I', b, 0x4b10, 0x4b20, 1, 0x4b40)
        b[0x4b20:0x4b22] = b'/\0'
        struct.pack_into('>IIHHIII', b, 0x4b40, 0, 0, 1, 0, 0x4b80, 0, 0)
        struct.pack_into('>II', b, 0x4b80, len(stream), len(stream))
    tail = b'SYNTHETIC_ORIGINAL_GPU'+bytes(107)
    struct.pack_into('>I', b, 4, cut+len(tail))
    struct.pack_into('>I', b, 0x6c, cut)
    raw = bytes(b)+tail
    MDL3(raw).validate()
    return raw, stream


class FixtureEditor:
    """Use actual shape/model/bounds writers, substitute resource allocation."""
    alloc, put, u32, u16 = Editor.alloc, Editor.put, Editor.u32, Editor.u16
    shape, model, bound = Editor.shape, Editor.model, Editor.bound

    def __init__(self, raw):
        self.raw = raw; self.m = MDL3(raw); self.b = bytearray(raw[:self.m.cut])
        self.finished = False
        self.banks = 2 if self.m.u16(0x10) == 11 else 1
        self.bank_size = 65 if self.banks == 2 else 8
        self.fvf_rows = [bytearray(self.m.span(self.m.u32(0x40), 120))]
        self.shape_rows = [bytearray(self.m.span(self.m.u32(0x38)+48*i, 48)) for i in range(self.m.u16(0x14))]
        self.new_shapes = []; self.new_materials = []; self.stream_shapes = []
        self.model_changes = []; self.images = []

    def fvf(self, template):
        return 0

    def texture_keys(self, template, bank=0):
        return [('diffuseMapSampler', 5), ('ambientMapSampler', 5), ('emissionMapSampler', 7)]

    def clone_material(self, template, label, bindings):
        mid = self.bank_size+len(self.new_materials)
        self.new_materials.append({'id': mid, 'name': label, 'replacement': False})
        return mid

    def image(self, image, label):
        self.images.append(label)
        return len(self.images)

    def finish(self):
        self.u32(0x38, self.alloc(b''.join(self.shape_rows)))
        self.u16(0x14, len(self.shape_rows))
        self.b.extend(bytes(-len(self.b)%128))
        cut = len(self.b); tail = self.raw[self.m.cut:]
        self.u32(0x6c, cut); self.u32(4, cut+len(tail))
        self.finished = True
        return bytes(self.b)+tail, {'added_shapes': copy.deepcopy(self.new_shapes),
                                   'added_materials': copy.deepcopy(self.new_materials)}


class FixtureTextures:
    def __init__(self, scene, editor):
        pass

    def source_image(self, material):
        return Image.new('RGBA', (4, 4), (128, 128, 128, 255))


class FixtureMaterials:
    def __init__(self, scene, editor, lod, passive_source_lenses=False):
        self.scene = scene; self.editor = editor; self.lod = lod
        self.textures = FixtureTextures(scene, editor); self.cache = {}; self.report = []

    def recipe(self, material, role):
        key = material, role
        if key not in self.cache:
            transparent = role == 'glass' or self.scene.materials[material]['alpha_mode'] == 'BLEND'
            recipe = {'native_material': self.editor.clone_material(0, str(key), {}), 'fvf': 0,
                'constant_uv': (.5, .5), 'transparent': transparent}
            self.cache[key] = recipe; self.report.append(recipe)
        return self.cache[key]


def native_assets():
    assets = {'info': b'synthetic inherited info', 'interior': b'synthetic inherited interior'}
    for lod in ('hq', 'race'):
        assets[lod+'/body'], assets[lod+'/body_s'] = native_fixture(lod)
        assets[lod+'/wheel'], _ = native_fixture(lod, wheel=True)
    return assets


def resource_fixture(assets):
    stack = ExitStack()
    pins = {name: sha256(raw) for name, raw in assets.items()}
    stack.enter_context(patch.dict('garagehub.rx8_assembly.RX8_ASSETS', pins, clear=True))
    stack.enter_context(patch('garagehub.rx8_assembly.Editor', FixtureEditor))
    stack.enter_context(patch('garagehub.rx8_assembly.Materials', FixtureTextures))
    stack.enter_context(patch('garagehub.rx8_assembly.RX8BodyMaterials', FixtureMaterials))
    stack.enter_context(patch('garagehub.rx8_assembly.shadow_reference_geometry', return_value=[]))
    return stack


class RX8AssemblyTests(unittest.TestCase):
    def test_current_71_part_inventory_is_accounted_with_blocked_logo_and_suspension(self):
        scene, inspection = scene_fixture(71)
        plan = assembly_plan(scene, inspection, fit_decision=FIT, dispositions=DISPOSITIONS)
        self.assertEqual(plan['source_parts'], 71)
        self.assertEqual(sum(plan['triangle_dispositions'].values()), scene.triangles)
        blocked = {row['id'] for row in plan['coverage'] if row['disposition'] == 'blocked'}
        self.assertEqual(blocked, {'motion_unresolved'} | {'alpha_decal_'+corner for corner in ('FL','FR','RL','RR')})
        self.assertFalse(plan['installable'])
        self.assertFalse(plan['full_source_geometry_emitted'])
        self.assertTrue(plan['all_source_parts_accounted'])

    def test_source_names_hash_and_inventory_size_do_not_select_a_native_recipe(self):
        scene, inspection = scene_fixture()
        for part, row in zip(scene.parts, inspection['parts']):
            part.id = row['id'] = 'renamed_'+part.id
            part.name = 'unknown'
        scene.digest = '1'*64
        plan = assembly_plan(scene, inspection, fit_decision=FIT)
        self.assertEqual(plan['source_sha256'], '1'*64)
        self.assertNotEqual(plan['source_parts'], 62)
        self.assertEqual({r['id'] for r in plan['coverage']}, {p.id for p in scene.parts})

    def test_fit_requires_explicit_supported_policy_and_does_not_accept_nominal_y(self):
        scene, inspection = scene_fixture()
        for decision in (None, {}, {'policy': 'automatic'}, {'policy': POLICY, 'target_y': .2}):
            with self.subTest(decision=decision), self.assertRaises(GarageError):
                assembly_plan(scene, inspection, fit_decision=decision)

    def test_shared_frame_reports_historical_residual_without_new_placement(self):
        scene, inspection = scene_fixture()
        plan = assembly_plan(scene, inspection, fit_decision=FIT)
        fit = plan['fit']
        self.assertTrue(fit['historically_observed_reference'])
        for key in ('current_mount_verified', 'nominal_target_y_used', 'body_fit_solved', 'new_alignment_offsets_applied', 'physics_changed'):
            self.assertFalse(fit[key])
        matrix = np.asarray(fit['body_source_to_native_rotation'])
        origin = np.asarray(fit['body_source_origin_metres'])
        for corner, frame in fit['wheel_frames'].items():
            source = np.asarray(inspection['wheel_centers'][corner])
            expected = (source-origin)@matrix.T
            np.testing.assert_array_equal(expected, frame['source_center_in_native_body_frame'])
            for role in ('brake_rotor','brake_caliper'):
                np.testing.assert_array_equal(expected, fit['mechanical_frames'][role+':'+corner]['source_center_in_body_frame'])
            self.assertEqual(frame['radial_offset_metres'], [0., 0.])
            self.assertGreater(np.linalg.norm(frame['source_body_minus_fitted_center_metres']), .01)

    def test_stale_tyre_center_is_rejected_before_candidate_authoring(self):
        scene, inspection = scene_fixture()
        inspection['wheel_centers']['FL'][0] += .01
        with self.assertRaisesRegex(GarageError, 'centre differs'):
            assembly_plan(scene, inspection, fit_decision=FIT)

    def test_duplicate_or_incomplete_inspection_cannot_change_source_accounting(self):
        scene, inspection = scene_fixture()
        for rows in (inspection['parts'][:-1], inspection['parts']+[inspection['parts'][0]]):
            with self.subTest(length=len(rows)), self.assertRaises(GarageError):
                assembly_plan(scene, {**inspection, 'parts': rows}, fit_decision=FIT)

    def test_exclusions_require_reasons_and_unknown_dispositions_are_rejected(self):
        scene, inspection = scene_fixture()
        next(r for r in inspection['parts'] if r['id'] == 'shell')['role'] = 'ignore'
        plan = assembly_plan(scene, inspection, fit_decision=FIT)
        self.assertEqual(next(r for r in plan['coverage'] if r['id'] == 'shell')['disposition'], 'blocked')
        plan = assembly_plan(scene, inspection, fit_decision=FIT,
            dispositions={'shell': {'disposition': 'excluded', 'reason': 'Explicit synthetic exclusion review.'}})
        self.assertEqual(plan['triangle_dispositions']['excluded'], 2)
        for dispositions in ({'missing': {'disposition': 'blocked','reason':'test'}},
                             {'shell': {'disposition':'excluded','reason':''}},
                             {'shell': {'disposition':'emit','reason':'bypass'}}):
            with self.subTest(dispositions=dispositions), self.assertRaises(GarageError):
                assembly_plan(scene, inspection, fit_decision=FIT, dispositions=dispositions)

    def test_cutout_is_blocked_and_normal_map_limit_is_not_lost_in_glass_recipe(self):
        scene, inspection = scene_fixture()
        scene.materials[1].update(alpha_mode='MASK', normal_map=True)
        plan = assembly_plan(scene, inspection, fit_decision=FIT)
        row = next(r for r in plan['coverage'] if r['id'] == 'shell')
        self.assertEqual(row['disposition'], 'blocked')
        self.assertIn('alpha-cutout', row['reason'])
        self.assertTrue(any('normal-map' in message for message in row['material_limitations']))

    def test_unresolved_mechanical_proposal_never_becomes_static_geometry(self):
        scene, inspection = scene_fixture()
        row = next(r for r in inspection['parts'] if r['id'] == 'shell')
        row.update(role='body', mechanical_role_proposals=['brake_rotor','brake_caliper'])
        plan = assembly_plan(scene, inspection, fit_decision=FIT)
        self.assertEqual(next(r for r in plan['coverage'] if r['id'] == 'shell')['disposition'], 'blocked')

    def test_multiple_opaque_mechanical_materials_are_not_flattened_into_one_draw(self):
        scene, inspection = scene_fixture()
        alpha = next(p for p in scene.parts if p.id == 'alpha_decal_FL')
        alpha.material = 4
        plan = assembly_plan(scene, inspection, fit_decision=FIT)
        relevant = [r for r in plan['coverage'] if r['role'] == 'brake_caliper' and r['corner'] == 'FL']
        self.assertEqual({r['disposition'] for r in relevant}, {'blocked'})
        self.assertTrue(all('multi-draw' in r['reason'] for r in relevant))

    def test_race_wheel_low_high_and_default_targets_survive_multiple_source_draws(self):
        raw, _ = native_fixture('race', wheel=True)
        model = MDL3(raw)
        for ci in range(4):
            at = model.u32(0x30)+48*ci
            original = model.span(model.u32(at+24), model.u32(at+28))
            high = original[15:21]
            for count in (0, 1, 2, 7):
                shapes = list(range(16, 16+count))
                program, receipt = compose_wheel_program(original, 'race', ci, shapes)
                self.assertEqual(wheel_state_calls(program, 0), [(s, ci*2+1) for s in shapes])
                self.assertEqual(wheel_state_calls(program, 1), wheel_state_calls(original, 1))
                self.assertEqual(wheel_state_calls(program, -1), [])
                self.assertEqual(wheel_state_calls(program, 2), [])
                self.assertEqual(10+program[9], len(program)-1)
                high_start = 8+struct.unpack_from('>H', program, 6)[0]
                self.assertEqual(program[high_start:high_start+6], high)
                self.assertTrue(receipt['default_to_end_preserved'])

    def test_old_emblem_default_target_defect_is_detected_independently(self):
        raw, _ = native_fixture('race', wheel=True)
        model = MDL3(raw); at = model.u32(0x30)
        old = model.span(model.u32(at+24), model.u32(at+28))
        # This is the historical faulty insertion, not a call to its helper.
        broken = old[:6]+struct.pack('>H', 10)+old[8:13]+bytes((59,16,1))+old[13:]
        self.assertEqual(wheel_state_calls(broken, 0), [(0,1),(16,1)])
        self.assertEqual(wheel_state_calls(broken, 1), wheel_state_calls(old,1))
        self.assertNotEqual(wheel_state_calls(broken, 2), [])

    def test_body_composition_preserves_patched_suffix_and_race_visibility_targets(self):
        lights = {'headlamp':[113], 'running':[114], 'brake':[115]}
        for lod in ('hq','race'):
            for mid in (0,5):
                original = ORIGINAL_PROGRAMS[lod+'_'+str(mid)]
                patched = bytearray(original)
                cut = 204 if (lod,mid)==('hq',0) else 215 if mid==0 else 207
                patched[cut+21] = 120
                result, receipt = compose_body_program(original, bytes(patched), lod, mid, [102,103], [104], lights)
                self.assertEqual(result[receipt['mechanical_suffix_start_after']:], bytes(patched[cut:]))
                for operand in (21,):
                    self.assertEqual(result[receipt['mechanical_suffix_start_after']+operand], 120)
                if lod=='race' and mid==0:
                    self.assertEqual(result[:4], bytes.fromhex('05007202'))
                    self.assertEqual(6+struct.unpack_from('>H',result,4)[0],11)
                    self.assertEqual(8+struct.unpack_from('>H',result,6)[0],len(result)-1)
                    self.assertEqual(11+struct.unpack_from('>H',result,9)[0],len(result)-1)
                if mid==5:self.assertEqual(result[:3],bytes((59,96,17)))

    def test_body_program_rejects_changed_original_identity_or_static_prefix(self):
        original = ORIGINAL_PROGRAMS['hq_0']
        lights = {'headlamp':[], 'running':[], 'brake':[]}
        bad = bytearray(original);bad[1] ^= 1
        with self.assertRaises(GarageError):
            compose_body_program(bytes(bad),bytes(bad),'hq',0,[102],[],lights)
        with self.assertRaises(GarageError):
            compose_body_program(original,bytes(bad),'hq',0,[102],[],lights)

    def test_combined_serialization_accounts_for_71_parts_in_both_lods(self):
        scene, inspection = scene_fixture(71)
        assets = native_assets()
        source_before = [(p.positions.copy(),p.normals.copy(),p.uv.copy(),p.indices.copy()) for p in scene.parts]
        with resource_fixture(assets):
            output, receipt = build_assembly(scene, inspection, assets, fit_decision=FIT, dispositions=DISPOSITIONS)
        self.assertEqual(set(output),set(assets))
        for name in ('info','interior','hq/body_s','race/body_s'):
            self.assertEqual(output[name],assets[name])
        for lod, report in receipt['lods'].items():
            self.assertEqual(report['plan']['source_parts'],71)
            self.assertEqual(report['emitted_base_source_triangles'],report['plan']['triangle_dispositions']['emit'])
            self.assertEqual(len(report['body']['mechanical']['shapes']),8)
            self.assertTrue(report['body_and_mechanical_draws_composed_together'])
            self.assertTrue(report['body']['final_material_and_sweep_union_verified'])
            self.assertFalse(report['installable'])
            self.assertGreater(report['body']['lamp_redraw_triangles'],0)
            model=MDL3(output[lod+'/body'])
            for program in report['body']['programs']:
                mid=program['model'];at=model.u32(0x30)+48*mid
                raw=model.span(model.u32(at+24),model.u32(at+28))
                for change in program['mechanical_draws']:
                    p=change['composed_operand_offset']
                    self.assertEqual(raw[p-1:p+2],bytes((59,change['new_shape'],change['context_preserved'])))
        for part, arrays in zip(scene.parts,source_before):
            for name, before in zip(('positions','normals','uv','indices'), arrays):
                np.testing.assert_array_equal(getattr(part,name),before)

    def test_final_model_keeps_mechanical_materials_and_sweep_outside_static_bounds(self):
        scene, inspection = scene_fixture()
        rotor = next(p for p in scene.parts if p.id=='brake_rotor_FL')
        rotor.positions[:,1] += 8.
        assets = native_assets()
        with resource_fixture(assets):
            output, receipt=build_assembly_lod(scene,inspection,assets['hq/body'],assets['hq/body_s'],
                assets['hq/wheel'],'hq',fit_decision=FIT,dispositions=DISPOSITIONS)
        model=MDL3(output['body'])
        for mid in (0,5):
            q=model.u32(0xb0)+64*mid
            actual=set(struct.unpack('>'+'H'*model.u16(q+48),model.span(model.u32(q),model.u16(q+48)*2)))
            required={r['native_material'] for r in receipt['body']['mechanical']['shapes']}
            self.assertTrue(required<=actual)
            at=model.u32(0x30)+48*mid
            bounds=np.frombuffer(model.span(model.u32(at+20),96),dtype='>f4').reshape(8,3)
            self.assertLess(bounds[:,1].min(),-7.)
            self.assertGreater(bounds[:,1].max(),7.)

    def test_lab_output_cannot_be_reused_as_an_original_donor(self):
        scene, inspection=scene_fixture();assets=native_assets()
        with resource_fixture(assets):
            output,_=build_assembly_lod(scene,inspection,assets['hq/body'],assets['hq/body_s'],assets['hq/wheel'],
                'hq',fit_decision=FIT,dispositions=DISPOSITIONS)
            with self.assertRaisesRegex(GarageError,'original RX8'):
                build_assembly_lod(scene,inspection,output['body'],assets['hq/body_s'],assets['hq/wheel'],
                    'hq',fit_decision=FIT,dispositions=DISPOSITIONS)

    def test_final_native_guard_rejects_lost_material_and_changed_packed_descriptor(self):
        scene,inspection=scene_fixture();assets=native_assets()
        with resource_fixture(assets):
            output,receipt=build_assembly_lod(scene,inspection,assets['hq/body'],assets['hq/body_s'],assets['hq/wheel'],
                'hq',fit_decision=FIT,dispositions=DISPOSITIONS)
        raw=output['body'];model=MDL3(raw)
        shapes=receipt['body']['static_shapes']+receipt['body']['lamp_redraw_shapes']+receipt['body']['mechanical']['shapes']
        programs={}
        for mid in (0,5):
            at=model.u32(0x30)+48*mid
            programs[mid]={'program':model.span(model.u32(at+24),model.u32(at+28)),
                           'materials':[r['native_material'] for r in shapes]}
        altered=bytearray(raw);struct.pack_into('>H',altered,model.u32(0xb0)+48,1)
        with self.assertRaisesRegex(GarageError,'lost a referenced appended material'):
            _native_check(assets['hq/body'],bytes(altered),shapes,programs)
        altered=bytearray(raw);altered[model.u32(0x38)+44]^=1
        with self.assertRaisesRegex(GarageError,'original packed/native shape'):
            _native_check(assets['hq/body'],bytes(altered),shapes,programs)
