"""Axis/units edits must affect geometry and previews without changing source."""
from __future__ import annotations

import io
import json
import struct
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .errors import GarageError
from .glb import GLB
from .model_input_tests import TRIANGLE_OBJ, embedded_glb
from .projects import Store, PREPARATION_REVISION
from .scene_coordinates import UP_AXES, COORDINATE_SYSTEM, coordinate_settings, coordinate_description, prepare_scene
from .util import atomic_json, read_json, sha256


def preview_header(raw):
    size = struct.unpack_from('<I', raw, 4)[0]
    return json.loads(raw[16:16 + size])


def four_tyres_obj():
    lines = []
    for count, (corner, x, z) in enumerate((('FL', 100, 200), ('FR', -100, 200), ('RL', 100, -200), ('RR', -100, -200))):
        start = count * 3 + 1
        lines.extend((f'o {corner}', f'v {x-10} 10 {z-30}', f'v {x+10} 90 {z-30}', f'v {x} 50 {z+30}', f'f {start} {start+1} {start+2}'))
    return ('\n'.join(lines) + '\n').encode()


class SceneCoordinateTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'projects')

    def tearDown(self):
        self.tmp.cleanup()

    def test_identity_returns_original_scene_and_unchanged_float_bytes(self):
        source = GLB(embedded_glb()).scene()
        result = prepare_scene(source)
        self.assertIs(result, source)
        self.assertEqual(result.parts[0].positions.tobytes(), source.parts[0].positions.tobytes())
        self.assertEqual(result.raw, source.raw)
        self.assertEqual(result.digest, source.digest)

    def test_all_up_axes_are_proper_rotations_and_preserve_winding(self):
        source = GLB(embedded_glb()).scene()
        for axis in UP_AXES:
            with self.subTest(axis=axis):
                description = coordinate_description(axis)
                matrix = np.asarray(description['source_to_y_up_rotation'])
                vector = np.zeros(3)
                vector['XYZ'.index(axis[1])] = 1 if axis[0] == '+' else -1
                np.testing.assert_array_equal(matrix @ vector, [0, 1, 0])
                self.assertAlmostEqual(float(np.linalg.det(matrix)), 1.)
                part = prepare_scene(source, axis).parts[0]
                triangle = part.indices[0]
                cross = np.cross(part.positions[triangle[1]] - part.positions[triangle[0]], part.positions[triangle[2]] - part.positions[triangle[0]])
                self.assertGreater(float(cross @ part.normals[triangle].mean(axis=0)), 0.)
                np.testing.assert_array_equal(part.indices, source.parts[0].indices)

    def test_z_up_rotation_and_units_copy_only_prepared_vectors(self):
        source = GLB(embedded_glb()).scene()
        result = prepare_scene(source, '+Z', .01)
        np.testing.assert_allclose(result.parts[0].positions, [[0, 0, 0], [.01, 0, 0], [0, 0, -.01]])
        np.testing.assert_allclose(result.parts[0].normals, [[0, 1, 0]] * 3)
        np.testing.assert_allclose(source.parts[0].positions, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
        self.assertFalse(np.shares_memory(result.parts[0].positions, source.parts[0].positions))
        self.assertFalse(np.shares_memory(result.parts[0].normals, source.parts[0].normals))
        self.assertIs(result.parts[0].uv, source.parts[0].uv)
        self.assertIs(result.parts[0].indices, source.parts[0].indices)
        self.assertIs(result.materials, source.materials)
        self.assertEqual(result.parts[0].id, source.parts[0].id)
        self.assertEqual(result.raw, source.raw)
        self.assertEqual(result.digest, source.digest)

    def test_invalid_coordinates_raise_actionable_errors(self):
        for axis in ('Z', '+Q', None, [], 2):
            with self.subTest(axis=axis):
                with self.assertRaises(GarageError):
                    coordinate_settings(axis, 1)
        for value in (0, -1, True, '0.01', None, float('nan'), float('inf'), 1e-7, 1e7, 10**1000):
            with self.subTest(value=value):
                with self.assertRaises(GarageError):
                    coordinate_settings('+Y', value)
        self.assertEqual(coordinate_settings('+Y', 1e-6)['metres_per_unit'], 1e-6)
        self.assertEqual(coordinate_settings('+Y', 1e6)['metres_per_unit'], 1e6)

    def test_store_cache_is_keyed_by_project_axis_and_absolute_scale(self):
        p = self.store.create(TRIANGLE_OBJ, 'car.obj')
        first = self.store.scene(p['id'])
        self.assertIs(first, self.store.scene(p['id']))
        self.store.update(p['id'], {'source_up_axis': '+Z', 'metres_per_unit': .01})
        second = self.store.scene(p['id'])
        self.assertIs(second, self.store.scene(p['id']))
        self.assertIsNot(second, first)
        self.store.update(p['id'], {'metres_per_unit': .001})
        third = self.store.scene(p['id'])
        np.testing.assert_allclose(third.parts[0].positions[1], [.001, 0, 0])
        self.store.update(p['id'], {'source_up_axis': '+Y', 'metres_per_unit': 1})
        restored = self.store.scene(p['id'])
        self.assertEqual(restored.parts[0].positions.tobytes(), first.parts[0].positions.tobytes())
        self.assertEqual(restored.parts[0].normals.tobytes(), first.parts[0].normals.tobytes())

    def test_store_rebuilds_both_previews_and_preserves_original_inputs(self):
        p = self.store.create(TRIANGLE_OBJ, 'car.obj')
        folder = self.store.folder(p['id'])
        source = (folder / 'source.glb').read_bytes()
        preview = (folder / 'preview.bin').read_bytes()
        result = self.store.update(p['id'], {'source_up_axis': '+Z', 'metres_per_unit': .01})
        self.assertEqual((folder / 'source.glb').read_bytes(), source)
        self.assertEqual((folder / 'original_input.bin').read_bytes(), TRIANGLE_OBJ)
        self.assertEqual(result['source_sha256'], sha256(source))
        self.assertEqual(result['source_metadata'], p['source_metadata'])
        self.assertNotEqual((folder / 'preview.bin').read_bytes(), preview)
        for name in ('preview.bin', 'software_preview.bin'):
            np.testing.assert_allclose(preview_header((folder / name).read_bytes())['bounds'], self.store.scene(p['id']).bounds)
        np.testing.assert_allclose(result['inspection']['bounds'], [[0, 0, -.01], [.01, 0, 0]])

    def test_coordinates_invalidate_review_even_in_combined_request(self):
        p = self.store.create(TRIANGLE_OBJ, 'car.obj')
        self.store.update(p['id'], {'approved_roles': True})
        same = self.store.update(p['id'], {'source_up_axis': '+Y', 'metres_per_unit': 1})
        self.assertTrue(same['approved_roles'])
        changed = self.store.update(p['id'], {'source_up_axis': '+Z', 'approved_roles': True})
        self.assertFalse(changed['approved_roles'])

    def test_front_proposal_recomputed_or_explicitly_retained(self):
        source = b'v 0 0 0\nv 4 .2 0\nv 0 0 3\nf 1 2 3\n'
        p = self.store.create(source, 'car.obj')
        self.assertEqual(p['forward'], '+X')
        changed = self.store.update(p['id'], {'source_up_axis': '+X'})
        self.assertEqual(changed['forward'], '+Z')
        explicit = self.store.update(p['id'], {'metres_per_unit': .1, 'forward': '-Z'})
        self.assertEqual(explicit['forward'], '-Z')
        self.assertEqual(explicit['inspection']['forward'], '-Z')

    def test_auto_wheelbase_recomputed_in_metres_unless_explicit(self):
        p = self.store.create(four_tyres_obj(), 'centimetres.obj')
        assignments = {part['id']: {'role': 'tyre', 'corner': part['name']} for part in p['inspection']['parts']}
        self.store.update(p['id'], {'overrides': assignments, 'wheelbase_metres': 7})
        scaled = self.store.update(p['id'], {'metres_per_unit': .01})
        self.assertAlmostEqual(scaled['wheelbase_metres'], 4.)
        self.assertAlmostEqual(scaled['inspection']['wheelbase'], 4.)
        explicit = self.store.update(p['id'], {'metres_per_unit': .02, 'wheelbase_metres': 2.7})
        self.assertAlmostEqual(explicit['wheelbase_metres'], 2.7)
        self.assertAlmostEqual(explicit['inspection']['wheelbase'], 8.)

    def test_project_roundtrip_restores_coordinates_preview_and_review(self):
        p = self.store.create(TRIANGLE_OBJ, 'artist.obj')
        self.store.update(p['id'], {'source_up_axis': '+Z', 'metres_per_unit': .01, 'forward': '-Z'})
        self.store.update(p['id'], {'approved_roles': True})
        original_preview = (self.store.folder(p['id']) / 'preview.bin').read_bytes()
        path = self.root / 'scaled.gt6car'
        self.store.export(p['id'], path)
        restored = self.store.import_archive(path.read_bytes())
        self.assertEqual(restored['source_up_axis'], '+Z')
        self.assertEqual(restored['metres_per_unit'], .01)
        self.assertEqual(restored['forward'], '-Z')
        self.assertTrue(restored['approved_roles'])
        self.assertEqual(restored['source_sha256'], p['source_sha256'])
        self.assertEqual((self.store.folder(restored['id']) / 'preview.bin').read_bytes(), original_preview)
        self.assertEqual((self.store.folder(restored['id']) / 'original_input.bin').read_bytes(), TRIANGLE_OBJ)

    def test_neutral_geometry_and_manifest_describe_the_same_prepared_space(self):
        p = self.store.create(TRIANGLE_OBJ, 'centimetres.obj')
        self.store.update(p['id'], {'source_up_axis': '+Z', 'metres_per_unit': .01})
        out = self.root / 'prepared.zip'
        self.store.neutral(p['id'], out)
        with zipfile.ZipFile(out) as archive:
            meta = json.loads(archive.read('car.json'))
            binary = archive.read('geometry.bin')
        self.assertEqual(meta['coordinate_system'], COORDINATE_SYSTEM)
        self.assertEqual(meta['source_up_axis'], '+Z')
        self.assertEqual(meta['metres_per_unit'], .01)
        self.assertEqual(meta['geometry_sha256'], sha256(binary))
        self.assertTrue(meta['coordinate_transform']['source_bytes_unchanged'])
        position = meta['parts'][0]['buffers']['positions']
        actual = np.frombuffer(binary[position['offset']:position['offset'] + position['bytes']], '<f4').reshape(-1, 3)
        np.testing.assert_array_equal(actual, self.store.scene(p['id']).parts[0].positions)

    def test_legacy_defaults_and_invalid_settings_preserve_saved_project(self):
        p = self.store.create(embedded_glb(), 'legacy.glb')
        folder = self.store.folder(p['id'])
        stored = read_json(folder / 'project.json')
        stored.pop('source_up_axis')
        stored.pop('metres_per_unit')
        atomic_json(folder / 'project.json', stored)
        legacy = self.store.summary(p['id'])
        self.assertEqual(legacy['source_up_axis'], '+Y')
        self.assertEqual(legacy['metres_per_unit'], 1.)
        original = (folder / 'project.json').read_bytes()
        for changes in ({'source_up_axis': 'Z'}, {'metres_per_unit': float('nan')}, {'metres_per_unit': -1}):
            with self.subTest(changes=changes):
                with self.assertRaises(GarageError):
                    self.store.update(p['id'], changes)
                self.assertEqual((folder / 'project.json').read_bytes(), original)

    def test_legacy_preparation_refreshes_once_and_preserves_manual_assignments(self):
        p = self.store.create(TRIANGLE_OBJ, 'legacy.obj')
        pid = p['id']
        folder = self.store.folder(pid)
        part = p['inspection']['parts'][0]['id']
        self.store.update(pid, {'overrides': {part: {'role': 'paint', 'corner': None}}, 'approved_roles': True, 'forward': '-Z'})
        metadata = read_json(folder / 'project.json')
        metadata.pop('preparation_revision')
        metadata.pop('source_up_axis')
        metadata.pop('metres_per_unit')
        atomic_json(folder / 'project.json', metadata)
        atomic_json(folder / 'inspection.json', {'stale': True})
        (folder / 'preview.bin').write_bytes(b'old preview')
        (folder / 'software_preview.bin').write_bytes(b'old software preview')
        raw_before = (folder / 'source.glb').read_bytes()
        from .preview import build_preview
        with patch('garagehub.projects.build_preview', wraps=build_preview) as builder:
            refreshed = self.store.summary(pid)
            self.assertEqual(builder.call_count, 2)
            self.store.summary(pid)
            self.assertEqual(builder.call_count, 2)
        self.assertEqual(refreshed['preparation_revision'], PREPARATION_REVISION)
        self.assertFalse(refreshed['approved_roles'])
        self.assertEqual(refreshed['inspection']['parts'][0]['role'], 'paint')
        self.assertEqual(refreshed['forward'], '-Z')
        self.assertEqual((folder / 'source.glb').read_bytes(), raw_before)
        self.assertEqual((folder / 'original_input.bin').read_bytes(), TRIANGLE_OBJ)
        self.assertEqual((folder / 'preview.bin').read_bytes()[:4], b'GGV1')

    def test_old_archived_review_is_reset_when_geometry_preparation_changes(self):
        p = self.store.create(TRIANGLE_OBJ, 'old.obj')
        self.store.update(p['id'], {'approved_roles': True})
        archive = self.root / 'old.gt6car'
        self.store.export(p['id'], archive)
        from .project_input_tests import rewrite_project
        legacy = rewrite_project(archive.read_bytes(), lambda project: project.pop('preparation_revision'))
        restored = self.store.import_archive(legacy)
        self.assertEqual(restored['preparation_revision'], PREPARATION_REVISION)
        self.assertFalse(restored['approved_roles'])

    def test_revision_two_refresh_preserves_assignments_coordinates_reference_and_intent(self):
        p=self.store.create(four_tyres_obj(),'reviewed_centimetres.obj');pid=p['id']
        overrides={part['id']:{'role':'tyre','corner':part['name']} for part in p['inspection']['parts']}
        reviewed=self.store.update(pid,{'source_up_axis':'+Y','metres_per_unit':.01,'forward':'+Z',
            'overrides':overrides,'wheelbase_metres':7.,'notes':'Keep the reviewed source setup.',
            'installation':{'mode':'replace','replace_car_id':82}})
        self.store.update(pid,{'approved_roles':True})
        folder=self.store.folder(pid);stored=read_json(folder/'project.json')
        stored['preparation_revision']=2;atomic_json(folder/'project.json',stored)
        atomic_json(folder/'inspection.json',{'stale':True,'wheelbase':999})
        (folder/'preview.bin').write_bytes(b'revision two preview')
        (folder/'software_preview.bin').write_bytes(b'revision two software preview')
        source=(folder/'source.glb').read_bytes();original=(folder/'original_input.bin').read_bytes()
        from .preview import build_preview
        with patch('garagehub.projects.build_preview',wraps=build_preview) as builder:
            current=self.store.summary(pid)
            self.assertEqual(builder.call_count,2)
            self.store.summary(pid)
            self.assertEqual(builder.call_count,2)
        self.assertEqual(current['preparation_revision'],PREPARATION_REVISION)
        self.assertFalse(current['approved_roles'])
        self.assertEqual(current['overrides'],overrides)
        self.assertEqual(current['forward'],'+Z')
        self.assertEqual(current['source_up_axis'],'+Y')
        self.assertEqual(current['metres_per_unit'],.01)
        self.assertEqual(current['wheelbase_metres'],7.)
        self.assertAlmostEqual(current['inspection']['wheelbase'],4.)
        self.assertEqual(current['notes'],reviewed['notes'])
        self.assertEqual(current['installation'],reviewed['installation'])
        self.assertEqual((folder/'source.glb').read_bytes(),source)
        self.assertEqual((folder/'original_input.bin').read_bytes(),original)
        for name in ('preview.bin','software_preview.bin'):
            np.testing.assert_allclose(preview_header((folder/name).read_bytes())['bounds'],self.store.scene(pid).bounds)


if __name__ == '__main__':
    unittest.main()
