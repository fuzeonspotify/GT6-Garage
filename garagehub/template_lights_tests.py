"""Bare-body lamp assembly tests with genuine serialized, wheel-free source GLB.

Native file serialization is a separate owned-PC check. The orchestration test
labels its synthetic serializer boundary explicitly.
"""
from __future__ import annotations

import copy
import hashlib
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .car_geometry import prepare_parts
from .car_profiles import PROFILE
from .errors import GarageError
from .glb import GLB, unit_normals
from .recognition import infer
from .scene_coordinates import prepare_scene
from .template_shell import (normalize_shell_settings, review_shell, approve_shell,
                             resolve_shell, assemble_shell, build_shell)
from .template_shell_independent_tests import shell_fixture, source_snapshot
from .template_shell_tests import native_fixture, body_boundary, synthetic_race_writer, SYNTHETIC_STOCK_RACE
from .template_lights import PART_PREFIX, verify_prepared_mounts


PLACEMENT = {'scale': 1., 'translation_metres': [0., .35, 0.]}


def bare_body_glb(*, open_ends=False, omit_end=None, include_wheel=False, source_from_metres=None):
    """A separately authored body-only GLB; no hidden lamp objects or source wheels."""
    matrix = np.eye(3) if source_from_metres is None else np.asarray(source_from_metres)
    points = np.array([[-.9, .2, -1.8], [.9, .2, -1.8], [.9, 1.3, -1.8], [-.9, 1.3, -1.8],
                       [-.9, .2, 1.8], [.9, .2, 1.8], [.9, 1.3, 1.8], [-.9, 1.3, 1.8]])
    faces = np.array([[0, 2, 1], [0, 3, 2], [4, 5, 6], [4, 6, 7],
                      [0, 4, 7], [0, 7, 3], [1, 2, 6], [1, 6, 5],
                      [0, 1, 5], [0, 5, 4], [3, 7, 6], [3, 6, 2]])
    if open_ends:
        faces = faces[4:]
    elif omit_end == 'negative_z':
        faces = faces[2:]
    elif omit_end == 'positive_z':
        faces = np.concatenate((faces[:2], faces[4:]))
    elif omit_end is not None:
        raise ValueError('Unknown synthetic body end to omit.')
    specs = [('Plain body shell', 'body', points, faces)]
    if include_wheel:
        # Deliberate authored mechanical evidence for exclusion tests. It is
        # not counted as the wheel-free fixture or a valid spinning component.
        specs.append(('Source wheel to replace', 'rim',
                      np.array([[1., .2, -.8], [1., .5, -.8], [1., .3, -.5]]),
                      np.array([[0, 1, 2]])))
    doc = {'asset': {'version': '2.0', 'generator': 'Bare-body supplied-light QA'},
           'buffers': [], 'bufferViews': [], 'accessors': [],
           'materials': [{'name': 'Plain shell finish', 'pbrMetallicRoughness': {
               'baseColorFactor': [.25, .35, .5, 1.], 'metallicFactor': 0.}}],
           'meshes': [], 'nodes': [], 'scenes': [{'nodes': list(range(len(specs)))}], 'scene': 0}
    binary = bytearray()
    def attribute(data, component, kind, dtype):
        binary.extend(b'\0'*(-len(binary) % 4))
        data = np.asarray(data, dtype=dtype)
        view = len(doc['bufferViews'])
        doc['bufferViews'].append({'buffer': 0, 'byteOffset': len(binary), 'byteLength': data.nbytes})
        binary.extend(data.tobytes())
        accessor = len(doc['accessors'])
        doc['accessors'].append({'bufferView': view, 'componentType': component,
                                 'count': len(data), 'type': kind})
        return accessor
    for index, (name, role, p, f) in enumerate(specs):
        n = unit_normals(p, f).astype(float) @ np.linalg.inv(matrix)
        n /= np.linalg.norm(n, axis=1)[:, None]
        pos = attribute(p @ matrix.T, 5126, 'VEC3', '<f4')
        normal = attribute(n, 5126, 'VEC3', '<f4')
        uv = attribute(np.full((len(p), 2), .5), 5126, 'VEC2', '<f4')
        ids = attribute(f.reshape(-1), 5123, 'SCALAR', '<u2')
        doc['meshes'].append({'primitives': [{'attributes': {'POSITION': pos, 'NORMAL': normal,
                                      'TEXCOORD_0': uv}, 'indices': ids, 'material': 0}]})
        doc['nodes'].append({'mesh': index, 'name': name, 'extras': {'gt6_role': role}})
    doc['buffers'] = [{'byteLength': len(binary)}]
    raw_json = json.dumps(doc, sort_keys=True, separators=(',', ':')).encode()
    raw_json += b' '*(-len(raw_json) % 4)
    binary.extend(b'\0'*(-len(binary) % 4))
    return (struct.pack('<4sII', b'glTF', 2, 28+len(raw_json)+len(binary))+
            struct.pack('<II', len(raw_json), 0x4e4f534a)+raw_json+
            struct.pack('<II', len(binary), 0x004e4942)+binary)


def bare_shell_fixture(**kwargs):
    up = kwargs.pop('source_up_axis', '+Y')
    metres = kwargs.pop('metres_per_unit', 1.)
    forward = kwargs.pop('forward', '+Z')
    raw = bare_body_glb(**kwargs)
    original = GLB(raw).scene()
    scene = prepare_scene(original, up, metres)
    project = {'schema': 2, 'id': 'd'*32, 'name': 'Bare shell supplied lights',
        'source_sha256': original.digest, 'source_up_axis': up, 'metres_per_unit': metres,
        'forward': forward, 'overrides': {}, 'wheel_partitions': [],
        'installation': {'mode': 'new_entry', 'replace_car_id': None},
        'approved_roles': True, 'template_inheritance_approved': True,
        'inspection': infer(scene, forward)}
    return scene, project


def supplied_review(scene, project, placement=None, dispositions=None, lighting=None):
    return review_shell(scene, project, placement or PLACEMENT, dispositions,
                        lighting or {'mode': 'supplied'})


class TemplateLightsTests(unittest.TestCase):
    def test_textured_race_reduction_preserves_attributes_borders_and_mount(self):
        from dataclasses import replace
        from .textured_lod import reduce_textured_part
        from .surface_guards import boundary_edges
        side=23
        y,x=np.mgrid[:side,:side]
        xy=np.column_stack((x.ravel(),y.ravel()))/side
        points=np.column_stack((xy,np.zeros(len(xy))))
        normals=np.tile([0.,0.,1.],(len(points),1))
        starts=(y[:-1,:-1]*side+x[:-1,:-1]).ravel()
        faces=np.vstack([np.column_stack((starts,starts+1,starts+side+1)),
                         np.column_stack((starts,starts+side+1,starts+side))])
        base=GLB(bare_body_glb()).scene().parts[0]
        uv=xy*np.array([2.,-1.])+[.125,.25]
        part=replace(base,positions=points,normals=normals,uv=uv,indices=faces)
        contact=len(starts)//2+side//2
        p,n,u,f,report=reduce_textured_part(part,200,{contact})
        self.assertLess(len(f),len(faces))
        self.assertLessEqual(report['relative_attribute_error'],report['relative_error_limit'])
        lookup={tuple(v):i for i,v in enumerate(points)}
        original=np.array([lookup[tuple(v)] for v in p])
        np.testing.assert_array_equal(n,normals[original])
        np.testing.assert_array_equal(u,uv[original])
        restored=original[f]
        self.assertIn(tuple(faces[contact]),map(tuple,restored))
        self.assertEqual(set(map(tuple,boundary_edges(faces))),set(map(tuple,boundary_edges(restored))))

    def test_mixed_light_sets_keep_authored_end_and_generate_only_missing_pair(self):
        from .shell_setup import suggest_setup
        for missing, removed, expected in [('rear',[3],'supplied_rear'),('front',[1,2],'supplied_front')]:
            with self.subTest(missing=missing):
                scene, project = shell_fixture()
                project['assembly_mode']='template_shell'
                excluded={}
                for idx in removed:
                    pid=scene.parts[idx].id
                    project['overrides'][pid]={'role':'ignore'}
                    excluded[pid]={'disposition':'excluded','reason':'Removed by fixture author'}
                project['inspection']=infer(scene,'+Z',project['overrides'])
                project['template_shell_settings']={'dispositions':excluded}
                proposal=suggest_setup(scene,project,'keep_size')
                settings=proposal['changes']['template_shell_settings']
                self.assertEqual(settings['lighting']['mode'],expected)
                self.assertEqual(proposal['lights'][missing]['origin'],'supplied')
                review=supplied_review(scene,project,dispositions=excluded,lighting=settings['lighting'])
                self.assertTrue(review['ready_for_candidate'],review['blockers'])
                generated=review['generated_lighting']
                self.assertEqual({m['name'] for m in generated['mounts']},{missing+'_left',missing+'_right'})
                self.assertEqual(len(generated['generated_parts']),4)
                self.assertEqual(review['lamp_mount_checks']['race']['centres_checked'],2)
                self.assertEqual(supplied_review(scene,project,dispositions=excluded)['blockers'][0]['code'],'shell_lamps')

    def test_one_sided_authored_headlight_reaches_parts_review(self):
        from .shell_setup import suggest_setup
        scene,project=shell_fixture()
        pid=scene.parts[1].id
        project.update(assembly_mode='template_shell',overrides={pid:{'role':'ignore'}},
                       template_shell_settings={'dispositions':{pid:{'disposition':'excluded','reason':'Removed by fixture author'}}})
        project['inspection']=infer(scene,'+Z',project['overrides'])
        setup=suggest_setup(scene,project,'keep_size')
        self.assertEqual(setup['lights']['front']['origin'],'review')
        self.assertEqual(setup['changes']['template_shell_settings']['lighting']['mode'],'source')

    def test_bare_source_gets_separately_accounted_supplied_lamps(self):
        scene, project = bare_shell_fixture()
        self.assertEqual(len(scene.parts), 1)
        self.assertEqual(scene.triangles, 12)
        self.assertFalse(review_shell(scene, project, PLACEMENT)['ready_for_candidate'])
        review = supplied_review(scene, project)
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        self.assertEqual(review['source_parts'], 1)
        self.assertEqual(review['triangle_dispositions'], {'emit': 12, 'excluded': 0})
        generated = review['generated_lighting']
        self.assertEqual(len(generated['generated_part_ids']), 8)
        self.assertEqual(generated['generated_triangles'], 184)
        self.assertEqual(review['prepared_budgets']['hq']['triangles'], 196)
        self.assertEqual(review['prepared_budgets']['race']['triangles'], 196)
        self.assertFalse(generated['runtime_verified'])
        for lod in ('hq', 'race'):
            self.assertTrue(review['lamp_mount_checks'][lod]['passed'])
            self.assertEqual(review['lamp_mount_checks'][lod]['samples'], 32)

    def test_generated_surfaces_have_outward_winding_and_actual_attachment(self):
        scene, project = bare_shell_fixture()
        review = supplied_review(scene, project)
        assembly, assembly_project, receipt = assemble_shell(scene, project, review)
        matrix, shift = np.asarray(review['matrix']), np.asarray(review['translation_metres'])
        prepared, _, _ = prepare_parts(assembly, assembly_project['inspection'], 'hq',
                                       fit_solution=(matrix, shift, review))
        for part in prepared:
            if not part['id'].startswith(PART_PREFIX):
                continue
            p, f, n = part['positions'], part['faces'], part['normals']
            cross = np.cross(p[f[:, 1]]-p[f[:, 0]], p[f[:, 2]]-p[f[:, 0]])
            self.assertTrue(np.all(np.linalg.norm(cross, axis=1) > 0))
            self.assertTrue(np.all(np.sum(cross*n[f[:, 0]], axis=1) > 0))
            np.testing.assert_allclose(np.linalg.norm(n, axis=1), 1.)
            if part['role'] in ('headlight', 'taillight'):
                expected = -1 if part['role'] == 'headlight' else 1
                np.testing.assert_allclose(n[:, 2], expected)
        for mount in receipt['mounts']:
            side = mount['outward_native_z']
            self.assertTrue(mount['center_metres'][2]*side > 1.8)
            for hit in mount['mount_samples']:
                self.assertEqual(hit['source_part_id'], scene.parts[0].id)
                self.assertAlmostEqual(hit['position_metres'][2], side*1.8, places=6)

    def test_existing_source_lights_need_explicit_exclusion_before_supplied_mode(self):
        scene, project = shell_fixture()
        self.assertFalse(supplied_review(scene, project)['ready_for_candidate'])
        dispositions = {}
        for row in project['inspection']['parts']:
            if row['role'] in ('headlight', 'taillight'):
                project['overrides'][row['id']] = {'role': 'ignore', 'corner': None}
                dispositions[row['id']] = {'disposition': 'excluded', 'reason': 'Replace with supplied lights'}
        project['inspection'] = infer(scene, project['forward'], project['overrides'])
        review = supplied_review(scene, project, dispositions=dispositions)
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        self.assertEqual(review['triangle_dispositions'], {'emit': 12, 'excluded': 6})

    def test_source_mechanics_can_only_be_explicitly_excluded_not_baked_into_body(self):
        scene, project = bare_shell_fixture(include_wheel=True)
        wheel = scene.parts[1].id
        for role in ('rim', 'body', 'ignore'):
            altered = copy.deepcopy(project)
            altered['overrides'][wheel] = {'role': role, 'corner': None}
            altered['inspection'] = infer(scene, altered['forward'], altered['overrides'])
            self.assertFalse(supplied_review(scene, altered)['ready_for_candidate'])
        project['overrides'][wheel] = {'role': 'ignore', 'corner': None}
        project['inspection'] = infer(scene, project['forward'], project['overrides'])
        review = supplied_review(scene, project, dispositions={wheel: {
            'disposition': 'excluded', 'reason': 'Replace source wheel with supplied running gear'}})
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        self.assertEqual(review['triangle_dispositions'], {'emit': 12, 'excluded': 1})
        self.assertTrue(review['coverage'][1]['mechanical_evidence_excluded'])

    def test_original_input_arrays_and_materials_remain_unchanged(self):
        scene, project = bare_shell_fixture()
        before, project_before = source_snapshot(scene), copy.deepcopy(project)
        review = supplied_review(scene, project)
        assembly, _, _ = assemble_shell(scene, project, review)
        self.assertEqual(source_snapshot(scene), before)
        self.assertEqual(project, project_before)
        self.assertEqual(assembly.raw, scene.raw)
        self.assertEqual(assembly.digest, scene.digest)
        self.assertEqual(len(scene.parts), 1)
        self.assertEqual(len(assembly.parts), 9)

    def test_lighting_and_flip_choices_invalidate_saved_approval(self):
        scene, project = bare_shell_fixture()
        review = supplied_review(scene, project)
        decision = approve_shell(scene, project, PLACEMENT, {}, review['review_digest'], True,
                                 {'mode': 'supplied'})
        self.assertTrue(resolve_shell(scene, project, decision)[2]['approved'])
        for change in ({'lighting': {'mode': 'supplied', 'front_height_ratio': .6}},
                       {'placement': {**PLACEMENT, 'flip_front': True}}):
            altered = copy.deepcopy(decision)
            altered.update(change)
            with self.assertRaises(GarageError):
                resolve_shell(scene, project, altered)
        forged = copy.deepcopy(review)
        forged['lighting']['rear_height_ratio'] = .65
        with self.assertRaises(GarageError):
            assemble_shell(scene, project, forged)

    def test_flip_front_is_proper_rotation_and_placement_applied_once(self):
        scene, project = bare_shell_fixture()
        a = supplied_review(scene, project, placement={'scale': 1.2, 'translation_metres': [0., .2, .1]})
        b = supplied_review(scene, project, placement={'scale': 1.2, 'translation_metres': [0., .2, .1], 'flip_front': True})
        self.assertTrue(a['ready_for_candidate'] and b['ready_for_candidate'])
        np.testing.assert_allclose(np.asarray(b['matrix']), np.diag([-1., 1., -1.]) @ np.asarray(a['matrix']))
        self.assertGreater(np.linalg.det(b['matrix']), 0)
        assembly, derived, generated = assemble_shell(scene, project, b)
        prepared, _, _ = prepare_parts(assembly, derived['inspection'], 'hq',
            fit_solution=(np.asarray(b['matrix']), np.asarray(b['translation_metres']), b))
        for mount in generated['mounts']:
            lens = next(p for p in prepared if p['id'] == PART_PREFIX+mount['name']+'_lens')
            np.testing.assert_allclose((lens['positions'].min(0)+lens['positions'].max(0))/2,
                                       mount['center_metres'], atol=1e-12)

    def test_missing_mount_blocks_review_before_native_output(self):
        scene, project = bare_shell_fixture(open_ends=True)
        review = supplied_review(scene, project)
        self.assertFalse(review['ready_for_candidate'])
        self.assertEqual(review['blockers'][0]['code'], 'shell_light_mount')
        self.assertIsNone(review['review_digest'])

    def test_single_open_end_cannot_mount_lamps_on_opposite_panel(self):
        # Source +Z is the declared front. Removing only that cap previously
        # let front rays pass through the car and attach to its intact rear.
        for omitted, expected_end in (('positive_z', 'front'), ('negative_z', 'rear')):
            for z_translation in (0., 2.8):
                with self.subTest(omitted=omitted, z_translation=z_translation):
                    scene, project = bare_shell_fixture(omit_end=omitted)
                    placement = {'scale': 1., 'translation_metres': [0., .35, z_translation]}
                    review = supplied_review(scene, project, placement=placement)
                    self.assertFalse(review['ready_for_candidate'])
                    self.assertEqual(review['blockers'][0]['code'], 'shell_light_mount')
                    self.assertIsNone(review['review_digest'])
                    self.assertIn(expected_end+' left', review['blockers'][0]['message'])
                    self.assertIn('opposite half', review['blockers'][0]['message'])

    def test_mount_halves_follow_body_midpoint_after_translation(self):
        scene, project = bare_shell_fixture()
        # Every surface is at positive native Z, so testing signs around the
        # world origin would wrongly reject the valid front attachment.
        review = supplied_review(scene, project,
                                 placement={'scale': 1., 'translation_metres': [0., .35, 2.8]})
        self.assertTrue(review['ready_for_candidate'], review['blockers'])
        receipt = review['generated_lighting']
        midpoint = float(np.asarray(receipt['body_mount_bounds_metres']).mean(0)[2])
        self.assertAlmostEqual(midpoint, 2.8)
        self.assertTrue(receipt['mount_half_check']['passed'])
        for mount in receipt['mounts']:
            for sample in mount['mount_samples']:
                self.assertGreater((sample['position_metres'][2]-midpoint)*mount['outward_native_z'], 0)

    def test_reduced_geometry_must_preserve_lamp_attachment(self):
        scene, project = bare_shell_fixture()
        review = supplied_review(scene, project)
        assembly, derived, generated = assemble_shell(scene, project, review)
        prepared, _, _ = prepare_parts(assembly, derived['inspection'], 'hq',
            fit_solution=(np.asarray(review['matrix']), np.asarray(review['translation_metres']), review))
        self.assertTrue(verify_prepared_mounts(prepared, generated)['passed'])
        altered = copy.deepcopy(prepared)
        altered[0]['positions'][:, 2] *= .98
        with self.assertRaises(GarageError):
            verify_prepared_mounts(altered, generated)

    def test_settings_are_finite_strict_and_roundtrip_normalized(self):
        good = normalize_shell_settings({'lighting': {'mode': 'supplied'}})
        self.assertEqual(normalize_shell_settings(good), good)
        for bad in ({'unknown': 1}, {'lighting': {'mode': 'basic'}},
                    {'lighting': {'mode': 'source', 'front_height_ratio': .4}},
                    {'lighting': {'mode': 'supplied', 'front_height_ratio': True}},
                    {'lighting': {'mode': 'supplied', 'rear_height_ratio': float('nan')}},
                    {'lighting': {'mode': 'supplied', 'half_spacing_ratio': .9}},
                    {'placement': {**PLACEMENT, 'flip_front': 1}},
                    {'dispositions': {'p': {'disposition': 'excluded', 'reason': ''}}}):
            with self.subTest(bad=bad), self.assertRaises(GarageError):
                normalize_shell_settings(bad)

    def test_complete_build_reports_source_and_generated_ownership_separately(self):
        scene, project = bare_shell_fixture()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            template, output = folder/'template', folder/'candidate'
            hashes = native_fixture(template)
            with synthetic_race_writer(), patch.dict('garagehub.template_shell.ASSETS', hashes, clear=True), \
                 patch('garagehub.template_shell.validate_template', return_value={'root': str(template)}), \
                 patch('garagehub.car_builder._build_body_with_fit', side_effect=body_boundary), \
                 patch('garagehub.car_builder.fit_projectors', side_effect=lambda raw, parts: (raw, [])):
                review = supplied_review(scene, project)
                decision = approve_shell(scene, project, PLACEMENT, {}, review['review_digest'], True,
                                         {'mode': 'supplied'})
                receipt = build_shell(scene, project, template, output, decision)
            self.assertEqual(receipt['source_triangles'], 12)
            self.assertEqual(receipt['source_parts'], 1)
            self.assertEqual(receipt['generated_lighting']['generated_triangles'], 184)
            for lod in ('hq', 'race'):
                self.assertEqual(receipt['body'][lod]['source_components_covered'], [scene.parts[0].id])
                self.assertEqual(len(receipt['body'][lod]['generated_components_covered']), 8)
                self.assertTrue(receipt['body'][lod]['synthetic_serializer_boundary'])
                expected = SYNTHETIC_STOCK_RACE if lod == 'race' else (template/lod/'wheel').read_bytes()
                self.assertEqual((output/PROFILE['model_path']/lod/'wheel').read_bytes(), expected)
            self.assertFalse(receipt['installable'])
            self.assertFalse(receipt['runtime_verified'])

