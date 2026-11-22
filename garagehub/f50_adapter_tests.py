"""Self-contained regressions for deterministic wheel grouping; no GT6 files."""
from __future__ import annotations
import unittest
import numpy as np
from types import SimpleNamespace
from .glb import Part,Scene
from .f50_wheel_adapter import split_four_corners,adapt_source,F50_SOURCE_SHA256,F50_WHEEL_GROUPS,F50_CORNERS
from .car_profiles import source_recipe
from .errors import GarageError

def synthetic_four():
    centers=[(1,1),(-1,1),(1,-1),(-1,-1)]
    pts=[];f=[];uv=[];norm=[]
    for cx,cz in centers:
        first=len(pts)
        pts.extend([(cx-.16,0,cz-.16),(cx+.16,0,cz-.16),
                    (cx+.16,0,cz+.16),(cx-.16,0,cz+.16)])
        uv.extend([(0,0),(1,0),(1,1),(0,1)]);norm.extend([(0,1,0)]*4)
        f.extend([(first,first+1,first+2),(first,first+2,first+3)])
    return Part('test','test',0,0,0,np.array(pts,np.float32),np.array(norm,np.float32),
                np.array(uv,np.float32),np.array(f,np.uint32),{},[])

class F50AdapterTests(unittest.TestCase):
    def test_four_corner_partition_exact_coverage(self):
        p=synthetic_four();groups=split_four_corners(p,'rim',0)
        self.assertEqual([x.id for x in groups],['test__FL','test__FR','test__RL','test__RR'])
        self.assertEqual(sum(len(x.indices) for x in groups),len(p.indices))
        self.assertEqual([len(x.indices) for x in groups],[2]*4)
        self.assertEqual({x.extras['gt6_source_part'] for x in groups},{'test'})
    def test_triangle_straddling_axle_is_rejected(self):
        p=synthetic_four();p.positions[0,0]=-.4
        with self.assertRaises(GarageError):split_four_corners(p,'tyre',0)
    def test_axle_center_ambiguous_rejected(self):
        p=synthetic_four();p.positions[0,2]=.01
        with self.assertRaises(GarageError):split_four_corners(p,'tyre',0)
    def test_missing_wheel_quadrant_rejected(self):
        p=synthetic_four();p.indices=p.indices[:6]
        with self.assertRaises(GarageError):split_four_corners(p,'rim',0)
    def test_unknown_source_unchanged(self):
        p=synthetic_four();s=Scene([p],[],{},b'','0'*64,[])
        self.assertIs(adapt_source(s),s)
    def test_known_f50_source_changed_structure_rejected(self):
        p=synthetic_four();s=Scene([p],[],{},b'',F50_SOURCE_SHA256,[])
        with self.assertRaises(GarageError):adapt_source(s)
    def test_known_recipe_uses_fingerprint_and_four_corners(self):
        wheel=[SimpleNamespace(id=part+'__'+corner,extras={'gt6_source_part':part,'gt6_role':role,'gt6_corner':corner}) for part,(_,role,_) in F50_WHEEL_GROUPS.items() for corner in F50_CORNERS]
        wheel.extend([SimpleNamespace(id=i,extras={}) for i in ('n3p0','n7p0','n27p0')])
        wheel.append(SimpleNamespace(id='n10p0',extras={},material=8,indices=np.zeros((2016,3),np.uint32)))
        r=source_recipe(SimpleNamespace(digest=F50_SOURCE_SHA256,parts=wheel))
        self.assertEqual(len(r),20);self.assertEqual(r['n47p0__FL'],{'role':'tyre','corner':'FL'})
        self.assertEqual(r['n41p0__RR'],{'role':'rim','corner':'RR'})
        self.assertEqual(r['n3p0']['role'],'paint');self.assertEqual(r['n7p0']['role'],'body')
        self.assertEqual(r['n27p0']['role'],'glass')
        self.assertEqual(r['n10p0'],{'role':'taillight','corner':None})
    def test_shared_rear_lenses_require_reviewed_material_and_faces(self):
        wheels=[SimpleNamespace(id=part+'__'+corner,extras={'gt6_source_part':part,'gt6_role':role,'gt6_corner':corner}) for part,(_,role,_) in F50_WHEEL_GROUPS.items() for corner in F50_CORNERS]
        wheels.extend(SimpleNamespace(id=i,extras={}) for i in ('n3p0','n7p0','n27p0'))
        rear=SimpleNamespace(id='n10p0',extras={},material=8,indices=np.zeros((2016,3),np.uint32));wheels.append(rear)
        self.assertEqual(source_recipe(SimpleNamespace(digest=F50_SOURCE_SHA256,parts=wheels))['n10p0']['role'],'taillight')
        rear.material=9
        with self.assertRaises(GarageError):source_recipe(SimpleNamespace(digest=F50_SOURCE_SHA256,parts=wheels))
        rear.material=8;rear.indices=rear.indices[:-1]
        with self.assertRaises(GarageError):source_recipe(SimpleNamespace(digest=F50_SOURCE_SHA256,parts=wheels))
    def test_shared_rear_lenses_missing_blocked(self):
        wheels=[SimpleNamespace(id=part+'__'+corner,extras={'gt6_source_part':part,'gt6_role':role,'gt6_corner':corner}) for part,(_,role,_) in F50_WHEEL_GROUPS.items() for corner in F50_CORNERS]
        wheels.extend(SimpleNamespace(id=i,extras={}) for i in ('n3p0','n7p0','n27p0'))
        with self.assertRaises(GarageError):source_recipe(SimpleNamespace(digest=F50_SOURCE_SHA256,parts=wheels))
    def test_incomplete_known_f50_recipe_rejected(self):
        with self.assertRaises(GarageError):
            source_recipe(SimpleNamespace(digest=F50_SOURCE_SHA256,parts=[]))
