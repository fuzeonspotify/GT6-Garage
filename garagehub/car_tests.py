"""Synthetic checks for the complete-exterior build contract; no game resources."""
from __future__ import annotations
import copy,io,json,struct,tempfile,threading,unittest
from pathlib import Path
from unittest.mock import patch
from types import SimpleNamespace
import numpy as np
from PIL import Image
from .errors import GarageError,Cancelled
from .car_geometry import alignment,batches,switch,evaluate,check_cancel
from .car_profiles import source_recipe,KNOWN_SOURCE,KNOWN_LAMPS,validate_template
from .car_builder import plan,fit_projectors
from .car_materials import Materials,srgb
from .car_pack import listing,trusted_packer,execute,finish_zip
from .projects import Store
from .glb import GLB
from .selftest import triangle_glb


def fitted_inspection():
    return {'forward':'+Z','wheelbase':2.55,'track':1.536523,'wheel_centers':{'FL':[.7682615,0,1.275],'FR':[-.7682615,0,1.275],'RL':[.7682615,0,-1.275],'RR':[-.7682615,0,-1.275]},'parts':[{'id':x,'role':'rim','corner':x,'material':0} for x in ('FL','FR','RL','RR')],'role_counts':{'rim':4,'headlight':1,'taillight':1}}

def source_part(name='p'):
    return {'id':name,'positions':np.array([[0,0,0],[1,0,0],[0,1,0],[1,1,0]],dtype=float),'normals':np.array([[0,0,1]]*4,dtype=float),'uv':np.array([[0,0],[1,0],[0,1],[1,1]],dtype=float),'faces':np.array([[0,1,2],[1,3,2]])}


class CarTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_fit_hits_all_four_native_centres(self):
        m,t,r=alignment(fitted_inspection());self.assertLess(r['maximum_wheel_centre_error_metres'],1e-6);self.assertGreater(np.linalg.det(m),0)
    def test_fit_missing_tyre_rejected(self):
        a=fitted_inspection();a['wheel_centers'].pop('RR')
        with self.assertRaises(GarageError):alignment(a)
    def test_fit_large_track_mismatch_rejected(self):
        a=fitted_inspection();a['track']=3
        with self.assertRaises(GarageError):alignment(a)
    def test_fit_bad_axle_cluster_rejected(self):
        a=fitted_inspection();a['wheel_centers']['FL'][2]+=.4
        with self.assertRaises(GarageError):alignment(a)
    def test_fit_zero_dimensions_rejected(self):
        a=fitted_inspection();a['wheelbase']=0
        with self.assertRaises(GarageError):alignment(a)
    def test_lamp_truth_table(self):
        call=lambda ids:b''.join(bytes((59,i,0)) for i in ids)
        program=switch(7,b'',call([105,106]))+switch(0,switch(7,b'',call([107])),call([108,109]))
        for night,brake,want in [(0,0,[]),(0,1,[108,109]),(1,0,[105,106,107]),(1,1,[105,106,108,109])]:
            with self.subTest(night=night,brake=brake):self.assertEqual(evaluate(program,night,brake),want)
    def test_oversized_lamp_branch_rejected(self):
        with self.assertRaises(GarageError):switch(7,bytes(255),b'')
    def test_unknown_lamp_selector_rejected(self):
        with self.assertRaises(GarageError):evaluate(switch(4,b'',b''),0,0)
    def test_unknown_lamp_opcode_rejected(self):
        with self.assertRaises(GarageError):evaluate(b'\xff',0,0)
    def test_batch_keeps_every_triangle(self):
        p=source_part();out=list(batches([p],1));self.assertEqual(len(out),2);self.assertEqual(sum(len(x[1])//6 for x in out),2)
        for vb,ib,positions,ids in out:self.assertEqual(len(vb),len(positions)*24);self.assertLess(np.frombuffer(ib,dtype='>u2').max(),len(positions));self.assertEqual(ids,['p'])
    def test_batch_does_not_merge_different_part_vertices(self):
        a,b=source_part('a'),source_part('b');out=list(batches([a,b]));self.assertEqual(len(out[0][2]),8);self.assertEqual(out[0][3],['a','b'])
    def test_batch_native_uv_override_preserved(self):
        a=source_part();a['native_uv']=np.tile([.25,.75],(4,1));vb,_,_,_=next(batches([a]));self.assertEqual(struct.unpack_from('>ff',vb,16),(.25,.75))
    def test_empty_batch_has_no_draw(self):self.assertEqual(list(batches([])),[])
    def test_unknown_source_does_not_get_known_recipe(self):
        self.assertEqual(source_recipe(SimpleNamespace(digest='0'*64,parts=[])),{})
    def test_known_source_must_have_expected_part_ids(self):
        with self.assertRaises(GarageError):source_recipe(SimpleNamespace(digest=KNOWN_SOURCE,parts=[]))
    def test_source_recipe_returns_private_copy(self):
        scene=SimpleNamespace(digest=KNOWN_SOURCE,parts=[SimpleNamespace(id=x,material=-1) for x in KNOWN_LAMPS]+[SimpleNamespace(id='paint'+str(i),material=13) for i in range(7)]+[SimpleNamespace(id='trim'+str(i),material=12) for i in range(34)]);r=source_recipe(scene);r['n250p0']['role']='ignore';self.assertEqual(source_recipe(scene)['n250p0']['role'],'headlight')
    def test_unknown_owned_template_rejected(self):
        with self.assertRaises(GarageError):validate_template(self.root)
    def test_plan_needs_role_and_inheritance_review(self):
        scene=SimpleNamespace(digest='a'*64,parts=[],triangles=0,materials=[{'base_texture':None}]);p={'inspection':fitted_inspection(),'approved_roles':False,'template_inheritance_approved':False}
        with patch('garagehub.car_builder.validate_template',return_value={'root':str(self.root)}):r=plan(scene,p,self.root)
        self.assertFalse(r['ready']);self.assertEqual(len(r['actions']),3);self.assertFalse(r['body_fit_status']['approved']);self.assertFalse(r['runtime_verified'])
    def test_plan_needs_light_roles(self):
        a=fitted_inspection();a['role_counts']['headlight']=0;scene=SimpleNamespace(digest='a'*64,parts=[],triangles=0,materials=[{'base_texture':None}])
        with patch('garagehub.car_builder.validate_template',return_value={'root':str(self.root)}):r=plan(scene,{'inspection':a,'approved_roles':True,'template_inheritance_approved':True},self.root)
        self.assertFalse(r['ready']);self.assertIn('headlamp',r['actions'][0])
    def test_plan_does_not_silently_change_real_wheelbase(self):
        scene=SimpleNamespace(digest='a'*64,parts=[],triangles=0,materials=[{'base_texture':None}]);p={'inspection':fitted_inspection(),'approved_roles':True,'template_inheritance_approved':True,'wheelbase_metres':3.2}
        with patch('garagehub.car_builder.validate_template',return_value={'root':str(self.root)}):r=plan(scene,p,self.root)
        self.assertFalse(r['ready']);self.assertIn('wheelbase',r['actions'][0])
    def test_textured_native_rim_requires_reviewed_profile(self):
        scene=SimpleNamespace(digest='a'*64,parts=[],triangles=0,materials=[{'base_texture':4}])
        p={'inspection':fitted_inspection(),'approved_roles':True,'template_inheritance_approved':True}
        with patch('garagehub.car_builder.validate_template',return_value={'root':str(self.root)}):
            result=plan(scene,p,self.root)
        self.assertFalse(result['ready'])
        self.assertTrue(any('textured rim' in x.lower() for x in result['actions']))

    def test_f50_not_exported_into_supra_visual_slot(self):
        from .f50_wheel_adapter import F50_SOURCE_SHA256
        scene=SimpleNamespace(digest=F50_SOURCE_SHA256,parts=[],triangles=0,materials=[{'base_texture':None}])
        p={'inspection':fitted_inspection(),'approved_roles':True,'template_inheritance_approved':True}
        with patch('garagehub.car_builder.validate_template',return_value={'root':str(self.root)}):
            result=plan(scene,p,self.root)
        self.assertFalse(result['ready'])
        self.assertTrue(any('F50' in x and 'Supra slot' in x for x in result['actions']))

    def test_consent_persists_in_project_archive(self):
        store=Store(self.root/'p');p=store.create(triangle_glb());self.assertFalse(p['template_inheritance_approved']);p=store.update(p['id'],{'template_inheritance_approved':True});out=self.root/'x.gt6car';store.export(p['id'],out);again=store.import_archive(out.read_bytes());self.assertTrue(again['template_inheritance_approved'])
    def test_cancel_before_native_tool_spawn(self):
        event=threading.Event();event.set()
        with patch('garagehub.car_pack.subprocess.Popen') as proc:
            with self.assertRaises(Cancelled):execute(self.root/'nothing.exe',[],self.root/'work','log',event)
        proc.assert_not_called();self.assertFalse((self.root/'work').exists())
    def test_unknown_executable_not_trusted(self):
        path=self.root/'tool.exe';path.write_bytes(b'MZ this is not the reviewed packer')
        with self.assertRaises(GarageError):trusted_packer(path)
    def test_listing_empty_rejected(self):
        f=self.root/'list.txt';f.write_text('not an index')
        with self.assertRaises(GarageError):listing(f)
    def test_listing_duplicate_rejected(self):
        f=self.root/'list.txt';line='car/a - Flags: Compressed | FileIndex: 1 (9/AA/BB)\n';f.write_text(line*2)
        with self.assertRaises(GarageError):listing(f)
    def test_listing_traversal_rejected(self):
        f=self.root/'list.txt';f.write_text('../a - Flags: Compressed | FileIndex: 1 (9/AA/BB)\n')
        with self.assertRaises(GarageError):listing(f)
    def test_listing_extracts_index(self):
        f=self.root/'list.txt';f.write_text('car/0004/0006/race/wheel - Flags: Compressed | FileIndex: 12 (9/AA/BB)\n');self.assertEqual(listing(f)['car/0004/0006/race/wheel']['index'],12)
    def test_finish_cancel_does_not_create_output(self):
        event=threading.Event();event.set();out=self.root/'test.zip'
        with self.assertRaises(Cancelled):finish_zip(self.root,out,event)
        self.assertFalse(out.exists())
    def test_projector_origin_only_translation(self):
        b=bytearray(1280);b[:4]=b'CAR5';struct.pack_into('>I',b,16,64);struct.pack_into('>II',b,88,6,128)
        for i in range(6):struct.pack_into('>3f',b,128+i*128+48,-.6 if i%2==0 else .6,.5,-2)
        prepared=[{'role':'headlight','positions':np.array([[-.7,.4,-2.1],[-.5,.6,-2],[.5,.4,-2.1],[.7,.6,-2]])}];out,changes=fit_projectors(bytes(b),prepared);allowed={128+i*128+off+j for i in range(6) for off in(48,84) for j in range(12)};self.assertTrue(all(a==c or i in allowed for i,(a,c) in enumerate(zip(b,out))));self.assertEqual(len(changes),6)
    def test_projector_missing_side_rejected(self):
        b=bytearray(1280);b[:4]=b'CAR5';struct.pack_into('>I',b,16,64);struct.pack_into('>II',b,88,6,128);struct.pack_into('>3f',b,176,-.6,.5,-2)
        with self.assertRaises(GarageError):fit_projectors(bytes(b),[{'role':'headlight','positions':np.array([[.5,.4,-2],[.7,.6,-2]])}])
    def test_srgb_conversion_clamps_and_linear_threshold(self):
        self.assertEqual(srgb([-1,0,1,2]).tolist(),[0,0,255,255]);self.assertEqual(int(srgb([.5])[0]),188)
    def test_material_without_texture_uses_source_factor(self):
        scene=GLB(triangle_glb()).scene();material=dict(scene.materials[0]);material.update(base_texture=None,color=[.5,0,1,.5]);im=Materials(scene,None).source_image(material);self.assertEqual(im.getpixel((0,0)),(188,0,255,128))
    def test_native_dialog_validated_paths_do_not_overwrite_cleanup_handle(self):
        from .server import App
        app=App(self.root/'app',self.root,allow_dialogs=True)
        def fake_dialog(command,**kwargs):Path(command[-1]).write_text(json.dumps({'path':str(self.root)}));return SimpleNamespace(returncode=0)
        try:
            with patch('garagehub.server.subprocess.run',side_effect=fake_dialog),patch('garagehub.server.check_baseline',return_value={'verified':True}),patch('garagehub.server.validate_template',return_value={}),patch('garagehub.server.trusted_packer',return_value={'verified':True}):
                self.assertTrue(app.choose('car-baseline')['profile']['verified']);self.assertTrue(app.choose('car-packer')['verified'])
            self.assertEqual(list((self.root/'app').glob('dialog_*.json')),[])
        finally:app.executor.shutdown(wait=True)

    def test_box_corner_order_matches_v029_exporter(self):
        from .native import corners
        lo=(-2.,-3.,-5.);hi=(7.,11.,13.)
        actual=np.frombuffer(corners([lo,hi]),dtype='>f4').reshape(8,3).tolist()
        self.assertEqual(actual,[[7.,-3.,13.],[7.,-3.,-5.],[7.,11.,-5.],[7.,11.,13.],[-2.,-3.,13.],[-2.,-3.,-5.],[-2.,11.,-5.],[-2.,11.,13.]])

    def test_box_face_has_perimeter_order_not_crossing_diagonals(self):
        from .native import corners
        p=np.frombuffer(corners([[-1,-2,-3],[1,2,3]]),dtype='>f4').reshape(8,3)
        for start in (0,4):
            q=p[start:start+4]
            self.assertTrue(np.all(q[:,0]==q[0,0]))
            for i in range(4):self.assertEqual(np.count_nonzero(q[(i+1)%4]-q[i]),1)

    def test_box_translated_coordinates_keep_native_order(self):
        from .native import corners
        d=np.array([13.,-8.,.5]);a=np.array([[-2.,-3.,-5.],[7.,11.,13.]])
        p=np.frombuffer(corners(a),dtype='>f4').reshape(8,3);q=np.frombuffer(corners(a+d),dtype='>f4').reshape(8,3)
        np.testing.assert_array_equal(q,p+d)

    def test_raw_body_span_roundtrip(self):
        from .body_streams import decode
        self.assertEqual(decode(b'native',6,False),b'native')

    def test_raw_body_span_wrong_length_rejected(self):
        from .body_streams import decode
        with self.assertRaises(GarageError):decode(b'native',7,False)

    def test_deflate_body_span_roundtrip(self):
        from .body_streams import decode
        import zlib
        source=b'geometry'*200;co=zlib.compressobj(wbits=-15);data=co.compress(source)+co.flush()
        self.assertEqual(decode(data,len(source),True),source)

    def test_zlib_body_span_roundtrip(self):
        from .body_streams import decode
        import zlib
        source=b'geometry'*200;self.assertEqual(decode(zlib.compress(source),len(source),True),source)

    def test_gzip_body_span_roundtrip(self):
        from .body_streams import decode
        import zlib
        source=b'geometry'*200;co=zlib.compressobj(wbits=31);data=co.compress(source)+co.flush()
        self.assertEqual(decode(data,len(source),True),source)

    def test_compressed_body_span_trailing_bytes_rejected(self):
        from .body_streams import decode
        import zlib
        with self.assertRaises(GarageError):decode(zlib.compress(b'geometry')+b'extra',8,True)
