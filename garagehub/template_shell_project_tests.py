"""Independent Store/HTTP/build-boundary checks for supplied-wheel projects.

GLB parsing, source preparation, assembly geometry, preview serialization,
settings, archives and HTTP handlers are real. Only owned-native authentication
and decoded wheel geometry are synthetic in the portable preview fixture.
Actual native decoding/packaging/game verification remains a separate gate.
"""
from __future__ import annotations

import copy
import hashlib
import http.client
import io
import json
import struct
import tempfile
import threading
import unittest
import zipfile
from contextlib import contextmanager, ExitStack
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .assembly_settings import saved_shell_decision
from .errors import GarageError
from .projects import Store
from .server import App, serve
from .template_lights_tests import bare_body_glb, PLACEMENT
from .template_shell import plan_shell, resolve_shell
from .project_body_fit_tests import material_only_variant
from .util import atomic_json, read_json


class TemplateShellProjectTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.store = Store(self.root/'projects')
        self.template = self.root/'synthetic_native_boundary'
        (self.template/'hq').mkdir(parents=True)
        self.native_raw = b'SYNTHETIC-NATIVE-WHEEL-BOUNDARY'
        (self.template/'hq/wheel').write_bytes(self.native_raw)

    def tearDown(self):
        self.temp.cleanup()

    def project(self):
        p = self.store.create(bare_body_glb(), 'bare_artist_shell.glb')
        return self.store.update(p['id'], {'assembly_mode': 'template_shell',
            'template_shell_settings': {'placement': copy.deepcopy(PLACEMENT),
                                       'lighting': {'mode': 'supplied'}, 'dispositions': {}},
            'installation': {'mode': 'new_entry', 'replace_car_id': None},
            'approved_roles': True, 'template_inheritance_approved': True,
            'forward': '+Z'})

    def files(self, pid):
        return {p.name: p.read_bytes() for p in self.store.folder(pid).iterdir() if p.is_file()}

    def native_check(self, root):
        if Path(root) != self.template or (self.template/'hq/wheel').read_bytes() != self.native_raw:
            raise GarageError('Synthetic native asset fingerprint changed.', 'template_mismatch')
        return {'root': str(self.template), 'synthetic_native_boundary': True}

    def decoded_wheels(self, raw, running_gear_plan=None):
        self.assertEqual(raw, self.native_raw)
        components = []
        for index, corner in enumerate(('FL', 'FR', 'RL', 'RR')):
            x = -.76 if corner[1] == 'L' else .76
            z = -1.27 if corner[0] == 'F' else 1.27
            components.append({'positions': np.array([[x, .1, z], [x, .2, z], [x, .1, z+.1]]),
                'normals': np.tile([1., 0., 0.], (3, 1)), 'uv': np.full((3, 2), .5),
                'faces': np.array([[0, 1, 2]], dtype=np.uint32), 'corner': corner,
                'role': 'tyre', 'preview_material': 0, 'shape': index,
                'reference_center_metres': [x, .1, z]})
        return components, {'synthetic_native_boundary': True,
                            'scope': 'Portable Store/HTTP fixture only; not decoded native geometry.'}

    @contextmanager
    def native_boundary(self):
        with ExitStack() as stack:
            for name in ('garagehub.car_profiles.validate_template',
                         'garagehub.template_shell.validate_template',
                         'garagehub.template_shell_preview.validate_template'):
                stack.enter_context(patch(name, side_effect=self.native_check))
            decoder = stack.enter_context(patch('garagehub.template_shell_preview.decode_template_wheels',
                                                 side_effect=self.decoded_wheels))
            yield decoder

    def approve(self, p):
        review = self.store.review_template_shell(p['id'], self.template)
        self.assertTrue(review['preview_available'], review['blockers'])
        binary = self.store.template_shell_preview(p['id'], review['review_digest'], self.template)
        self.assertEqual(binary[:4], b'GGV1')
        result = self.store.approve_template_shell(p['id'], review['review_digest'], True, self.template)
        self.assertTrue(result['template_shell_status']['approved'])
        return result, review

    @contextmanager
    def app(self):
        app = App(self.root/'app', Path(__file__).resolve().parents[1]/'web', allow_dialogs=False,development_authored=True)
        app.store = self.store
        app.config = {'car_baseline': str(self.template), 'car_packer': str(self.root/'synthetic_packer')}
        try:
            yield app
        finally:
            if app.server:
                app.server.shutdown()
                app.server.server_close()
            app.executor.shutdown(wait=True)

    @contextmanager
    def package_boundaries(self, project):
        destination = {'ready': True, 'blockers': [], 'display_name': 'Independent assembly',
                       'mode': project['installation']['mode'],
                       'car_id': project['installation'].get('replace_car_id') or 999}
        with ExitStack() as stack:
            stack.enter_context(patch('garagehub.server.check_baseline', return_value={}))
            stack.enter_context(patch('garagehub.server.trusted_packer', return_value={}))
            identity = stack.enter_context(patch('garagehub.server.identity_plan', return_value=destination))
            stack.enter_context(patch('garagehub.server.route_candidate'))
            stack.enter_context(patch('garagehub.server.pack_candidate', return_value={'files': []}))
            stack.enter_context(patch('garagehub.server.finish_zip', return_value={}))
            yield identity

    def test_review_is_read_only_and_actual_preview_get_precedes_explicit_approval(self):
        p = self.project(); pid = p['id']; before = self.files(pid)
        with self.native_boundary(), self.app() as app:
            server = serve(app)
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            port = server.server_port
            def request(method, path, data=None, authenticated=True):
                connection = http.client.HTTPConnection('127.0.0.1', port, timeout=3)
                headers = {'Content-Type': 'application/json'}
                if authenticated:
                    headers['X-Garage-Token'] = app.token
                connection.request(method, path, None if data is None else json.dumps(data), headers)
                response = connection.getresponse()
                payload = response.read(); status = response.status
                connection.close()
                return status, payload
            status, raw = request('POST', '/api/template-shell-review', {'id': pid})
            self.assertEqual(status, 200)
            review = json.loads(raw); digest = review['review_digest']
            self.assertTrue(review['preview_available'])
            self.assertEqual(self.files(pid), before)
            status, raw = request('POST', '/api/approve-template-shell',
                                  {'id': pid, 'review_digest': digest, 'confirmed': True})
            self.assertEqual(status, 400)
            self.assertEqual(json.loads(raw)['code'], 'shell_preview_required')
            path = '/api/template-shell-preview/'+pid+'?review_digest='+digest
            self.assertEqual(request('GET', path, authenticated=False)[0], 403)
            self.assertNotIn((pid, digest), self.store._shell_preview_served)
            status, raw = request('GET', path)
            self.assertEqual(status, 200)
            self.assertEqual(raw[:4], b'GGV1')
            size = struct.unpack_from('<I', raw, 4)[0]
            header = json.loads(raw[16:16+size])
            self.assertEqual(header['template_shell_digest'], digest)
            self.assertTrue(header['native_wheel_geometry']['synthetic_native_boundary'])
            self.assertEqual(header['generated_components']['generated_triangles'], 184)
            for confirmed in (False, 1, 'true'):
                self.assertEqual(request('POST', '/api/approve-template-shell',
                    {'id': pid, 'review_digest': digest, 'confirmed': confirmed})[0], 400)
            status, raw = request('POST', '/api/approve-template-shell',
                                  {'id': pid, 'review_digest': digest, 'confirmed': True})
            self.assertEqual(status, 200)
            self.assertTrue(json.loads(raw)['template_shell_status']['approved'])
            self.assertEqual({k: v for k, v in self.files(pid).items() if k != 'project.json'},
                             {k: v for k, v in before.items() if k != 'project.json'})

    def test_failed_preview_cannot_authorize_or_mutate_a_project(self):
        p = self.project(); before = self.files(p['id'])
        with self.native_boundary(), patch('garagehub.template_shell_preview.build_template_shell_preview',
                                          side_effect=GarageError('Injected preview failure.')):
            review = self.store.review_template_shell(p['id'], self.template)
            self.assertFalse(review['preview_available'])
            with self.assertRaises(GarageError):
                self.store.template_shell_preview(p['id'], review['review_digest'], self.template)
            with self.assertRaises(GarageError) as blocked:
                self.store.approve_template_shell(p['id'], review['review_digest'], True, self.template)
            self.assertEqual(blocked.exception.code, 'shell_preview_required')
        self.assertEqual(self.files(p['id']), before)

    def test_native_assets_are_reauthenticated_after_cached_preview(self):
        p = self.project()
        with self.native_boundary() as decoder:
            review = self.store.review_template_shell(p['id'], self.template)
            self.store.template_shell_preview(p['id'], review['review_digest'], self.template)
            decoder.assert_called_once()
            (self.template/'hq/wheel').write_bytes(self.native_raw+b'changed')
            with self.assertRaises(GarageError):
                self.store.template_shell_preview(p['id'], review['review_digest'], self.template)
            with self.assertRaises(GarageError):
                self.store.approve_template_shell(p['id'], review['review_digest'], True, self.template)
        self.assertFalse(self.store.summary(p['id'])['template_shell_status']['approved'])

    def test_source_and_destination_changes_invalidate_preview_and_approval(self):
        with self.native_boundary():
            for change in ({'installation': {'mode': 'replace', 'replace_car_id': 82}},
                           {'forward': '-Z'}, {'metres_per_unit': 1.01},
                           {'approved_roles': False}, {'template_inheritance_approved': False}):
                with self.subTest(change=change):
                    p, review = self.approve(self.project())
                    original = (self.store.folder(p['id'])/'source.glb').read_bytes()
                    result = self.store.update(p['id'], change)
                    self.assertFalse(result['template_shell_status']['approved'])
                    with self.assertRaises(GarageError):
                        self.store.template_shell_preview(p['id'], review['review_digest'], self.template)
                    with self.assertRaises(GarageError):
                        self.store.approve_template_shell(p['id'], review['review_digest'], True, self.template)
                    self.assertEqual((self.store.folder(p['id'])/'source.glb').read_bytes(), original)

    def test_changed_settings_remove_decision_and_cannot_be_forged_through_general_update(self):
        with self.native_boundary():
            p, _ = self.approve(self.project())
            before = self.files(p['id'])
            with self.assertRaises(GarageError):
                self.store.update(p['id'], {'template_shell_decision': p['template_shell_decision']})
            self.assertEqual(self.files(p['id']), before)
            settings = copy.deepcopy(p['template_shell_settings'])
            settings['placement']['translation_metres'][1] += .05
            changed = self.store.update(p['id'], {'template_shell_settings': settings})
            self.assertNotIn('template_shell_decision', changed)
            self.assertFalse(changed['template_shell_status']['approved'])

    def test_external_settings_decision_mismatch_cannot_reach_plan_or_native_output(self):
        with self.native_boundary():
            p, _ = self.approve(self.project())
            path = self.store.folder(p['id'])/'project.json'
            forged = read_json(path)
            forged['template_shell_settings']['lighting']['front_height_ratio'] = .6
            atomic_json(path, forged)
            with self.assertRaises(GarageError):
                saved_shell_decision(forged)
            with self.app() as app, self.package_boundaries(p), \
                 patch('garagehub.template_shell.plan_shell') as planner, \
                 patch('garagehub.template_shell.build_shell') as builder:
                self.assertFalse(app.conversion_plan(p['id'])['ready'])
                planner.assert_not_called()
                with self.assertRaises(GarageError):
                    app.convert_car(p['id'], None)
                builder.assert_not_called()
                self.assertFalse((app.root/'builds').exists())

    def test_original_source_bytes_are_rechecked_despite_populated_preview_cache(self):
        with self.native_boundary():
            p, review = self.approve(self.project())
            folder = self.store.folder(p['id'])
            (folder/'source.glb').write_bytes(material_only_variant((folder/'source.glb').read_bytes()))
            for action in (lambda: self.store.summary(p['id']),
                           lambda: self.store.template_shell_preview(p['id'], review['review_digest'], self.template),
                           lambda: self.store.build_inputs(p['id'])):
                with self.assertRaises(GarageError) as blocked:
                    action()
                self.assertEqual(blocked.exception.code, 'source_changed')
            with self.app() as app, patch('garagehub.template_shell.build_shell') as builder:
                with self.assertRaises(GarageError) as blocked:
                    app.convert_car(p['id'], None)
                self.assertEqual(blocked.exception.code, 'source_changed')
                builder.assert_not_called()
                self.assertFalse((app.root/'builds').exists())

    def test_restart_archive_and_rename_preserve_choice_and_original_bytes(self):
        with self.native_boundary():
            p, _ = self.approve(self.project())
        source = (self.store.folder(p['id'])/'source.glb').read_bytes()
        choice = copy.deepcopy(p['template_shell_decision'])
        p = self.store.update(p['id'], {'name': 'My distinct supplied-wheel car', 'notes': 'Reviewed assembly'})
        self.assertTrue(p['template_shell_status']['approved'])
        archive = self.root/'my_car.gt6car'
        self.store.export(p['id'], archive)
        reopened = Store(self.root/'reopened')
        q = reopened.import_archive(archive.read_bytes())
        self.assertNotEqual(q['id'], p['id'])
        self.assertEqual(q['assembly_mode'], 'template_shell')
        self.assertEqual(q['template_shell_settings'], p['template_shell_settings'])
        self.assertEqual(q['template_shell_decision'], choice)
        self.assertEqual(q['installation'], {'mode': 'new_entry', 'replace_car_id': None})
        self.assertTrue(q['template_shell_status']['approved'])
        self.assertEqual((reopened.folder(q['id'])/'source.glb').read_bytes(), source)
        restarted = Store(self.root/'reopened')
        self.assertTrue(restarted.summary(q['id'])['template_shell_status']['approved'])
        self.assertFalse(restarted._shell_preview_served)

    def test_running_gear_request_roundtrip_retains_independent_axles_and_blocks_output(self):
        from .running_gear import resolve_running_gear
        with self.native_boundary():
            p, old_review = self.approve(self.project())
            settings = copy.deepcopy(p['template_shell_settings'])
            axes = resolve_running_gear()['resolved']
            request = {'schema': 1, 'mode': 'custom_dimensions',
                       'front': copy.deepcopy(axes['front']), 'rear': copy.deepcopy(axes['rear'])}
            request['front']['track_metres'] += .12
            request['rear']['tyre_diameter_metres'] += .04
            request['rear']['center_z_metres'] += .15
            settings['running_gear'] = request
            changed = self.store.update(p['id'], {'template_shell_settings': settings})
            self.assertNotIn('template_shell_decision', changed)
            review = self.store.review_template_shell(p['id'], self.template)
            self.assertFalse(review['ready_for_candidate'])
            self.assertFalse(review['preview_available'])
            self.assertEqual(review['running_gear'], request)
            self.assertEqual({row['field'] for row in review['blockers']},
                             {'front.track_metres', 'rear.tyre_diameter_metres', 'rear.center_z_metres'})
            with self.assertRaises(GarageError):
                self.store.template_shell_preview(p['id'], old_review['review_digest'], self.template)
            with self.assertRaises(GarageError):
                self.store.approve_template_shell(p['id'], old_review['review_digest'], True, self.template)
        archive = self.root/'custom_dimensions.gt6car'
        self.store.export(p['id'], archive)
        reopened = Store(self.root/'custom_reopened')
        result = reopened.import_archive(archive.read_bytes())
        self.assertEqual(result['template_shell_settings'], settings)
        self.assertFalse(result['template_shell_status']['approved'])
        self.assertEqual(result['installation'], p['installation'])
        self.assertEqual((reopened.folder(result['id'])/'source.glb').read_bytes(),
                         (self.store.folder(p['id'])/'source.glb').read_bytes())

    def test_matching_custom_request_needs_fresh_review_and_shares_preview_build_plan(self):
        from .running_gear import resolve_running_gear
        with self.native_boundary():
            p, old = self.approve(self.project())
            original = self.store.template_shell_preview(p['id'], old['review_digest'], self.template)
            axes = resolve_running_gear()['resolved']
            settings = copy.deepcopy(p['template_shell_settings'])
            settings['running_gear'] = {'schema': 1, 'mode': 'custom_dimensions',
                'front': copy.deepcopy(axes['front']), 'rear': copy.deepcopy(axes['rear'])}
            p = self.store.update(p['id'], {'template_shell_settings': settings})
            self.assertFalse(p['template_shell_status']['approved'])
            p, review = self.approve(p)
            self.assertNotEqual(old['review_digest'], review['review_digest'])
            binary = self.store.template_shell_preview(p['id'], review['review_digest'], self.template)
            def unpack(raw):
                size = struct.unpack_from('<I', raw, 4)[0]
                return json.loads(raw[16:16+size]), raw[16+size:]
            before, old_geometry = unpack(original)
            header, new_geometry = unpack(binary)
            self.assertEqual(old_geometry, new_geometry)
            self.assertEqual(header['running_gear_plan_digest'], review['running_gear_plan']['plan_digest'])
            self.assertTrue(header['running_gear_native_no_op'])
            scene, current = self.store.build_inputs(p['id'])
            plan = plan_shell(scene, current, self.template, saved_shell_decision(current))
            self.assertEqual(plan['running_gear_plan'], review['running_gear_plan'])
            self.assertEqual(plan['running_gear_plan']['corners'], old['running_gear_plan']['corners'])

    def test_legacy_archive_preserves_choices_but_requires_current_assembly_review(self):
        with self.native_boundary():
            p, _ = self.approve(self.project())
        archive = self.root/'legacy_source.gt6car'
        self.store.export(p['id'], archive)
        with zipfile.ZipFile(archive) as z:
            members = {name: z.read(name) for name in z.namelist()}
        old = json.loads(members['project.json'])
        old['template_shell_settings'].pop('running_gear')
        old['template_shell_decision'].pop('running_gear')
        old['template_shell_decision']['schema'] = 1
        members['project.json'] = json.dumps(old).encode()
        manifest = json.loads(members['manifest.json'])
        manifest['files']['project.json'] = hashlib.sha256(members['project.json']).hexdigest()
        members['manifest.json'] = json.dumps(manifest).encode()
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as z:
            for name, raw in members.items():
                z.writestr(name, raw)
        reopened = Store(self.root/'legacy_reopened')
        q = reopened.import_archive(stream.getvalue())
        self.assertEqual(q['template_shell_settings']['running_gear'], {'schema': 1, 'mode': 'profile'})
        for key in ('placement', 'lighting', 'dispositions'):
            self.assertEqual(q['template_shell_settings'][key], p['template_shell_settings'][key])
        self.assertEqual(q['template_shell_decision'], old['template_shell_decision'])
        self.assertFalse(q['template_shell_status']['approved'])
        with self.assertRaises(GarageError):
            saved_shell_decision(q)
        with self.native_boundary():
            review = reopened.review_template_shell(q['id'], self.template)
            reopened.template_shell_preview(q['id'], review['review_digest'], self.template)
            approved = reopened.approve_template_shell(q['id'], review['review_digest'], True, self.template)
        self.assertTrue(approved['template_shell_status']['approved'])
        self.assertEqual(approved['template_shell_decision']['schema'], 2)

    def test_client_cannot_supply_resolved_running_gear_or_reuse_approval_for_replacement_change(self):
        from .running_gear import resolve_running_gear
        with self.native_boundary():
            p = self.project()
            p = self.store.update(p['id'], {'installation': {'mode': 'replace', 'replace_car_id': 82}})
            p, _ = self.approve(p)
            before = self.files(p['id'])
            settings = copy.deepcopy(p['template_shell_settings'])
            settings['running_gear']['resolved'] = resolve_running_gear()['resolved']
            with self.assertRaises(GarageError):
                self.store.update(p['id'], {'template_shell_settings': settings})
            self.assertEqual(self.files(p['id']), before)
            axes = resolve_running_gear()['resolved']
            settings['running_gear'] = {'schema': 1, 'mode': 'custom_dimensions',
                'front': copy.deepcopy(axes['front']), 'rear': copy.deepcopy(axes['rear'])}
            settings['running_gear']['front']['center_y_metres'] += .02
            result = self.store.update(p['id'], {'template_shell_settings': settings})
            self.assertEqual(result['installation'], {'mode': 'replace', 'replace_car_id': 82})
            self.assertFalse(result['template_shell_status']['approved'])
            with self.app() as app, self.package_boundaries(result), \
                 patch('garagehub.template_shell.build_shell') as builder:
                self.assertFalse(app.conversion_plan(p['id'])['ready'])
                with self.assertRaises(GarageError):
                    app.convert_car(p['id'], None)
                builder.assert_not_called()
                self.assertFalse((app.root/'builds').exists())

    def test_plan_and_build_share_one_fresh_snapshot_and_explicit_destination(self):
        with self.native_boundary():
            for mode, car_id in (('new_entry', None), ('replace', 82)):
                with self.subTest(mode=mode):
                    p = self.project()
                    p = self.store.update(p['id'], {'installation': {'mode': mode, 'replace_car_id': car_id}})
                    p, _ = self.approve(p)
                    with self.app() as app, self.package_boundaries(p) as identity, \
                         patch.object(self.store, 'build_inputs', wraps=self.store.build_inputs) as snapshot, \
                         patch('garagehub.template_shell.plan_shell', wraps=plan_shell) as planner, \
                         patch('garagehub.template_shell.build_shell') as builder, \
                         patch('garagehub.server.build_car') as authored:
                        result = app.convert_car(p['id'], None)
                        snapshot.assert_called_once_with(p['id'])
                        planner.assert_called_once(); builder.assert_called_once(); authored.assert_not_called()
                        self.assertIs(planner.call_args.args[0], builder.call_args.args[0])
                        self.assertIs(planner.call_args.args[1], builder.call_args.args[1])
                        self.assertIs(identity.call_args.args[0], builder.call_args.args[1])
                        self.assertEqual(result['installation']['mode'], mode)
                        self.assertEqual(result['assembly_mode'], 'template_shell')
                        self.assertFalse(result['runtime_verified'])

    def test_stale_saved_inspection_cannot_replace_fresh_build_source(self):
        with self.native_boundary():
            p, _ = self.approve(self.project())
            stale = copy.deepcopy(p['inspection'])
            stale['parts'][0]['role'] = 'rim'
            atomic_json(self.store.folder(p['id'])/'inspection.json', stale)
            scene, current = self.store.build_inputs(p['id'])
            self.assertEqual(current['inspection']['parts'][0]['role'], 'body')
            self.assertTrue(resolve_shell(scene, current, saved_shell_decision(current))[2]['approved'])

    def test_changed_source_cannot_export_an_archive_that_cannot_reopen(self):
        p = self.project()
        self.store.scene(p['id'])  # Populate the normal prepared-scene cache.
        source = self.store.folder(p['id'])/'source.glb'
        source.write_bytes(material_only_variant(source.read_bytes()))
        before = self.files(p['id'])
        destination = self.root/'must_not_export.gt6car'
        with self.assertRaises(GarageError) as blocked:
            self.store.export(p['id'], destination)
        self.assertEqual(blocked.exception.code, 'source_changed')
        self.assertFalse(destination.exists())
        self.assertEqual(self.files(p['id']), before)

    def test_changed_retained_original_obj_or_zip_blocks_export_before_output(self):
        obj = b'o body\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
            archive.writestr('model.obj', obj)
        for name, raw in (('original.obj', obj), ('original.zip', stream.getvalue())):
            with self.subTest(name=name):
                p = self.store.create(raw, name)
                folder = self.store.folder(p['id'])
                retained = folder/'original_input.bin'
                self.assertEqual(retained.read_bytes(), raw)
                normalized = (folder/'source.glb').read_bytes()
                retained.write_bytes(raw+b'changed')
                before = self.files(p['id'])
                output = self.root/(p['id']+'.gt6car')
                with self.assertRaises(GarageError) as blocked:
                    self.store.export(p['id'], output)
                self.assertEqual(blocked.exception.code, 'source_changed')
                self.assertFalse(output.exists())
                self.assertEqual(self.files(p['id']), before)
                self.assertEqual((folder/'source.glb').read_bytes(), normalized)

