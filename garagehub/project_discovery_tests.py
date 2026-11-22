"""User-reviewed tyre proposals retain exact source faces and reject stale edits."""
from __future__ import annotations

import copy
import tempfile
import threading
import unittest
import zipfile
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .errors import GarageError
from .glb import GLB
from .preview import build_preview
from .projects import Store
from .recognition import CORNERS
from .scene_coordinate_tests import four_tyres_obj, preview_header
from .scene_coordinates import prepare_scene


def combined_tyres_obj():
    lines=['o combined_tyres']+[line for line in four_tyres_obj().decode().splitlines() if not line.startswith('o ')]
    return ('\n'.join(lines)+'\n').encode()


class ProjectDiscoveryTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory();self.root=Path(self.temp.name)
        self.store=Store(self.root/'projects')

    def tearDown(self):
        self.temp.cleanup()

    def project(self,mode='replace'):
        p=self.store.create(combined_tyres_obj(),'combined_tyres.obj')
        self.part=p['inspection']['parts'][0]['id']
        p=self.store.update(p['id'],{'forward':'+Z','metres_per_unit':.01,'wheelbase_metres':4.,
            'overrides':{self.part:{'role':'tyre','corner':None}},'approved_roles':True,
            'notes':'Keep this artist intent','installation':{'mode':mode,'replace_car_id':82} if mode=='replace' else {'mode':mode}})
        return self.store.update(p['id'],{'approved_roles':True})

    def files(self,pid):
        return {p.name:p.read_bytes() for p in self.store.folder(pid).iterdir() if p.is_file()}

    def apply(self,p,proposal=None,confirmed=True):
        proposal=proposal or self.store.discover_tyres(p['id'],self.part)
        return self.store.apply_tyre_discovery(p['id'],self.part,proposal['proposal_token'],confirmed)

    def assert_faces(self,p):
        source=GLB((self.store.folder(p['id'])/'source.glb').read_bytes()).scene()
        parent=prepare_scene(source,p['source_up_axis'],p['metres_per_unit']).parts[0]
        current=self.store.scene(p['id']);parts={part.id:part for part in current.parts}
        for group in p['wheel_partitions'][0]['groups']:
            child=parts[group['id']];faces=group['source_face_indices']
            for attr in ('positions','normals','uv'):
                np.testing.assert_array_equal(getattr(child,attr)[child.indices],getattr(parent,attr)[parent.indices[faces]])
        self.assertEqual(current.triangles,source.triangles)

    def test_proposal_bootstraps_missing_centres_without_changing_any_saved_file(self):
        p=self.project();before=self.files(p['id'])
        self.assertLess(len(p['inspection']['wheel_centers']),4)
        proposal=self.store.discover_tyres(p['id'],self.part)
        self.assertEqual(self.files(p['id']),before)
        self.assertEqual({g['corner'] for g in proposal['corners']},set(CORNERS))
        self.assertTrue(all(g['triangles']==1 for g in proposal['corners']))
        self.assertEqual(proposal['triangles_preserved'],4)
        self.assertTrue(proposal['review_required']);self.assertFalse(proposal['runtime_verified'])
        self.assertTrue(self.store.summary(p['id'])['approved_roles'])

    def test_apply_requires_explicit_boolean_confirmation_and_preserves_inputs_and_destinations(self):
        for mode in (None,'new_entry','replace'):
            with self.subTest(mode=mode):
                p=self.project(mode);proposal=self.store.discover_tyres(p['id'],self.part);before=self.files(p['id'])
                for confirmation in (False,None,'true',1):
                    with self.assertRaises(GarageError):self.apply(p,proposal,confirmation)
                    self.assertEqual(self.files(p['id']),before)
                with patch('garagehub.projects.build_preview',wraps=build_preview) as preview:
                    result=self.apply(p,proposal);self.assertEqual(preview.call_count,2)
                after=self.files(p['id'])
                for name in ('source.glb','original_input.bin'):self.assertEqual(after[name],before[name])
                self.assertFalse(result['approved_roles']);self.assertEqual(result['part_count'],4)
                self.assertEqual(result['installation'],p['installation']);self.assertEqual(result['notes'],p['notes'])
                self.assertNotIn(self.part,result['overrides']);self.assert_faces(result)
                rows=result['inspection']['parts']
                self.assertEqual({r['corner'] for r in rows},set(CORNERS))
                self.assertTrue(all(r['role']=='tyre' for r in rows))
                for name in ('preview.bin','software_preview.bin'):
                    self.assertEqual(len(preview_header(after[name])['meshes']),4)

    def test_saved_settings_changes_reject_the_displayed_proposal_without_writes(self):
        changes=({'forward':'-Z'},{'metres_per_unit':.1},
                 {'overrides':{}},{'installation':{'mode':'new_entry'}},{'notes':'Changed elsewhere'})
        for change in changes:
            with self.subTest(change=change):
                p=self.project();proposal=self.store.discover_tyres(p['id'],self.part)
                self.store.update(p['id'],change);before=self.files(p['id'])
                with self.assertRaises(GarageError):self.apply(p,proposal)
                self.assertEqual(self.files(p['id']),before)

    def test_wrong_project_unknown_part_and_already_split_part_cannot_apply(self):
        p=self.project();part=self.part;proposal=self.store.discover_tyres(p['id'],part)
        other=self.project();before=self.files(other['id'])
        with self.assertRaises(GarageError):self.apply(other,proposal)
        self.assertEqual(self.files(other['id']),before)
        for unknown in ('missing',part+'__wheel_FL'):
            with self.assertRaises(GarageError):self.store.discover_tyres(p['id'],unknown)
        result=self.apply(p,proposal);before=self.files(p['id'])
        with self.assertRaises(GarageError):self.apply(p,proposal)
        self.assertEqual(self.files(p['id']),before);self.assert_faces(result)

    def test_replay_restart_axes_units_and_archive_retain_exact_reviewed_membership(self):
        result=self.apply(self.project());saved=copy.deepcopy(result['wheel_partitions'])
        self.store=Store(self.root/'projects')
        with patch('garagehub.wheel_discovery.discover_tyre_corners',side_effect=AssertionError('must replay reviewed faces')):
            result=self.store.summary(result['id'])
            for axis,scale in (('+Z',.1),('-X',1.),('+Y',.01)):
                result=self.store.update(result['id'],{'source_up_axis':axis,'metres_per_unit':scale})
                self.assertEqual(result['wheel_partitions'],saved);self.assert_faces(result)
            archive=self.root/'reviewed.gt6car';self.store.export(result['id'],archive)
            restored=self.store.import_archive(archive.read_bytes())
        for key in ('wheel_partitions','overrides','installation','notes','source_sha256','metres_per_unit'):
            self.assertEqual(restored[key],result[key])
        self.assertEqual(self.files(restored['id'])['original_input.bin'],combined_tyres_obj())
        self.assert_faces(restored)

    def test_preview_failure_retains_every_saved_file_and_unsplit_scene(self):
        p=self.project();proposal=self.store.discover_tyres(p['id'],self.part);before=self.files(p['id']);calls=0
        def fail_second(*args,**kwargs):
            nonlocal calls
            calls+=1
            if calls==2:raise GarageError('Synthetic second preview failure')
            return build_preview(*args,**kwargs)
        with patch('garagehub.projects.build_preview',side_effect=fail_second):
            with self.assertRaises(GarageError):self.apply(p,proposal)
        self.assertEqual(self.files(p['id']),before);self.assertEqual(len(self.store.scene(p['id']).parts),1)

    def test_metadata_limit_and_changed_source_stop_before_commit(self):
        p=self.project();proposal=self.store.discover_tyres(p['id'],self.part);before=self.files(p['id'])
        with patch('garagehub.projects.MAX_PROJECT_METADATA_BYTES',100),patch('garagehub.projects.build_preview') as preview:
            with self.assertRaisesRegex(GarageError,'metadata limit'):self.apply(p,proposal)
            preview.assert_not_called()
        self.assertEqual(self.files(p['id']),before)
        source=self.store.folder(p['id'])/'source.glb';source.write_bytes(source.read_bytes()+b'changed')
        before=self.files(p['id'])
        with self.assertRaises(GarageError):self.apply(p,proposal)
        self.assertEqual(self.files(p['id']),before)

    def test_summary_guidance_available_without_any_baseline_and_not_a_build_approval(self):
        p=self.project();guide=p['import_readiness']
        self.assertEqual(guide['scope'],'source_preflight')
        self.assertFalse(guide['build_readiness_checked']);self.assertFalse(guide['runtime_verified'])
        self.assertIn('wheel_layout',{t['code'] for t in guide['user_tasks']})
        prepared=self.apply(p)
        self.assertNotIn('wheel_layout',{t['code'] for t in prepared['import_readiness']['user_tasks']})
        self.assertIn('confirm_source_review',{t['code'] for t in prepared['import_readiness']['user_tasks']})

    def test_export_and_apply_cannot_mix_two_project_revisions_in_one_archive(self):
        p=self.project();proposal=self.store.discover_tyres(p['id'],self.part)
        archive=self.root/'concurrent.gt6car';writing=threading.Event();release=threading.Event()
        attempting=threading.Event();applied=threading.Event();original=zipfile.ZipFile.write
        def held_write(writer,filename,arcname=None,*args,**kwargs):
            if arcname=='source.glb':
                writing.set()
                if not release.wait(5):raise AssertionError('test failed to release archive writer')
            return original(writer,filename,arcname,*args,**kwargs)
        def apply():
            attempting.set()
            result=self.apply(p,proposal);applied.set();return result
        with ThreadPoolExecutor(max_workers=2) as workers,patch('zipfile.ZipFile.write',held_write):
            exporting=workers.submit(self.store.export,p['id'],archive)
            try:
                self.assertTrue(writing.wait(5));changing=workers.submit(apply)
                self.assertTrue(attempting.wait(5));self.assertFalse(applied.wait(.1))
            finally:release.set()
            exporting.result(timeout=5);current=changing.result(timeout=5)
        restored=self.store.import_archive(archive.read_bytes())
        self.assertEqual(restored['part_count'],1);self.assertEqual(restored['wheel_partitions'],[])
        self.assertEqual(current['part_count'],4);self.assert_faces(current)


if __name__=='__main__':unittest.main()
