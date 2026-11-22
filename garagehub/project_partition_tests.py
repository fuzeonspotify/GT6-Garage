"""Reviewed source face groups survive saves, preparation and archive replay."""
from __future__ import annotations
import copy,json,tempfile,unittest,zipfile
from pathlib import Path
from unittest.mock import patch
import numpy as np
from .errors import GarageError
from .glb import GLB
from .preview import build_preview
from .projects import Store,PREPARATION_REVISION
from .project_input_tests import rewrite_project
from .recognition import CORNERS
from .scene_coordinate_tests import four_tyres_obj,preview_header
from .scene_coordinates import prepare_scene
from .util import atomic_json,read_json


def grouped_obj():
    lines=[four_tyres_obj().decode().rstrip(),'o brake_rotors'];vertex=13
    for corner,x,z in (('FL',100,200),('FR',-100,200),('RL',100,-200),('RR',-100,-200)):
        lines.extend((f'v {x} 40 {z-10}',f'v {x} 60 {z-10}',f'v {x} 50 {z+10}',
                      f'f {vertex} {vertex+1} {vertex+2}'));vertex+=3
    return ('\n'.join(lines)+'\n').encode()


class ProjectPartitionTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=Store(self.root/'projects')
    def tearDown(self):self.tmp.cleanup()
    def project(self):
        p=self.store.create(grouped_obj(),'grouped.obj')
        rows=p['inspection']['parts'];self.group=next(r['id'] for r in rows if r['name']=='brake_rotors')
        overrides={r['id']:{'role':'tyre','corner':r['name']} for r in rows if r['name'] in CORNERS}
        return self.store.update(p['id'],{'forward':'+Z','metres_per_unit':.01,'wheelbase_metres':4.,
            'overrides':overrides,'notes':'Saved artist intent','installation':{'mode':'replace','replace_car_id':82}})
    def files(self,pid):
        return {p.name:p.read_bytes() for p in self.store.folder(pid).iterdir() if p.is_file()}
    def split(self,p):return self.store.partition_wheel_group(p['id'],self.group,'brake_rotor')
    def assert_faces(self,p):
        current=self.store.scene(p['id']);source=GLB((self.store.folder(p['id'])/'source.glb').read_bytes()).scene()
        prepared=prepare_scene(source,p['source_up_axis'],p['metres_per_unit'])
        original=next(x for x in prepared.parts if x.id==self.group);parts={x.id:x for x in current.parts}
        for saved in p['wheel_partitions'][0]['groups']:
            child=parts[saved['id']];faces=saved['source_face_indices']
            for attr in ('positions','normals','uv'):
                np.testing.assert_array_equal(getattr(child,attr)[child.indices],getattr(original,attr)[original.indices[faces]])
        self.assertEqual(current.triangles,source.triangles)

    def test_explicit_partition_persists_faces_motion_and_original_inputs(self):
        p=self.project();self.store.update(p['id'],{'approved_roles':True});before=self.files(p['id'])
        with patch('garagehub.projects.build_preview',wraps=build_preview) as builder:
            result=self.split(p);self.assertEqual(builder.call_count,2)
        after=self.files(p['id'])
        for name in ('source.glb','original_input.bin'):self.assertEqual(after[name],before[name])
        self.assertFalse(result['approved_roles']);self.assertEqual(result['part_count'],8)
        rows={r['id']:r for r in result['inspection']['parts']}
        for corner in CORNERS:
            row=rows[self.group+'__wheel_'+corner]
            self.assertEqual((row['role'],row['corner'],row['motion_intent']),('brake_rotor',corner,'wheel_spin'))
            self.assertFalse(row['role_review_required'])
        self.assertNotIn(self.group,rows);self.assertNotIn(self.group,result['overrides'])
        self.assertEqual(result['installation'],p['installation']);self.assertEqual(result['notes'],p['notes'])
        self.assertEqual(result['partition_result']['triangles_preserved'],4)
        self.assertFalse(result['partition_result']['runtime_verified']);self.assert_faces(result)
        for name in ('preview.bin','software_preview.bin'):
            header=preview_header(after[name]);self.assertEqual(len(header['meshes']),8)
            np.testing.assert_allclose(header['bounds'],self.store.scene(p['id']).bounds)

    def test_restart_axis_and_units_replay_saved_membership_without_repartition(self):
        result=self.split(self.project());saved=copy.deepcopy(result['wheel_partitions'])
        self.store=Store(self.root/'projects')
        with patch('garagehub.mechanical_parts.partition_wheel_corners',side_effect=AssertionError('must replay faces')):
            reopened=self.store.summary(result['id']);self.assertEqual(reopened['wheel_partitions'],saved)
            for axis,scale in (('+Z',.1),('-X',1.),('+Y',.01)):
                updated=self.store.update(result['id'],{'source_up_axis':axis,'metres_per_unit':scale})
                self.assertEqual(updated['wheel_partitions'],saved);self.assert_faces(updated)

    def test_partitioned_project_archive_roundtrip_preserves_reviewed_setup(self):
        result=self.split(self.project());result=self.store.update(result['id'],{'approved_roles':True})
        archive=self.root/'grouped.gt6car';self.store.export(result['id'],archive)
        restored=self.store.import_archive(archive.read_bytes())
        for key in ('wheel_partitions','overrides','installation','metres_per_unit','source_sha256','notes'):
            self.assertEqual(restored[key],result[key])
        self.assertTrue(restored['approved_roles']);self.assert_faces(restored)
        self.assertEqual(self.files(restored['id'])['original_input.bin'],grouped_obj())

    def test_tampered_saved_partition_is_rejected_and_partial_import_removed(self):
        result=self.split(self.project());archive=self.root/'grouped.gt6car';self.store.export(result['id'],archive)
        def corrupt(p):p['wheel_partitions'][0]['groups'][0]['source_face_indices']=[3]
        changed=rewrite_project(archive.read_bytes(),corrupt);before={p.name for p in self.store.root.iterdir()}
        with self.assertRaises(GarageError):self.store.import_archive(changed)
        self.assertEqual({p.name for p in self.store.root.iterdir()},before)

    def test_failed_preview_leaves_all_saved_files_unchanged(self):
        p=self.project();before=self.files(p['id']);calls=0
        def fail_second(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise GarageError('Synthetic software preview failure')
            return build_preview(*args,**kwargs)
        with patch('garagehub.projects.build_preview',side_effect=fail_second):
            with self.assertRaises(GarageError):self.split(p)
        self.assertEqual(self.files(p['id']),before);self.assertEqual(len(self.store.scene(p['id']).parts),5)

    def test_missing_tyres_unknown_source_and_duplicate_partition_do_not_write(self):
        p=self.project();self.store.update(p['id'],{'overrides':{}});before=self.files(p['id'])
        with self.assertRaises(GarageError):self.split(p)
        self.assertEqual(self.files(p['id']),before)
        p=self.project();result=self.split(p);before=self.files(p['id'])
        for part in (self.group,self.group+'__wheel_FL','missing'):
            with self.subTest(part=part),self.assertRaises(GarageError):self.store.partition_wheel_group(p['id'],part,'brake_rotor')
        self.assertEqual(self.files(p['id']),before)

    def test_metadata_limit_rejects_before_preview_or_saved_changes(self):
        p=self.project();before=self.files(p['id'])
        with patch('garagehub.projects.MAX_PROJECT_METADATA_BYTES',100),patch('garagehub.projects.build_preview') as preview:
            with self.assertRaisesRegex(GarageError,'metadata limit'):self.split(p)
            preview.assert_not_called()
        self.assertEqual(self.files(p['id']),before)

    def test_changed_source_fingerprint_cannot_authorize_partition(self):
        p=self.project();folder=self.store.folder(p['id']);stored=read_json(folder/'project.json');stored['source_sha256']='0'*64
        atomic_json(folder/'project.json',stored);before=self.files(p['id'])
        with self.assertRaisesRegex(GarageError,'fingerprint'):self.split(p)
        self.assertEqual(self.files(p['id']),before)

    def test_calibration_replays_partition_and_keeps_source_relative_membership(self):
        result=self.split(self.project());pid=result['id'];saved=copy.deepcopy(result['wheel_partitions'])
        self.store.update(pid,{'metres_per_unit':.1,'wheelbase_metres':4.})
        result=self.store.calibrate_units(pid)
        self.assertAlmostEqual(result['metres_per_unit'],.01);self.assertEqual(result['wheel_partitions'],saved)
        self.assert_faces(result);self.assertFalse(result['approved_roles'])

    def test_revision_three_refreshes_new_semantics_once_and_retains_intent(self):
        p=self.project();pid=p['id'];folder=self.store.folder(pid);stored=read_json(folder/'project.json')
        stored.update(preparation_revision=3,approved_roles=True);atomic_json(folder/'project.json',stored)
        atomic_json(folder/'inspection.json',{'stale':True})
        with patch('garagehub.projects.build_preview',wraps=build_preview) as preview:
            result=self.store.summary(pid);self.store.summary(pid);self.assertEqual(preview.call_count,2)
        self.assertEqual(result['preparation_revision'],PREPARATION_REVISION);self.assertFalse(result['approved_roles'])
        self.assertEqual(result['overrides'],p['overrides']);self.assertEqual(result['installation'],p['installation'])
        row=next(r for r in result['inspection']['parts'] if r['id']==self.group)
        self.assertTrue(row['role_review_required']);self.assertEqual(row['role'],'brake_rotor')

    def test_known_recipe_is_evaluated_on_canonical_source_and_filtered_after_split(self):
        p=self.project();group=self.group;calls=[]
        def recipe(scene):
            calls.append([x.id for x in scene.parts])
            self.assertEqual(len(scene.parts),5)
            return {group:{'role':'rim','corner':None}}
        self.store._recipes={}
        with patch('garagehub.projects.source_recipe',side_effect=recipe):
            self.assertIn(group,self.store.known_recipe(p['id']))
            result=self.split(p)
            self.assertEqual(self.store.known_recipe(p['id']),{})
            self.assertFalse(result['known_recipe_available'])
        self.assertTrue(calls)

    def test_neutral_export_keeps_each_brake_corner_motion_and_geometry(self):
        p=self.split(self.project());path=self.root/'neutral.zip';self.store.neutral(p['id'],path)
        with zipfile.ZipFile(path) as z:doc=json.loads(z.read('car.json'))
        brakes=[r for r in doc['parts'] if r['role']=='brake_rotor']
        self.assertEqual({r['corner'] for r in brakes},set(CORNERS))
        self.assertTrue(all(r['motion_intent']=='wheel_spin' and not r['role_review_required'] for r in brakes))
        self.assertFalse(doc['game_ready'])


if __name__=='__main__':unittest.main()
