"""Explicit reference calibration must change prepared units, never source data."""
from __future__ import annotations

import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import numpy as np

from .errors import GarageError
from .model_inputs import prepare_model
from .preview import build_preview
from .projects import Store, PREPARATION_REVISION
from .recognition import infer
from .scene_coordinate_tests import four_tyres_obj,preview_header
from .util import atomic_json,read_json


def source_obj(z_up=False):
    raw=four_tyres_obj()
    if not z_up:return raw
    lines=[]
    for line in raw.decode().splitlines():
        if line.startswith('v '):
            x,y,z=map(float,line.split()[1:]);line=f'v {x:g} {-z:g} {y:g}'
        lines.append(line)
    return ('\n'.join(lines)+'\n').encode()


class UnitCalibrationTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name)
        self.store=Store(self.root/'projects')

    def tearDown(self):
        self.tmp.cleanup()

    def project(self,scale=1.,reference=4.,z_up=False,glb=False):
        original=source_obj(z_up)
        if glb:original,_=prepare_model(original,'artist.obj')
        result=self.store.create(original,'artist.glb' if glb else 'artist.obj')
        overrides={row['id']:{'role':'tyre','corner':row['name']} for row in result['inspection']['parts']}
        result=self.store.update(result['id'],{'overrides':overrides,'source_up_axis':'+Z' if z_up else '+Y',
            'metres_per_unit':scale,'wheelbase_metres':reference,'forward':'+Z',
            'installation':{'mode':'replace','replace_car_id':82},'notes':'Artist-owned source.'})
        return self.store.update(result['id'],{'approved_roles':True})

    def files(self,pid):
        folder=self.store.folder(pid)
        return {path.relative_to(folder).as_posix():path.read_bytes() for path in folder.rglob('*') if path.is_file()}

    def test_typing_reference_does_not_rescale_geometry_or_previews(self):
        project=self.project(reference=8.);pid=project['id'];scene=self.store.scene(pid)
        before=self.files(pid);positions=[part.positions.copy() for part in scene.parts]
        changed=self.store.update(pid,{'wheelbase_metres':4.})
        self.assertEqual(changed['metres_per_unit'],1.)
        self.assertEqual(changed['wheelbase_metres'],4.)
        self.assertAlmostEqual(changed['inspection']['wheelbase'],400.)
        for part,old in zip(self.store.scene(pid).parts,positions):np.testing.assert_array_equal(part.positions,old)
        after=self.files(pid)
        for name in ('preview.bin','software_preview.bin','source.glb','original_input.bin'):
            self.assertEqual(after[name],before[name])

    def test_calibration_corrects_ten_and_hundred_fold_unit_errors(self):
        for scale in (.1,1.,.001,.0001):
            with self.subTest(old_metres_per_unit=scale):
                project=self.project(scale=scale);result=self.store.calibrate_units(project['id'])
                self.assertAlmostEqual(result['metres_per_unit'],.01,places=9)
                self.assertAlmostEqual(result['inspection']['wheelbase'],4.,places=5)
                self.assertAlmostEqual(result['unit_calibration']['measured_wheelbase_before_metres'],400*scale,places=5)
                self.assertEqual(result['wheelbase_metres'],4.)
                self.assertFalse(result['approved_roles'])
                self.assertFalse(result['unit_calibration']['runtime_verified'])

    def test_successive_calibrations_use_absolute_source_scale(self):
        project=self.project(scale=.1);pid=project['id']
        first=self.store.calibrate_units(pid);second=self.store.calibrate_units(pid)
        self.assertEqual(first['metres_per_unit'],second['metres_per_unit'])
        self.assertAlmostEqual(second['metres_per_unit'],.01)
        self.store.update(pid,{'wheelbase_metres':2.5})
        third=self.store.calibrate_units(pid);fourth=self.store.calibrate_units(pid)
        self.assertAlmostEqual(third['metres_per_unit'],.00625)
        self.assertEqual(third['metres_per_unit'],fourth['metres_per_unit'])
        self.assertAlmostEqual(fourth['inspection']['wheelbase'],2.5)

    def test_calibration_ignores_stale_inspection_measurements(self):
        project=self.project();pid=project['id'];folder=self.store.folder(pid)
        stale=read_json(folder/'inspection.json');stale['wheelbase']=4.
        stale['wheel_centers']={'FL':[.8,0,2],'FR':[-.8,0,2],'RL':[.8,0,-2],'RR':[-.8,0,-2]}
        atomic_json(folder/'inspection.json',stale)
        result=self.store.calibrate_units(pid)
        self.assertAlmostEqual(result['unit_calibration']['measured_wheelbase_before_metres'],400.)
        self.assertAlmostEqual(result['metres_per_unit'],.01)
        self.assertAlmostEqual(result['inspection']['wheelbase'],4.)

    def test_missing_or_contradictory_corners_leave_every_file_unchanged(self):
        for case in ('missing','swapped','duplicate'):
            with self.subTest(case=case):
                project=self.project();pid=project['id'];overrides=copy.deepcopy(project['overrides'])
                ids={row['corner']:row['id'] for row in project['inspection']['parts']}
                if case=='missing':del overrides[ids['RR']]
                elif case=='swapped':
                    overrides[ids['FL']]['corner']='FR';overrides[ids['FR']]['corner']='FL'
                else:overrides[ids['FL']]['corner']='FR'
                self.store.update(pid,{'overrides':overrides})
                before=self.files(pid)
                with self.assertRaises(GarageError):self.store.calibrate_units(pid)
                self.assertEqual(self.files(pid),before)

    def test_invalid_reference_leaves_every_file_unchanged(self):
        for reference in (None,0.,.29,12.1,True,'4',float('nan'),float('inf')):
            with self.subTest(reference=reference):
                project=self.project();pid=project['id'];folder=self.store.folder(pid)
                stored=read_json(folder/'project.json');stored['wheelbase_metres']=reference
                # Include corrupt/nonstandard numeric metadata to check the
                # calibration boundary independently of normal settings input.
                (folder/'project.json').write_text(json.dumps(stored),encoding='utf-8')
                before=self.files(pid)
                with self.assertRaises(GarageError):self.store.calibrate_units(pid)
                self.assertEqual(self.files(pid),before)

    def test_rejected_revision_two_calibration_does_not_migrate_saved_files(self):
        project=self.project();pid=project['id'];folder=self.store.folder(pid)
        stored=read_json(folder/'project.json');stored.update(preparation_revision=2,wheelbase_metres=None)
        atomic_json(folder/'project.json',stored);atomic_json(folder/'inspection.json',{'stale':True})
        (folder/'preview.bin').write_bytes(b'old preview')
        before=self.files(pid)
        with self.assertRaises(GarageError):self.store.calibrate_units(pid)
        self.assertEqual(self.files(pid),before)

    def test_calibration_preserves_original_obj_normalized_glb_and_saved_intent(self):
        project=self.project();pid=project['id'];before=self.files(pid)
        result=self.store.calibrate_units(pid);after=self.files(pid)
        for name in ('source.glb','original_input.bin'):self.assertEqual(after[name],before[name])
        for key in ('overrides','forward','source_up_axis','source_sha256','source_metadata','installation','notes'):
            self.assertEqual(result[key],project[key])
        self.assertEqual(after['original_input.bin'],source_obj())
        self.assertTrue(result['unit_calibration']['source_bytes_unchanged'])

    def test_calibration_preserves_exact_uploaded_glb(self):
        project=self.project(glb=True);pid=project['id'];before=self.files(pid)
        self.assertNotIn('original_input.bin',before)
        result=self.store.calibrate_units(pid)
        self.assertEqual(self.files(pid)['source.glb'],before['source.glb'])
        self.assertNotIn('original_input.bin',self.files(pid))
        self.assertEqual(result['source_metadata'],project['source_metadata'])

    def test_calibration_refreshes_both_previews_with_correct_normals_and_indices(self):
        project=self.project(z_up=True);pid=project['id'];old=copy.deepcopy(self.store.scene(pid))
        with patch('garagehub.projects.build_preview',wraps=build_preview) as builder:
            result=self.store.calibrate_units(pid)
            self.assertEqual(builder.call_count,2)
        current=self.store.scene(pid)
        for part,before in zip(current.parts,old.parts):
            np.testing.assert_allclose(part.positions,before.positions*.01,rtol=1e-6)
            np.testing.assert_array_equal(part.normals,before.normals)
            np.testing.assert_array_equal(part.indices,before.indices)
            np.testing.assert_array_equal(part.uv,before.uv)
            triangle=part.indices[0]
            cross=np.cross(part.positions[triangle[1]]-part.positions[triangle[0]],
                           part.positions[triangle[2]]-part.positions[triangle[0]])
            self.assertGreater(float(cross@part.normals[triangle].mean(0)),0.)
        folder=self.store.folder(pid)
        for name in ('preview.bin','software_preview.bin'):
            raw=(folder/name).read_bytes();header=preview_header(raw)
            np.testing.assert_allclose(header['bounds'],current.bounds)
            json_size,vertices,count=struct.unpack_from('<III',raw,4)
            vectors=np.frombuffer(raw,'<f4',count=vertices*6,offset=16+json_size).reshape(-1,6)
            np.testing.assert_array_equal(vectors[:,:3],np.concatenate([p.positions for p in current.parts]))
            np.testing.assert_array_equal(vectors[:,3:],np.concatenate([p.normals for p in current.parts]))
            indices=np.frombuffer(raw,'<u4',count=count,offset=16+json_size+vertices*24)
            self.assertEqual(len(indices),sum(p.indices.size for p in current.parts))
            self.assertTrue(header['preview_only'])
        self.assertFalse(result['approved_roles'])

    def test_noop_calibration_still_refreshes_previews_and_invalidates_approval(self):
        project=self.project(scale=.01);pid=project['id']
        with patch('garagehub.projects.build_preview',wraps=build_preview) as builder:
            result=self.store.calibrate_units(pid)
            self.assertEqual(builder.call_count,2)
        self.assertEqual(result['metres_per_unit'],project['metres_per_unit'])
        self.assertFalse(result['approved_roles'])

    def test_changed_postscale_automatic_tyre_group_aborts_without_saved_changes(self):
        project=self.project();pid=project['id'];before=self.files(pid);calls=0
        def shifted_inference(*args,**kwargs):
            nonlocal calls
            report=infer(*args,**kwargs);calls+=1
            if calls==2:
                # Keep valid centres while simulating a marginal heuristic
                # selecting a different source component after float rounding.
                report['parts'][0]['id']='different_automatic_component'
            return report
        with patch('garagehub.projects.infer',side_effect=shifted_inference):
            with self.assertRaisesRegex(GarageError,'automatic tyre grouping'):
                self.store.calibrate_units(pid)
        self.assertEqual(self.files(pid),before)

    def test_valid_calibration_refreshes_revision_two_after_validation(self):
        project=self.project();pid=project['id'];folder=self.store.folder(pid)
        stored=read_json(folder/'project.json');stored['preparation_revision']=2
        atomic_json(folder/'project.json',stored);atomic_json(folder/'inspection.json',{'stale':True})
        result=self.store.calibrate_units(pid)
        self.assertEqual(result['preparation_revision'],PREPARATION_REVISION)
        self.assertAlmostEqual(result['metres_per_unit'],.01)
        self.assertAlmostEqual(result['inspection']['wheelbase'],4.)
        self.assertEqual(result['overrides'],project['overrides'])
        self.assertFalse(result['approved_roles'])


if __name__=='__main__':
    unittest.main()
