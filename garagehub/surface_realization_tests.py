"""Focused opaque two-sided conversion and native render-state contracts.

Native state fixtures below are independently serialized, synthetic MDL3.
Production authentication is substituted only for those fixtures. Actual owned
template checks and in-game rendering acceptance remain separate evidence.
"""
from __future__ import annotations

import copy
import struct
import types
import unittest
from unittest.mock import patch

import numpy as np
from PIL import Image

from .car_geometry import prepare_parts, batches
from .car_profiles import ASSETS, PROFILE
from .errors import GarageError
from .glb import Part, Scene
from .surface_realization import (METHOD, PLAN_KEY, SHELL_SCOPE,
    opaque_two_sided_plan, validate_opaque_two_sided_plan, duplicate_backfaces,
    verify_native_opaque_culling)
from .template_shell_independent_tests import shell_fixture, source_snapshot
from .util import sha256


def synthetic_opaque_body():
    """Write only the material structures needed by the state checker."""
    ms, materials = 0x100, 0x120
    states = materials+7*79*52
    data = bytearray(states+2*160)
    data[:4] = b'MDL3'
    struct.pack_into('>I', data, 4, len(data))
    struct.pack_into('>H', data, 12, 14)
    struct.pack_into('>I', data, 0x44, ms)
    struct.pack_into('>I', data, 0x6c, len(data))
    struct.pack_into('>H', data, ms, 7*79)
    data[ms+4:ms+6] = bytes((7, 2))
    struct.pack_into('>I', data, ms+8, materials)
    struct.pack_into('>I', data, ms+16, states)
    for state in (states, states+160):
        for offset, value in ((0, 0x00780304), (0x7c, 0x000c0a6c),
                              (0x80, 0x0203), (0x84, 1), (0x88, 1),
                              (0x8c, 0x0004183c), (0x90, 1)):
            struct.pack_into('>I', data, state+offset, value)
    return bytes(data), {'materials': materials, 'states': states}


def fit(scene, inspection, *, scope=SHELL_SCOPE, policy=True):
    review = {'scope': scope, 'within_profile_limits': True,
              'fit': {'test_transform': 'identity'}}
    if policy:
        review[PLAN_KEY] = opaque_two_sided_plan(scene, inspection)
    return np.eye(3), np.zeros(3), review


def one_surface(*, triangles=1, textured=False):
    """Independent small component with deliberately distinct authored UVs."""
    points = np.array([[0., 0., 0.], [1., 0., 0.], [0., 1., 0.]])
    normals = np.array([[0., 0., 1.]]*3)
    uv = np.array([[.1, .2], [.8, .2], [.1, .9]])
    faces = np.tile(np.array([[0, 1, 2]], np.int64), (triangles, 1))
    material = {'id': 0, 'name': 'Opaque two-sided', 'alpha_mode': 'OPAQUE',
                'double_sided': True, 'transmission': 0., 'normal_map': False,
                'metallic': 0., 'base_texture': 0 if textured else None,
                'color': [.4, .5, .6, 1.], 'emissive': [0., 0., 0.]}
    part = Part('source-sheet', 'Authored sheet', 0, 0, 0, points, normals, uv, faces)
    scene = Scene([part], [material], {}, b'fixture source bytes', 'fixture', [])
    inspection = {'forward': '+Z', 'parts': [{'id': part.id, 'role': 'body', 'corner': None}]}
    return scene, inspection


class SurfaceRealizationTests(unittest.TestCase):
    def test_material_help_identifies_shared_livery_and_preserves_texture_alpha(self):
        scene, inspection = one_surface(textured=True)
        first = scene.parts[0]
        second = copy.copy(first)
        second.id = 'other-panel'
        scene.parts.append(second)
        inspection['parts'] = [{'id': p.id, 'role': 'paint'} for p in scene.parts]
        scene.materials[0].update(name='Patrol livery', alpha_mode='BLEND')
        for alpha, expected_note in ((255, 'is opaque'), (254, 'contains transparent pixels')):
            with self.subTest(alpha=alpha):
                image = Image.new('RGBA', (1024, 4), (10, 20, 30, 255))
                image.putpixel((1, 0), (10, 20, 30, alpha))
                with patch('garagehub.material_help.Materials.embedded_image', return_value=image):
                    with self.assertRaises(GarageError) as caught:
                        opaque_two_sided_plan(scene, inspection)
                help_data = caught.exception.detail['material_help']
                self.assertEqual((help_data['material'], help_data['part_id'], help_data['part_count']),
                                 ('Patrol livery', first.id, 2))
                self.assertIn(expected_note, help_data['texture_note'])
                self.assertIn('Base Color', ' '.join(help_data['steps']))
                self.assertIn('Body detail', ' '.join(help_data['steps']))
                self.assertEqual(scene.materials[0]['alpha_mode'], 'BLEND')

    def test_cutout_and_specular_have_different_material_instructions(self):
        from .template_shell import _materials
        scene, inspection = one_surface()
        scene.materials[0]['alpha_mode'] = 'MASK'
        with self.assertRaises(GarageError) as caught:
            opaque_two_sided_plan(scene, inspection)
        help_data = caught.exception.detail['material_help']
        self.assertIn('openings', ' '.join(help_data['steps']))
        self.assertNotIn('Alpha to 1.000', ' '.join(help_data['steps']))
        scene.document['materials'] = [{'extensions': {'KHR_materials_specular': {'specularColorFactor': [2., 2., 2.]}}}]
        row = dict(inspection['parts'][0], material=0, disposition='emit')
        with self.assertRaises(GarageError) as caught:
            _materials(scene, [row])
        self.assertIn('KHR_materials_specular', str(caught.exception))
        self.assertIn('IOR Level to 0.5', ' '.join(caught.exception.detail['material_help']['steps']))

    def test_plan_counts_original_faces_once_and_excludes_ignored_components(self):
        scene, project = shell_fixture(extra_material={'doubleSided': True}, with_badge=True)
        before = source_snapshot(scene)
        inspection = copy.deepcopy(project['inspection'])
        inspection['parts'][-1]['role'] = 'ignore'
        plan = opaque_two_sided_plan(scene, inspection)
        self.assertEqual(plan['part_ids'], [scene.parts[0].id])
        self.assertEqual(plan['source_triangles'], 12)
        self.assertEqual(plan['generated_backface_triangles_before_lod'], 12)
        self.assertEqual(plan['emitted_triangles_before_lod'], 24)
        self.assertEqual(plan['source_vertices'], 8)
        self.assertEqual(source_snapshot(scene), before)
        self.assertEqual(validate_opaque_two_sided_plan(scene, inspection, plan),
                         frozenset((scene.parts[0].id,)))
        self.assertFalse(plan['native_rendering_verified'])

    def test_transparent_cutout_transmission_lamps_and_normal_maps_stay_blocked(self):
        mutations = [({'alpha_mode': 'BLEND'}, 'body'), ({'alpha_mode': 'MASK'}, 'body'),
                     ({'transmission': .001}, 'body'), ({'normal_map': True}, 'body'),
                     ({'emissive': [.1, 0., 0.]}, 'body'), ({}, 'glass'),
                     ({}, 'headlight'), ({}, 'taillight'), ({}, 'brakelight')]
        for material_changes, role in mutations:
            with self.subTest(material=material_changes, role=role):
                scene, inspection = one_surface()
                scene.materials[0].update(material_changes)
                inspection['parts'][0]['role'] = role
                with self.assertRaises(GarageError):
                    opaque_two_sided_plan(scene, inspection)

    def test_source_alpha_factor_is_ignored_only_for_opaque_materials(self):
        scene, inspection = one_surface()
        scene.materials[0]['color'][3] = .2
        self.assertEqual(opaque_two_sided_plan(scene, inspection)['source_triangles'], 1)
        scene.materials[0]['alpha_mode'] = 'BLEND'
        with self.assertRaises(GarageError):
            opaque_two_sided_plan(scene, inspection)

    def test_current_part_roles_counts_and_method_are_bound_to_plan(self):
        scene, inspection = one_surface()
        original = opaque_two_sided_plan(scene, inspection)
        for key, value in [('method', 'drop_double_sided'), ('part_ids', []),
                           ('source_triangles', 2), ('native_face_culling_required', False)]:
            with self.subTest(key=key):
                changed = copy.deepcopy(original)
                changed[key] = value
                with self.assertRaises(GarageError):
                    validate_opaque_two_sided_plan(scene, inspection, changed)
        inspection['parts'][0]['role'] = 'paint'
        with self.assertRaises(GarageError):
            validate_opaque_two_sided_plan(scene, inspection, original)

    def test_backfaces_preserve_uv_and_front_arrays_with_independent_opposite_normals(self):
        scene, _ = one_surface()
        part = scene.parts[0]
        before = source_snapshot(scene)
        p, n, uv, faces = duplicate_backfaces(part.positions, part.normals, part.uv, part.indices)
        np.testing.assert_array_equal(p, np.concatenate((part.positions, part.positions)))
        np.testing.assert_array_equal(uv, np.concatenate((part.uv, part.uv)))
        np.testing.assert_array_equal(n[:3], part.normals)
        np.testing.assert_array_equal(n[3:], -part.normals)
        np.testing.assert_array_equal(faces, [[0, 1, 2], [3, 5, 4]])
        cross = np.cross(p[faces[:, 1]]-p[faces[:, 0]], p[faces[:, 2]]-p[faces[:, 0]])
        self.assertTrue(np.all(np.einsum('ij,ij->i', cross, n[faces[:, 0]]) > 0))
        self.assertEqual(source_snapshot(scene), before)

    def test_backface_helper_rejects_invalid_arrays_and_indices(self):
        scene, _ = one_surface()
        part = scene.parts[0]
        for wrong in (np.array([[0, 1, 3]]), np.array([[-1, 0, 1]]),
                      np.array([[0., 1., 2.]]), np.array([[0, 1]])):
            with self.subTest(faces=wrong.tolist()), self.assertRaises(GarageError):
                duplicate_backfaces(part.positions, part.normals, part.uv, wrong)

    def test_prepared_lods_account_generated_faces_without_mutating_authentic_source(self):
        scene, project = shell_fixture(extra_material={'doubleSided': True})
        before = source_snapshot(scene)
        for lod in ('hq', 'race'):
            parts, reports, _ = prepare_parts(scene, project['inspection'], lod,
                                             fit_solution=fit(scene, project['inspection']))
            body = parts[0]
            self.assertEqual(len(body['faces']), 24)
            self.assertEqual(reports[0]['source_triangles'], 12)
            self.assertEqual(reports[0]['output_triangles'], 24)
            self.assertEqual(reports[0]['surface_realization']['generated_backface_triangles'], 12)
            self.assertTrue(reports[0]['surface_realization']['generated_after_lod_preparation'])
            self.assertEqual(sum(row['source_triangles'] for row in reports), 18)
            self.assertEqual(sum(row['output_triangles'] for row in reports), 30)
            self.assertNotIn('surface_realization', reports[1])
        self.assertEqual(source_snapshot(scene), before)

    def test_simplifier_sees_front_faces_only_and_budget_includes_both_sides(self):
        scene, inspection = one_surface(triangles=600)
        called = []
        def simplify(p, f, *, target_count, **kwargs):
            called.append((len(p), len(f), target_count))
            return p, f[:target_count], target_count
        def replay(p, f, collapses):
            return p, f[:collapses], np.arange(len(p))
        fake = types.SimpleNamespace(simplify=simplify, replay_simplification=replay)
        with patch.dict('sys.modules', {'fast_simplification': fake}), patch.dict(
                PROFILE['body_triangles'], {'hq': 600, 'race': 300}):
            for lod, front in (('hq', 300), ('race', 150)):
                parts, reports, _ = prepare_parts(scene, inspection, lod,
                                                 fit_solution=fit(scene, inspection))
                self.assertEqual(called[-1], (3, 600, front))
                self.assertEqual(len(parts[0]['faces']), front*2)
                self.assertEqual(reports[0]['source_triangles'], 600)
                self.assertEqual(reports[0]['surface_realization']['prepared_front_triangles'], front)
                np.testing.assert_array_equal(parts[0]['normals'][:3], -parts[0]['normals'][3:])

    def test_final_triangle_and_vertex_gates_include_generated_backfaces(self):
        scene, inspection = one_surface(triangles=10001, textured=True)
        with patch.dict(PROFILE['body_triangles'], {'hq': 10000}):
            with self.assertRaisesRegex(GarageError, 'geometry exceeds'):
                prepare_parts(scene, inspection, 'hq', fit_solution=fit(scene, inspection))
        scene, inspection = one_surface()
        with patch.dict(PROFILE, {'max_static_vertices': 5}):
            with self.assertRaisesRegex(GarageError, 'Vertex budget'):
                prepare_parts(scene, inspection, 'hq', fit_solution=fit(scene, inspection))

    def test_authored_fit_ignores_new_policy_and_retains_original_native_bytes(self):
        scene, inspection = one_surface()
        legacy = fit(scene, inspection, scope='body_fit_review', policy=False)
        injected = copy.deepcopy(legacy)
        injected[2][PLAN_KEY] = {'untrusted': 'not an authored-route option'}
        with patch('garagehub.surface_realization.validate_opaque_two_sided_plan',
                   side_effect=AssertionError('Authored route must not consume shell policy')):
            before, old_reports, _ = prepare_parts(scene, inspection, 'hq', fit_solution=legacy)
            after, new_reports, _ = prepare_parts(scene, inspection, 'hq', fit_solution=injected)
        self.assertEqual(old_reports, new_reports)
        self.assertEqual(len(after[0]['faces']), 1)
        old, new = list(batches(before)), list(batches(after))
        self.assertEqual([(row[0], row[1], row[3]) for row in old],
                         [(row[0], row[1], row[3]) for row in new])
        np.testing.assert_array_equal(after[0]['normals'], scene.parts[0].normals)

    def test_native_culling_check_reads_all_banks_and_preserves_bytes(self):
        raw, _ = synthetic_opaque_body()
        with patch.dict(ASSETS, {'hq/body': sha256(raw), 'race/body': sha256(raw)}):
            for lod in ('hq', 'race'):
                proof = verify_native_opaque_culling(raw, lod)
                self.assertEqual(len(proof['material_states']), 21)
                self.assertEqual({row['bank'] for row in proof['material_states']}, set(range(7)))
                self.assertEqual({row['template_material'] for row in proof['material_states']}, {0, 1, 2})
                self.assertTrue(proof['face_culling_enabled'])
                self.assertFalse(proof['native_rendering_verified'])
                self.assertEqual(proof['asset_sha256'], sha256(raw))

    def test_native_culling_authentication_and_lod_are_required(self):
        raw, _ = synthetic_opaque_body()
        with self.assertRaises(GarageError):
            verify_native_opaque_culling(raw, 'hq')
        with self.assertRaises(GarageError):
            verify_native_opaque_culling(raw, 'other')

    def test_disabled_culling_in_last_paint_bank_cannot_hide_behind_valid_first_bank(self):
        raw, at = synthetic_opaque_body()
        changed = bytearray(raw)
        struct.pack_into('>H', changed, at['materials']+(6*79+2)*52+6, 1)
        struct.pack_into('>I', changed, at['states']+160+0x90, 0)
        changed = bytes(changed)
        with patch.dict(ASSETS, {'hq/body': sha256(changed)}):
            with self.assertRaisesRegex(GarageError, 'face culling'):
                verify_native_opaque_culling(changed, 'hq')

    def test_unreviewed_cull_method_alpha_blend_and_depth_state_are_rejected(self):
        raw, at = synthetic_opaque_body()
        for offset, value in ((0x8c, 0x00041840), (4, 1), (0x10, 1),
                              (0x6c, 1), (0x84, 0), (0x88, 0)):
            with self.subTest(offset=offset):
                changed = bytearray(raw)
                struct.pack_into('>I', changed, at['states']+offset, value)
                changed = bytes(changed)
                with patch.dict(ASSETS, {'hq/body': sha256(changed)}):
                    with self.assertRaises(GarageError):
                        verify_native_opaque_culling(changed, 'hq')


if __name__ == '__main__':
    unittest.main()
