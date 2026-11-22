"""Offline paint/trim regression cases; no owned game data."""
from __future__ import annotations
import unittest,copy
import numpy as np
from .glb import Scene,Part,unit_normals
from .recognition import infer


def scene_case(rough=.05,metal=.5,color=.8,texture=None,second_rough=.5):
    faces=np.array([[0,2,1],[0,3,2],[4,5,6],[4,6,7],[0,1,5],[0,5,4],[2,3,7],[2,7,6],[0,4,7],[0,7,3],[1,2,6],[1,6,5]])
    parts=[];materials=[]
    for i,scale in enumerate(([.95,.6,2.3],[.85,.5,2.1])):
        p=np.array([[-1,-1,-1],[1,-1,-1],[1,1,-1],[-1,1,-1],[-1,-1,1],[1,-1,1],[1,1,1],[-1,1,1]],float)*scale
        parts.append(Part('p'+str(i),'Object_'+str(i),i,0,i,p,unit_normals(p,faces),np.zeros((8,2)),faces))
        materials.append({'id':i,'name':'Material.'+str(i),'color':[.02,.02,.02,1] if i==0 else [color]*3+[1],'metallic':0 if i==0 else metal,'roughness':second_rough if i==0 else rough,'alpha_mode':'OPAQUE','transmission':0.,'base_texture':None if i==0 else texture,'double_sided':False,'emissive':[0,0,0],'normal_map':False})
    return Scene(parts,materials,{},b'','0'*64,[])


class PaintTests(unittest.TestCase):
    def test_largest_black_trim_is_not_paint(self):
        r=infer(scene_case());self.assertEqual([p['id'] for p in r['parts'] if p['role']=='paint'],['p1']);self.assertEqual(r['paint_proposal']['selected_material'],1)
    def test_black_gloss_paint_is_not_rejected_by_color(self):
        r=infer(scene_case(color=.005));self.assertEqual(r['parts'][1]['role'],'paint')
    def test_full_chrome_not_automatically_paint(self):
        self.assertFalse(any(p['role']=='paint' for p in infer(scene_case(metal=1))['parts']))
    def test_textured_source_not_silently_replaced_by_factory_paint(self):
        self.assertFalse(any(p['role']=='paint' for p in infer(scene_case(texture=0))['parts']))
    def test_transmission_not_paint(self):
        s=scene_case();s.materials[1]['transmission']=.8;self.assertNotEqual(infer(s)['parts'][1]['role'],'paint')
    def test_two_smooth_shells_require_review(self):
        r=infer(scene_case(second_rough=.05));self.assertEqual(r['paint_proposal']['status'],'ambiguous');self.assertFalse(any(p['role']=='paint' for p in r['parts']));self.assertTrue(any(i['code']=='paint_ambiguous' for i in r['issues']))
    def test_explicit_body_tag_is_preserved(self):
        s=scene_case();s.parts[1].extras['gt6_role']='body';self.assertEqual(infer(s)['parts'][1]['role'],'body')
    def test_named_matte_paint_preserved(self):
        s=scene_case(rough=.8);s.parts[1].name='car_paint';r=infer(s);self.assertEqual(r['parts'][1]['role'],'paint');self.assertEqual(r['paint_proposal']['status'],'authored_or_saved')
    def test_user_override_has_priority(self):
        r=infer(scene_case(),overrides={'p0':{'role':'paint'},'p1':{'role':'body'}});self.assertEqual([p['role'] for p in r['parts']],['paint','body'])
    def test_explicit_body_assignment_resolves_ambiguity(self):
        r=infer(scene_case(second_rough=.05),overrides={'p0':{'role':'body'}});self.assertEqual(r['parts'][0]['role'],'body');self.assertEqual(r['parts'][1]['role'],'paint')
    def test_material_order_independent(self):
        s=scene_case();s.parts.reverse();r=infer(s);self.assertEqual([p['id'] for p in r['parts'] if p['role']=='paint'],['p1'])
    def test_source_geometry_unchanged(self):
        s=scene_case();before=[p.positions.copy() for p in s.parts];infer(s)
        for p,b in zip(s.parts,before):np.testing.assert_array_equal(p.positions,b)
