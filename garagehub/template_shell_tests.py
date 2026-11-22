"""Focused developer-route orchestration and production-boundary regressions.

Source parsing, placement, preparation and approval checks are real. Native
serializer/authentication mocks are explicitly labelled; owned native assets
and complete package verification are separate evidence, not fabricated here.
"""
from __future__ import annotations

import copy
import hashlib
import json
import tempfile
import unittest
from contextlib import contextmanager
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .car_builder import build_body, plan as production_plan
from .car_geometry import prepare_parts
from .car_profiles import PROFILE
from .errors import GarageError
from .recognition import infer
from .template_shell import (review_shell, approve_shell, resolve_shell,
                             plan_shell, build_shell)
from .template_shell_independent_tests import shell_fixture, wheel_free_glb
from .glb import GLB


PLACEMENT = {'scale': 1., 'translation_metres': [0., 0., 0.]}
SYNTHETIC_STOCK_RACE = b'synthetic stock packed race wheel'


@contextmanager
def synthetic_race_writer():
    """Model the authenticated writer at this synthetic orchestration boundary."""
    with patch('garagehub.running_gear.STOCK_SHA256', hashlib.sha256(SYNTHETIC_STOCK_RACE).hexdigest()), \
         patch('garagehub.template_shell.restore_stock_race_wheel', return_value=SYNTHETIC_STOCK_RACE):
        yield


def decision_for(scene, project, placement=None, dispositions=None):
    placement = PLACEMENT if placement is None else placement
    review = review_shell(scene, project, placement, dispositions)
    if not review['ready_for_candidate']:
        raise AssertionError(review['blockers'])
    return approve_shell(scene, project, placement, dispositions,
                         review['review_digest'], True)


def native_fixture(folder):
    """Synthetic file boundary only, never represented as GT6-native data."""
    names = ('info', 'interior', 'hq/body', 'hq/body_s', 'hq/wheel',
             'race/body', 'race/body_s', 'race/wheel')
    hashes = {}
    for name in names:
        path = folder/name
        path.parent.mkdir(parents=True, exist_ok=True)
        raw = ('synthetic owned resource '+name).encode()
        path.write_bytes(raw)
        hashes[name] = hashlib.sha256(raw).hexdigest()
    return hashes


def body_boundary(scene, project, template, lod, output, cancel, *, fit_solution):
    parts, preparation, fit = prepare_parts(scene, project['inspection'], lod,
                                           cancel, fit_solution=fit_solution)
    payload = b''.join(p['positions'].astype('<f8').tobytes()+p['faces'].astype('<u4').tobytes()
                       for p in parts)
    output.write_bytes(b'SYNTHETIC-MODEL\0'+payload)
    output.with_name('body_s').write_bytes(b'SYNTHETIC-STREAM\0'+payload)
    return {'source_components_covered': [p['id'] for p in parts],
            'source_preparation': preparation, 'fit': fit,
            'synthetic_serializer_boundary': True}, parts


class TemplateShellTests(unittest.TestCase):
    def test_explicit_confirmation_and_valid_decision_required(self):
        scene, project = shell_fixture()
        review = review_shell(scene, project, PLACEMENT)
        for value in (False, None, 1, 'yes'):
            with self.subTest(value=value), self.assertRaises(GarageError):
                approve_shell(scene, project, PLACEMENT, None,
                              review['review_digest'], value)
        for value in (None, {}, {'schema': 1, 'review_digest': review['review_digest']}):
            with self.subTest(value=value), self.assertRaises(GarageError):
                resolve_shell(scene, project, value)

    def test_invalid_scale_translation_and_placement_keys_are_blocked(self):
        scene, project = shell_fixture()
        cases = [{'scale': value, 'translation_metres': [0., 0., 0.]}
                 for value in (True, 0., -1., float('nan'), float('inf'), '1')]
        cases += [{'scale': 1., 'translation_metres': value}
                  for value in ([0., 0.], [0., False, 0.], [0., float('inf'), 0.], '0,0,0')]
        cases += [{**PLACEMENT, 'matrix': np.eye(3).tolist()},
                  {'scale': 1., 'translation_metres': [0., 0., 13.]}]
        for value in cases:
            with self.subTest(value=value):
                report = review_shell(scene, project, value)
                self.assertFalse(report['ready_for_candidate'])
                self.assertIsNone(report['review_digest'])
                self.assertTrue(report['blockers'])

    def test_source_lamps_are_mandatory_and_must_remain_on_both_template_sides(self):
        scene, project = shell_fixture()
        for role in ('headlight', 'taillight'):
            altered = copy.deepcopy(project)
            for row in altered['inspection']['parts']:
                if row['role'] == role:
                    altered['overrides'][row['id']] = {'role': 'body', 'corner': None}
            altered['inspection'] = infer(scene, altered['forward'], altered['overrides'])
            report = review_shell(scene, altered, PLACEMENT)
            self.assertFalse(report['ready_for_candidate'])
            self.assertEqual(report['blockers'][0]['code'], 'shell_lamps')
        report = review_shell(scene, project, {'scale': 1., 'translation_metres': [2., 0., 0.]})
        self.assertFalse(report['ready_for_candidate'])
        self.assertEqual(report['blockers'][0]['code'], 'shell_lamps')

    def test_inheritance_and_explicit_destination_stay_required(self):
        scene, project = shell_fixture()
        for changes in ({'template_inheritance_approved': False},
                        {'installation': {'mode': None, 'replace_car_id': None}},
                        {'installation': {'mode': 'replace', 'replace_car_id': None}}):
            altered = {**project, **changes}
            self.assertFalse(review_shell(scene, altered, PLACEMENT)['ready_for_candidate'])

    def test_template_contract_change_invalidates_approval(self):
        scene, project = shell_fixture()
        decision = decision_for(scene, project)
        with patch.dict('garagehub.template_shell.PROFILE', {'label': 'A different reviewed profile'}):
            with self.assertRaises(GarageError):
                resolve_shell(scene, project, decision)

    def test_real_production_body_approval_and_source_wheel_gates_remain(self):
        scene, project = shell_fixture()
        decision_for(scene, project)
        with patch('garagehub.car_builder.validate_template', return_value={'root': 'synthetic'}):
            report = production_plan(scene, project, Path('synthetic'))
        self.assertFalse(report['ready'])
        self.assertTrue(any('rim' in action.lower() for action in report['actions']))
        with patch('garagehub.car_builder._build_body_with_fit') as serializer:
            with self.assertRaises(GarageError):
                build_body(scene, project, Path('missing-native'), 'hq', Path('unused'), None)
            serializer.assert_not_called()

    def test_plan_authenticates_native_template_after_fresh_approval(self):
        scene, project = shell_fixture()
        decision = decision_for(scene, project)
        with patch('garagehub.template_shell.validate_template',
                   side_effect=GarageError('Unknown owned assets.')) as authentication:
            with self.assertRaises(GarageError):
                plan_shell(scene, project, Path('unreviewed'), decision)
        authentication.assert_called_once()

    def test_complete_orchestration_preserves_wheel_and_interior_bytes(self):
        scene, project = shell_fixture()
        original = copy.deepcopy((scene, project))
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            template, output = folder/'template', folder/'candidate'
            hashes = native_fixture(template)
            native_before = {name: (template/name).read_bytes() for name in hashes}
            with synthetic_race_writer(), patch.dict('garagehub.template_shell.ASSETS', hashes, clear=True), \
                 patch('garagehub.template_shell.validate_template',
                       return_value={'root': str(template), 'synthetic_authentication_boundary': True}) as auth, \
                 patch('garagehub.car_builder._build_body_with_fit', side_effect=body_boundary), \
                 patch('garagehub.car_builder.fit_projectors',
                       side_effect=lambda raw, parts: (raw+b'source-lamp-placement', [])), \
                 patch('garagehub.car_builder.wheels.build', side_effect=AssertionError('Source rims must not be rebuilt')), \
                 patch('garagehub.car_builder.showroom_wheels', side_effect=AssertionError('Source rims must not be rebuilt')):
                decision = decision_for(scene, project)
                report = build_shell(scene, project, template, output, decision)
            self.assertEqual(auth.call_count, 2)
            self.assertEqual(set(report['source_virtual_assets']),
                             {PROFILE['model_path']+'/'+name for name in hashes})
            self.assertEqual(len(report['source_virtual_assets']), 8)
            for name in ('hq/wheel', 'interior'):
                self.assertEqual((output/PROFILE['model_path']/name).read_bytes(), native_before[name])
            self.assertEqual((output/PROFILE['model_path']/'race/wheel').read_bytes(), SYNTHETIC_STOCK_RACE)
            self.assertEqual(report['race_wheels']['policy'], 'stock_packed_race_wheel')
            self.assertEqual(report['race_wheels']['source_sha256'], hashes['race/wheel'])
            self.assertEqual(native_before, {name: (template/name).read_bytes() for name in hashes})
            self.assertTrue(report['passed'])
            self.assertFalse(report['runtime_tested'])
            self.assertFalse(report['production_enabled'])
            self.assertFalse(report['installable'])
            self.assertEqual(report['kind'], 'template-shell-native-candidate')
            self.assertEqual(report['triangle_dispositions'], {'emit': 18, 'excluded': 0})
            self.assertEqual(report['geometry_review']['source_anchors']['wheel_centers'], {})
            self.assertTrue((output/'build_receipt.json').is_file())
        self.assertEqual(project, original[1])
        self.assertEqual(scene.raw, original[0].raw)
        for actual, before in zip(scene.parts, original[0].parts):
            for attribute in ('positions', 'normals', 'uv', 'indices'):
                np.testing.assert_array_equal(getattr(actual, attribute), getattr(before, attribute))

    def test_failed_native_build_removes_partial_assets_and_keeps_failure_receipt(self):
        scene, project = shell_fixture()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            template, output = folder/'template', folder/'candidate'
            hashes = native_fixture(template)
            calls = []
            def fail_on_race(*args, **kwargs):
                calls.append(args[3])
                if args[3] == 'race':
                    raise GarageError('Synthetic serializer failure.')
                return body_boundary(*args, **kwargs)
            with patch.dict('garagehub.template_shell.ASSETS', hashes, clear=True), \
                 patch('garagehub.template_shell.validate_template', return_value={'root': str(template)}), \
                 patch('garagehub.car_builder._build_body_with_fit', side_effect=fail_on_race):
                decision = decision_for(scene, project)
                with self.assertRaises(GarageError):
                    build_shell(scene, project, template, output, decision)
            self.assertEqual(calls, ['hq', 'race'])
            self.assertEqual([p.name for p in output.iterdir()], ['FAILED.json'])
            failure = json.loads((output/'FAILED.json').read_text())
            self.assertTrue(failure['partial_native_assets_removed'])
            self.assertFalse(failure['release_ready'])

    def test_build_uses_one_frozen_caller_snapshot(self):
        scene, project = shell_fixture()
        with tempfile.TemporaryDirectory() as directory:
            folder = Path(directory)
            template, output = folder/'template', folder/'candidate'
            hashes = native_fixture(template)
            captured = []
            def writer(frozen_scene, frozen_project, *args, **kwargs):
                captured.append((frozen_scene, frozen_project))
                # The caller's changes must not affect the build's frozen inputs.
                scene.parts[0].positions[:, 0] += 100
                project['forward'] = '-X'
                return body_boundary(frozen_scene, frozen_project, *args, **kwargs)
            with synthetic_race_writer(), patch.dict('garagehub.template_shell.ASSETS', hashes, clear=True), \
                 patch('garagehub.template_shell.validate_template', return_value={'root': str(template)}), \
                 patch('garagehub.car_builder._build_body_with_fit', side_effect=writer), \
                 patch('garagehub.car_builder.fit_projectors', return_value=(b'synthetic fitted info', [])):
                decision = decision_for(scene, project)
                report = build_shell(scene, project, template, output, decision)
            self.assertIs(captured[0][0], captured[1][0])
            self.assertIs(captured[0][1], captured[1][1])
            self.assertIsNot(captured[0][0], scene)
            self.assertIsNot(captured[0][1], project)
            self.assertEqual(report['geometry_review']['source']['forward'], '+Z')


if __name__ == '__main__':
    unittest.main()
