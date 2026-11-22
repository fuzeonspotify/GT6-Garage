"""Offline source-independent wheel shader/animation safety regressions."""
from __future__ import annotations
import copy,struct,unittest
import numpy as np
from types import SimpleNamespace
from .errors import GarageError
from .native_editor import Editor
from .native import MDL3
from .glb import Part,Scene
from .recognition import CORNERS,axes
from .rx8_wheel_adapter import (wheel_program_calls,insert_emblem_low_speed,
    source_rim_records,shadow_reference_geometry,REFERENCE_MOUNTS)
from .wheel_mounts import rigid_axle_mount

def donor_program(ci):
    sid=3*ci;ctx=2*ci+1
    return bytes((5,0,0,2,0,4,0,7,9,11,59,sid,ctx,9,6,59,sid+1,ctx,59,sid+2,ctx,0))


def rim_fixture(forward='+Z'):
    """Source dimensions deliberately unlike the native shadow envelope."""
    left,up,front=axes(forward);basis=np.array([left,up,front])
    parts=[];records=[];centers={};radii={}
    for ci,corner in enumerate(CORNERS):
        center=np.array([.83 if corner[1]=='L' else -.83,.34,
                         1.30 if corner[0]=='F' else -1.30])@basis
        centers[corner]=center.tolist();radii[corner]=.34
        tyre=np.array([[x,y,z] for x in (-.15,.15) for y in (-.34,.34) for z in (-.34,.34)])
        positions=tyre@basis+center
        pid='tyre_'+corner
        parts.append(Part(pid,pid,ci,0,0,positions,np.tile(up,(8,1)),np.zeros((8,2)),
                          np.array([[0,1,2],[3,4,5]],dtype=np.uint32)))
        records.append({'id':pid,'role':'tyre','corner':corner})
        for sid,material in (('n41p0',0),('n42p0',1)):
            local=np.array([[.11,0,0],[.10,.24,0],[.12,0,.24],[.11,-.24,0]])
            if material==1:local[:,1:]*=.12
            pid=sid+'__'+corner
            parts.append(Part(pid,pid,ci,0,material,local@basis+center,
                              np.tile(left,(4,1)),np.array([[0.,0.],[1.,0.],[1.,1.],[0.,1.]]),
                              np.array([[0,1,2],[0,2,3]],dtype=np.uint32)))
            records.append({'id':pid,'role':'rim','corner':corner})
    scene=Scene(parts,[{'base_texture':None},{'base_texture':0}],{},b'synthetic','a'*64,[])
    inspection={'forward':forward,'wheel_centers':centers,'wheel_radii':radii,'parts':records}
    return scene,inspection


def shadow_fixture(material_name='ShadowCBaseEnv1_NoDirt',position_stream=0):
    """A real serialized direct-mesh fixture with a shadow material label."""
    b=bytearray(4096);b[:4]=b'MDL3';struct.pack_into('>IIH',b,4,len(b),0,14)
    struct.pack_into('>I',b,0x6c,len(b));struct.pack_into('>H',b,0x14,8)
    struct.pack_into('>H',b,0x18,1);struct.pack_into('>I',b,0x38,256)
    struct.pack_into('>I',b,0x40,640)
    for at,name in ((1000,'position'),(1012,'normal'),(1024,'Flex'),(1040,material_name)):
        raw=name.encode()+b'\0';b[at:at+len(raw)]=raw
    struct.pack_into('>III',b,640,1024,1,800);b[664:666]=bytes((2,16))
    struct.pack_into('>I4B',b,800,1000,0,3,2,position_stream)
    struct.pack_into('>I4B',b,808,1012,12,1,6,0)
    points=[(-.04,-.25,0),(.04,.25,0),(.04,0,.25),(-.04,0,-.25)]
    for i,p in enumerate(points):struct.pack_into('>3fI',b,1280+i*16,*p,0)
    struct.pack_into('>6H',b,1400,0,1,2,0,2,3)
    for sid in range(8):
        at=256+sid*48;struct.pack_into('>h',b,at+2,-1)
        if sid>=4:
            struct.pack_into('>Hhh',b,at,0x1809,0,4)
            struct.pack_into('>II',b,at+8,4,1280);struct.pack_into('>II',b,at+20,6,1400)
            struct.pack_into('>H',b,at+38,2)
    struct.pack_into('>I',b,1536+4*52,1040)
    model=MDL3(bytes(b));model.validate()
    return SimpleNamespace(m=model,material_ptr=1536,bank_size=5,
                           texture_keys=lambda material:[],shader_key=lambda material:('Flex',1))

class RX8WheelTests(unittest.TestCase):
    def test_shadow_reference_is_not_a_physical_tyre_or_scale_target(self):
        editor=shadow_fixture();before=editor.m.data
        rows=shadow_reference_geometry(editor,'hq')
        self.assertEqual([row['shape'] for row in rows],[4,5,6,7])
        for row in rows:
            self.assertEqual(row['role'],'shadow')
            self.assertEqual(row['material_name'],'ShadowCBaseEnv1_NoDirt')
            self.assertAlmostEqual(row['radial_aabb_radius'],.25)
            self.assertAlmostEqual(row['axial_width'],.08)
            self.assertFalse(row['physical_tyre_measurement'])
            self.assertFalse(row['used_for_source_scale'])
        self.assertEqual(editor.m.data,before)
    def test_nonshadow_material_cannot_satisfy_shadow_reference_contract(self):
        with self.assertRaises(GarageError):shadow_reference_geometry(shadow_fixture('PhysicalTyre'),'hq')
    def test_shadow_reference_checks_actual_shader_and_sampler_role(self):
        editor=shadow_fixture();editor.shader_key=lambda material:('Flex',0)
        with self.assertRaises(GarageError):shadow_reference_geometry(editor,'hq')
        editor=shadow_fixture();editor.texture_keys=lambda material:[('diffuseMapSampler',0)]
        with self.assertRaises(GarageError):shadow_reference_geometry(editor,'hq')
    def test_shadow_position_requires_reviewed_direct_vertex_stream(self):
        with self.assertRaises(GarageError):
            shadow_reference_geometry(shadow_fixture(position_stream=1),'hq')
    def test_source_rim_dimensions_preserved_at_all_four_corners(self):
        scene,inspection=rim_fixture();original=copy.deepcopy(scene)
        for corner in CORNERS:
            records,report=source_rim_records(scene,inspection,corner)
            self.assertEqual(report['radial_scale'],1.)
            self.assertEqual(report['axial_scale'],1.)
            self.assertIsNone(report['donor_tyre_radius_m'])
            self.assertIsNone(report['donor_tyre_width_m'])
            self.assertEqual(report['physical_tyre_dimensions_status'],'unverified')
            for record in records:
                part=next(p for p in scene.parts if p.id==record['id'])
                a=part.positions[:,None,:]-part.positions[None,:,:]
                b=record['points'][:,None,:]-record['points'][None,:,:]
                np.testing.assert_allclose(np.linalg.norm(a,axis=2),np.linalg.norm(b,axis=2),rtol=0,atol=1e-14)
                np.testing.assert_array_equal(record['faces'],part.indices)
                emitted=np.ndarray((len(part.positions),3),dtype='>f4',buffer=record['vertex'],strides=(24,4))
                np.testing.assert_allclose(emitted,record['points'],rtol=0,atol=2e-8)
        for a,b in zip(scene.parts,original.parts):
            np.testing.assert_array_equal(a.positions,b.positions)
            np.testing.assert_array_equal(a.normals,b.normals)
            np.testing.assert_array_equal(a.indices,b.indices)
    def test_mount_keeps_spin_axis_and_reports_body_height_length_mismatch(self):
        _,inspection=rim_fixture()
        for corner in CORNERS:
            center,matrix,offset,report=rigid_axle_mount(inspection,corner,REFERENCE_MOUNTS[corner])
            self.assertEqual(offset[1:].tolist(),[0.,0.])
            np.testing.assert_allclose(matrix.T@matrix,np.eye(3),rtol=0,atol=1e-15)
            self.assertAlmostEqual(np.linalg.det(matrix),1.)
            mismatch=report['source_body_minus_fitted_center_metres']
            self.assertAlmostEqual(mismatch[0],0.)
            self.assertGreater(abs(mismatch[1]),.1)
            self.assertGreater(abs(mismatch[2]),.04)
            self.assertFalse(report['body_alignment_applied'])
            self.assertFalse(report['suspension_or_physics_changed'])
            self.assertFalse(report['mount_pose_is_acceptance'])
            # Changing a spin angle keeps every point on the local X axis
            # fixed. The source centre is on that axis after conversion.
            spin=np.array([[1.,0,0],[0,0,-1],[0,1,0]])
            local=(center-center)@matrix.T+offset
            np.testing.assert_array_equal(spin@local,local)
    def test_rigid_mount_supports_all_declared_source_forward_axes(self):
        baseline=None
        for forward in ('+Z','-Z','+X','-X'):
            scene,inspection=rim_fixture(forward)
            actual={corner:source_rim_records(scene,inspection,corner)[0] for corner in CORNERS}
            if baseline is None:baseline=actual
            for corner in CORNERS:
                for a,b in zip(actual[corner],baseline[corner]):
                    np.testing.assert_allclose(a['points'],b['points'],atol=1e-15,rtol=0)
                    self.assertEqual(a['index'],b['index'])
    def test_scaled_sheared_reflected_or_nonfinite_mount_rejected(self):
        _,inspection=rim_fixture();base=np.array(REFERENCE_MOUNTS['FL'])
        for change in ('scale','shear','reflect','nan'):
            matrix=base.copy()
            if change=='scale':matrix[:,:3]*=.75
            elif change=='shear':matrix[0,1]+=.1
            elif change=='reflect':matrix[:,0]*=-1
            else:matrix[0,3]=float('nan')
            with self.subTest(change=change),self.assertRaises(GarageError):
                rigid_axle_mount(inspection,'FL',matrix)
    def test_incomplete_or_nonfinite_source_centres_rejected(self):
        _,inspection=rim_fixture()
        del inspection['wheel_centers']['RR']
        with self.assertRaises(GarageError):rigid_axle_mount(inspection,'FL',REFERENCE_MOUNTS['FL'])
        _,inspection=rim_fixture();inspection['wheel_centers']['FL'][0]=float('nan')
        with self.assertRaises(GarageError):rigid_axle_mount(inspection,'FL',REFERENCE_MOUNTS['FL'])
    def test_four_wheels_native_state_table(self):
        for ci in range(4):
            original=donor_program(ci)
            self.assertEqual(wheel_program_calls(original,0),[(3*ci,2*ci+1)])
            self.assertEqual(wheel_program_calls(original,1),[(3*ci+1,2*ci+1),(3*ci+2,2*ci+1)])
    def test_rim_emblem_moves_with_native_wheel_and_hides_in_blur(self):
        for ci in range(4):
            p=donor_program(ci)
            new=insert_emblem_low_speed(p,3*ci,16+ci,2*ci+1)
            self.assertEqual(len(new),25)
            self.assertEqual(wheel_program_calls(new,0),[(3*ci,2*ci+1),(16+ci,2*ci+1)])
            self.assertEqual(wheel_program_calls(new,1),wheel_program_calls(p,1))
    def test_emblem_insertion_preserves_out_of_range_selector_fallback(self):
        # Decode the original selector's fall-through separately from the
        # production helper, whose public evaluator accepts only states 0/1.
        # A fallback must still reach End without drawing a blur component.
        def fallback(program):
            self.assertEqual(program[:4],bytes((5,0,0,2)))
            at=4+2*program[3]
            self.assertEqual(program[at],9)
            target=at+2+program[at+1]
            calls=[];at=target
            while at<len(program):
                if program[at]==0:return target,calls
                self.assertEqual(program[at],59)
                self.assertLess(at+2,len(program))
                calls.append(tuple(program[at+1:at+3]));at+=3
            self.fail('Native fallback does not reach End.')
        for ci in range(4):
            with self.subTest(corner=ci):
                old=donor_program(ci)
                new=insert_emblem_low_speed(old,3*ci,16+ci,2*ci+1)
                self.assertEqual(fallback(old),(len(old)-1,[]))
                self.assertEqual(fallback(new),(len(new)-1,[]))
    def test_emblem_insertion_preserves_exact_case_draw_bytes(self):
        for ci in range(4):
            with self.subTest(corner=ci):
                old=donor_program(ci)
                new=insert_emblem_low_speed(old,3*ci,16+ci,2*ci+1)
                starts=[at+2+struct.unpack_from('>H',new,at)[0] for at in (4,6)]
                self.assertEqual(new[starts[0]:starts[0]+3],old[10:13])
                self.assertEqual(new[starts[0]+3:starts[0]+6],bytes((59,16+ci,2*ci+1)))
                self.assertEqual(new[starts[1]:-1],old[15:-1])
    def test_changed_donor_program_cannot_be_rewritten(self):
        b=bytearray(donor_program(0));b[9]=12
        with self.assertRaises(GarageError):insert_emblem_low_speed(bytes(b),0,16,1)
    def test_unexpected_speed_branch_context_rejected(self):
        with self.assertRaises(GarageError):insert_emblem_low_speed(donor_program(0),0,16,0)
    def test_unsupported_wheel_opcode_rejected(self):
        with self.assertRaises(GarageError):wheel_program_calls(b'\xff\0',0)
    def test_unterminated_program_rejected(self):
        with self.assertRaises(GarageError):wheel_program_calls(b'\x3b\0\0',0)
    def test_invalid_speed_selector_rejected(self):
        with self.assertRaises(GarageError):wheel_program_calls(donor_program(0),2)
    def test_legitimate_empty_native_sampler_key_table(self):
        class M:
            @staticmethod
            def u16(p):return 0
            @staticmethod
            def u32(p):return 0
        o=SimpleNamespace(m=M(),material_ptr=1000,bank_size=5,banks=1,key_ptr=5600,key_count=3)
        self.assertEqual(Editor.texture_keys(o,4),[])
    def test_nonempty_native_key_must_stay_in_table(self):
        class M:
            @staticmethod
            def u16(p):return 1
            @staticmethod
            def u32(p):return 0
        o=SimpleNamespace(m=M(),material_ptr=1000,bank_size=5,banks=1,key_ptr=5600,key_count=3)
        with self.assertRaises(GarageError):Editor.texture_keys(o,0)
    def test_out_of_range_native_material_rejected(self):
        o=SimpleNamespace(bank_size=5,banks=1,m=None)
        with self.assertRaises(GarageError):Editor.texture_keys(o,-1)
        with self.assertRaises(GarageError):Editor.texture_keys(o,5)
