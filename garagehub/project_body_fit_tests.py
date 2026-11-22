"""Portable fit decisions use current source bytes and the production transform."""
from __future__ import annotations

import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .body_fit import approve_body_fit, body_fit_review, resolve_body_fit
from .car_builder import plan
from .car_geometry import prepare_parts
from .errors import GarageError
from .glb import GLB
from .projects import Store
from .server import App
from .util import atomic_json, read_json
from .wheel_fit_tests import wheel_scene


def fitted_car_obj(scale=1.):
    """An unfamiliar artist source with explicit bodies, tyres, rims and lamps."""
    lines=[];offset=1
    def mesh(name,points,faces):
        nonlocal offset
        lines.append('o '+name)
        lines.extend('v '+' '.join(format(float(v)*scale,'.12g') for v in point) for point in points)
        lines.extend('f '+' '.join(str(offset+i) for i in face) for face in faces)
        offset+=len(points)
    for corner in ('FL','FR','RL','RR'):
        x=.7682615 if corner[1]=='L' else -.7682615
        z=1.275 if corner[0]=='F' else -1.275
        points=[[x+dx,dy,z+dz] for dx in (-.12,.12) for dy in (-.32,.32) for dz in (-.32,.32)]
        faces=[[0,2,3],[0,3,1],[4,5,7],[4,7,6],[0,1,5],[0,5,4],
               [2,6,7],[2,7,3],[0,4,6],[0,6,2],[1,3,7],[1,7,5]]
        mesh('tyre_'+corner,points,faces)
        mesh('rim_'+corner,[[x,0,z],[x,.1,z],[x,0,z+.1]],[[0,1,2]])
    mesh('body',[[0,.5,0],[.4,.5,0],[0,.8,.3]],[[0,1,2]])
    mesh('headlight',[[-.6,.3,1.7],[-.4,.3,1.7],[.6,.3,1.7],[.4,.4,1.7]],[[0,1,3],[1,2,3]])
    mesh('taillight',[[-.5,.3,-1.7],[.5,.3,-1.7],[0,.5,-1.7]],[[0,1,2]])
    return ('\n'.join(lines)+'\n').encode()


def material_only_variant(raw):
    """Keep valid GLB geometry intact while changing one imported material."""
    document=GLB(raw).doc
    document['materials'][0]['pbrMetallicRoughness']['baseColorFactor']=[.1,.2,.3,1.]
    metadata=json.dumps(document,separators=(',',':')).encode('utf-8')
    metadata+=b' '*(-len(metadata)%4)
    old_size=struct.unpack_from('<I',raw,12)[0]
    tail=raw[20+old_size:]
    return (struct.pack('<4sII',b'glTF',2,20+len(metadata)+len(tail))
            +struct.pack('<II',len(metadata),0x4e4f534a)+metadata+tail)


class ProjectBodyFitTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'projects')

    def tearDown(self):self.temp.cleanup()

    def project(self,scale=1.):
        p=self.store.create(fitted_car_obj(scale),'artist_model.obj')
        overrides={}
        for row in p['inspection']['parts']:
            name=row['name'];role=name.split('_')[0]
            overrides[row['id']]={'role':'paint' if role=='body' else role,
                                 'corner':name[-2:] if role in ('rim','tyre') else None}
        return self.store.update(p['id'],{'overrides':overrides,'forward':'+Z',
            'wheelbase_metres':2.55*scale,'installation':{'mode':'new_entry','replace_car_id':None},
            'approved_roles':True,'template_inheritance_approved':True})

    def files(self,pid):
        return {p.name:p.read_bytes() for p in self.store.folder(pid).iterdir() if p.is_file()}

    def approve(self,p,policy='adapt_body'):
        review=self.store.review_body_fit(p['id'],policy)
        self.assertTrue(review['available'],review.get('issue'))
        self.assertTrue(review['within_profile_limits'],review.get('issue'))
        result=self.store.approve_body_fit(p['id'],policy,review['review_digest'],True)
        return result,review

    def test_review_and_preview_are_read_only_then_explicit_choice_is_saved(self):
        p=self.project();pid=p['id'];before=self.files(pid)
        review=self.store.review_body_fit(pid,'keep_proportions')
        raw=self.store.body_fit_preview(pid,'keep_proportions',review['review_digest'])
        count=struct.unpack_from('<I',raw,4)[0];header=json.loads(raw[16:16+count])
        self.assertEqual(header['body_fit_digest'],review['review_digest'])
        self.assertEqual({x['id'] for x in review['preview_parts']},{x['id'] for x in header['meshes']})
        self.assertEqual(self.files(pid),before)
        for invalid in (False,None,1,'true'):
            with self.assertRaises(GarageError):
                self.store.approve_body_fit(pid,'keep_proportions',review['review_digest'],invalid)
            self.assertEqual(self.files(pid),before)
        p=self.store.approve_body_fit(pid,'keep_proportions',review['review_digest'],True)
        self.assertTrue(p['body_fit_status']['approved'])
        self.assertEqual(p['body_fit']['policy'],'keep_proportions')
        self.assertEqual(p['installation'],{'mode':'new_entry','replace_car_id':None})
        self.assertEqual({k:v for k,v in self.files(pid).items() if k!='project.json'},
                         {k:v for k,v in before.items() if k!='project.json'})

    def test_destination_change_rejects_cached_preview_and_previous_approval(self):
        p,review=self.approve(self.project());pid=p['id']
        p=self.store.update(pid,{'installation':{'mode':'replace','replace_car_id':82}})
        self.assertFalse(p['body_fit_status']['approved']);before=self.files(pid)
        with self.assertRaises(GarageError):self.store.body_fit_preview(pid,'adapt_body',review['review_digest'])
        with self.assertRaises(GarageError):self.store.approve_body_fit(pid,'adapt_body',review['review_digest'],True)
        self.assertEqual(self.files(pid),before)
        p,_=self.approve(p);self.assertTrue(p['body_fit_status']['approved'])

    def test_reference_or_source_units_change_invalidates_fit_without_mutating_source(self):
        for changes in ({'wheelbase_metres':2.56},{'metres_per_unit':1.001},{'forward':'-Z'}):
            with self.subTest(changes=changes):
                p,_=self.approve(self.project());source=(self.store.folder(p['id'])/'source.glb').read_bytes()
                result=self.store.update(p['id'],changes)
                self.assertFalse(result['body_fit_status']['approved'])
                self.assertEqual((self.store.folder(p['id'])/'source.glb').read_bytes(),source)

    def test_original_source_is_rechecked_even_when_preview_is_cached(self):
        p,review=self.approve(self.project());pid=p['id'];folder=self.store.folder(pid)
        source=(folder/'source.glb').read_bytes();replacement=bytearray(source);replacement[-1]^=1
        (folder/'source.glb').write_bytes(replacement)
        before=self.files(pid)
        with self.assertRaises(GarageError):self.store.body_fit_preview(pid,'adapt_body',review['review_digest'])
        with self.assertRaises(GarageError):self.store.approve_body_fit(pid,'adapt_body',review['review_digest'],True)
        self.assertEqual(self.files(pid),before)

    def test_cached_material_only_source_change_blocks_summary_export_and_app_build(self):
        p,_=self.approve(self.project());pid=p['id'];folder=self.store.folder(pid)
        cached=self.store.scene(pid)
        replacement=material_only_variant((folder/'source.glb').read_bytes())
        changed=GLB(replacement).scene()
        self.assertNotEqual(changed.digest,cached.digest)
        self.assertNotEqual(changed.materials,cached.materials)
        for old,new in zip(cached.parts,changed.parts):
            np.testing.assert_array_equal(old.positions,new.positions)
        (folder/'source.glb').write_bytes(replacement);before=self.files(pid)
        neutral=self.root/'must_not_export.zip'
        for operation in (lambda:self.store.scene(pid),lambda:self.store.summary(pid),
                          lambda:self.store.neutral(pid,neutral)):
            with self.assertRaises(GarageError) as rejected:operation()
            self.assertEqual(rejected.exception.code,'source_changed')
        self.assertFalse(neutral.exists())
        app=App(self.root/'app',self.root,allow_dialogs=False,development_authored=True);app.store=self.store
        app.config={'car_baseline':str(self.root/'baseline'),'car_packer':str(self.root/'packer')}
        try:
            with patch('garagehub.server.car_plan') as planner, \
                 patch('garagehub.server.build_car') as builder:
                with self.assertRaises(GarageError) as rejected:app.convert_car(pid,None)
                self.assertEqual(rejected.exception.code,'source_changed')
                planner.assert_not_called();builder.assert_not_called()
            self.assertFalse((app.root/'builds').exists())
            self.assertEqual(list(app.exports.iterdir()),[])
            self.assertEqual(self.files(pid),before)
        finally:app.executor.shutdown(wait=True)

    def test_build_snapshot_reinfers_current_inspection_for_default_conversion_plan(self):
        p,_=self.approve(self.project());pid=p['id'];cached=self.store.scene(pid)
        stale=copy.deepcopy(p['inspection']);stale['wheel_centers']['FL'][0]+=1.
        atomic_json(self.store.folder(pid)/'inspection.json',stale);before=self.files(pid)
        self.assertFalse(self.store.summary(pid)['body_fit_status']['approved'])
        scene,current=self.store.build_inputs(pid)
        self.assertIsNot(scene,cached)
        self.assertEqual(current['inspection'],p['inspection'])
        self.assertTrue(resolve_body_fit(scene,current)[2]['approved'])
        self.assertEqual(self.files(pid),before)
        app=App(self.root/'app',self.root,allow_dialogs=False,development_authored=True);app.store=self.store
        app.config={'car_baseline':str(self.root/'baseline')}
        try:
            with patch('garagehub.car_builder.validate_template',return_value={'root':'synthetic'}), \
                 patch('garagehub.server.check_baseline',return_value={}):
                result=app.conversion_plan(pid)
            self.assertTrue(result['body_fit_status']['approved'])
            self.assertEqual(result['fit']['body_fit']['review_digest'],p['body_fit']['review_digest'])
            self.assertEqual(self.files(pid),before)
        finally:app.executor.shutdown(wait=True)

    def test_app_conversion_reuses_one_fresh_snapshot_for_plan_and_builder(self):
        p,_=self.approve(self.project());pid=p['id']
        app=App(self.root/'app',self.root,allow_dialogs=False,development_authored=True);app.store=self.store
        app.config={'car_baseline':str(self.root/'baseline'),'car_packer':str(self.root/'packer')}
        destination={'ready':True,'blockers':[],'mode':'new_entry',
                     'display_name':'My separate car','car_id':999}
        try:
            with patch.object(self.store,'build_inputs',wraps=self.store.build_inputs) as snapshot, \
                 patch('garagehub.car_builder.validate_template',return_value={'root':'synthetic'}), \
                 patch('garagehub.server.check_baseline',return_value={}), \
                 patch('garagehub.server.trusted_packer',return_value={}), \
                 patch('garagehub.server.identity_plan',return_value=destination), \
                 patch('garagehub.server.car_plan',wraps=plan) as planner, \
                 patch('garagehub.server.build_car') as builder, \
                 patch('garagehub.server.route_candidate'), \
                 patch('garagehub.server.pack_candidate',return_value={'files':[]}), \
                 patch('garagehub.server.finish_zip',return_value={}):
                result=app.convert_car(pid,None)
                snapshot.assert_called_once_with(pid)
                planner.assert_called_once();builder.assert_called_once()
                self.assertIs(planner.call_args.args[0],builder.call_args.args[0])
                self.assertIs(planner.call_args.args[1],builder.call_args.args[1])
                self.assertEqual(builder.call_args.args[1]['inspection'],p['inspection'])
            self.assertEqual(result['installation']['mode'],'new_entry')
        finally:app.executor.shutdown(wait=True)

    def test_archive_restart_and_rename_retain_exact_choice_and_new_identity_intent(self):
        p,_=self.approve(self.project());pid=p['id'];choice=copy.deepcopy(p['body_fit'])
        p=self.store.update(pid,{'name':'My own new car','notes':'Artist notes'})
        self.assertTrue(p['body_fit_status']['approved'])
        output=self.root/'saved.gt6car';self.store.export(pid,output)
        fresh=Store(self.root/'reopened');q=fresh.import_archive(output.read_bytes())
        self.assertNotEqual(pid,q['id']);self.assertEqual(q['body_fit'],choice)
        self.assertTrue(q['body_fit_status']['approved']);self.assertEqual(q['installation'],p['installation'])
        again=Store(self.root/'reopened').summary(q['id'])
        self.assertTrue(again['body_fit_status']['approved'])
        self.assertEqual((fresh.folder(q['id'])/'original_input.bin').read_bytes(),fitted_car_obj())

    def test_general_settings_cannot_forge_an_approved_fit(self):
        p=self.project();before=self.files(p['id'])
        with self.assertRaises(GarageError):self.store.update(p['id'],{'body_fit':{'schema':1,'policy':'adapt_body','review_digest':'0'*64}})
        self.assertEqual(self.files(p['id']),before)

    def test_old_project_requires_explicit_fit_review_before_full_plan(self):
        p=self.project();self.assertFalse(p['body_fit_status']['approved'])
        self.assertIn('body_fit_review',[x['code'] for x in p['import_readiness']['build_review_tasks']])
        with patch('garagehub.car_builder.validate_template',return_value={'root':'synthetic'}):
            result=plan(self.store.scene(p['id']),p,self.root)
        self.assertFalse(result['ready'])
        p,_=self.approve(p)
        with patch('garagehub.car_builder.validate_template',return_value={'root':'synthetic'}):
            result=plan(self.store.scene(p['id']),p,self.root)
        self.assertTrue(result['ready'],result['actions'])

    def test_explicit_adaptation_resolves_wheelbase_stretch_gate_but_not_unit_mismatch(self):
        p=self.project(scale=1.08)
        self.assertIn('profile_wheelbase',[x['code'] for x in p['import_readiness']['capability_limits']])
        p,review=self.approve(p)
        self.assertNotIn('profile_wheelbase',[x['code'] for x in p['import_readiness']['capability_limits']])
        self.assertAlmostEqual(review['scale_xyz'][1],1/1.08,places=6)
        changed=self.store.update(p['id'],{'wheelbase_metres':2.55})
        self.assertFalse(changed['body_fit_status']['approved'])
        self.assertIn('source_units',[x['code'] for x in changed['import_readiness']['user_tasks']])

    def test_native_body_preparation_consumes_approved_keep_transform_and_normals(self):
        scene,inspection=wheel_scene(scale=.98)
        project={'inspection':inspection,'installation':{'mode':'new_entry','replace_car_id':None},
                 'wheelbase_metres':2.55*.98}
        review=body_fit_review(scene,project,'keep_proportions')
        project['body_fit']=approve_body_fit(scene,project,'keep_proportions',review['review_digest'],True)
        solution=resolve_body_fit(scene,project)
        # Lamp surfaces remain small and are never simplified by this converter.
        for material in scene.materials:material.update(color=[.5,.5,.5,1],metallic=0.,roughness=.5)
        before=[copy.deepcopy(part) for part in scene.parts]
        converted,_,fit=prepare_parts(scene,inspection,'hq',fit_solution=solution)
        originals={part.id:part for part in scene.parts};matrix,translation,_=solution
        self.assertEqual(fit['body_fit']['policy'],'keep_proportions')
        self.assertEqual(fit['uniform_scale'],1.)
        for part in converted:
            original=originals[part['id']]
            np.testing.assert_allclose(part['positions'],original.positions@matrix.T+translation,rtol=0,atol=1e-12)
            expected=original.normals@np.linalg.inv(matrix)
            expected/=np.linalg.norm(expected,axis=1)[:,None]
            np.testing.assert_allclose(part['normals'],expected,rtol=0,atol=1e-12)
        for old,new in zip(before,scene.parts):
            for field in ('positions','normals','uv','indices'):
                np.testing.assert_array_equal(getattr(old,field),getattr(new,field))
