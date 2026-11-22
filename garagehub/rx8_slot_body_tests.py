"""Pure, offline regression tests; no GT6 install or original source required."""
from __future__ import annotations
import struct,tempfile,unittest
from pathlib import Path
import numpy as np
from .errors import GarageError
from .rx8_slot_body_lab import linked_program,experiment,audit_candidate_stream,SUFFIX,SUFFIX_HEAD
from .rx8_body_materials import RX8BodyMaterials
from .packed_streams_tests import make_packed_fixture
from .util import sha256


def make_candidate_stream_fixture():
    model,cpu=make_packed_fixture()
    model.extend(bytes(0x400-len(model)))
    struct.pack_into('>I',model,4,len(model))
    struct.pack_into('>I',model,0x6c,len(model))
    struct.pack_into('>I',model,0xdc,0x300)
    struct.pack_into('>4I',model,0x300,1,0,0x310,0)
    struct.pack_into('>3I',model,0x310,0x31c,1,0x320)
    model[0x31c:0x31e]=b'/\0'
    struct.pack_into('>IIHHIII',model,0x320,0,0,1,0,0x340,0,0)
    struct.pack_into('>II',model,0x340,len(cpu),len(cpu))
    # This candidate converted shape 0 to ordinary inline geometry, while
    # shape 1 still uses the donor's shared packed payloads.
    struct.pack_into('>h',model,0x140+2,0)
    struct.pack_into('>I',model,0x140+12,0x280)
    struct.pack_into('>I',model,0x140+24,0x290)
    struct.pack_into('>I',model,0x140+44,0)
    return model,cpu


class DummyModel:
    def __init__(self,lod,mid,malform=False):
        self.mid=mid
        cut=SUFFIX[lod,mid]
        self.bc=bytearray(b'X'*cut+SUFFIX_HEAD+b'\x2c\x36\x00\x00')
        if malform:self.bc[cut]=99
    def u32(self,at):
        if at==0x30:return 0x1000
        if at==0x1000+self.mid*48+24:return 0
        if at==0x1000+self.mid*48+28:return len(self.bc)
        raise AssertionError(f'Unknown model pointer {at}')
    def span(self,p,n):
        assert p==0 and n==len(self.bc)
        return bytes(self.bc)


class FakeMaterialsEditor:
    banks=2;bank_size=65
    def __init__(self):self.images={};self.materials=[]
    def texture_keys(self,template,bank):
        return [('diffuseMapSampler',5 if bank==0 else 6),('ambientMapSampler',5 if bank==0 else 6)]
    def image(self,img,label):
        key=(img.size,img.tobytes());self.images.setdefault(key,len(self.images)+1)
        return self.images[key]
    def clone_material(self,template,label,bindings):
        self.materials.append((template,tuple(sorted(bindings.items()))))
        return len(self.materials)+64
    def fvf(self,template):return template


class RX8SlotBodyTests(unittest.TestCase):
    def test_serialized_candidate_original_stream_mismatches_are_reported(self):
        model,stream=make_candidate_stream_fixture()
        report=audit_candidate_stream(model,stream,[0],variant='inline_with_original_stream')
        self.assertEqual(report['selected_slots_with_issues'],[0])
        self.assertEqual(report['selected_issue_count'],3)
        self.assertFalse(report['model_binding_consistent'])
        self.assertEqual(report['candidate_body_sha256'],sha256(model))
        self.assertEqual(report['candidate_stream_sha256'],sha256(stream))
        self.assertFalse(report['installable'])
    def test_serialized_cleared_sidecar_removes_only_reported_binding_mismatches(self):
        model,original=make_candidate_stream_fixture()
        cleared=bytearray(original)
        struct.pack_into('>5I',cleared,32+12,0,0,0,0,0)
        report=audit_candidate_stream(model,cleared,[0],variant='inline_with_cleared_sidecar')
        self.assertEqual(report['selected_issue_count'],0)
        self.assertEqual(report['selected_slots_with_issues'],[])
        self.assertTrue(report['model_binding_consistent'])
        self.assertEqual(report['packed_binding_count'],1)
        self.assertEqual(report['binding_classes']['0'],'empty_binding')
        self.assertEqual(report['binding_classes']['1'],'packed_streamed')
        self.assertFalse(report['installable'])
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(report['payload_bytes_reclaimed'])
        self.assertFalse(report['payload_reclamation_allowed'])
    def test_candidate_audit_preserves_model_stream_and_shared_payload_bytes(self):
        model,stream=make_candidate_stream_fixture()
        before_model,before_stream=bytes(model),bytes(stream)
        report=audit_candidate_stream(model,stream,[0],variant='inline_with_original_stream')
        self.assertEqual(bytes(model),before_model)
        self.assertEqual(bytes(stream),before_stream)
        self.assertEqual(report['selected_to_retained_shared_targets'],[640,656,672,688])
    def test_mechanical_suffix_preserved_in_both_models_and_lods(self):
        for lod in ('hq','race'):
            for mid in (0,5):
                model=DummyModel(lod,mid)
                p,digest=linked_program(model,lod,mid,[0,1,2],[3,4])
                self.assertTrue(p.endswith(bytes(model.bc)[SUFFIX[lod,mid]:]))
                self.assertEqual(p.count(bytes([59,0,17 if mid==5 else 0])),1)
                if mid==5:self.assertTrue(p.startswith(bytes((59,96,17))))
                self.assertEqual(p[-1],0)
    def test_invalid_native_mechanical_prefix_blocks_export(self):
        with self.assertRaises(GarageError):linked_program(DummyModel('hq',0,True),'hq',0,[0],[])
    def test_slot_overflow_blocks_export(self):
        with self.assertRaises(GarageError):linked_program(DummyModel('race',0),'race',0,[40],[])
    def test_duplicate_draw_is_not_accepted(self):
        with self.assertRaises(GarageError):linked_program(DummyModel('hq',5),'hq',5,[1],[1])
    def test_not_installable_lab_rejects_wrong_source(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t);f=p/'different.glb';f.write_bytes(b'not F50')
            with self.assertRaises(GarageError):experiment(f,p,p/'out')
            self.assertFalse((p/'out').exists())
    def test_semantically_identical_flat_materials_share_native_slot(self):
        class S:
            materials=[{'id':0,'color':[.15,.2,.25,1.], 'base_texture':None,'metallic':.0,
                        'alpha_mode':'OPAQUE','transmission':0,'normal_map':None,'name':'A'},
                       {'id':1,'color':[.15,.2,.25,1.], 'base_texture':None,'metallic':.0,
                        'alpha_mode':'OPAQUE','transmission':0,'normal_map':None,'name':'B'},
                       {'id':2,'color':[.3,.2,.25,1.], 'base_texture':None,'metallic':.0,
                        'alpha_mode':'OPAQUE','transmission':0,'normal_map':None,'name':'C'}]
        e=FakeMaterialsEditor();r=RX8BodyMaterials(S(),e,'hq')
        x=r.recipe(0,'body');y=r.recipe(1,'body');z=r.recipe(2,'body')
        self.assertEqual(x['native_material'],y['native_material'])
        self.assertTrue(y['native_material_reused'])
        self.assertNotEqual(x['native_material'],z['native_material'])
        self.assertEqual(len(e.materials),2)
    def test_uv_image_double_reflection_equivalence(self):
        # Earlier manual F50 path exported V'=1-V, with vertically flipped
        # source image. The current native path uses raw V and raw source rows.
        # Both produce identical texel references; flipping ONLY the new
        # images would reintroduce reversed plate text and lamp atlases.
        h,w=16,8;rgba=np.arange(h*w*4,dtype=np.uint16).reshape(h,w,4)
        for y in range(h):
            orig=rgba[y];other=rgba[::-1][h-1-y]
            np.testing.assert_array_equal(orig,other)
        for v in np.linspace(.003,.997,500):
            old_y=min(h-1,int((1-v)*h));new_y=min(h-1,int(v*h))
            np.testing.assert_array_equal(rgba[new_y],rgba[::-1][old_y])
