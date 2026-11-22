"""Lossless explicit corner partitions, saved replay and rejection boundaries."""
from __future__ import annotations

import copy
import json
import unittest

import numpy as np

from .errors import GarageError
from .f50_adapter_tests import synthetic_four
from .glb import Scene, MAX_PARTS
from .mechanical_parts import partition_wheel_corners, replay_wheel_partition
from .recognition import CORNERS, CORNER_ROLES, MOTION_INTENTS
from .scene_coordinates import UP_AXES, prepare_scene


def inspection():
    return {'forward':'+Z','wheel_centers':{
        k:[1. if k[1]=='L' else -1.,0.,1. if k[0]=='F' else -1.] for k in CORNERS}}


def prepared(part,axis,scale):
    return prepare_scene(Scene([part],[],{},b'unchanged','a'*64,[]),axis,scale).parts[0]


class MechanicalPartitionTests(unittest.TestCase):
    def assert_exact(self,part,children,receipt):
        seen=[]
        for child,group in zip(children,receipt['groups']):
            selected=part.indices[group['source_face_indices']];seen.extend(group['source_face_indices'])
            for name in ('positions','normals','uv'):
                np.testing.assert_array_equal(getattr(child,name)[child.indices],getattr(part,name)[selected])
            self.assertEqual((child.node,child.primitive,child.material),(part.node,part.primitive,part.material))
            self.assertEqual(child.extras['gt6_motion_intent'],MOTION_INTENTS[receipt['role']])
        self.assertEqual(sorted(seen),list(range(len(part.indices))))
        self.assertEqual(len(seen),len(set(seen)))

    def test_partition_retains_every_triangle_attribute_and_explicit_motion_role(self):
        part=synthetic_four();part.name='Combined material: requires reviewed selection'
        part.uv=np.arange(len(part.positions)*2,dtype=np.float32).reshape(-1,2)/17
        for role in CORNER_ROLES:
            with self.subTest(role=role):
                children,receipt=partition_wheel_corners(part,inspection(),role,known_ids=[part.id,'shell'])
                self.assertEqual([p.id for p in children],['test__wheel_'+k for k in CORNERS])
                self.assertEqual([p.extras['gt6_corner'] for p in children],list(CORNERS))
                self.assertEqual(receipt['role_confirmation'],'explicit_caller')
                self.assertFalse(receipt['runtime_verified'])
                self.assertEqual(json.loads(json.dumps(receipt,allow_nan=False)),receipt)
                self.assert_exact(part,children,receipt)

    def test_role_is_required_and_never_inferred_from_material_or_part_name(self):
        part=synthetic_four();part.name='brake_disc caliper'
        for role in (None,'body','rotor','mixed_brakes',True):
            with self.subTest(role=role),self.assertRaises(GarageError):
                partition_wheel_corners(part,inspection(),role)

    def test_replay_preserves_saved_faces_through_all_axis_and_unit_preparations(self):
        part=synthetic_four();_,receipt=partition_wheel_corners(part,inspection(),'brake_caliper')
        for axis in UP_AXES:
            for scale in (1e-6,.0001,.01,1.,100.,10000.,1e6):
                with self.subTest(axis=axis,scale=scale):
                    current=prepared(part,axis,scale)
                    children=replay_wheel_partition(current,receipt,source_part=part,known_ids=[part.id])
                    self.assert_exact(current,children,receipt)
                    self.assertEqual([p.extras['gt6_corner'] for p in children],list(CORNERS))

    def test_first_partition_in_prepared_coordinates_hashes_canonical_source(self):
        part=synthetic_four();current=prepared(part,'+Y',.01)
        measured=inspection();measured['wheel_centers']={k:(np.array(v)*.01).tolist() for k,v in measured['wheel_centers'].items()}
        children,receipt=partition_wheel_corners(current,measured,'brake_rotor',source_part=part)
        _,canonical=partition_wheel_corners(part,inspection(),'brake_rotor')
        self.assertEqual(receipt['source_part'],canonical['source_part'])
        self.assertEqual(receipt['groups'],canonical['groups'])
        self.assert_exact(current,children,receipt)
        replay=replay_wheel_partition(prepared(part,'+Z',100.),receipt,source_part=part)
        self.assert_exact(prepared(part,'+Z',100.),replay,receipt)

    def test_replay_requires_original_hash_after_coordinates_change(self):
        part=synthetic_four();_,receipt=partition_wheel_corners(part,inspection(),'rim')
        with self.assertRaisesRegex(GarageError,'geometry hash'):
            replay_wheel_partition(prepared(part,'+Z',100.),receipt)

    def test_changed_canonical_geometry_identity_uv_or_topology_rejected(self):
        part=synthetic_four();_,receipt=partition_wheel_corners(part,inspection(),'brake_rotor')
        for kind in ('positions','normals','uv','indices','id','material'):
            with self.subTest(kind=kind):
                changed=copy.deepcopy(part)
                if kind=='indices':changed.indices[0]=changed.indices[0,::-1]
                elif kind=='id':changed.id='other'
                elif kind=='material':changed.material+=1
                else:getattr(changed,kind)[0,0]+=.05
                with self.assertRaises(GarageError):replay_wheel_partition(changed,receipt)

    def test_valid_canonical_hash_cannot_authorize_edited_prepared_geometry(self):
        part=synthetic_four();_,receipt=partition_wheel_corners(part,inspection(),'brake_caliper')
        for kind in ('one_vertex','nonuniform','reflection','translation','normal','uv','topology'):
            with self.subTest(kind=kind):
                changed=prepared(copy.deepcopy(part),'+Y',100.)
                if kind=='one_vertex':changed.positions[0,1]+=.01
                elif kind=='nonuniform':changed.positions[:,0]*=1.1
                elif kind=='reflection':changed.positions[:,0]*=-1
                elif kind=='translation':changed.positions[:,1]+=.01
                elif kind=='normal':changed.normals[0]=[1,0,0]
                elif kind=='uv':changed.uv[0,0]+=.1
                elif kind=='topology':changed.indices[0]=changed.indices[0,::-1]
                with self.assertRaises(GarageError):replay_wheel_partition(changed,receipt,source_part=part)

    def test_triangle_crossing_corner_ownership_rejected_without_clipping(self):
        part=synthetic_four();part.positions[0,0]=-1.
        before=part.positions.copy()
        with self.assertRaisesRegex(GarageError,'triangle crosses'):
            partition_wheel_corners(part,inspection(),'brake_rotor')
        np.testing.assert_array_equal(part.positions,before)

    def test_vertex_tied_between_corners_rejected_without_nearest_tiebreak(self):
        part=synthetic_four();part.positions[0,0]=0.
        with self.assertRaisesRegex(GarageError,'equally close'):
            partition_wheel_corners(part,inspection(),'brake_rotor')

    def test_missing_corner_surfaces_are_not_manufactured(self):
        part=synthetic_four();part.indices=part.indices[:6]
        with self.assertRaisesRegex(GarageError,'all four tyre corners'):
            partition_wheel_corners(part,inspection(),'brake_caliper')

    def test_invalid_incomplete_crossed_and_degenerate_centres_rejected(self):
        for kind in ('missing','crossed','coincident','nonfinite','boolean'):
            with self.subTest(kind=kind):
                measured=inspection();c=measured['wheel_centers']
                if kind=='missing':del c['RR']
                elif kind=='crossed':c['FL'],c['FR']=c['FR'],c['FL']
                elif kind=='coincident':c['FR']=c['FL']
                elif kind=='nonfinite':c['FL'][0]=float('nan')
                else:c['FL'][0]=True
                with self.assertRaises(GarageError):partition_wheel_corners(synthetic_four(),measured,'rim')

    def test_child_identity_collision_duplicate_known_ids_and_part_budget_rejected(self):
        part=synthetic_four()
        for ids in ([part.id,'test__wheel_FL'],[part.id,part.id],[str(i) for i in range(MAX_PARTS-3)],'test'):
            with self.subTest(count=len(ids)),self.assertRaises(GarageError):
                partition_wheel_corners(part,inspection(),'rim',known_ids=ids)
        ids=[part.id]+[str(i) for i in range(MAX_PARTS-4)]
        children,_=partition_wheel_corners(part,inspection(),'rim',known_ids=ids)
        self.assertEqual(len(ids)-1+len(children),MAX_PARTS)

    def test_saved_face_duplicates_omissions_order_and_out_of_range_rejected(self):
        part=synthetic_four();_,original=partition_wheel_corners(part,inspection(),'brake_rotor')
        for kind in ('duplicate_inside','duplicate_across','missing','out_of_range','boolean','reordered'):
            with self.subTest(kind=kind):
                receipt=copy.deepcopy(original);groups=receipt['groups'];indices=groups[0]['source_face_indices']
                if kind=='duplicate_inside':indices[1]=indices[0]
                elif kind=='duplicate_across':groups[1]['source_face_indices'][0]=indices[0]
                elif kind=='missing':indices.pop();groups[0]['triangles']-=1;groups[0]['vertices']=3
                elif kind=='out_of_range':indices[0]=len(part.indices)
                elif kind=='boolean':indices[0]=False
                else:indices.reverse()
                with self.assertRaises(GarageError):replay_wheel_partition(part,receipt)

    def test_saved_role_motion_labels_counts_hash_and_acceptance_claim_checked(self):
        part=synthetic_four();_,original=partition_wheel_corners(part,inspection(),'brake_caliper')
        for kind in ('schema','role','motion','corner','id','count','hash','confirmation','runtime'):
            with self.subTest(kind=kind):
                receipt=copy.deepcopy(original)
                if kind=='schema':receipt['schema']=True
                elif kind=='role':receipt['role']='body'
                elif kind=='motion':receipt['motion_intent']='wheel_spin'
                elif kind=='corner':receipt['groups'][0]['corner']='FR'
                elif kind=='id':receipt['groups'][0]['id']='renamed'
                elif kind=='count':receipt['groups'][0]['vertices']+=1
                elif kind=='hash':receipt['source_part']['geometry_sha256']='0'*64
                elif kind=='confirmation':receipt['role_confirmation']='name_guess'
                else:receipt['runtime_verified']=True
                with self.assertRaises(GarageError):replay_wheel_partition(part,receipt)

    def test_unreferenced_vertex_does_not_add_faces_or_block_valid_partition(self):
        part=synthetic_four()
        part.positions=np.vstack((part.positions,[0,0,0])).astype(np.float32)
        part.normals=np.vstack((part.normals,[0,1,0])).astype(np.float32)
        part.uv=np.vstack((part.uv,[0,0])).astype(np.float32)
        children,receipt=partition_wheel_corners(part,inspection(),'brake_rotor')
        self.assert_exact(part,children,receipt)
        self.assertEqual(sum(len(x.positions) for x in children),len(part.positions)-1)

    def test_invalid_triangle_or_vertex_arrays_fail_before_emitting_parts(self):
        for kind in ('empty','nonfinite','float_faces','negative','missing_vertex','normals_count','uv_count'):
            with self.subTest(kind=kind):
                part=synthetic_four()
                if kind=='empty':part.indices=part.indices[:0]
                elif kind=='nonfinite':part.positions[0,0]=float('inf')
                elif kind=='float_faces':part.indices=part.indices.astype(float)
                elif kind=='negative':part.indices=part.indices.astype(np.int64);part.indices[0,0]=-1
                elif kind=='missing_vertex':part.indices[0,0]=len(part.positions)
                elif kind=='normals_count':part.normals=part.normals[:-1]
                else:part.uv=part.uv[:-1]
                with self.assertRaises(GarageError):partition_wheel_corners(part,inspection(),'rim')

    def test_partition_and_replay_preserve_inputs_and_child_attributes_are_independent(self):
        part=synthetic_four();part.extras={'nested':{'untouched':[1,2]}};part.warnings=['source warning']
        before=copy.deepcopy(part);measured=inspection();old_measured=copy.deepcopy(measured)
        children,receipt=partition_wheel_corners(part,measured,'brake_caliper')
        old_receipt=copy.deepcopy(receipt)
        replay_wheel_partition(part,receipt)
        self.assertEqual(receipt,old_receipt);self.assertEqual(measured,old_measured)
        children[0].positions[0,0]+=1;children[0].extras['nested']['untouched'].append(3)
        children[0].warnings.append('child only')
        self.assertEqual(part.extras,before.extras);self.assertEqual(part.warnings,before.warnings)
        for name in ('positions','normals','uv','indices'):
            np.testing.assert_array_equal(getattr(part,name),getattr(before,name))


if __name__=='__main__':unittest.main()
