"""No unresolved or dynamic brake geometry may escape through static/rim paths."""
import copy,unittest
from pathlib import Path
from unittest.mock import patch
from .car_builder import plan
from .car_geometry import prepare_parts
from .errors import GarageError
from .mechanical_policy import mechanical_actions
from .mechanical_roles_tests import add_brake
from .recognition_tests import scene_case
from .recognition import infer
from .wheel_fit_tests import wheel_scene
from .wheels import geometry


class MechanicalPolicyTests(unittest.TestCase):
    def test_explicit_brakes_block_production_plan_even_after_role_approval(self):
        for role in ('brake_rotor','brake_caliper'):
            scene,report=wheel_scene();part=copy.deepcopy(scene.parts[1]);part.id='custom_brake';scene.parts.append(part)
            report['parts'].append({'id':part.id,'role':role,'corner':'FL','material':0})
            with patch('garagehub.car_builder.validate_template',return_value={'root':'synthetic'}):
                result=plan(scene,{'inspection':report,'approved_roles':True,'template_inheritance_approved':True},Path('/synthetic'))
            self.assertFalse(result['ready']);self.assertTrue(any('no reviewed route' in a for a in result['actions']))

    def test_ambiguous_body_proposals_fail_before_body_fit_or_emission(self):
        scene=scene_case();add_brake(scene,name='brake_disc_and_caliper');report=infer(scene,forward='+Z')
        with patch('garagehub.car_geometry.alignment',side_effect=AssertionError('must reject first')):
            with self.assertRaisesRegex(GarageError,'Mixed rotating'):prepare_parts(scene,report,'hq')

    def test_explicit_rotor_and_caliper_never_fall_back_to_static_body(self):
        for role in ('brake_rotor','brake_caliper'):
            scene=scene_case();add_brake(scene,role=role,corner='FL');report=infer(scene,forward='+Z')
            with self.subTest(role=role),self.assertRaisesRegex(GarageError,'static body'):
                prepare_parts(scene,report,'hq')

    def test_missing_brake_corner_has_actionable_partition_instruction(self):
        actions=mechanical_actions({'parts':[{'id':'combined','role':'brake_rotor','corner':None}]})
        self.assertTrue(any('split its same-role group by wheel corner' in a for a in actions))

    def test_explicit_user_body_or_ignore_clears_name_only_brake_proposal(self):
        scene=scene_case();add_brake(scene,name='brake_disc_and_caliper')
        for role in ('body','ignore'):
            report=infer(scene,forward='+Z',overrides={'brake':{'role':role,'corner':None}})
            self.assertEqual(mechanical_actions(report),[])

    def test_rim_emitter_rejects_mechanical_roles_before_using_rim_frame(self):
        for roles in (('brake_rotor',),('brake_caliper',),('rim','brake_rotor')):
            with patch('garagehub.wheels.rim_frame',side_effect=AssertionError('wrong frame')):
                with self.assertRaisesRegex(GarageError,'rims and valves only'):geometry(None,None,'FL',roles=roles)

    def test_standalone_rim_subset_remains_identical_with_other_brake_assignments(self):
        scene,report=wheel_scene();before=geometry(scene,report,'FL')
        part=copy.deepcopy(scene.parts[1]);part.id='caliper';scene.parts.append(part)
        report['parts'].append({'id':part.id,'role':'brake_caliper','corner':'FL','material':0})
        after=geometry(scene,report,'FL')
        self.assertEqual(after[:2],before[:2]);self.assertEqual(after[-1],before[-1])


if __name__=='__main__':unittest.main()
