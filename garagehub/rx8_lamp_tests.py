"""Offline native switch + source-light material regressions (no GT6 assets)."""
from __future__ import annotations
import unittest
import numpy as np
from PIL import Image
from .errors import GarageError
from .rx8_lamp_lab import state_program,emitter_recipe,emit_lamp_surfaces
from .rx8_slot_body_lab import linked_program
from .rx8_slot_body_tests import DummyModel
from .car_geometry import evaluate

class FakeEditor:
    banks=2
    def __init__(self):self.images=[];self.clones=[]
    def image(self,img,label):self.images.append(np.asarray(img));return len(self.images)+30
    def texture_keys(self,template,bank):
        return [('diffuseMapSampler',4),('emissionMapSampler',5)]
    def clone_material(self,template,label,bindings):
        self.clones.append((template,label,bindings));return 80+len(self.clones)
    def fvf(self,template):return 3

class FakeMaterials:
    def __init__(self):
        self.scene=type('S',(),{'materials':[{'base_texture':9}]})()
        self.editor=FakeEditor();self.lod='hq'
        self.textures=type('T',(),{'source_image':lambda self,mat:Image.new('RGBA',(4,4),(200,100,60,180))})()

class F50LampTests(unittest.TestCase):
    def lamp(self):
        return {'headlamp':[21,22],'running':[23],'brake':[24]}
    def test_all_four_native_state_combinations(self):
        for ctx in (0,17):
            p,t=state_program(self.lamp(),ctx)
            self.assertEqual(len(t),4)
            self.assertEqual(t['night0_brake0'],[])
            self.assertEqual(t['night1_brake0'],[21,22,23])
            self.assertEqual(t['night0_brake1'],[24])
            self.assertEqual(t['night1_brake1'],[21,22,24])
            self.assertEqual(evaluate(p+b'\0',True,False),[21,22,23])
    def test_brake_must_replace_running_not_double_it(self):
        p,t=state_program(self.lamp(),0)
        for key,val in t.items():
            self.assertFalse(23 in val and 24 in val,key)
    def test_missing_lamp_class_rejected(self):
        for name in ('headlamp','running','brake'):
            x=self.lamp();x[name]=[]
            with self.assertRaises(GarageError):state_program(x,0)
    def test_extra_lamp_class_rejected(self):
        x=self.lamp();x['unknown']=[12]
        with self.assertRaises(GarageError):state_program(x,0)
    def test_emission_uses_source_colours_and_alters_brightness(self):
        f=FakeMaterials();running=emitter_recipe(f,0,'running')
        brake=emitter_recipe(f,0,'brake')
        a,b=f.editor.images
        np.testing.assert_array_equal(a[0,0],np.array([60,30,18,255]))
        np.testing.assert_array_equal(b[0,0],np.array([200,100,60,255]))
        self.assertNotEqual(running['native_material'],brake['native_material'])
        self.assertEqual(running['fvf'],brake['fvf'])
        self.assertEqual(len(f.editor.clones),2)
    def test_invalid_emitter_kind_rejected(self):
        with self.assertRaises(GarageError):emitter_recipe(FakeMaterials(),0,'parking')
    def test_lamp_program_preserves_mechanical_suffix(self):
        for lod in ('hq','race'):
            for mid in (0,5):
                old=DummyModel(lod,mid)
                lp,_=state_program(self.lamp(),17 if mid==5 else 0)
                new,_=linked_program(old,lod,mid,[0,1],[2],lp)
                self.assertIn(lp,new)
                self.assertTrue(new.endswith(bytes(old.bc)[__import__('garagehub.rx8_slot_body_lab',fromlist=['SUFFIX']).SUFFIX[lod,mid]:]))
                self.assertLess(new.index(lp),new.index(bytes((59,2,17 if mid==5 else 0))))
    def test_original_body_without_lamps_stays_supported(self):
        old=DummyModel('hq',0)
        a,_=linked_program(old,'hq',0,[0],[1])
        b,_=linked_program(old,'hq',0,[0],[1],b'')
        self.assertEqual(a,b)
    def test_invented_rear_lenses_cannot_be_substituted(self):
        scene=type('S',(),{'parts':[]})()
        with self.assertRaises(GarageError):
            emit_lamp_surfaces(scene,{'parts':[]},None,None,'hq',None,None,21)
    def test_lamp_command_requires_bytes(self):
        with self.assertRaises(GarageError):
            linked_program(DummyModel('race',0),'race',0,[0],[1],None)

    def test_static_source_lenses_are_passive_only_with_explicit_native_switch(self):
        from .rx8_body_materials import RX8BodyMaterials,TEMPLATES
        class MaterialEditor(FakeEditor):
            bank_size=65
            def texture_keys(self,template,bank):
                if template==2:
                    return [('diffuseMapSampler',4),('ambientMapSampler',4)]
                return [('diffuseMapSampler',4),('emissionMapSampler',5)]
        source={'id':0,'color':[1.,.5,.5,1.],'base_texture':1,'metallic':1.,
                'alpha_mode':'OPAQUE','transmission':0.,'normal_map':False,'name':'source red lens',
                'emissive':[1.,1.,1.]}
        scene=type('S',(),{'materials':[source]})()
        for active in (False,True):
            e=MaterialEditor();f=RX8BodyMaterials(scene,e,'hq',passive_source_lenses=active)
            f.textures=type('Images',(),{'source_image':lambda self,material:Image.new('RGBA',(4,4),(180,20,25,255))})()
            rec=f.recipe(0,'taillight')
            self.assertEqual(rec['native_template'],TEMPLATES['hq']['body'] if active else TEMPLATES['hq']['lamp'])
            self.assertEqual(rec['passive_source_lens'],active)
            self.assertEqual(rec['base_lens_emission_sampler_disabled'],active)
            self.assertEqual('emissionMapSampler' in e.clones[0][2],not active)
            np.testing.assert_array_equal(e.images[0][0,0],np.array([180,20,25,255]))
            # Original source material attributes must never be rewritten.
            self.assertEqual(source['emissive'],[1.,1.,1.])

    def test_source_rear_lens_is_not_passive_in_legacy_lab(self):
        from .rx8_body_materials import RX8BodyMaterials,TEMPLATES
        class MaterialEditor(FakeEditor):
            bank_size=65
        e=MaterialEditor();scene=type('S',(),{'materials':[{'id':0,'color':[1,1,1,1],
            'base_texture':None,'metallic':0,'alpha_mode':'OPAQUE','transmission':0.,
            'normal_map':False,'name':'source headlamp'}]})()
        x=RX8BodyMaterials(scene,e,'hq')
        x.textures=type('T',(),{'source_image':lambda self,mat:Image.new('RGBA',(4,4),(155,155,155,255))})()
        self.assertEqual(x.recipe(0,'headlight')['native_template'],TEMPLATES['hq']['lamp'])

    def test_passive_lens_rejects_unsafe_transparency(self):
        from .rx8_body_materials import RX8BodyMaterials
        class MaterialEditor(FakeEditor):
            bank_size=65
            def texture_keys(self,template,bank):
                return [('diffuseMapSampler',4)] if template==2 else super().texture_keys(template,bank)
        scene=type('S',(),{'materials':[{'id':0,'color':[1,1,1,.4],'base_texture':1,
            'metallic':0,'alpha_mode':'BLEND','transmission':.5,'normal_map':False,'name':'lens'}]})()
        x=RX8BodyMaterials(scene,MaterialEditor(),'hq',passive_source_lenses=True)
        with self.assertRaises(GarageError):x.recipe(0,'headlight')

    def test_nonemissive_donor_family_required_in_both_material_banks(self):
        from .rx8_body_materials import RX8BodyMaterials
        class BadDonor(FakeEditor):
            bank_size=65
        with self.assertRaises(GarageError):
            RX8BodyMaterials(type('S',(),{'materials':[]})(),BadDonor(),'hq',passive_source_lenses=True)
