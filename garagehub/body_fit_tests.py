"""Explicit static-body policy, immutable reference and stale-review proofs."""
from __future__ import annotations

import copy
import itertools
import json
import unittest
from dataclasses import replace
from unittest.mock import patch

import numpy as np

from .body_fit import (POLICIES, body_fit_review, approve_body_fit,
                       resolve_body_fit)
from .car_geometry import alignment
from .car_profiles import PROFILE, ASSETS
from .errors import GarageError
from .recognition import CORNERS, axes
from .wheel_fit_tests import wheel_scene


def fixture(forward='+Z', scale=1.):
    scene, inspection = wheel_scene(forward=forward, scale=scale)
    project = {'schema': 2, 'id': 'a'*32, 'name': 'Unfamiliar source model',
        'created': 1., 'source_sha256': scene.digest, 'forward': forward,
        'source_up_axis': '+Y', 'metres_per_unit': 1.,
        'wheelbase_metres': inspection['wheelbase'], 'wheel_partitions': [],
        'overrides': {}, 'installation': {'mode': 'new_entry', 'replace_car_id': None},
        'inspection': inspection, 'approved_roles': True,
        'template_inheritance_approved': True}
    return scene, project


def source_bytes(scene):
    return [(p.positions.tobytes(), p.normals.tobytes(), p.uv.tobytes(),
             p.indices.tobytes()) for p in scene.parts]


def approve(scene, project, policy='adapt_body'):
    review = body_fit_review(scene, project, policy)
    choice = approve_body_fit(scene, project, policy, review['review_digest'], True)
    project['body_fit'] = choice
    return review


class BodyFitTests(unittest.TestCase):
    def test_original_size_is_rigid_in_each_declared_axis_and_preserves_inputs(self):
        for forward in ('+Z', '-Z', '+X', '-X'):
            with self.subTest(forward=forward):
                scene, project = fixture(forward, 1.03)
                original, saved = source_bytes(scene), copy.deepcopy(project)
                review = body_fit_review(scene, project, 'keep_proportions')
                self.assertTrue(review['available'])
                self.assertTrue(review['within_profile_limits'])
                matrix = np.array(review['matrix'])
                left, up, front = axes(forward)
                np.testing.assert_array_equal(matrix, [-left, up, -front])
                np.testing.assert_array_equal(matrix.T@matrix, np.eye(3))
                self.assertAlmostEqual(np.linalg.det(matrix), 1.)
                self.assertEqual(review['scale_xyz'], [1., 1., 1.])
                self.assertEqual(review['scale_change_percent_xyz'], [0., 0., 0.])
                pairs = scene.parts[-1].positions[1:]-scene.parts[-1].positions[:-1]
                np.testing.assert_allclose(np.linalg.norm(pairs@matrix.T, axis=1),
                                           np.linalg.norm(pairs, axis=1), rtol=0, atol=1e-15)
                self.assertEqual(project, saved)
                self.assertEqual(source_bytes(scene), original)

    def test_original_size_does_not_add_a_fitted_pitch_or_roll(self):
        scene, project = fixture()
        for part in scene.parts:
            part.positions[:, 1] += .02*part.positions[:, 2]
        for point in project['inspection']['wheel_centers'].values():
            point[1] += .02*point[2]
        review = body_fit_review(scene, project, 'keep_proportions')
        self.assertTrue(review['available'])
        self.assertTrue(review['within_profile_limits'])
        np.testing.assert_array_equal(review['matrix'], np.diag([-1., 1., -1.]))
        self.assertAlmostEqual(review['maximum_wheel_centre_error_metres'], .0255)
        self.assertIn('declared_axes', review['fit']['fit_method'])

    def test_adaptation_uses_exact_existing_production_solution(self):
        for forward, scale in itertools.product(('+Z', '-X'), (.1, 1., 2.)):
            with self.subTest(forward=forward, scale=scale):
                scene, project = fixture(forward, scale)
                old_matrix, old_translation, old_fit = alignment(project['inspection'])
                review = body_fit_review(scene, project, 'adapt_body')
                self.assertTrue(review['within_profile_limits'])
                np.testing.assert_array_equal(review['matrix'], old_matrix)
                np.testing.assert_array_equal(review['translation_metres'], old_translation)
                for key, value in old_fit.items():
                    self.assertEqual(review['fit'][key], value)
                self.assertAlmostEqual(review['scale_xyz'][0], 1./scale)

    def test_scale_changes_are_disclosed_in_target_axes(self):
        scene, project = fixture()
        for part in scene.parts:
            part.positions[:, 0] *= .9
        for point in project['inspection']['wheel_centers'].values():
            point[0] *= .9
        project['inspection']['track'] *= .9
        review = body_fit_review(scene, project, 'adapt_body')
        self.assertTrue(review['within_profile_limits'])
        np.testing.assert_allclose(review['scale_xyz'], [1/.9, 1., 1.], atol=1e-15)
        np.testing.assert_allclose(review['scale_change_percent_xyz'], [100/9, 0., 0.], atol=1e-13)
        self.assertLess(review['maximum_wheel_centre_error_metres'], 1e-14)

    def test_excess_original_size_residual_is_visible_but_cannot_be_approved(self):
        scene, project = fixture(scale=1.2)
        review = body_fit_review(scene, project, 'keep_proportions')
        self.assertTrue(review['available'])
        self.assertFalse(review['within_profile_limits'])
        self.assertEqual(review['issue_code'], 'body_fit_limit')
        self.assertGreater(review['maximum_wheel_centre_error_metres'], .06)
        with self.assertRaises(GarageError):
            approve_body_fit(scene, project, 'keep_proportions', review['review_digest'], True)
        self.assertNotIn('body_fit', project)
        self.assertTrue(body_fit_review(scene, project, 'adapt_body')['within_profile_limits'])

    def test_existing_profile_anisotropy_and_cached_measurement_gates_remain(self):
        for mode in ('anisotropy', 'stale_wheelbase', 'stale_track'):
            scene, project = fixture()
            if mode == 'anisotropy':
                for part in scene.parts:
                    part.positions[:, 0] *= .5
                for point in project['inspection']['wheel_centers'].values():
                    point[0] *= .5
                project['inspection']['track'] *= .5
            else:
                project['inspection'][mode.replace('stale_', '')] *= 1.1
            for policy in POLICIES:
                with self.subTest(mode=mode, policy=policy):
                    report = body_fit_review(scene, project, policy)
                    self.assertFalse(report['available'])
                    self.assertFalse(report['within_profile_limits'])
                    self.assertIsNone(report['review_digest'])
                    self.assertIsNone(report['matrix'])
                    self.assertEqual(report['issue_code'], 'car_fit')

    def test_reference_is_fixed_independent_of_source_and_private(self):
        scene, project = fixture()
        different, other = fixture(scale=1.2)
        first = body_fit_review(scene, project, 'adapt_body')
        second = body_fit_review(different, other, 'adapt_body')
        self.assertEqual(first['reference'], second['reference'])
        self.assertFalse(first['reference']['source_dependent'])
        self.assertTrue(first['reference']['requires_template_authentication'])
        self.assertFalse(first['reference']['current_mount_verified'])
        first['reference']['wheel_geometry']['FL']['center_metres'][0] = 100
        first['reference']['asset_sha256']['hq/body'] = '0'*64
        third = body_fit_review(scene, project, 'adapt_body')
        self.assertEqual(second['reference'], third['reference'])
        self.assertEqual(PROFILE['wheel_geometry']['FL']['center_metres'][0], -.7682615)

    def test_nominal_target_data_cannot_replace_native_profile_centres(self):
        scene, project = fixture()
        first = body_fit_review(scene, project, 'adapt_body')
        project['selected_target_geometry'] = {'wheel_centers': {c: [100, 100, 100] for c in CORNERS},
                                             'nominal_ride_height_metres': 50.}
        second = body_fit_review(scene, project, 'adapt_body')
        self.assertEqual(first['reference'], second['reference'])
        self.assertEqual(first['matrix'], second['matrix'])
        self.assertEqual(first['translation_metres'], second['translation_metres'])

    def test_confirmation_requires_the_actual_boolean_true(self):
        scene, project = fixture()
        review = body_fit_review(scene, project, 'adapt_body')
        for value in (None, False, 1, 'true', [], {}, np.bool_(True)):
            with self.subTest(value=repr(value)), self.assertRaises(GarageError):
                approve_body_fit(scene, project, 'adapt_body', review['review_digest'], value)
        choice = approve_body_fit(scene, project, 'adapt_body', review['review_digest'], True)
        self.assertEqual(set(choice), {'schema', 'policy', 'review_digest'})
        self.assertNotIn('body_fit', project)

    def test_saved_choice_recomputes_transform_and_cannot_inject_a_matrix(self):
        scene, project = fixture()
        review = approve(scene, project)
        matrix, translation, resolved = resolve_body_fit(scene, project)
        self.assertTrue(resolved['approved'])
        np.testing.assert_array_equal(matrix, review['matrix'])
        np.testing.assert_array_equal(translation, review['translation_metres'])
        matrix[:] = 100
        np.testing.assert_array_equal(resolve_body_fit(scene, project)[0], review['matrix'])
        project['body_fit']['matrix'] = np.eye(3).tolist()
        with self.assertRaises(GarageError):
            resolve_body_fit(scene, project)

    def test_project_rename_new_id_and_archive_roundtrip_retain_valid_choice(self):
        scene, project = fixture()
        before = approve(scene, project)
        project.update(id='b'*32, name='My new car', created=234., notes='New description',
                       approved_roles=False, template_inheritance_approved=False)
        restored = json.loads(json.dumps(project))
        resolved = resolve_body_fit(scene, restored)[2]
        self.assertTrue(resolved['approved'])
        self.assertEqual(before['review_digest'], resolved['review_digest'])

    def test_preparation_and_destination_changes_invalidate_saved_review(self):
        changes = {
            'units': lambda p: p.update(metres_per_unit=.1),
            'up': lambda p: p.update(source_up_axis='+Z'),
            'forward': lambda p: p.update(forward='-Z'),
            'reference': lambda p: p.update(wheelbase_metres=2.551),
            'override': lambda p: p['overrides'].update(headlight={'role': 'paint', 'corner': None}),
            'partition': lambda p: p['wheel_partitions'].append({'changed_selection': True}),
            'role': lambda p: p['inspection']['parts'][-1].update(role='body'),
            'destination': lambda p: p.update(installation={'mode': 'replace', 'replace_car_id': 82}),
        }
        for name, change in changes.items():
            with self.subTest(change=name):
                scene, project = fixture()
                approve(scene, project)
                change(project)
                with self.assertRaises(GarageError):
                    resolve_body_fit(scene, project)

    def test_geometry_normals_uvs_and_faces_are_all_bound_to_review(self):
        for field in ('positions', 'normals', 'uv', 'indices'):
            with self.subTest(field=field):
                scene, project = fixture()
                approve(scene, project)
                part = scene.parts[-1]
                if field == 'positions':
                    part.positions[0, 0] += .01
                elif field == 'normals':
                    part.normals[0] *= -1
                elif field == 'uv':
                    part.uv[0, 0] = .25
                else:
                    part.indices[0] = part.indices[0, [0, 2, 1]]
                with self.assertRaises(GarageError):
                    resolve_body_fit(scene, project)

    def test_changed_source_fingerprint_is_not_treated_as_same_geometry(self):
        scene, project = fixture()
        approve(scene, project)
        altered = replace(scene, digest='b'*64)
        result = body_fit_review(altered, project, 'adapt_body')
        self.assertFalse(result['available'])
        self.assertEqual(result['issue_code'], 'source_changed')
        with self.assertRaises(GarageError):
            resolve_body_fit(altered, project)

    def test_profile_centres_and_asset_hashes_invalidate_saved_choice(self):
        scene, project = fixture()
        old = approve(scene, project)
        profile = copy.deepcopy(PROFILE)
        for corner in CORNERS:
            profile['wheel_geometry'][corner]['center_metres'][0] *= 1.005
        assets = {**ASSETS, 'hq/body': 'b'*64}
        for name, value in (('PROFILE', profile), ('ASSETS', assets)):
            with self.subTest(contract=name), patch('garagehub.body_fit.'+name, value):
                current = body_fit_review(scene, project, 'adapt_body')
                self.assertTrue(current['within_profile_limits'])
                self.assertNotEqual(current['reference']['reference_digest'], old['reference']['reference_digest'])
                with self.assertRaises(GarageError):
                    resolve_body_fit(scene, project)

    def test_rim_resizing_is_explicit_and_body_policy_does_not_claim_rigid_wheels(self):
        scene, project = fixture()
        reviews = [body_fit_review(scene, project, policy) for policy in POLICIES]
        self.assertEqual(reviews[0]['wheel_conversion'], reviews[1]['wheel_conversion'])
        wheels = reviews[0]['wheel_conversion']
        self.assertFalse(wheels['source_rim_rigidity_guaranteed'])
        self.assertFalse(wheels['body_choice_changes_wheel_conversion'])
        self.assertTrue(wheels['native_tyres_retained'])
        front = wheels['corners'][0]
        self.assertAlmostEqual(front['radial_scale'], .32165/.32)
        self.assertAlmostEqual(front['axial_scale'], .235/.24)
        for review in reviews:
            self.assertFalse(review['mechanical_roles_supported_by_this_choice'])
            self.assertFalse(review['physics_changed'])
            self.assertFalse(review['runtime_verified'])
            self.assertFalse(review['source_geometry_changed'])
            self.assertFalse(review['approved'])

    def test_missing_or_invalid_geometry_reports_remain_finite_json(self):
        scene, project = fixture()
        cases = []
        missing = copy.deepcopy(project); missing['inspection']['wheel_centers'].pop('RR')
        cases.append((scene, missing))
        stale = copy.deepcopy(project); stale['inspection']['parts'].pop()
        cases.append((scene, stale))
        nonfinite = copy.deepcopy(project); nonfinite['inspection']['wheelbase'] = float('nan')
        cases.append((scene, nonfinite))
        bad_scene = copy.deepcopy(scene); bad_scene.parts[0].positions[0, 0] = float('inf')
        cases.append((bad_scene, project))
        for source, saved in cases:
            with self.subTest(case=len(saved['inspection']['parts'])):
                result = body_fit_review(source, saved, 'adapt_body')
                self.assertFalse(result['available'])
                self.assertIsNone(result['matrix'])
                self.assertTrue(result['issue'])
                json.dumps(result, allow_nan=False)

    def test_invalid_policy_and_missing_or_forged_saved_decisions_are_rejected(self):
        scene, project = fixture()
        for policy in (None, 'automatic', {}, 1):
            with self.subTest(policy=repr(policy)), self.assertRaises(GarageError):
                body_fit_review(scene, project, policy)
        for decision in (None, {}, {'schema': True, 'policy': 'adapt_body', 'review_digest': 'a'*64},
                         {'schema': 1, 'policy': 'adapt_body', 'review_digest': 'a'*64},
                         {'schema': 2, 'policy': 'adapt_body', 'review_digest': 'a'*64}):
            saved = {**project, 'body_fit': decision}
            with self.subTest(decision=decision), self.assertRaises(GarageError):
                resolve_body_fit(scene, saved)
        with self.assertRaises(GarageError):
            resolve_body_fit(scene, project, require_approved=False)
        project['body_fit'] = {'policy': 'adapt_body'}
        self.assertFalse(resolve_body_fit(scene, project, require_approved=False)[2]['approved'])

    def test_review_contains_no_proprietary_assets_or_actual_template_authentication_claim(self):
        scene, project = fixture()
        report = body_fit_review(scene, project, 'adapt_body')
        restored = json.loads(json.dumps(report, allow_nan=False))
        self.assertEqual(report, restored)
        self.assertEqual(report['reference']['asset_sha256'], ASSETS)
        self.assertTrue(report['reference']['requires_template_authentication'])
        self.assertFalse(report['reference']['current_mount_verified'])
        self.assertNotIn('template_root', report)
        self.assertIsNone(report['issue'])


if __name__ == '__main__':
    unittest.main()
