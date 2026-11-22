"""The first-use guide shares production gates without implying a build result."""
from __future__ import annotations

import copy
import json
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .car_builder import plan
from .f50_wheel_adapter import F50_SOURCE_SHA256
from .import_readiness import import_readiness, source_assessment
from .mechanical_policy import mechanical_actions, mechanical_requirements
from .wheel_fit_tests import wheel_scene


def prepared():
    scene, inspection = wheel_scene()
    project = {'inspection': inspection, 'approved_roles': True,
               'template_inheritance_approved': True, 'wheelbase_metres': 2.55,
               'installation': {'mode': None, 'replace_car_id': None}}
    return scene, project


class ImportReadinessTests(unittest.TestCase):
    def test_source_guide_works_before_template_or_packer_setup(self):
        scene, project = prepared()
        project['approved_roles'] = False
        with patch('garagehub.car_builder.validate_template', side_effect=AssertionError('No template is configured')):
            result = import_readiness(scene, project)
        self.assertEqual([r['code'] for r in result['user_tasks']], ['confirm_source_review'])
        self.assertFalse(result['build_readiness_checked'])
        self.assertFalse(result['runtime_verified'])
        self.assertNotIn('ready', result)
        self.assertEqual(result['scope'], 'source_preflight')

    def test_native_limit_is_visible_alongside_editable_brake_review(self):
        scene, project = prepared()
        project['inspection']['parts'].append({'id': 'brake_group', 'role': 'brake_rotor',
            'corner': None, 'material': 0, 'role_review_required': True})
        project['approved_roles'] = False
        result = import_readiness(scene, project)
        self.assertEqual([r['code'] for r in result['user_tasks']],
                         ['mechanical_role_review', 'mechanical_corners', 'confirm_source_review'])
        self.assertEqual([r['code'] for r in result['capability_limits']], ['mechanical_native_route'])
        self.assertEqual(result['capability_limits'][0]['action'], 'save_project')
        self.assertEqual(result['capability_limits'][0]['part_ids'], ['brake_group'])
        project['inspection']['parts'][-1].update(corner='FL', role_review_required=False)
        project['approved_roles'] = True
        after = import_readiness(scene, project)
        self.assertEqual(after['user_tasks'], [])
        self.assertEqual(after['capability_limits'], result['capability_limits'])

    def test_material_limits_do_not_suggest_ignoring_or_relabelling_parts(self):
        scene, project = prepared()
        scene.materials[0].update(alpha_mode='MASK', base_texture=0)
        result = import_readiness(scene, project)
        self.assertEqual({r['code'] for r in result['capability_limits']}, {'alpha_cutout', 'textured_rims'})
        self.assertTrue(all(r['action'] == 'save_project' for r in result['capability_limits']))
        self.assertEqual(result['user_tasks'], [])
        self.assertTrue(result['source_review_complete'])
        self.assertFalse(result['build_readiness_checked'])

    def test_missing_wheels_and_size_are_guided_before_confirmation(self):
        scene, project = prepared()
        project['approved_roles'] = False
        project['inspection']['wheel_centers'].pop('RR')
        result = import_readiness(scene, project)
        self.assertEqual(result['user_tasks'][0]['code'], 'wheel_layout')
        self.assertEqual(result['user_tasks'][0]['action'], 'review_wheels')
        scene, project = prepared()
        project['approved_roles'] = False
        project['wheelbase_metres'] = 2.5
        result = import_readiness(scene, project)
        self.assertEqual(result['user_tasks'][0]['code'], 'source_units')
        self.assertEqual(result['user_tasks'][0]['action'], 'review_scale')
        self.assertEqual(result['user_tasks'][-1]['code'], 'confirm_source_review')

    def test_source_review_does_not_choose_destination_or_accept_inheritance(self):
        scene, project = prepared()
        project['template_inheritance_approved'] = False
        result = import_readiness(scene, project)
        self.assertEqual(result['user_tasks'], [])
        self.assertEqual([r['code'] for r in result['build_review_tasks']], ['template_inheritance', 'body_fit_review'])
        self.assertIsNone(project['installation']['mode'])
        self.assertIsNone(project['installation']['replace_car_id'])
        self.assertFalse(project['template_inheritance_approved'])

    def test_valid_wheel_layout_outside_profile_ratio_is_a_native_capability_limit(self):
        scene, project = prepared()
        inspection = project['inspection']
        centers = copy.deepcopy(inspection['wheel_centers'])
        for center in inspection['wheel_centers'].values():
            center[0] *= 2
        inspection['track'] *= 2
        records = {row['id']: row for row in inspection['parts']}
        for part in scene.parts:
            corner = records[part.id]['corner']
            if corner:
                part.positions[:, 0] += centers[corner][0]
        result = import_readiness(scene, project)
        self.assertEqual(result['user_tasks'], [])
        self.assertEqual([row['code'] for row in result['capability_limits']], ['profile_fit_ratio'])
        self.assertEqual(result['capability_limits'][0]['action'], 'save_project')
        with patch('garagehub.car_builder.validate_template', return_value={'root': 'synthetic'}):
            native = plan(scene, project, Path('/synthetic'))
        self.assertFalse(native['ready'])
        self.assertEqual(native['actions'], [result['capability_limits'][0]['detail'],
                                            'Review and confirm how the body should fit the vehicle template.'])

    def test_known_unsupported_source_and_wheelbase_limits_remain_visible(self):
        scene, project = prepared()
        scene.digest = F50_SOURCE_SHA256
        project['wheelbase_metres'] = 2.7
        result = import_readiness(scene, project)
        self.assertEqual({r['code'] for r in result['capability_limits']},
                         {'source_native_profile', 'profile_wheelbase'})
        self.assertFalse(result['build_readiness_checked'])

    def test_source_requirements_preserve_production_actions_and_diagnostics(self):
        scene, project = prepared()
        project.update(approved_roles=False, template_inheritance_approved=False, wheelbase_metres=2.7)
        project['inspection']['role_counts']['headlight'] = 0
        project['inspection']['issues'] = [{'code': 'paint_ambiguous', 'message': 'Choose paint surfaces.'}]
        scene.materials[0].update(alpha_mode='MASK', base_texture=1)
        shared = source_assessment(scene, project)
        with patch('garagehub.car_builder.validate_template', return_value={'root': 'synthetic'}):
            result = plan(scene, project, Path('/synthetic'))
        self.assertEqual(result['actions'], [r['message'] for r in shared['requirements']])
        self.assertEqual(result['actions'][:3], ['Confirm the reviewed component roles and front direction.',
                                              'Choose paint surfaces.', 'Assign at least one headlamp emitting surface.'])
        self.assertEqual(result['fit'], shared['fit'])
        self.assertEqual(result['source_orientation'], shared['source_orientation'])
        self.assertFalse(result['ready'])
        self.assertEqual(mechanical_actions(project['inspection']),
                         [r['message'] for r in mechanical_requirements(project['inspection'])])

    def test_summary_is_serializable_and_does_not_modify_project_or_geometry(self):
        scene, project = prepared()
        original_project = copy.deepcopy(project)
        original_parts = copy.deepcopy(scene.parts)
        original_materials = copy.deepcopy(scene.materials)
        result = import_readiness(scene, project)
        json.dumps(result, allow_nan=False)
        self.assertEqual(project, original_project)
        self.assertEqual(scene.materials, original_materials)
        for before, after in zip(original_parts, scene.parts):
            for field in ('positions', 'normals', 'uv', 'indices'):
                np.testing.assert_array_equal(getattr(before, field), getattr(after, field))
        result['user_tasks'].append({'code': 'caller_only'})
        self.assertEqual(import_readiness(scene, project)['user_tasks'], [])

    def test_summary_omits_expensive_reports_without_skipping_their_source_gates(self):
        scene, project = prepared()
        with patch('garagehub.import_readiness.tyre_fit_report', side_effect=AssertionError('Full plan only')), \
             patch('garagehub.import_readiness.wheel_source_orientation_report', side_effect=AssertionError('Full plan only')):
            result = import_readiness(scene, project)
        self.assertTrue(result['source_review_complete'])
        self.assertFalse(result['build_readiness_checked'])


if __name__ == '__main__':
    unittest.main()
