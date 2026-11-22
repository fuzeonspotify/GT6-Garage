"""Small offline regressions for panel/filler-hole boundary preservation."""
from __future__ import annotations
import unittest
import numpy as np
from .surface_guards import boundary_edges,verify_boundaries
from .errors import GarageError


def ring():
    p=np.array([[-2,-2,0],[2,-2,0],[2,2,0],[-2,2,0],[-.4,-.4,0],[.4,-.4,0],[.4,.4,0],[-.4,.4,0]],float)
    f=np.array([[0,1,5],[0,5,4],[1,2,6],[1,6,5],[2,3,7],[2,7,6],[3,0,4],[3,4,7]],np.int64);return p,f


class SurfaceTests(unittest.TestCase):
    def test_original_annulus_edges_pass(self):
        p,f=ring();r=verify_boundaries(p,f,p,f,np.arange(8));self.assertEqual(r['retained_boundary_edges'],8)
    def test_filler_border_expansion_is_rejected(self):
        p,f=ring();q=p.copy();q[4:]*=1.8
        with self.assertRaises(GarageError):verify_boundaries(p,f,q,f,np.arange(8))
    def test_filler_border_vertex_deletion_rejected(self):
        p,f=ring();mapping=np.arange(8);mapping[4]=-1
        with self.assertRaises(GarageError):verify_boundaries(p,f,p,f,mapping)
    def test_missing_triangle_opens_extra_boundary(self):
        p,f=ring()
        with self.assertRaises(GarageError):verify_boundaries(p,f,p,f[:-1],np.arange(8))
    def test_reindexing_boundary_is_legal(self):
        p,f=ring();order=np.arange(7,-1,-1);r=verify_boundaries(p,f,p[order],order[f],order);self.assertTrue(r['passed'])
    def test_small_float_roundoff_tolerated(self):
        p,f=ring();self.assertTrue(verify_boundaries(p,f,p+1e-8,f,np.arange(8))['passed'])
    def test_merged_boundary_vertices_rejected(self):
        p,f=ring();mapping=np.arange(8);mapping[4]=5
        with self.assertRaises(GarageError):verify_boundaries(p,f,p,f,mapping)
    def test_extra_closed_component_has_no_open_boundary(self):
        p=np.array([[0,0,0],[1,0,0],[0,1,0],[0,0,1]],float);f=np.array([[0,2,1],[0,1,3],[1,2,3],[2,0,3]]);self.assertEqual(len(boundary_edges(f)),0);self.assertTrue(verify_boundaries(p,f,p,f,np.arange(4))['passed'])
    def test_guard_does_not_mutate_input(self):
        p,f=ring();a=p.copy();b=f.copy();verify_boundaries(p,f,p,f,np.arange(8));np.testing.assert_array_equal(p,a);np.testing.assert_array_equal(f,b)
    def test_installed_simplifier_preserves_open_panel_edges(self):
        import fast_simplification as fs
        p=np.array([[x,y,.01*x*x] for y in np.linspace(-1,1,15) for x in np.linspace(-1,1,15)],float);f=[]
        for y in range(14):
            for x in range(14):
                if 5<=y<=8 and 5<=x<=8:continue
                a=y*15+x;f.extend([[a,a+1,a+16],[a,a+16,a+15]])
        f=np.array(f,np.int64);_,_,c=fs.simplify(p,f,target_count=150,preserve_border=True,return_collapses=True);q,g,m=fs.replay_simplification(p,f,c);self.assertTrue(verify_boundaries(p,f,q,g,m)['passed'])
