"""Mechanical intent must survive generic recognition without inventing rigs."""
from __future__ import annotations

import copy
import unittest

import numpy as np

from .recognition import CORNERS, MECHANICAL_ROLES, MOTION_INTENTS, infer
from .recognition_tests import CENTRES, by_id, circular_part, scene_case


def add_brake(scene,name='brake_disc',role=None,corner=None,small=False):
    centre=CENTRES['FL']+np.array([0,.2,0]) if small else CENTRES['FL']
    extras={} if role is None else {'gt6_role':role,'gt6_corner':corner}
    part=circular_part('brake',centre,radius=.04 if small else .23,
                       width=.035 if small else .04,material=1,extras=extras)
    part.name=name;scene.parts.append(part)
    return part


class MechanicalRoleTests(unittest.TestCase):
    def test_explicit_rotor_and_caliper_keep_corner_and_distinct_motion(self):
        for role in MECHANICAL_ROLES:
            with self.subTest(role=role):
                scene=scene_case();add_brake(scene,role=role,corner='FR',small=True)
                row=by_id(infer(scene,forward='+Z'))['brake']
                self.assertEqual((row['role'],row['corner']),(role,'FR'))
                self.assertEqual(row['motion_intent'],MOTION_INTENTS[role])
                self.assertFalse(row['role_review_required'])
                self.assertEqual(row['mechanical_role_proposals'],[])

    def test_authored_brakes_never_establish_tyres_even_as_four_dark_discs(self):
        for role in MECHANICAL_ROLES:
            with self.subTest(role=role):
                scene=scene_case()
                for corner,part in zip(CORNERS,scene.parts[:4]):
                    part.extras={'gt6_role':role,'gt6_corner':corner}
                report=infer(scene,forward='+Z')
                self.assertEqual(report['wheel_centers'],{})
                self.assertEqual(report['wheel_radii'],{})
                self.assertIsNone(report['wheelbase'])
                self.assertTrue(all(by_id(report)['disc_'+k]['role']==role for k in CORNERS))

    def test_saved_mechanical_override_wins_and_explicit_unassigned_stays_unassigned(self):
        scene=scene_case();add_brake(scene,role='rim',corner='RR',small=True)
        for corner in ('RL',None):
            with self.subTest(corner=corner):
                report=infer(scene,forward='+Z',overrides={'brake':{'role':'brake_caliper','corner':corner}})
                row=by_id(report)['brake']
                self.assertEqual((row['role'],row['corner']),('brake_caliper',corner))
                self.assertEqual(row['motion_intent'],'upright_nonspin')
                self.assertFalse(row['role_review_required'])

    def test_saved_body_and_ignore_override_brake_names_and_authored_roles(self):
        scene=scene_case();add_brake(scene,role='brake_rotor',corner='FL')
        for role in ('body','ignore'):
            with self.subTest(role=role):
                row=by_id(infer(scene,forward='+Z',overrides={'brake':{'role':role,'corner':'FL'}}))['brake']
                self.assertEqual((row['role'],row['corner']),(role,None))
                self.assertIsNone(row['motion_intent'])
                self.assertFalse(row['role_review_required'])

    def test_named_discs_are_review_proposals_not_tyres_or_rims(self):
        for name in ('brake_disc','brakedisc','Brake Disk','rotor','wheel_brake_rotor'):
            with self.subTest(name=name):
                scene=scene_case();part=add_brake(scene,name=name)
                part.material=0  # Dark geometry must not become a tyre candidate.
                report=infer(scene,forward='+Z');row=by_id(report)['brake']
                self.assertEqual((row['role'],row['corner']),('brake_rotor','FL'))
                self.assertTrue(row['role_review_required'])
                self.assertLess(row['confidence'],.75)
                self.assertEqual(row['motion_intent'],'wheel_spin')
                np.testing.assert_allclose(report['wheel_centers']['FL'],CENTRES['FL'],atol=1e-15)
                self.assertTrue(any(x['code']=='mechanical_role_review' for x in report['issues']))

    def test_small_named_caliper_is_not_promoted_to_spinning_valve(self):
        scene=scene_case();add_brake(scene,name='wheel_caliper',small=True)
        row=by_id(infer(scene,forward='+Z'))['brake']
        self.assertEqual((row['role'],row['corner']),('brake_caliper','FL'))
        self.assertEqual(row['motion_intent'],'upright_nonspin')
        self.assertTrue(row['role_review_required'])

    def test_composite_disc_caliper_name_has_no_automatic_single_motion_role(self):
        scene=scene_case();add_brake(scene,name='brake_disc_and_caliper',small=True)
        row=by_id(infer(scene,forward='+Z'))['brake']
        self.assertEqual((row['role'],row['corner']),('body',None))
        self.assertEqual(row['mechanical_role_proposals'],list(MECHANICAL_ROLES))
        self.assertTrue(row['role_review_required'])
        self.assertIsNone(row['motion_intent'])

    def test_multicorner_material_group_keeps_unassigned_corner(self):
        scene=scene_case();part=add_brake(scene)
        # A single material primitive containing left and right surfaces cannot
        # acquire a corner from the centroid of either subset.
        part.positions=np.vstack((part.positions,part.positions-np.array([1.6,0,0])))
        part.normals=np.tile(part.normals,(2,1));part.uv=np.tile(part.uv,(2,1))
        old=part.indices;part.indices=np.vstack((old,old+len(part.positions)//2))
        row=by_id(infer(scene,forward='+Z'))['brake']
        self.assertEqual(row['role'],'brake_rotor')
        self.assertIsNone(row['corner'])
        self.assertTrue(row['role_review_required'])

    def test_invalid_or_incomplete_tyre_quartet_cannot_propose_brake_corner(self):
        for mode in ('missing','crossed'):
            with self.subTest(mode=mode):
                scene=scene_case();add_brake(scene,name='caliper',small=True)
                for corner,part in zip(CORNERS,scene.parts[:4]):
                    part.extras={'gt6_role':'tyre','gt6_corner':corner}
                if mode=='missing':scene.parts[1].extras={'gt6_role':'ignore'}
                else:
                    scene.parts[0].extras['gt6_corner']='FR';scene.parts[1].extras['gt6_corner']='FL'
                row=by_id(infer(scene,forward='+Z'))['brake']
                self.assertEqual(row['role'],'brake_caliper')
                self.assertIsNone(row['corner'])

    def test_authored_brake_without_corner_keeps_role_with_visible_corner_proposal(self):
        scene=scene_case();add_brake(scene,role='brake_caliper',small=True)
        row=by_id(infer(scene,forward='+Z'))['brake']
        self.assertEqual((row['role'],row['corner']),('brake_caliper','FL'))
        self.assertIn('corner proposed',row['reason'])
        self.assertFalse(row['role_review_required'])

    def test_named_brakes_without_tyres_do_not_manufacture_four_axles(self):
        scene=scene_case()
        for part in scene.parts[:4]:part.name='brake_disc'
        report=infer(scene,forward='+Z')
        self.assertEqual(report['wheel_centers'],{})
        self.assertTrue(all(by_id(report)['disc_'+k]['role']=='brake_rotor' for k in CORNERS))

    def test_mechanical_proposals_do_not_mutate_source_or_saved_assignments(self):
        scene=scene_case();add_brake(scene,name='brake_disc_and_caliper')
        saved={'disc_FL':{'role':'tyre','corner':'FL'}}
        before=copy.deepcopy(scene);before_saved=copy.deepcopy(saved)
        infer(scene,forward='+Z',overrides=saved)
        self.assertEqual(saved,before_saved)
        for old,part in zip(before.parts,scene.parts):
            self.assertEqual(old.extras,part.extras)
            for name in ('positions','normals','uv','indices'):
                np.testing.assert_array_equal(getattr(old,name),getattr(part,name))


if __name__=='__main__':unittest.main()
