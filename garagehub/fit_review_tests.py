"""Integration regressions for reference units and selected-target provenance."""
import copy
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from .car_builder import plan
from .car_geometry import alignment
from .car_profiles import PROFILE
from .car_tests import fitted_inspection
from .fit_review import profile_assembly_review, reference_wheelbase_review, selected_target_assembly_review
from .identity_pipeline import identity_plan
from .target_geometry import target_geometry_report
from .target_geometry_tests import fixture


class FitReviewTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)

    def tearDown(self):
        self.tmp.cleanup()

    def test_tenfold_wrong_units_cannot_be_approved_by_typing_target_wheelbase(self):
        inspection = fitted_inspection()
        inspection['wheel_centers'] = {k: [v / 10 for v in point] for k, point in inspection['wheel_centers'].items()}
        inspection['wheelbase'] /= 10
        inspection['track'] /= 10
        before = copy.deepcopy(inspection)
        scene = SimpleNamespace(digest='a'*64, parts=[], triangles=0, materials=[{'base_texture': None}])
        project = {'inspection': inspection, 'approved_roles': True,
                   'template_inheritance_approved': True, 'wheelbase_metres': 2.55}
        with patch('garagehub.car_builder.validate_template', return_value={}):
            result = plan(scene, project, self.root)
        self.assertFalse(result['ready'])
        self.assertTrue(any('Set units from wheelbase' in item for item in result['actions']))
        self.assertAlmostEqual(result['fit']['uniform_scale'], 10)
        self.assertAlmostEqual(result['fit']['source_units']['entered_to_measured_ratio'], 10)
        self.assertFalse(result['fit']['assembly']['transform_applied'])
        self.assertEqual(inspection, before)

    def test_reference_precision_does_not_become_a_physical_fit_threshold(self):
        inspection = fitted_inspection()
        inside = reference_wheelbase_review(profile_assembly_review(inspection, 2.5505))
        outside = reference_wheelbase_review(profile_assembly_review(inspection, 2.552))
        self.assertTrue(inside['consistent'])
        self.assertFalse(outside['consistent'])
        self.assertEqual(outside['comparison_tolerance_metres'], .001)
        self.assertIn('not a target fit tolerance', outside['scope'])
        self.assertIsNone(reference_wheelbase_review(profile_assembly_review(inspection))['consistent'])

    def test_review_keeps_existing_applied_profile_transform_separate(self):
        inspection = fitted_inspection()
        _, _, before = alignment(inspection)
        report = profile_assembly_review(inspection, 2.55)
        _, _, after = alignment(inspection)
        self.assertEqual(before, after)
        self.assertEqual(report['rigid_fit']['scale'], 1)
        self.assertFalse(report['physics_changed'])
        self.assertFalse(report['transform_applied'])
        self.assertFalse(report['runtime_verified'])
        self.assertEqual(report['target']['provenance']['profile_id'], PROFILE['id'])

    def test_missing_or_crossed_corners_remain_actionable_and_unavailable(self):
        for mode in ('missing', 'crossed'):
            inspection = fitted_inspection()
            if mode == 'missing':
                inspection['wheel_centers'].pop('RR')
            else:
                inspection['wheel_centers']['FL'], inspection['wheel_centers']['FR'] = inspection['wheel_centers']['FR'], inspection['wheel_centers']['FL']
            result = profile_assembly_review(inspection)
            self.assertFalse(result['available'])
            self.assertTrue(result['issue'])
            self.assertFalse(reference_wheelbase_review(result)['available'])

    def test_selected_target_nominal_fit_keeps_db_provenance_and_unknown_height(self):
        path = self.root/'target.sqlite'
        fixture(path)
        target = target_geometry_report(path, 27)
        report = selected_target_assembly_review(fitted_inspection(), target, 2.55)
        self.assertTrue(report['available'])
        self.assertIsNone(report['rigid_fit'])
        self.assertFalse(report['target']['target_y_known'])
        self.assertEqual(report['target']['dimensions']['rear_track_metres'], 1.602)
        self.assertEqual(report['target']['provenance']['target']['car_id'], 27)
        self.assertEqual(report['target']['provenance']['database_sha256'], target['database']['sha256'])
        self.assertTrue(all(row['residual_metres'][1] is None for row in report['corner_residuals'].values()))

    def test_incomplete_selected_chassis_does_not_invent_profile_dimensions(self):
        result = selected_target_assembly_review(fitted_inspection(), {'physical': {'chassis': {'nominal': None}}})
        self.assertFalse(result['available'])
        self.assertFalse(result['transform_applied'])
        self.assertNotIn('target', result)

    def test_identity_choice_selects_inherited_donor_or_exact_replacement_geometry(self):
        path = self.root/'target.sqlite'
        fixture(path)
        original = path.read_bytes()
        context = {'database': str(path), 'names': {}, 'encrypted_sha256': 'a'*64,
                   'baseline_header': 'b'*64, 'reserved_model_paths': [], 'reserved_car_ids': []}
        for mode, selected, expected, scope in (('new_entry', None, 11, 'inherited_donor'),
                                                ('replace', 27, 27, 'explicit_replacement_target')):
            with self.subTest(mode=mode), patch('garagehub.identity_pipeline.database_context', return_value=context), \
                 patch('garagehub.identity_pipeline.PROFILE', {**PROFILE, 'car_id': 11}), \
                 patch('garagehub.specdb.plan_install', return_value={'ready': True, 'mode': mode}):
                report = identity_plan({'name': 'Arbitrary source', 'installation': {'mode': mode, 'replace_car_id': selected}}, self.root, None, None)
            geometry = report['target_geometry']
            self.assertEqual(geometry['target']['car_id'], expected)
            self.assertEqual(geometry['identity_scope'], scope)
            self.assertTrue(geometry['nominal_data_complete'])
            self.assertFalse(geometry['physics_changed'])
        self.assertEqual(path.read_bytes(), original)
