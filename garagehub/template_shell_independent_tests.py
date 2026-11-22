"""Independent shell-contract QA using a real, genuinely wheel-free GLB.

These fixtures contain a closed body box and authored lamp quads. They do not
reuse, strip or relabel wheel fixtures. Native build/runtime acceptance is a
separate gate; these tests establish source and review-contract behavior only.
"""
from __future__ import annotations

import copy
import io
import json
import struct
import tempfile
import unittest
from dataclasses import replace
from pathlib import Path
from unittest.mock import patch

import numpy as np
from PIL import Image

from .errors import GarageError
from .glb import GLB
from .recognition import infer
from .scene_coordinates import prepare_scene
from .util import sha256


def wheel_free_glb(*, source_from_metres=None, extra_material=None,
                   shell_role='body', shell_name='Independent shell', with_badge=False):
    """Author an independent valid GLB, optionally in another coordinate frame."""
    transform = np.eye(3) if source_from_metres is None else np.asarray(source_from_metres)
    body = np.array([[-.9, .2, -1.8], [.9, .2, -1.8], [.9, 1.3, -1.8], [-.9, 1.3, -1.8],
                     [-.9, .2, 1.8], [.9, .2, 1.8], [.9, 1.3, 1.8], [-.9, 1.3, 1.8]])
    faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                      [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
                      [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2]])
    lamp_faces = np.array([[0, 1, 2], [0, 2, 3]])
    specs = [(shell_name, shell_role, body, faces, 0)]
    for x, name in ((-.65, 'Left source headlamp'), (.65, 'Right source headlamp')):
        points = np.array([[x-.12, .5, 1.81], [x+.12, .5, 1.81],
                           [x+.12, .62, 1.81], [x-.12, .62, 1.81]])
        specs.append((name, 'headlight', points, lamp_faces, 1))
    rear = np.array([[-.7, .55, -1.81], [-.7, .67, -1.81],
                     [.7, .67, -1.81], [.7, .55, -1.81]])
    specs.append(('Rear source lamp', 'taillight', rear, lamp_faces, 2))
    if with_badge:
        badge = np.array([[-.1, 1.31, -.1], [-.1, 1.31, .1],
                          [.1, 1.31, .1], [.1, 1.31, -.1]])
        specs.append(('Optional roof badge', 'body', badge, lamp_faces, 0))
    materials = [
        {'name': 'Uniform shell', 'pbrMetallicRoughness': {'baseColorFactor': [.3, .4, .6, 1.], 'metallicFactor': 0}},
        {'name': 'Source front emitters', 'pbrMetallicRoughness': {'baseColorFactor': [.9, .9, .9, 1.], 'metallicFactor': 0}},
        {'name': 'Source rear emitter', 'pbrMetallicRoughness': {'baseColorFactor': [.8, .02, .02, 1.], 'metallicFactor': 0}},
    ]
    if extra_material:
        materials[0].update(copy.deepcopy(extra_material))
    doc = {'asset': {'version': '2.0', 'generator': 'Independent wheel-free contract QA'},
           'buffers': [], 'bufferViews': [], 'accessors': [], 'materials': materials,
           'meshes': [], 'nodes': [], 'scenes': [{'nodes': list(range(len(specs)))}], 'scene': 0}
    binary = bytearray()

    def attribute(values, component, shape, dtype):
        while len(binary) % 4:
            binary.append(0)
        values = np.asarray(values, dtype=dtype)
        raw = values.tobytes()
        view = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': len(raw)})
        binary.extend(raw)
        index = len(doc['accessors'])
        doc['accessors'].append({'bufferView': view, 'componentType': component,
                                 'count': len(values), 'type': shape})
        return index

    for number, (name, role, points, triangles, material) in enumerate(specs):
        # Compute normal directions independently of the product's generator.
        normals = np.zeros_like(points)
        cross = np.cross(points[triangles[:, 1]]-points[triangles[:, 0]],
                         points[triangles[:, 2]]-points[triangles[:, 0]])
        for column in range(3):
            np.add.at(normals, triangles[:, column], cross)
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        source_points = points @ transform.T
        source_normals = normals @ np.linalg.inv(transform)
        source_normals /= np.linalg.norm(source_normals, axis=1)[:, None]
        position = attribute(source_points, 5126, 'VEC3', '<f4')
        normal = attribute(source_normals, 5126, 'VEC3', '<f4')
        uv = attribute(np.zeros((len(points), 2)), 5126, 'VEC2', '<f4')
        indices = attribute(triangles.reshape(-1), 5123, 'SCALAR', '<u2')
        doc['meshes'].append({'primitives': [{'attributes': {'POSITION': position, 'NORMAL': normal, 'TEXCOORD_0': uv},
                                              'indices': indices, 'material': material}]})
        node = {'mesh': number, 'name': name}
        if role is not None:
            node['extras'] = {'gt6_role': role}
        doc['nodes'].append(node)
    doc['buffers'] = [{'byteLength': len(binary)}]
    metadata = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
    metadata += b' ' * (-len(metadata) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    length = 28 + len(metadata) + len(binary)
    return (struct.pack('<4sII', b'glTF', 2, length)
            + struct.pack('<II', len(metadata), 0x4e4f534a) + metadata
            + struct.pack('<II', len(binary), 0x004e4942) + binary)


def shell_fixture(*, source_up_axis='+Y', metres_per_unit=1., forward='+Z',
                  source_from_metres=None, extra_material=None,
                  shell_role='body', shell_name='Independent shell', with_badge=False):
    raw = wheel_free_glb(source_from_metres=source_from_metres,
                        extra_material=extra_material, shell_role=shell_role,
                        shell_name=shell_name, with_badge=with_badge)
    source = GLB(raw).scene()
    scene = prepare_scene(source, source_up_axis, metres_per_unit)
    project = {'schema': 2, 'id': 'f'*32, 'name': 'Independent wheel-free shell',
               'source_sha256': source.digest, 'source_up_axis': source_up_axis,
               'metres_per_unit': metres_per_unit, 'forward': forward,
               'overrides': {}, 'wheel_partitions': [],
               'installation': {'mode': 'new_entry', 'replace_car_id': None},
               'approved_roles': True, 'template_inheritance_approved': True,
               'inspection': infer(scene, forward)}
    return scene, project


def source_snapshot(scene):
    return (scene.raw, scene.digest, copy.deepcopy(scene.document), copy.deepcopy(scene.materials),
            [(p.id, p.positions.tobytes(), p.normals.tobytes(), p.uv.tobytes(), p.indices.tobytes())
             for p in scene.parts])


def unsupported_channel_fixture(channel):
    """Embed a valid authored texture in an unsupported core glTF channel."""
    parsed = GLB(wheel_free_glb())
    document = copy.deepcopy(parsed.doc)
    png = io.BytesIO()
    Image.new('RGBA', (2, 2), (128, 0, 255, 255)).save(png, format='PNG')
    binary = bytearray(parsed.buffers[0])
    binary.extend(b'\0' * (-len(binary) % 4))
    offset = len(binary)
    binary.extend(png.getvalue())
    view = len(document['bufferViews'])
    document['bufferViews'].append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(png.getvalue())})
    document['textures'] = [{'source': 0}]
    document['images'] = [{'bufferView': view, 'mimeType': 'image/png'}]
    if channel == 'metallicRoughnessTexture':
        document['materials'][0]['pbrMetallicRoughness'][channel] = {'index': 0}
    else:
        document['materials'][0][channel] = {'index': 0}
    if channel == 'emissiveTexture':
        document['materials'][0]['emissiveFactor'] = [1., 1., 1.]
    document['buffers'] = [{'byteLength': len(binary)}]
    metadata = json.dumps(document, sort_keys=True, separators=(',', ':')).encode()
    metadata += b' ' * (-len(metadata) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    raw = (struct.pack('<4sII', b'glTF', 2, 28 + len(metadata) + len(binary))
           + struct.pack('<II', len(metadata), 0x4e4f534a) + metadata
           + struct.pack('<II', len(binary), 0x004e4942) + binary)
    scene = GLB(raw).scene()
    _, project = shell_fixture()
    project.update(source_sha256=scene.digest, inspection=infer(scene, '+Z'))
    return scene, project


class TemplateShellIndependentTests(unittest.TestCase):
    def review(self, scene, project, placement=None, dispositions=None):
        from .template_shell import review_shell
        return review_shell(scene, project, placement or {'scale': 1., 'translation_metres': [0., 0., 0.]}, dispositions)

    def decision(self, scene, project, placement=None, dispositions=None):
        from .template_shell import approve_shell
        placement = placement or {'scale': 1., 'translation_metres': [0., 0., 0.]}
        review = self.review(scene, project, placement, dispositions)
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        return approve_shell(scene, project, placement, dispositions, review['review_digest'], True)

    def assert_blocked(self, scene, project, placement=None, dispositions=None):
        review = self.review(scene, project, placement, dispositions)
        self.assertFalse(review['ready_for_candidate'], review)
        self.assertTrue(review['blockers'])
        self.assertIsNone(review['review_digest'])
        return review

    def test_real_wheel_free_source_keeps_unmeasured_anchors_and_all_faces(self):
        scene, project = shell_fixture()
        original, saved = source_snapshot(scene), copy.deepcopy(project)
        review = self.review(scene, project)
        self.assertEqual(sha256(scene.raw), scene.digest)
        self.assertEqual(scene.triangles, 18)
        self.assertEqual(len(scene.parts), 4)
        self.assertEqual(project['inspection']['wheel_centers'], {})
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        self.assertEqual(review['source_anchors']['status'], 'unmeasured')
        self.assertEqual(review['source_anchors']['wheel_centers'], {})
        self.assertIsNone(review['source_anchors']['maximum_wheel_centre_error_metres'])
        self.assertEqual(review['triangle_dispositions'], {'emit': 18, 'excluded': 0})
        self.assertFalse(review['runtime_verified'])
        self.assertFalse(review['production_enabled'])
        self.assertEqual(source_snapshot(scene), original)
        self.assertEqual(project, saved)

    def test_equivalent_source_units_and_up_axes_place_the_same_geometry_once(self):
        from .template_shell import resolve_shell
        canonical, _ = shell_fixture()
        # Hand-authored rotations: +Z-forward body expressed in each forward
        # direction, then optionally encoded as centimetres in a Z-up source.
        forwards = {
            '+Z': np.eye(3),
            '-Z': np.diag([-1., 1., -1.]),
            '+X': np.array([[0., 0., 1.], [0., 1., 0.], [-1., 0., 0.]]),
            '-X': np.array([[0., 0., -1.], [0., 1., 0.], [1., 0., 0.]]),
        }
        inverse_z_up = np.array([[1., 0., 0.], [0., 0., -1.], [0., 1., 0.]])
        placement = {'scale': 1.15, 'translation_metres': [.03, .07, -.09]}
        native_rotation = np.diag([-1., 1., -1.])
        for forward, rotation in forwards.items():
            for up, units, encoding in (('+Y', 1., np.eye(3)), ('+Z', .01, inverse_z_up*100.)):
                with self.subTest(forward=forward, up=up, units=units):
                    scene, project = shell_fixture(source_up_axis=up, metres_per_unit=units,
                                                   forward=forward, source_from_metres=encoding@rotation)
                    decision = self.decision(scene, project, placement)
                    matrix, translation, review = resolve_shell(scene, project, decision)
                    for base_part, prepared_part in zip(canonical.parts, scene.parts):
                        expected = base_part.positions@native_rotation.T*1.15 + placement['translation_metres']
                        actual = prepared_part.positions@matrix.T + translation
                        np.testing.assert_allclose(actual, expected, rtol=0, atol=5e-7)
                    np.testing.assert_allclose(matrix.T@matrix, np.eye(3)*1.15**2, rtol=0, atol=1e-14)
                    self.assertEqual(review['source_anchors']['wheel_centers'], {})

    def test_raw_bytes_cannot_change_behind_a_previously_approved_digest(self):
        from .template_shell import resolve_shell
        scene, project = shell_fixture()
        decision = self.decision(scene, project)
        changed = scene.raw.replace(b'Independent shell', b'Independent Shell')
        self.assertNotEqual(changed, scene.raw)
        forged = replace(scene, raw=changed)
        # The changed GLB is still valid; failure must be source identity, not
        # corruption or a parser refusing the substitute file.
        self.assertEqual(GLB(changed).scene().triangles, 18)
        with self.assertRaises(GarageError):
            resolve_shell(forged, project, decision)

    def test_mutated_prepared_attributes_cannot_reuse_review_or_replace_raw_source(self):
        from .template_shell import resolve_shell
        mutations = {
            'position': lambda s: s.parts[0].positions.__setitem__((0, 0), -.88),
            'normal': lambda s: s.parts[0].normals.__setitem__((0, 0), .2),
            'uv': lambda s: s.parts[0].uv.__setitem__((0, 0), .3),
            'face': lambda s: s.parts[0].indices.__setitem__(0, s.parts[0].indices[0, [0, 2, 1]]),
            'material': lambda s: s.materials[0]['color'].__setitem__(0, .8),
        }
        for name, mutate in mutations.items():
            with self.subTest(attribute=name):
                scene, project = shell_fixture()
                decision = self.decision(scene, project)
                mutate(scene)
                with self.assertRaises(GarageError):
                    resolve_shell(scene, project, decision)
                self.assert_blocked(scene, project)

    def test_forged_or_incomplete_component_inspection_cannot_authorize_a_shell(self):
        mutations = {
            'missing_surface': lambda p: p['inspection']['parts'].pop(),
            'duplicate_surface': lambda p: p['inspection']['parts'].append(copy.deepcopy(p['inspection']['parts'][0])),
            'wrong_material': lambda p: p['inspection']['parts'][0].update(material=1),
            'wrong_face_count': lambda p: p['inspection']['parts'][0].update(triangles=1),
            'invented_centres': lambda p: p['inspection'].update(wheel_centers={'FL': [1., 0., 1.]}),
        }
        for name, mutate in mutations.items():
            with self.subTest(inspection=name):
                scene, project = shell_fixture()
                mutate(project)
                self.assert_blocked(scene, project)

    def test_exclusion_needs_explicit_reason_and_keeps_exact_face_accounting(self):
        scene, project = shell_fixture(with_badge=True)
        badge = scene.parts[-1]
        self.assertEqual(len(badge.indices), 2)
        project['overrides'][badge.id] = {'role': 'ignore', 'corner': None}
        project['inspection'] = infer(scene, project['forward'], project['overrides'])
        self.assert_blocked(scene, project)
        self.assert_blocked(scene, project, dispositions={badge.id: {'disposition': 'excluded', 'reason': ''}})
        dispositions = {badge.id: {'disposition': 'excluded', 'reason': 'Remove the optional roof badge from this candidate.'}}
        review = self.review(scene, project, dispositions=dispositions)
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        self.assertEqual(review['triangle_dispositions'], {'emit': 18, 'excluded': 2})
        self.assertEqual(sum(review['triangle_dispositions'].values()), scene.triangles)
        self.assertEqual(len(review['coverage']), len(scene.parts))
        self.decision(scene, project, dispositions=dispositions)

    def test_source_wheel_or_brake_semantics_cannot_be_laundered_into_body(self):
        for source_role, name in (('rim', 'Source wheel'), ('brake_rotor', 'Source brake disc'),
                                  (None, 'Brake rotor and caliper fused group')):
            with self.subTest(role=source_role, name=name):
                scene, project = shell_fixture(shell_role=source_role, shell_name=name)
                project['overrides'][scene.parts[0].id] = {'role': 'body', 'corner': None}
                project['inspection'] = infer(scene, project['forward'], project['overrides'])
                self.assertEqual(project['inspection']['parts'][0]['role'], 'body')
                self.assert_blocked(scene, project)

    def test_unknown_shell_needs_manual_component_review(self):
        scene, project = shell_fixture(shell_role=None, shell_name='Mesh 0001')
        row = project['inspection']['parts'][0]
        self.assertEqual(row['role'], 'body')
        self.assertLess(row['confidence'], .75)
        project['approved_roles'] = False
        self.assert_blocked(scene, project)
        # Explicit manual approval is a scope boundary, not an automated proof
        # that arbitrary fused geometry is physically free of moving parts.
        project['approved_roles'] = True
        self.assertTrue(self.review(scene, project)['ready_for_candidate'])

    def test_unsupported_source_materials_fail_before_any_output_directory(self):
        from .template_shell import approve_shell, build_shell
        # Opaque two-sided surfaces now have an explicit geometry realization;
        # transparent two-sided materials still lack a native recipe.
        for material in ({'alphaMode': 'MASK'}, {'doubleSided': True, 'alphaMode': 'BLEND'}):
            with self.subTest(material=material):
                scene, project = shell_fixture(extra_material=material)
                review = self.assert_blocked(scene, project)
                with self.assertRaises(GarageError):
                    approve_shell(scene, project, {'scale': 1., 'translation_metres': [0., 0., 0.]},
                                  {}, 'a'*64, True)
                valid_scene, valid_project = shell_fixture()
                decision = self.decision(valid_scene, valid_project)
                with tempfile.TemporaryDirectory() as folder:
                    output = Path(folder)/'rejected-output'
                    with patch('garagehub.template_shell.validate_template', return_value={'root': folder}):
                        with self.assertRaises(GarageError):
                            build_shell(scene, project, Path(folder), output, decision)
                    self.assertFalse(output.exists())

    def test_opaque_two_sided_shell_review_binds_generated_surface_policy(self):
        from .template_shell import resolve_shell, plan_shell
        scene, project = shell_fixture(extra_material={'doubleSided': True})
        original = source_snapshot(scene)
        review = self.review(scene, project)
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        self.assertEqual(review['triangle_dispositions'], {'emit': 18, 'excluded': 0})
        self.assertEqual(review['opaque_two_sided_realization']['part_ids'], ['n0p0'])
        self.assertEqual(review['prepared_budgets']['hq']['triangles'], 30)
        decision = self.decision(scene, project)
        _, _, resolved = resolve_shell(scene, project, decision)
        self.assertEqual(resolved['opaque_two_sided_realization'], review['opaque_two_sided_realization'])
        # A prepared geometry plan alone never authenticates native rendering.
        with tempfile.TemporaryDirectory() as folder:
            with patch('garagehub.template_shell.validate_template', return_value={'root': folder}):
                with self.assertRaises(FileNotFoundError):
                    plan_shell(scene, project, Path(folder), decision)
        self.assertEqual(source_snapshot(scene), original)

    def test_native_unsupported_texture_channels_cannot_receive_approval(self):
        for channel in ('metallicRoughnessTexture', 'occlusionTexture', 'emissiveTexture'):
            with self.subTest(channel=channel):
                scene, project = unsupported_channel_fixture(channel)
                self.assertEqual(scene.triangles, 18)
                # Native material conversion only transfers the reviewed base
                # texture recipe. Other authored maps must not disappear under
                # an otherwise successful generic shell review.
                self.assert_blocked(scene, project)

    def test_transparent_body_cannot_silently_take_native_opaque_palette(self):
        for feature in ('blend', 'transmission', 'paint_blend'):
            with self.subTest(feature=feature):
                material = {'pbrMetallicRoughness': {
                    'baseColorFactor': [.3, .4, .6, .2], 'metallicFactor': 0.}}
                role = 'paint' if feature == 'paint_blend' else 'body'
                if feature in ('blend', 'paint_blend'):
                    material['alphaMode'] = 'BLEND'
                    if feature == 'paint_blend':
                        # Paint is opaque regardless of the opaque-palette
                        # metallic threshold; the same review gate must cover it.
                        material['pbrMetallicRoughness']['metallicFactor'] = .8
                else:
                    material['extensions'] = {'KHR_materials_transmission': {'transmissionFactor': .8}}
                scene, project = shell_fixture(shell_role=role, extra_material=material)
                self.assertEqual(project['inspection']['parts'][0]['role'], role)
                self.assert_blocked(scene, project)
                # Assigning the explicit supported optical role is a truthful,
                # recoverable remedy; the source material remains untouched.
                project['overrides'][scene.parts[0].id] = {'role': 'glass', 'corner': None}
                project['inspection'] = infer(scene, '+Z', project['overrides'])
                review = self.review(scene, project)
                self.assertTrue(review['ready_for_candidate'], review['blockers'])

    def test_prepared_coordinate_claim_must_reproduce_the_actual_glb(self):
        scene, project = shell_fixture()
        for field, value in (('metres_per_unit', .01), ('source_up_axis', '+Z')):
            with self.subTest(field=field):
                changed = copy.deepcopy(project)
                changed[field] = value
                self.assert_blocked(scene, changed)

    def test_saved_placement_cannot_accept_an_injected_matrix_or_destination_change(self):
        from .template_shell import resolve_shell
        scene, project = shell_fixture()
        decision = self.decision(scene, project)
        forged = copy.deepcopy(decision)
        forged['matrix'] = np.eye(3).tolist()
        with self.assertRaises(GarageError):
            resolve_shell(scene, project, forged)
        forged = copy.deepcopy(decision)
        forged['placement']['translation_metres'][0] += .2
        with self.assertRaises(GarageError):
            resolve_shell(scene, project, forged)
        changed = copy.deepcopy(project)
        changed['installation'] = {'mode': 'replace', 'replace_car_id': 82}
        with self.assertRaises(GarageError):
            resolve_shell(scene, changed, decision)


if __name__ == '__main__':
    unittest.main()
