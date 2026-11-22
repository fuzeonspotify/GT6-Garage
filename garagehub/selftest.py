"""Offline synthetic regression suite; no user/game files or network required."""
from __future__ import annotations
import io,json,struct,tempfile,threading,unittest,zipfile
from pathlib import Path
import numpy as np
from .glb import GLB,trs
from .recognition import infer
from .native import MDL3,pack_normal
from .projects import Store
from .packages import source_package,export_package,install_files,read_sfo
from .util import sha256,hash_file,safe_rel,inside
from .errors import GarageError,Cancelled


def triangle_glb(change=None,raw_positions=None):
    pos=np.array(raw_positions if raw_positions is not None else [[0,0,0],[1,0,0],[0,1,0]],dtype='<f4')
    binary=pos.tobytes()+np.array([0,1,2],dtype='<u2').tobytes()
    doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':len(binary)}],'bufferViews':[{'buffer':0,'byteOffset':0,'byteLength':pos.nbytes},{'buffer':0,'byteOffset':pos.nbytes,'byteLength':6}],'accessors':[{'bufferView':0,'componentType':5126,'count':3,'type':'VEC3'},{'bufferView':1,'componentType':5123,'count':3,'type':'SCALAR'}],'meshes':[{'primitives':[{'attributes':{'POSITION':0},'indices':1}]}],'nodes':[{'mesh':0,'name':'body'}],'scenes':[{'nodes':[0]}],'scene':0}
    if change:change(doc)
    j=json.dumps(doc,separators=(',',':')).encode();j+=b' '*(-len(j)%4);binary+=b'\0'*(-len(binary)%4)
    return b'glTF'+struct.pack('<II',2,12+8+len(j)+8+len(binary))+struct.pack('<II',len(j),0x4e4f534a)+j+struct.pack('<II',len(binary),0x004e4942)+binary


def sfo(title='BCES01893',update='01.22'):
    fields={'TITLE_ID':title,'APP_VER':update};keys=bytearray();data=bytearray();entries=[]
    for key,value in fields.items():
        k=len(keys);keys+=key.encode()+b'\0';o=len(data);raw=value.encode()+b'\0';data+=raw;entries.append(struct.pack('<HHIII',k,0x0204,len(raw),len(raw),o))
    kp=20+16*len(entries);dp=kp+len(keys)
    return struct.pack('<4sIIII',b'\0PSF',0x101,kp,dp,len(entries))+b''.join(entries)+keys+data


def native_layout(shared=False):
    b=bytearray(1024);b[:4]=b'MDL3';struct.pack_into('>IIH',b,4,len(b),0,14);struct.pack_into('>I',b,0x6c,len(b));struct.pack_into('>H',b,0x18,2);struct.pack_into('>I',b,0x40,256)
    b[800:809]=b'position\0';b[820:825]=b'Flex\0'
    for i in range(2):
        p=256+i*120;fp=600 if shared else 600+i*16;struct.pack_into('>III',b,p,820,i,fp);b[p+24:p+26]=bytes((1,12));struct.pack_into('>I4B',b,fp,800,0,3,2,0)
    return bytes(b)


class CoreTests(unittest.TestCase):
    def setUp(self):self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
    def tearDown(self):self.tmp.cleanup()
    def test_glb_triangle_and_missing_normals(self):
        s=GLB(triangle_glb()).scene();self.assertEqual(s.triangles,1);np.testing.assert_allclose(s.parts[0].normals,[[0,0,1]]*3)
    def test_preview_keeps_all_small_source_triangles(self):
        from .preview import build_preview
        s=GLB(triangle_glb()).scene();raw=build_preview(s);n=struct.unpack_from('<I',raw,4)[0];meta=json.loads(raw[16:16+n]);self.assertEqual(meta['preview_triangles'],1);self.assertFalse(meta['meshes'][0]['surface_simplified']);self.assertIn('no triangle skipping',meta['geometry_policy'])
    def test_project_has_software_preview(self):
        store=Store(self.root/'projects');p=store.create(triangle_glb());self.assertEqual((store.folder(p['id'])/'software_preview.bin').read_bytes()[:4],b'GGV1')
    def test_header_length_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb()[:-1])
    def test_wrong_magic_rejected(self):
        b=triangle_glb()
        with self.assertRaises(GarageError):GLB(b'FAIL'+b[4:])
    def test_external_buffer_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(lambda j:j['buffers'][0].update(uri='https://example.invalid/x')))
    def test_external_image_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(lambda j:j.update(images=[{'uri':'../password.txt'}])))
    def test_draco_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(lambda j:j.update(extensionsRequired=['KHR_draco_mesh_compression'])))
    def test_skin_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(lambda j:j['nodes'][0].update(skin=0))).scene()
    def test_node_cycle_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(lambda j:j['nodes'][0].update(children=[0]))).scene()
    def test_accessor_range_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(lambda j:j['accessors'][0].update(count=4))).scene()
    def test_negative_accessor_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb()).accessor(-1)
    def test_nonfinite_positions_rejected(self):
        with self.assertRaises(GarageError):GLB(triangle_glb(raw_positions=[[float('nan'),0,0],[1,0,0],[0,1,0]])).scene()
    def test_negative_transform_keeps_front_face(self):
        s=GLB(triangle_glb(lambda j:j['nodes'][0].update(scale=[-1,1,1]))).scene();p=s.parts[0];i=p.indices[0];self.assertGreater(float(np.dot(np.cross(p.positions[i[1]]-p.positions[i[0]],p.positions[i[2]]-p.positions[i[0]]),p.normals[i].mean(0))),0)
    def test_translation(self):
        s=GLB(triangle_glb(lambda j:j['nodes'][0].update(translation=[2,3,4]))).scene();np.testing.assert_allclose(s.parts[0].positions[0],[2,3,4])
    def test_cancellation_before_parse(self):
        e=threading.Event();e.set()
        with self.assertRaises(Cancelled):GLB(triangle_glb()).scene(e)
    def test_matrix_and_trs_rejected(self):
        with self.assertRaises(GarageError):trs({'matrix':np.eye(4).ravel().tolist(),'scale':[1,1,1]})
    def test_native_unique_layouts_pass(self):self.assertTrue(MDL3(native_layout()).validate()['unique_pointer_owners'])
    def test_native_shared_fields_rejected(self):
        with self.assertRaises(GarageError) as e:MDL3(native_layout(True)).validate()
        self.assertEqual(e.exception.code,'shared_fvf_pointer')
    def test_native_bad_pointer_rejected(self):
        b=bytearray(native_layout());struct.pack_into('>I',b,264,10000)
        with self.assertRaises(GarageError):MDL3(bytes(b)).validate()
    def test_normal_packing_finite_and_length(self):
        for n in ([1,0,0],[0,1,0],[0,0,-1],[-1,-1,-1]):self.assertEqual(len(pack_normal(n)),4)
    def test_paths_reject_traversal(self):
        for p in ('../outside','a/../../b','/root','C:/Windows/a','a\\b'):
            with self.subTest(p=p):
                with self.assertRaises(GarageError):safe_rel(p)
    def test_project_immutable_source_and_roundtrip(self):
        raw=triangle_glb();s=Store(self.root/'projects');p=s.create(raw,'<car>.glb');pid=p['id'];part=p['inspection']['parts'][0]['id'];s.update(pid,{'overrides':{part:{'role':'paint','corner':None}},'approved_roles':True});z=self.root/'car.gt6car';s.export(pid,z);r=s.import_archive(z.read_bytes());self.assertNotEqual(r['id'],pid);self.assertEqual(r['inspection']['parts'][0]['role'],'paint');self.assertEqual((s.folder(pid)/'source.glb').read_bytes(),raw)
    def test_invalid_assignment_rejected(self):
        s=Store(self.root/'p');p=s.create(triangle_glb())
        with self.assertRaises(GarageError):s.update(p['id'],{'overrides':{'unknown':{'role':'rim','corner':'FL'}}})
    def test_project_traversal_archive_rejected(self):
        b=io.BytesIO()
        with zipfile.ZipFile(b,'w') as z:z.writestr('../oops','bad')
        with self.assertRaises(GarageError):Store(self.root/'p').import_archive(b.getvalue())
    def test_neutral_export_is_not_native(self):
        s=Store(self.root/'p');p=s.create(triangle_glb());out=self.root/'neutral.zip';r=s.neutral(p['id'],out)
        self.assertFalse(r['game_ready'])
        with zipfile.ZipFile(out) as z:self.assertEqual(json.loads(z.read('car.json'))['geometry_sha256'],sha256(z.read('geometry.bin')))
    def test_sfo_roundtrip(self):self.assertEqual(read_sfo(sfo())['APP_VER'],'01.22')
    def test_sfo_invalid(self):
        with self.assertRaises(GarageError):read_sfo(b'bad')
    def fixture_package(self):
        root=self.root/'pkg';patch=root/'PatchOut';(patch/'9/AA').mkdir(parents=True);(patch/'K').mkdir();(patch/'9/AA/BB').write_bytes(b'data-new');(patch/'K/4D').write_bytes(b'header-new')
        files=[dict(relative_path=f.relative_to(patch).as_posix(),bytes=f.stat().st_size,sha256=hash_file(f)) for f in patch.rglob('*') if f.is_file()]
        doc=dict(passed=True,source_roundtrip_match=True,candidate_id='synthetic',files=files,header_sha256=hash_file(patch/'K/4D'),source_virtual_assets={});(root/'package_receipt.json').write_text(json.dumps(doc));return root
    def test_export_header_last(self):
        root=self.fixture_package();out=self.root/'mod.zip';r=export_package(root,out)
        with zipfile.ZipFile(out) as z:
            self.assertEqual(z.namelist()[-1],'Install/K/4D')
            instructions=z.read('README_INSTALL.txt').decode()
            pc,ps3=instructions.split('PS3 over FTP')
            self.assertIn('RPCS3 on PC',pc)
            self.assertIn('PDIPFS/K/4D last',pc)
            self.assertIn('/dev_hdd0/game/'+r['manifest']['title_id']+'/USRDIR/PDIPFS/',ps3)
            self.assertIn('Upload Install/K/4D LAST',ps3)
            self.assertIn('complete matching backup',ps3)
        self.assertFalse(r['manifest']['physical_ps3_tested'])
    def test_changed_payload_rejected(self):
        root=self.fixture_package();(root/'PatchOut/K/4D').write_bytes(b'modified')
        with self.assertRaises(GarageError):source_package(root)
    def test_export_cancel_removes_partial(self):
        root=self.fixture_package();event=threading.Event();event.set();out=self.root/'mod.zip'
        with self.assertRaises(Cancelled):export_package(root,out,event)
        self.assertFalse(out.exists());self.assertFalse(out.with_name(out.name+'.partial').exists())
    def target(self):
        target=self.root/'game/USRDIR/PDIPFS';(target/'K').mkdir(parents=True);(target/'K/4D').write_bytes(b'header-old');(target.parent.parent/'PARAM.SFO').write_bytes(sfo());return target
    def test_install_success_and_order(self):
        root,m=source_package(self.fixture_package());target=self.target();order=[];r=install_files(root,m,target,self.root/'backup',expected_header=hash_file(target/'K/4D'),after_write=order.append)
        self.assertTrue(r['installed']);self.assertEqual(order[-1],'K/4D');self.assertEqual((target/'K/4D').read_bytes(),b'header-new');self.assertFalse(r['game_launched'])
    def test_install_interrupted_restores_original(self):
        root,m=source_package(self.fixture_package());target=self.target()
        def fail(path):raise OSError('simulated interrupted write')
        with self.assertRaises(OSError):install_files(root,m,target,self.root/'backup',expected_header=hash_file(target/'K/4D'),after_write=fail)
        self.assertEqual((target/'K/4D').read_bytes(),b'header-old');self.assertFalse((target/'9/AA/BB').exists());self.assertEqual(json.loads((self.root/'backup/journal.json').read_text())['state'],'rolled_back')
    def test_stale_install_hash_rejected(self):
        root,m=source_package(self.fixture_package());target=self.target()
        with self.assertRaises(GarageError):install_files(root,m,target,self.root/'backup',expected_header='0'*64)
        self.assertFalse((self.root/'backup').exists())
    def test_wrong_game_update_rejected(self):
        root,m=source_package(self.fixture_package());target=self.target();(target.parent.parent/'PARAM.SFO').write_bytes(sfo(update='01.05'))
        with self.assertRaises(GarageError):install_files(root,m,target,self.root/'backup',expected_header=hash_file(target/'K/4D'))
    def test_package_path_traversal_rejected(self):
        folder=self.fixture_package();p=folder/'package_receipt.json';j=json.loads(p.read_text());j['files'][0]['relative_path']='../../outside';p.write_text(json.dumps(j))
        with self.assertRaises(GarageError):source_package(folder)


class ServerTests(unittest.TestCase):
    def setUp(self):
        from .server import App,serve
        import sys
        self.tmp=tempfile.TemporaryDirectory();base=Path(getattr(sys,'_MEIPASS',Path(__file__).resolve().parent.parent))
        self.app=App(Path(self.tmp.name),base/'web',allow_dialogs=False);self.server=serve(self.app)
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
    def tearDown(self):
        self.server.shutdown();self.server.server_close();self.app.executor.shutdown(wait=True);self.tmp.cleanup()
    def request(self,path,headers=None):
        import http.client
        c=http.client.HTTPConnection('127.0.0.1',self.server.server_port,timeout=4);c.request('GET',path,headers=headers or {});r=c.getresponse();data=r.read();code=r.status;c.close();return code,data
    def test_requires_session_token(self):self.assertEqual(self.request('/api/state')[0],403)
    def test_valid_session_works(self):self.assertEqual(self.request('/api/state',{'X-Garage-Token':self.app.token})[0],200)
    def test_cross_origin_rejected(self):self.assertEqual(self.request('/api/state',{'X-Garage-Token':self.app.token,'Origin':'https://example.invalid'})[0],403)
    def test_host_rebinding_rejected(self):self.assertEqual(self.request('/api/state',{'X-Garage-Token':self.app.token,'Host':'attacker.invalid'})[0],403)
    def test_static_path_traversal_rejected(self):self.assertEqual(self.request('/../run.py')[0],404)
    def test_no_full_car_capability_claim(self):
        _,raw=self.request('/api/state',{'X-Garage-Token':self.app.token});d=json.loads(raw);self.assertFalse(d['capabilities']['full_car_conversion'])


def test_suite():
    from .car_tests import CarTests
    from .paint_tests import PaintTests
    from .surface_tests import SurfaceTests
    from .f50_adapter_tests import F50AdapterTests
    from .donor_profile_tests import DonorProfileTests
    from .rx8_wheel_tests import RX8WheelTests
    from .rx8_wheel_cli_tests import RX8WheelCLITests
    from .rx8_body_tests import RX8BodyTests
    from .rx8_slot_body_tests import RX8SlotBodyTests
    from .rx8_lamp_tests import F50LampTests
    from .rx8_stream_guard_tests import RX8StreamGuardTests
    from .rx8_stream_sidecar_tests import RX8SidecarTests
    from .glb_normalization_tests import GLBNormalizationTests
    from .model_input_tests import ModelInputTests
    from .car_name_tests import CarNameTests
    from .project_input_tests import ProjectInputTests
    from .specdb_tests import SpecDBTests
    from .scene_coordinate_tests import SceneCoordinateTests
    from .identity_pipeline_tests import IdentityPipelineTests
    from .engineering_history_tests import EngineeringHistoryTests
    from .wheel_fit_tests import WheelFitTests,RimWindingTests
    from .packed_streams_tests import PackedStreamTests
    from .body_streams_tests import BodyStreamAttachTests,NativeResourceReuseTests
    from .ordinary_streams_tests import OrdinaryStreamTests
    from .recognition_tests import RecognitionTests
    from .assembly_fit_tests import AssemblyFitTests
    from .target_geometry_tests import TargetGeometryTests
    from .fit_review_tests import FitReviewTests
    from .unit_calibration_tests import UnitCalibrationTests
    from .body_alignment_tests import BodyAlignmentTests
    from .mechanical_roles_tests import MechanicalRoleTests
    from .mechanical_parts_tests import MechanicalPartitionTests
    from .project_partition_tests import ProjectPartitionTests
    from .mechanical_policy_tests import MechanicalPolicyTests
    from .mechanical_native_tests import MechanicalNativeTests
    from .rx8_mechanical_tests import RX8MechanicalTests
    from .rx8_assembly_tests import RX8AssemblyTests
    from .runtime_comparison_tests import RuntimeComparisonTests
    from .import_readiness_tests import ImportReadinessTests
    from .wheel_discovery_tests import WheelDiscoveryTests
    from .project_discovery_tests import ProjectDiscoveryTests
    from .showroom_build_tests import ShowroomBuildTests
    from .body_fit_tests import BodyFitTests
    from .body_fit_preview_tests import BodyFitPreviewTests
    from .project_body_fit_tests import ProjectBodyFitTests
    from .template_shell_tests import TemplateShellTests
    from .template_shell_independent_tests import TemplateShellIndependentTests
    from .template_lights_tests import TemplateLightsTests
    from .template_shell_preview_tests import TemplateShellPreviewTests
    from .template_shell_project_tests import TemplateShellProjectTests
    from .surface_realization_tests import SurfaceRealizationTests
    from .car_texture_transform_tests import CarTextureTransformTests
    from .chassis_probe_tests import ChassisProbeTests
    from .native_wheel_contract_tests import NativeWheelContractTests
    from .chassis_probe_package_tests import ChassisProbePackageTests
    from .chassis_session_tests import ChassisSessionTests
    from .tyre_size_plan_tests import TyreSizePlanTests
    from .running_gear_tests import RunningGearTests
    from .shell_setup_tests import ShellSetupTests
    return unittest.TestSuite([unittest.defaultTestLoader.loadTestsFromTestCase(cls) for cls in (CoreTests,ServerTests,CarTests,PaintTests,SurfaceTests,F50AdapterTests,DonorProfileTests,RX8WheelTests,RX8WheelCLITests,RX8BodyTests,RX8SlotBodyTests,F50LampTests,RX8StreamGuardTests,RX8SidecarTests,GLBNormalizationTests,ModelInputTests,CarNameTests,ProjectInputTests,SpecDBTests,SceneCoordinateTests,IdentityPipelineTests,EngineeringHistoryTests,WheelFitTests,RimWindingTests,PackedStreamTests,BodyStreamAttachTests,NativeResourceReuseTests,OrdinaryStreamTests,RecognitionTests,AssemblyFitTests,TargetGeometryTests,FitReviewTests,UnitCalibrationTests,BodyAlignmentTests,MechanicalRoleTests,MechanicalPartitionTests,ProjectPartitionTests,MechanicalPolicyTests,MechanicalNativeTests,RX8MechanicalTests,RX8AssemblyTests,RuntimeComparisonTests,ImportReadinessTests,WheelDiscoveryTests,ProjectDiscoveryTests,ShowroomBuildTests,BodyFitTests,BodyFitPreviewTests,ProjectBodyFitTests,TemplateShellTests,TemplateShellIndependentTests,TemplateLightsTests,TemplateShellPreviewTests,TemplateShellProjectTests,SurfaceRealizationTests,CarTextureTransformTests,ChassisProbeTests,NativeWheelContractTests,ChassisProbePackageTests,ChassisSessionTests,TyreSizePlanTests,RunningGearTests,ShellSetupTests)])


def selftest():
    from .engineering_history import audit_engineering_history,collected_test_ids
    suite=test_suite();history=audit_engineering_history(collected_test_ids(suite))
    text=io.StringIO();r=unittest.TextTestRunner(stream=text,verbosity=2).run(suite)
    return {'passed':r.wasSuccessful() and history['passed'],'tests_run':r.testsRun,'failures':len(r.failures),'errors':len(r.errors),'details':text.getvalue(),'engineering_history':history,'scope':'Synthetic parsing, project, pointer ownership, export, transaction rollback, loopback security and engineering-history references. No game launched.'}


def run():
    r=selftest();print(r['details']);print(json.dumps({k:v for k,v in r.items() if k!='details'}));return 0 if r['passed'] else 1
if __name__=='__main__':raise SystemExit(run())
