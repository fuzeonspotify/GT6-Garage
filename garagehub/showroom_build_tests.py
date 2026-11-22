"""Exercise showroom completion without distributing proprietary native assets.

Rim geometry and the tyre-fit report use their production implementations.
Only the native serializer boundary and its asset fingerprint are synthetic;
actual donor serialization remains part of the owned-PC build verification.
"""
from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .body_fit import approve_body_fit, body_fit_review, resolve_body_fit
from .car_builder import build_car, showroom_wheels
from .car_geometry import prepare_parts
from .car_profiles import PROFILE
from .recognition import CORNERS
from .wheel_fit import tyre_fit_report
from .wheel_fit_tests import wheel_scene
from .wheels import geometry


class _ShowroomEditor:
    output = b'synthetic serialized showroom candidate'

    def __init__(self, raw):
        self.m = object()
        self.shapes = []
        self.models = []

    def shape(self, vertices, indices, positions, *args, **kwargs):
        self.shapes.append({'vertices': bytes(vertices), 'indices': bytes(indices),
                            'positions': np.asarray(positions).copy()})

    def model(self, model_id, program, materials, points):
        self.models.append({'id': model_id, 'program': bytes(program)})

    def finish(self):
        return self.output, {'synthetic_native_serializer': True,
                             'shape_count': len(self.shapes),
                             'model_count': len(self.models)}


class _GeometryShowroomEditor(_ShowroomEditor):
    def finish(self):
        _, report = super().finish()
        # Include the actual production geometry in the synthetic container so
        # the regression can detect any unintended change to wheel payloads.
        return b''.join(row['vertices']+row['indices'] for row in self.shapes), report


class ShowroomBuildTests(unittest.TestCase):
    def test_showroom_completion_returns_real_tyre_measurements_and_preserves_source(self):
        scene, inspection = wheel_scene()
        original = {part.id: tuple(np.asarray(getattr(part, field)).copy()
                                  for field in ('positions', 'normals', 'uv', 'indices'))
                    for part in scene.parts}
        raw = b'synthetic owned showroom template'
        editor = _ShowroomEditor(raw)
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template, output = root/'template', root/'output'
            template.write_bytes(raw)
            with patch('garagehub.car_builder.Editor', return_value=editor), \
                 patch.dict('garagehub.car_builder.ASSETS',
                            {'hq/wheel': hashlib.sha256(raw).hexdigest()}):
                result = showroom_wheels(scene, inspection, template, output, None)
            self.assertEqual(output.read_bytes(), _ShowroomEditor.output)
            self.assertEqual(template.read_bytes(), raw)

        self.assertEqual(len(editor.shapes), 4)
        self.assertEqual({row['id'] for row in editor.models}, {0, 1, 2, 3})
        self.assertTrue(result['native_hq_tyres_preserved'])
        fit = result['tyre_fit']
        self.assertEqual(fit['status'], 'measured')
        self.assertEqual(fit['dimensional_compatibility'], 'unreviewed')
        self.assertEqual({row['corner'] for row in fit['corners']}, {'FL', 'FR', 'RL', 'RR'})
        for row in fit['corners']:
            self.assertTrue(row['available'])
            self.assertAlmostEqual(row['source']['radius_metres'], .32)
            self.assertAlmostEqual(row['source']['width_metres'], .24)
            self.assertEqual(row['source']['parts'], ['tyre_'+row['corner']])
        for part in scene.parts:
            for field, before in zip(('positions', 'normals', 'uv', 'indices'), original[part.id]):
                np.testing.assert_array_equal(getattr(part, field), before)

    def test_complete_car_wheel_receipts_follow_selected_body_fit_without_changing_rims(self):
        scale = 1.03
        scene, inspection = wheel_scene(scale=scale)
        project = {'name': 'Original-size new car', 'inspection': inspection,
                   'wheelbase_metres': inspection['wheelbase'],
                   'approved_roles': True, 'template_inheritance_approved': True,
                   'installation': {'mode': 'new_entry', 'replace_car_id': None}}
        raw = b'synthetic owned showroom template'

        def body_writer(scene, project, template, lod, output, cancel):
            parts, preparation, fit = prepare_parts(
                scene, project['inspection'], lod, cancel,
                fit_solution=resolve_body_fit(scene, project))
            output.write_bytes(b'synthetic body container')
            output.with_name('body_s').write_bytes(b'synthetic body stream')
            return {'fit': fit, 'source_preparation': preparation}, parts

        def race_writer(scene, inspection, template, cancel):
            payloads = [geometry(scene, inspection, c, cancel=cancel) for c in CORNERS]
            return (b''.join(vb+ib for vb, ib, *_ in payloads),
                    {'tyre_fit': tyre_fit_report(scene, inspection),
                     'synthetic_native_serializer': True})

        wheel_bytes = {}
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            template = root/'template'
            for lod in ('hq', 'race'):
                (template/lod).mkdir(parents=True)
                (template/lod/'wheel').write_bytes(raw)
            (template/'info').write_bytes(b'synthetic info')
            (template/'interior').write_bytes(b'unchanged interior')
            with patch('garagehub.car_builder.validate_template',
                       return_value={'root': str(template)}), \
                 patch.dict('garagehub.car_builder.ASSETS',
                            {'hq/wheel': hashlib.sha256(raw).hexdigest()}), \
                 patch('garagehub.car_builder.build_body', side_effect=body_writer), \
                 patch('garagehub.car_builder.wheels.build', side_effect=race_writer), \
                 patch('garagehub.car_builder.Editor', side_effect=_GeometryShowroomEditor), \
                 patch('garagehub.car_builder.fit_projectors',
                       return_value=(b'synthetic projector output', [])):
                for policy in ('keep_proportions', 'adapt_body'):
                    with self.subTest(policy=policy):
                        review = body_fit_review(scene, project, policy)
                        project['body_fit'] = approve_body_fit(
                            scene, project, policy, review['review_digest'], True)
                        output = root/policy
                        receipt = build_car(scene, project, template, output)
                        persisted = json.loads((output/'build_receipt.json').read_text())
                        wheel_bytes[policy] = {
                            lod: (output/PROFILE['model_path']/lod/'wheel').read_bytes()
                            for lod in ('hq', 'race')}
                        expected_scale = scale if policy == 'keep_proportions' else 1.
                        for key in ('race_wheels', 'showroom_wheels'):
                            fit = persisted[key]['tyre_fit']
                            self.assertEqual(fit, receipt[key]['tyre_fit'])
                            self.assertEqual(fit, persisted['geometry_review']['tyre_fit'])
                            self.assertEqual(fit, persisted['body']['hq']['fit']['tyre_fit'])
                            self.assertEqual(fit['status'], 'measured')
                            for row in fit['corners']:
                                native = PROFILE['wheel_geometry'][row['corner']]
                                expected_center = np.array(native['center_metres'])
                                expected_center[[0, 2]] *= expected_scale
                                np.testing.assert_allclose(row['body']['center_metres'],
                                                           expected_center, atol=1e-14)
                                self.assertAlmostEqual(row['body']['radius_metres'],
                                                       .32*expected_scale)
                                self.assertAlmostEqual(row['body']['width_metres'],
                                                       .24*expected_scale)
                                expected_error = np.linalg.norm(
                                    expected_center-np.array(native['center_metres']))
                                self.assertAlmostEqual(row['differences']['center_error_metres'],
                                                       expected_error)
                                self.assertAlmostEqual(row['rim_transform']['radial_scale'],
                                                       native['tyre_radius_metres']/(.32*scale))
                                self.assertAlmostEqual(row['rim_transform']['axial_scale'],
                                                       native['tyre_width_metres']/(.24*scale))
                                if policy == 'keep_proportions':
                                    self.assertGreater(expected_error, .04)

        self.assertEqual(wheel_bytes['keep_proportions'], wheel_bytes['adapt_body'])
        # Standalone rim diagnostics retain their independently scoped default.
        for row in tyre_fit_report(scene, inspection)['corners']:
            self.assertLess(row['differences']['center_error_metres'], 1e-14)
            self.assertAlmostEqual(row['body']['radius_metres'], .32)


if __name__ == '__main__':
    unittest.main()
