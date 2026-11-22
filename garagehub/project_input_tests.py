"""Integration checks for original inputs and explicit installation intent."""
from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from .errors import GarageError
from .install_targets import installation, selection_actions
from .model_input_tests import TRIANGLE_OBJ, embedded_glb, model_zip
from .projects import Store
from .util import sha256


def archive_files(raw):
    with zipfile.ZipFile(io.BytesIO(raw)) as archive:
        return {name: archive.read(name) for name in archive.namelist()}


def rewrite_project(raw, change=None, changes=None, refresh=True):
    files = archive_files(raw)
    project = json.loads(files['project.json'])
    if change:
        change(project)
    files['project.json'] = json.dumps(project).encode()
    if changes:
        files.update(changes)
    manifest = json.loads(files['manifest.json'])
    if refresh and manifest.get('schema') == 2:
        manifest['files'] = {name: sha256(files[name]) for name in manifest['files'] if name in files}
    files['manifest.json'] = json.dumps(manifest).encode()
    return model_zip(files)


class ProjectInputTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.store = Store(self.root / 'projects')

    def tearDown(self):
        self.tmp.cleanup()

    def exported(self, source=TRIANGLE_OBJ, filename='car.obj'):
        project = self.store.create(source, filename)
        path = self.root / (project['id'] + '.gt6car')
        self.store.export(project['id'], path)
        return project, path.read_bytes()

    def test_obj_original_and_normalized_fingerprints_remain_distinct(self):
        project = self.store.create(TRIANGLE_OBJ, 'original_car.obj')
        folder = self.store.folder(project['id'])
        normalized = (folder / 'source.glb').read_bytes()
        self.assertEqual((folder / 'original_input.bin').read_bytes(), TRIANGLE_OBJ)
        self.assertEqual(project['source_sha256'], sha256(normalized))
        self.assertEqual(project['source_metadata']['normalized_sha256'], sha256(normalized))
        self.assertEqual(project['source_metadata']['source_sha256'], sha256(TRIANGLE_OBJ))
        self.assertNotEqual(project['source_sha256'], project['source_metadata']['source_sha256'])
        self.assertEqual(project['source_filename'], 'original_car.obj')
        self.assertTrue(any('up-axis' in warning for warning in project['source_warnings']))

    def test_zip_upload_keeps_exact_original_zip(self):
        source = model_zip({'model/car.obj': TRIANGLE_OBJ, 'credits.txt': b'Original author information'})
        project = self.store.create(source, 'my_model.zip')
        folder = self.store.folder(project['id'])
        self.assertEqual((folder / 'original_input.bin').read_bytes(), source)
        self.assertEqual(project['source_metadata']['input_format'], 'zip')
        self.assertEqual(project['source_metadata']['archive_model_path'], 'model/car.obj')

    def test_project_roundtrip_preserves_original_roles_and_both_install_modes(self):
        source = model_zip({'car.obj': TRIANGLE_OBJ})
        for choice in ({'mode': 'new_entry', 'replace_car_id': None}, {'mode': 'replace', 'replace_car_id': 2145}):
            with self.subTest(choice=choice):
                p = self.store.create(source, 'authored_model.zip')
                part_id = p['inspection']['parts'][0]['id']
                self.store.update(p['id'], {'overrides': {part_id: {'role': 'paint', 'corner': None}}, 'approved_roles': True, 'installation': choice, 'notes': 'Player-reviewed setup'})
                target = self.root / (p['id'] + '.gt6car')
                self.store.export(p['id'], target)
                payload = archive_files(target.read_bytes())
                manifest = json.loads(payload['manifest.json'])
                self.assertEqual(manifest['files']['original_input.bin'], sha256(source))
                restored = self.store.import_archive(target.read_bytes())
                self.assertNotEqual(restored['id'], p['id'])
                self.assertEqual(restored['source_sha256'], p['source_sha256'])
                self.assertEqual(restored['source_metadata'], p['source_metadata'])
                self.assertEqual(restored['source_filename'], 'authored_model.zip')
                self.assertEqual(restored['installation'], choice)
                self.assertEqual(restored['inspection']['parts'][0]['role'], 'paint')
                self.assertTrue(restored['approved_roles'])
                self.assertEqual(restored['notes'], 'Player-reviewed setup')
                self.assertEqual((self.store.folder(restored['id']) / 'original_input.bin').read_bytes(), source)

    def test_legacy_project_does_not_acquire_replacement_intent(self):
        original, archive = self.exported(embedded_glb(), 'old_car.glb')
        files = archive_files(archive)
        p = json.loads(files['project.json'])
        p.pop('installation', None)
        p.pop('source_metadata', None)
        p['schema'] = 1
        files['project.json'] = json.dumps(p).encode()
        files['manifest.json'] = json.dumps({'schema': 1, 'kind': 'gt6-preparation-project', 'source_sha256': original['source_sha256'], 'game_ready': False}).encode()
        restored = self.store.import_archive(model_zip(files))
        self.assertEqual(restored['installation'], {'mode': None, 'replace_car_id': None})
        self.assertTrue(selection_actions(restored['installation']))

    def test_installation_changes_never_infer_a_target_or_donor(self):
        self.assertEqual(installation({'mode': 'replace'}), {'mode': 'replace', 'replace_car_id': None})
        self.assertTrue(selection_actions({'mode': 'replace'}))
        self.assertFalse(selection_actions({'mode': 'new_entry'}))
        p = self.store.create(TRIANGLE_OBJ, 'car.obj')
        initial = (self.store.folder(p['id']) / 'project.json').read_bytes()
        for bad in ({'mode': 'new_entry', 'replace_car_id': 42}, {'mode': 'replace', 'replace_car_id': True}, {'mode': 'replace', 'replace_car_id': '42'}, {'mode': 'replace', 'replace_car_id': -1}, {'mode': 'replace', 'replace_car_id': 2**31}, {'mode': 'new_entry', 'donor_car_id': 42}, {'mode': 'unknown'}, []):
            with self.subTest(bad=bad):
                with self.assertRaises(GarageError):
                    self.store.update(p['id'], {'installation': bad})
                self.assertEqual((self.store.folder(p['id']) / 'project.json').read_bytes(), initial)

    def test_tampered_original_bytes_rejected_without_leftover_project(self):
        _, raw = self.exported()
        before = set(x.name for x in self.store.root.iterdir())
        changed = rewrite_project(raw, changes={'original_input.bin': b'changed model'}, refresh=False)
        with self.assertRaises(GarageError):
            self.store.import_archive(changed)
        self.assertEqual(set(x.name for x in self.store.root.iterdir()), before)

    def test_invalid_provenance_hash_or_installation_cleans_up(self):
        _, raw = self.exported()
        before = set(x.name for x in self.store.root.iterdir())
        mutations = [lambda p: p['source_metadata'].update(normalized_sha256='0' * 64),
                     lambda p: p['source_metadata'].update(source_sha256='0' * 64),
                     lambda p: p.update(installation={'mode': 'new_entry', 'replace_car_id': 22}),
                     lambda p: p.update(overrides={'unrelated-part': {'role': 'paint', 'corner': None}})]
        for change in mutations:
            with self.subTest(change=change):
                with self.assertRaises(GarageError):
                    self.store.import_archive(rewrite_project(raw, change))
                self.assertEqual(set(x.name for x in self.store.root.iterdir()), before)

    def test_malformed_provenance_rejected_with_user_error(self):
        _, raw = self.exported()
        for invalid in ({'warnings': 'not a list'}, {'warnings': [42]}, {'normalized_format': 'fbx'}, {'input_format': 'exe'}, {'source_filename': None}, {'schema': 999}):
            with self.subTest(invalid=invalid):
                changed = rewrite_project(raw, lambda p: p['source_metadata'].update(invalid))
                with self.assertRaises(GarageError):
                    self.store.import_archive(changed)

    def test_unknown_archive_schema_and_unmanifested_original_rejected(self):
        _, raw = self.exported()
        for mutation in ('schema', 'original_manifest'):
            files = archive_files(raw)
            manifest = json.loads(files['manifest.json'])
            if mutation == 'schema':
                manifest['schema'] = 999
            else:
                del manifest['files']['original_input.bin']
            files['manifest.json'] = json.dumps(manifest).encode()
            with self.subTest(mutation=mutation):
                with self.assertRaises(GarageError):
                    self.store.import_archive(model_zip(files))

    def test_original_and_normalized_relationship_is_verified(self):
        _, raw = self.exported()
        unrelated = TRIANGLE_OBJ.replace(b'v 1 0 0', b'v 20 0 0')
        changed = rewrite_project(raw, lambda p: p['source_metadata'].update(source_sha256=sha256(unrelated)), {'original_input.bin': unrelated})
        with self.assertRaises(GarageError):
            self.store.import_archive(changed)

    def test_valid_project_larger_than_one_input_limit_can_roundtrip(self):
        p, raw = self.exported()
        folder = self.store.folder(p['id'])
        individual_limit = max((folder / 'source.glb').stat().st_size, (folder / 'original_input.bin').stat().st_size) + 1
        self.assertGreater(len(raw), individual_limit)
        # A project stores two individually bounded inputs plus metadata. Use a
        # small bound to exercise the distinction without allocating 512 MiB.
        with patch('garagehub.projects.MAX_MODEL_BYTES', individual_limit):
            restored = self.store.import_archive(raw)
        self.assertEqual(restored['source_sha256'], p['source_sha256'])

    def test_individual_original_input_cannot_exceed_model_limit(self):
        source = TRIANGLE_OBJ + b'#' + b'x' * 10000 + b'\n'
        _, raw = self.exported(source)
        self.assertLess(len(raw), 4096)
        with patch('garagehub.projects.MAX_MODEL_BYTES', 4096):
            with self.assertRaises(GarageError):
                self.store.import_archive(raw)


if __name__ == '__main__':
    unittest.main()
