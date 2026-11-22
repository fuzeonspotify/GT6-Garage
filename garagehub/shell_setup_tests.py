import copy
import tempfile
import unittest
from pathlib import Path

import numpy as np

from .engineering_history import resource_root
from .errors import GarageError
from .glb import GLB
from .projects import Store
from .recognition import infer
from .shell_setup import suggest_setup
from .surface_realization import native_finish_substitutions, opaque_two_sided_plan
from .template_lights_tests import bare_body_glb


class ShellSetupTests(unittest.TestCase):
    def test_stock_catalogue_error_names_the_bad_ftp_file_before_starting_packer(self):
        from unittest.mock import patch
        from .stock_setup import prepare_stock

        root = Path(self.temp.name)
        update = root/'PDIPFS'
        (update/'K').mkdir(parents=True)
        (update/'K/4D').write_bytes(b'index')
        (update/'9/QE').mkdir(parents=True)
        (update/'9/QE/0B').write_bytes(b'incomplete')
        with (patch('garagehub.stock_setup.SEED', {}),
              patch('garagehub.stock_setup.trusted_packer',side_effect=AssertionError('Unexpected native packer'))):
            with self.assertRaisesRegex(GarageError,'PDIPFS/9/QE/0B differs'):
                prepare_stock(root/'profile',update,None,None)
        self.assertFalse((root/'profile').exists())

    def test_stock_update_picker_needs_one_folder_and_no_disc_dialog(self):
        from unittest.mock import patch
        from .desktop import dialog
        from .server import App

        root = Path(self.temp.name)
        update = root / 'PDIPFS'
        (update / 'K').mkdir(parents=True)
        (update / 'K/4D').touch()
        app = App(root / 'profile', root)
        self.addCleanup(app.executor.shutdown)

        def run_picker(command, **kwargs):
            dialog(command[command.index('--dialog') + 1], command[-1])

        with (patch('tkinter.Tk'), patch('tkinter.filedialog.askdirectory', return_value=str(update)) as folders,
              patch('tkinter.filedialog.askopenfilename', side_effect=AssertionError('Unexpected disc dialog')),
              patch('garagehub.server.subprocess.run', side_effect=run_picker),
              patch.object(app, 'prepare_from_game', return_value={'prepared':True}) as prepare):
            job = app.choose('game-data')
            app.executor.shutdown(wait=True)
            self.assertEqual(app.job(job['id'])['result'], {'prepared':True})
            folders.assert_called_once()
            self.assertEqual(prepare.call_args.args[:2], (update.resolve(), None))

    def test_pack_retries_only_the_private_staging_rename(self):
        from unittest.mock import patch
        from .car_pack import pack_archive
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory)
            output=folder/'PatchOut'
            (folder/'pack.log').write_text(f"Access to the path '{output}_temp' is denied.\n at System.IO.FileSystem.MoveDirectory")
            error=GarageError('pack stopped','packer_failed')
            with patch('garagehub.car_pack.execute',side_effect=[error,None]) as run, patch('garagehub.car_pack.time.sleep'):
                pack_archive(None,folder/'seed',folder/'input',output,folder,None)
                self.assertEqual([c.args[3] for c in run.call_args_list],['pack.log','pack_retry_1.log'])
            (folder/'pack.log').write_text('Invalid input model')
            with patch('garagehub.car_pack.execute',side_effect=error) as run, self.assertRaises(GarageError):
                pack_archive(None,folder/'seed',folder/'input',output,folder,None)
            self.assertEqual(run.call_count,1)

    def test_game_setup_retries_a_locked_stock_archive(self):
        from unittest.mock import patch
        from .stock_setup import _unpack_owned
        with tempfile.TemporaryDirectory() as directory:
            folder=Path(directory);log=folder/'disc.log'
            log.write_text('The file is being used by another process.')
            locked=GarageError('Extraction stopped.','packer_failed')
            with patch('garagehub.stock_setup.execute',side_effect=[locked,None]) as execute, patch('garagehub.stock_setup.time.sleep'):
                _unpack_owned('packer',folder/'GT.VOL',['1'],folder,folder,'disc',folder,None)
            self.assertEqual(execute.call_count,2)
            log.write_text('Invalid disc archive')
            with patch('garagehub.stock_setup.execute',side_effect=locked):
                with self.assertRaisesRegex(GarageError,'stock-setup.log'):
                    _unpack_owned('packer',folder/'GT.VOL',['1'],folder,folder,'disc',folder,None)
            self.assertEqual((folder/'stock-setup.log').read_text(),'Invalid disc archive')

    def test_native_opaque_finish_can_replace_normal_detail_after_review(self):
        from .template_shell import _materials
        scene=GLB(bare_body_glb()).scene()
        part=scene.parts[0]
        source=scene.document['materials'][part.material]
        source['normalTexture']={'index':0}
        scene.materials[part.material].update(double_sided=True,normal_map=True)
        row={'id':part.id,'role':'body','material':part.material,'disposition':'emit'}
        with self.assertRaises(GarageError):_materials(scene,[row])
        _materials(scene,[dict(row,use_native_finish=True)])
        with self.assertRaises(GarageError):opaque_two_sided_plan(scene,infer(scene,'+Z',{part.id:row}))
        plan=opaque_two_sided_plan(scene,infer(scene,'+Z',{part.id:dict(row,use_native_finish=True)}))
        self.assertIn(part.id,plan['part_ids'])
        self.assertNotIn('normalTexture',native_finish_substitutions(dict(source,alphaMode='BLEND')))

    def test_animated_driver_only_writes_selected_cabin_targets(self):
        import hashlib
        import struct
        from unittest.mock import patch
        from .driver_fit import fit_driver, TARGETS
        raw=bytearray(17756);raw[:4]=b'CAR5'
        struct.pack_into('>II',raw,12,1,24);struct.pack_into('>I',raw,104,3200)
        for i in TARGETS.values():struct.pack_into('>3f',raw,3200+i*4,.35,.3+i/100,.16-i/100)
        allowed={3200+i*4+n for i in TARGETS.values() for n in range(12)}
        with patch('garagehub.driver_fit.NATIVE_BLOCK_SHA256',hashlib.sha256(raw[3200:3344]).hexdigest()):
            self.assertEqual(fit_driver(bytes(raw))[0],raw)
            config={'seat_metres':[-.28,.06,-.2],'reach':.82,'targets':{'left_foot':[-.4,.12,-.7]}}
            data,proof=fit_driver(bytes(raw),config)
            self.assertTrue(all(i in allowed for i,(a,b) in enumerate(zip(raw,data)) if a!=b))
            self.assertEqual(len(data),len(raw))
            np.testing.assert_allclose(proof['native_targets']['seat'],config['seat_metres'])
            np.testing.assert_allclose(proof['native_targets']['left_foot'],config['targets']['left_foot'])
            original=proof['reference_targets']; expected=np.array(config['seat_metres'])+.82*(np.array(original['steering'])-original['seat'])
            np.testing.assert_allclose(proof['native_targets']['steering'],expected)
        with self.assertRaisesRegex(GarageError,'rig differs'):fit_driver(bytes(raw),config)

    def test_driver_seat_is_saved_in_placement_and_rejects_invalid_values(self):
        from .template_shell import normalize_shell_settings
        from .driver_fit import normalize_driver
        self.assertNotIn('driver',normalize_shell_settings()['placement'])
        good={'seat_metres':[-.28,.06,-.2],'reach':.82}
        self.assertEqual(normalize_shell_settings({'placement':{'driver':good,'scale':1,'translation_metres':[0,0,0]}})['placement']['driver'],good)
        for bad in ({'reach':True},{'reach':1.5},{'seat_metres':[0,float('nan'),0]},{'targets':{'seat':[0,0,0]}},{'targets':{'steering':[4,0,0]}}):
            with self.assertRaises(GarageError):normalize_driver(dict(good,**bad))

    def test_body_loading_selects_largest_texture_edge_that_fits(self):
        from .car_builder import _next_texture_edge
        report = {'new_cut': 498304, 'gpu_tail_bytes_before': 425728,
                  'images': [{'width':512,'height':512,'format':136} for _ in range(10)]}
        self.assertEqual(_next_texture_edge(report,'race',None),64)
        self.assertEqual(_next_texture_edge(report,'race',64),32)

    def test_body_loading_refuses_oversized_metadata_without_export(self):
        from .car_builder import _next_texture_edge
        report = {'new_cut': 800000, 'gpu_tail_bytes_before': 425728,
                  'images': [{'width':4,'height':4,'format':134}]}
        with self.assertRaisesRegex(GarageError,'loading budget'):
            _next_texture_edge(report,'race',4)

    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.store = Store(Path(self.temp.name))
        self.sedan = (resource_root()/'docs/evidence/assembly_ui/assets/kenney_sedan.glb').read_bytes()

    def project(self, raw):
        pid = self.store.create(raw)['id']
        self.store.update(pid, {'assembly_mode':'template_shell', 'installation':{'mode':'new_entry'}})
        return pid

    def test_sedan_first_pass_and_numeric_mismatch(self):
        pid = self.project(self.sedan)
        original = (self.store.folder(pid)/'project.json').read_bytes()
        plan = self.store.shell_setup(pid, 'wheelbase')
        self.assertEqual(original, (self.store.folder(pid)/'project.json').read_bytes())
        self.assertEqual(len(plan['exclusions']), 4)
        config = plan['changes']['template_shell_settings']
        self.assertAlmostEqual(config['placement']['scale'], 1.93181828, places=6)
        self.assertAlmostEqual(plan['fit']['corners'][0]['supplied_over_source'], .555, places=2)
        self.assertEqual(config['lighting']['mode'], 'supplied')
        self.assertEqual(plan['notes'], [])
        self.assertGreater(config['lighting']['front_height_ratio'], .2)
        self.store.shell_setup(pid,'wheelbase',apply=True,token=plan['token'])
        result = self.store.update(pid, {'approved_roles':True, 'template_inheritance_approved':True})
        self.assertEqual(result['installation']['mode'], 'new_entry')
        review = self.store.review_template_shell(pid)
        self.assertFalse(review['ready_for_candidate'])
        self.assertEqual(review['wheel_check']['status'], 'incompatible')
        self.assertIn('85–115%', review['blockers'][0]['message'])
        self.assertEqual(review['triangle_dispositions'], {'emit':704,'excluded':1328})
        for lod in ('hq','race'):
            self.assertTrue(review['lamp_mount_checks'][lod]['passed'])
        self.assertEqual((self.store.folder(pid)/'source.glb').read_bytes(), self.sedan)

    def test_saved_edits_invalidate_setup_and_preserve_size(self):
        pid = self.project(self.sedan)
        plan = self.store.shell_setup(pid,'keep_size')
        self.assertEqual(plan['changes']['template_shell_settings']['placement']['scale'],1.)
        self.store.update(pid,{'forward':'-Z'})
        with self.assertRaisesRegex(GarageError,'project changed'):
            self.store.shell_setup(pid,'keep_size',apply=True,token=plan['token'])
        fresh = self.store.shell_setup(pid,'keep_size')
        self.assertNotEqual(plan['token'], fresh['token'])
        self.assertEqual(self.store.summary(pid)['overrides'],{})

    def test_player_import_replaces_wheels_and_accepts_a_different_stock_size(self):
        from .assembly_settings import saved_shell_decision
        p = self.store.create(self.sedan, 'Sedan.glb', stock_replacement=True)
        settings = p['template_shell_settings']
        self.assertEqual(settings['running_gear']['tyre_size'], 'game')
        self.assertEqual(len(settings['dispositions']), 4)
        self.assertTrue(all(p['overrides'][pid]['role']=='ignore' for pid in settings['dispositions']))
        self.assertEqual((self.store.folder(p['id'])/'source.glb').read_bytes(), self.sedan)
        setup = self.store.shell_setup(p['id'],'wheelbase')
        self.assertEqual(setup['wheel_check']['status'],'compatible')
        self.assertLess(setup['wheel_check']['corners'][0]['diameter_ratio'],.60)
        with self.assertRaises(GarageError):
            saved_shell_decision(p)
        legacy = self.store.create(self.sedan)
        self.assertEqual(self.store.player_project(legacy['id'])['template_shell_settings'],settings)
        settings['running_gear'].pop('tyre_size')
        self.store.update(p['id'], {'template_shell_settings':settings})
        self.assertEqual(self.store.shell_setup(p['id'],'wheelbase')['wheel_check']['status'],'incompatible')

    def test_scale_search_finds_a_narrow_interval_between_coarse_guesses(self):
        from unittest.mock import patch
        from types import SimpleNamespace
        from .running_gear import resolve_running_gear, CORNERS
        from .shell_setup import _find_placement
        gear=resolve_running_gear({'mode':'checked_stock'})
        wheels={}
        for index,corner in enumerate(CORNERS):
            ref=gear['corners'][corner]
            ratio=1.15*.984 if index<2 else .85*.986
            wheels[corner]={'parts':[corner],'center':np.array(ref['center_metres']),
                            'diameter':2*ref['tyre_radius_metres']/ratio,'rim_only':False}
        placement={'scale':1.,'translation_metres':[0.,0.,0.]}
        folder=Path(self.temp.name)
        (folder/'hq').mkdir()
        (folder/'hq/wheel').write_bytes(b'fixture')
        with patch('garagehub.template_shell_preview.decode_template_wheels',return_value=([],{})), \
             patch('garagehub.shell_setup.sample_native_faces',return_value={}), \
             patch('garagehub.shell_setup.visibility_for_body',return_value={
                 c:{k:{'fraction':1.} for k in ('rim','tyre')} for c in CORNERS}):
            result=_find_placement(SimpleNamespace(parts=[]),[],np.eye(3),wheels,gear,placement,folder)
        self.assertTrue(result['matched'])
        self.assertTrue(.984-1e-10 <= placement['scale'] <= .986+1e-10)

    def test_saved_wheel_default_keeps_exact_position_and_excludes_the_previous_driver(self):
        from .shell_setup import stock_defaults
        setup={'running_gear':{'mode':'checked_stock','tyre_size':'game','wheel_set':'supra_factory'},
               'placement':{'scale':.88,'translation_metres':[.02,.05,-.18],'flip_front':False,
                            'driver':{'seat_metres':[-.28,.06,-.2],'reach':.82}}}
        defaults=stock_defaults(setup)
        p=self.store.create(self.sedan,stock_replacement=True,wheel_defaults=defaults)
        placement=p['template_shell_settings']['placement']
        self.assertEqual(placement,defaults['placement'])
        self.assertNotIn('driver',placement)
        self.assertEqual(p['stock_placement_mode'],'saved_placement')
        result=self.store.shell_setup(p['id'],'saved_placement')
        self.assertEqual(result['changes']['template_shell_settings']['placement'],placement)
        self.assertEqual(result['wheel_check']['status'],'incompatible')

    def test_grouped_rotating_wheels_are_discarded_only_if_whole_animated_subtree_is_removed(self):
        from .template_shell import _wheel_animations_only
        scene=GLB(self.sedan).scene()
        project={'id':'car','assembly_mode':'template_shell','forward':'+Z','inspection':infer(scene,'+Z')}
        removed={x['id'] for x in suggest_setup(scene,project,'wheelbase')['exclusions']}
        wheels=[p for p in scene.parts if p.id in removed]
        body=next(p for p in scene.parts if p.id not in removed)
        joined=copy.deepcopy(wheels[0]); joined.name='Combined Wheels'; joined.id='combined'
        joined.positions=np.concatenate([p.positions for p in wheels])
        offsets=np.cumsum([0]+[len(p.positions) for p in wheels[:-1]])
        joined.indices=np.concatenate([p.indices+offset for p,offset in zip(wheels,offsets)])
        scene.parts=[body,joined]
        project['inspection']=infer(scene,'+Z')
        self.assertEqual([x['id'] for x in suggest_setup(scene,project,'wheelbase')['exclusions']],['combined'])
        scene.document['animations']=[{'channels':[{'target':{'node':joined.node,'path':'rotation'}}]}]
        project.update(overrides={'combined':{'role':'ignore'}},template_shell_settings={
            'dispositions':{'combined':{'disposition':'excluded','reason':'Stock wheels'}}})
        self.assertTrue(_wheel_animations_only(scene,project))
        scene.document['animations'][0]['channels'][0]['target']['node']=body.node
        self.assertFalse(_wheel_animations_only(scene,project))
        joined.indices=np.vstack([joined.indices,[0,offsets[1],offsets[1]+1]])
        project['inspection']=infer(scene,'+Z')
        self.assertIsNone(suggest_setup(scene,project,'wheelbase')['fit'])

    def test_checked_source_picks_are_corner_specific_and_do_not_override_size(self):
        from .running_gear import normalize_running_gear
        pid = self.project(self.sedan)
        plan = self.store.shell_setup(pid, 'wheelbase')
        ids = {row['corner']: row['parts'][0] for row in plan['fit']['corners']}
        def choose(picks):
            self.store.update(pid, {'template_shell_settings':{'running_gear':{'mode':'checked_stock','source_tyres':picks}}})
            return self.store.shell_setup(pid, 'wheelbase')['wheel_check']
        self.assertEqual(choose(ids)['status'], 'incompatible')
        self.assertEqual(choose({'FL':ids['RR']})['status'], 'unmeasured')
        with self.assertRaises(GarageError):
            normalize_running_gear({'mode':'checked_stock','source_tyres':{'FL':'a','FR':'a'}})

    def test_checked_bare_shell_stays_unmeasured_and_cannot_be_approved(self):
        from .template_shell import approve_shell
        pid = self.project(bare_body_glb())
        settings = {'running_gear':{'mode':'checked_stock'},'lighting':{'mode':'supplied'}}
        self.store.update(pid, {'template_shell_settings':settings,'approved_roles':True,
                                'template_inheritance_approved':True})
        review = self.store.review_template_shell(pid)
        self.assertEqual(review['wheel_check']['status'], 'unmeasured')
        self.assertFalse(review['ready_for_candidate'])
        self.assertFalse(review['preview_available'])
        scene, project = self.store.build_inputs(pid)
        with self.assertRaisesRegex(GarageError, 'Four outer tyres'):
            approve_shell(scene, project, review['placement'], review['dispositions'], review['review_digest'],
                          True, review['lighting'], review['running_gear'])

    def test_body_in_front_of_either_wheel_face_blocks_only_the_near_side(self):
        from .wheel_check import visibility_for_body
        from .running_gear import resolve_running_gear
        gear = resolve_running_gear()
        samples = {}
        for corner, ref in gear['corners'].items():
            x, y, z = ref['center_metres']
            samples[corner] = {role:(np.array([[y,z]]), np.array([abs(x)])) for role in ('rim','tyre')}
        x,y,z = gear['corners']['FL']['center_metres']
        body = {'id':'arch', 'positions':np.array([[x-.2,y-.1,z-.1],[x-.2,y+.1,z-.1],[x-.2,y,z+.1]]),
                'faces':np.array([[0,1,2]])}
        hit = visibility_for_body([body],gear,samples)
        self.assertEqual(hit['FL']['rim']['occluders'],[{'part':'arch','samples':1}])
        self.assertEqual(hit['FL']['tyre']['visible'],0)
        for corner in ('FR','RL','RR'):
            self.assertEqual(hit[corner]['rim']['fraction'],1.)
        body['positions'][:,0] += .4
        self.assertEqual(visibility_for_body([body],gear,samples)['FL']['rim']['fraction'],1.)

    def test_bare_body_preserves_placement_and_open_body_reports_missing_mounts(self):
        for opened in (False,True):
            pid=self.project(bare_body_glb(open_ends=opened))
            placement={'scale':.82,'translation_metres':[0,.11,.16],'flip_front':True}
            self.store.update(pid,{'template_shell_settings':{'placement':placement}})
            plan=self.store.shell_setup(pid,'wheelbase')
            self.assertIsNone(plan['fit'])
            self.assertEqual(plan['changes']['template_shell_settings']['placement'],placement)
            self.assertEqual(plan['exclusions'],[])
            self.assertEqual(any('No pair' in line for line in plan['notes']),opened)

    def test_round_anonymous_and_named_reverse_axis(self):
        scene=GLB(self.sedan).scene()
        for part in scene.parts:
            part.positions = part.positions * [-1,1,-1]
        self.assertEqual(infer(scene)['forward'],'-Z')
        self.assertEqual(infer(scene,'+Z')['forward'],'+Z')
        for part in scene.parts:
            part.name='Mesh '+part.id
        project={'id':'model', 'assembly_mode':'template_shell','forward':'-Z','inspection':infer(scene,'-Z')}
        result=suggest_setup(scene,project,'wheelbase')
        self.assertEqual(len(result['exclusions']),4)
        self.assertAlmostEqual(result['fit']['source_wheelbase_metres'],1.32,places=4)

    def test_native_optics_are_explicit_role_scoped_and_keep_source_material(self):
        scene=GLB(bare_body_glb()).scene()
        part=scene.parts[0]
        material=scene.materials[part.material]
        material.update(double_sided=True,emissive=[1.,.5,.2])
        before=copy.deepcopy(material)
        ordinary=infer(scene,'+Z',{part.id:{'role':'headlight'}})
        with self.assertRaises(GarageError):
            opaque_two_sided_plan(scene,ordinary)
        reviewed=infer(scene,'+Z',{part.id:{'role':'headlight','use_native_optics':True}})
        plan=opaque_two_sided_plan(scene,reviewed)
        self.assertEqual(plan['part_ids'],[])
        self.assertEqual(plan['native_exterior_optics'][0]['source_emissive'],before['emissive'])
        self.assertEqual(material,before)
        with self.assertRaises(GarageError):
            infer(scene,'+Z',{part.id:{'role':'body','use_native_optics':True}})

    def test_grouped_brakes_replaced_only_when_every_face_belongs_to_a_wheel(self):
        scene = GLB(self.sedan).scene()
        project = {'id':'car','assembly_mode':'template_shell','forward':'+Z',
                   'inspection':infer(scene,'+Z')}
        wheel_ids = {row['id'] for row in suggest_setup(scene, project, 'wheelbase')['exclusions']}
        wheels = [p for p in scene.parts if p.id in wheel_ids]
        component = copy.deepcopy(wheels[0])
        component.id, component.name, component.extras = 'brakes', 'brake discs', {}
        component.material = len(scene.materials)
        scene.materials.append(dict(scene.materials[wheels[0].material], id=component.material, name='disc metal'))
        vertices, faces, offset = [], [], 0
        for part in wheels:
            vertices.append(part.center + .5*(part.positions-part.center))
            faces.append(part.indices + offset)
            offset += len(part.positions)
        component.positions = np.concatenate(vertices)
        component.indices = np.concatenate(faces)
        scene.parts.append(component)

        def proposed(overrides=None):
            project['overrides'] = overrides or {}
            project['inspection'] = infer(scene, '+Z', project['overrides'])
            return {row['id'] for row in suggest_setup(scene, project, 'wheelbase')['exclusions']}

        self.assertIn('brakes', proposed())
        self.assertNotIn('brakes', proposed({'brakes':{'role':'body'}}))
        original = component.indices
        component.indices = np.vstack([original, [0, len(vertices[0]), len(vertices[0])+1]])
        self.assertNotIn('brakes', proposed())
        component.indices = original
        component.positions[original[0,0]] += [0, 1, 0]
        self.assertNotIn('brakes', proposed())

    def test_source_finish_loss_requires_review_and_keeps_unsupported_maps_blocked(self):
        from .template_shell import _materials
        scene = GLB(bare_body_glb()).scene()
        part = scene.parts[0]
        source = scene.document['materials'][part.material]
        source.update(emissiveFactor=[1.,.5,0.], emissiveTexture={'index':0},
                      extensions={'KHR_materials_specular':{'specularFactor':.4}})
        scene.materials[part.material].update(double_sided=True, emissive=[1.,.5,0.])
        row = {'id':part.id,'role':'body', 'material':part.material, 'disposition':'emit'}
        with self.assertRaises(GarageError):
            _materials(scene, [row])
        self.assertEqual(native_finish_substitutions(source),
                         ['emissiveFactor','emissiveTexture','KHR_materials_specular'])
        reviewed = dict(row,use_native_finish=True)
        _materials(scene,[reviewed])
        records=opaque_two_sided_plan(scene, infer(scene,'+Z',{part.id:reviewed}))['parts']
        self.assertEqual(records[0]['source_emissive'],[1.,.5,0.])
        source['extensions']['KHR_materials_specular']['specularTexture']={'index':1}
        with self.assertRaisesRegex(GarageError,'KHR_materials_specular'):
            _materials(scene, [reviewed])
