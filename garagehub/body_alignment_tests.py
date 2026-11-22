"""Four-centre body fitting: independent linear solve and geometry invariants.

These checks use synthetic explicit native frames plus the portable original
Supra centre measurements. No proprietary native files or game are required.
"""
from __future__ import annotations

import copy
import itertools
import unittest

import numpy as np

from .car_geometry import alignment
from .car_profiles import PROFILE
from .errors import GarageError
from .recognition import CORNERS, axes


def _source(front_track=1.5,rear_track=1.6,wheelbase=2.55,forward='+Z',units=1.,origin=(0.,0.,0.),heights=None):
    left,up,front=axes(forward)
    heights=(.2,.2,.2,.2) if heights is None else heights
    canonical=np.array([[front_track/2,heights[0],wheelbase/2],
                        [-front_track/2,heights[1],wheelbase/2],
                        [rear_track/2,heights[2],-wheelbase/2],
                        [-rear_track/2,heights[3],-wheelbase/2]])
    points=canonical@np.array([left,up,front])*units+origin
    return {'forward':forward,'wheel_centers':dict(zip(CORNERS,points.tolist())),
            'wheelbase':wheelbase*units,'track':(front_track+rear_track)/2*units}


def _points(inspection):
    return np.array([inspection['wheel_centers'][k] for k in CORNERS],float)


def _native_from_source(inspection,matrix=None,translation=(0.,.05,0.)):
    if matrix is None:
        left,up,front=axes(inspection['forward']);matrix=np.array([-left,up,-front])
    return _points(inspection)@np.asarray(matrix).T+translation


def _profile(target):
    result=copy.deepcopy(PROFILE)
    for i,k in enumerate(CORNERS):result['wheel_geometry'][k]['center_metres']=np.asarray(target)[i].tolist()
    # Scalar metadata is intentionally not recomputed. The explicit native
    # centres, including Y, must determine the fit rather than these old values.
    return result


def _linear_oracle(inspection,target):
    """Solve 12 equations / 5 unknowns, independently of centroid formulas."""
    left,up,front=axes(inspection['forward'])
    basis=np.array([-left,up,-front]);p=_points(inspection)@basis.T
    design=np.zeros((12,5))
    for i in range(4):
        design[3*i,0]=p[i,0]
        design[3*i+1,1]=p[i,1]
        design[3*i+2,1]=p[i,2]
        design[3*i:3*i+3,2:]=np.eye(3)
    solution=np.linalg.lstsq(design,np.asarray(target).reshape(-1),rcond=None)[0]
    matrix=np.diag([solution[0],solution[1],solution[1]])@basis
    return matrix,solution[2:],design,solution


class BodyAlignmentTests(unittest.TestCase):
    def test_matching_unequal_front_rear_tracks_do_not_narrow_the_body(self):
        inspection=_source();target=_native_from_source(inspection)
        matrix,translation,report=alignment(inspection,_profile(target))
        np.testing.assert_array_equal(matrix,np.diag([-1.,1.,-1.]))
        np.testing.assert_allclose(_points(inspection)@matrix.T+translation,target,rtol=0,atol=1e-15)
        self.assertEqual(report['uniform_scale'],1.)
        self.assertEqual(report['lateral_fit_ratio'],1.)
        self.assertLess(report['maximum_wheel_centre_error_metres'],1e-15)
        self.assertEqual(report['source_front_track_metres'],1.5)
        self.assertEqual(report['source_rear_track_metres'],1.6)
        # A rigid identity fit preserves attached geometry, not just centres.
        attached=np.array([[.13,.07,.29],[-.31,.17,.48],[.19,-.23,.11]])
        transformed=attached@matrix.T+translation
        np.testing.assert_allclose(np.linalg.norm(transformed[:,None]-transformed[None,:],axis=-1),
                                   np.linalg.norm(attached[:,None]-attached[None,:],axis=-1),atol=1e-15)

    def test_reversed_track_asymmetry_also_preserves_an_exact_match(self):
        inspection=_source(front_track=1.72,rear_track=1.46,wheelbase=3.1)
        target=_native_from_source(inspection,translation=(.12,-.04,.23))
        matrix,translation,report=alignment(inspection,_profile(target))
        np.testing.assert_allclose(matrix,np.diag([-1.,1.,-1.]),atol=1e-15)
        self.assertLess(report['maximum_wheel_centre_error_metres'],1e-15)

    def test_exact_common_body_scales_fit_both_axles_without_a_warp(self):
        inspection=_source(front_track=1.56,rear_track=1.72,heights=(.19,.21,.24,.22))
        expected=np.diag([-.96,.99,-.99]);target=_native_from_source(inspection,expected,(.1,-.2,.3))
        matrix,translation,report=alignment(inspection,_profile(target))
        np.testing.assert_allclose(matrix,expected,rtol=0,atol=4e-16)
        np.testing.assert_allclose(translation,[.1,-.2,.3],rtol=0,atol=3e-16)
        self.assertLess(report['maximum_wheel_centre_error_metres'],1e-15)

    def test_incompatible_tracks_use_both_axles_in_least_squares(self):
        inspection=_source();target=_native_from_source(_source(front_track=1.53,rear_track=1.58))
        matrix,translation,report=alignment(inspection,_profile(target))
        expected=(1.5*1.53+1.6*1.58)/(1.5**2+1.6**2)
        self.assertAlmostEqual(abs(matrix[0,0]),expected,places=14)
        residual=_points(inspection)@matrix.T+translation-target
        # The least-squares optimum makes the residual orthogonal to its
        # design column. Front-only scaling violates this necessary condition.
        self.assertAlmostEqual(float(residual[:,0]@(-_points(inspection)[:,0])),0.,places=14)
        self.assertGreater(report['maximum_wheel_centre_error_metres'],.01)
        self.assertTrue(report['dimensional_review_required'])

    def test_full_independent_linear_solve_includes_supplied_target_height(self):
        inspection=_source(heights=(.19,.22,.25,.23),origin=(.3,-.2,.7))
        target=_native_from_source(inspection,np.diag([-.98,1.015,-1.015]),(.1,.03,-.17))
        target+=np.array([[.006,.004,-.003],[-.004,-.006,.002],[.003,-.002,.004],[-.005,.003,-.002]])
        expected_matrix,expected_translation,design,solution=_linear_oracle(inspection,target)
        matrix,translation,report=alignment(inspection,_profile(target))
        np.testing.assert_allclose(matrix,expected_matrix,rtol=0,atol=2e-15)
        np.testing.assert_allclose(translation,expected_translation,rtol=0,atol=2e-15)
        actual=_points(inspection)@matrix.T+translation
        objective=float(np.sum((actual-target)**2))
        self.assertAlmostEqual(objective,float(np.sum((design@solution-target.reshape(-1))**2)),places=15)
        for column in range(5):
            for direction in (-1.,1.):
                changed=solution.copy();changed[column]+=direction*1e-4
                self.assertGreater(float(np.sum((design@changed-target.reshape(-1))**2)),objective)
        self.assertAlmostEqual(report['maximum_wheel_centre_error_metres'],float(np.linalg.norm(actual-target,axis=1).max()))

    def test_equal_tracks_do_not_bypass_the_full_least_squares_problem(self):
        inspection=_source(front_track=1.5,rear_track=1.5,heights=(.18,.21,.24,.2))
        target=_native_from_source(inspection)
        target[:,1]+=[.012,-.007,-.003,.004]
        expected_matrix,expected_translation,_,_=_linear_oracle(inspection,target)
        matrix,translation,_=alignment(inspection,_profile(target))
        np.testing.assert_allclose(matrix,expected_matrix,rtol=0,atol=2e-15)
        np.testing.assert_allclose(translation,expected_translation,rtol=0,atol=2e-15)
        self.assertNotAlmostEqual(matrix[1,1],1.,places=9)

    def test_all_forward_axes_and_source_scales_retain_corner_labels(self):
        target=_native_from_source(_source())
        for forward,units in itertools.product(('+Z','-Z','+X','-X'),(.001,1.,1000.)):
            with self.subTest(forward=forward,units=units):
                inspection=_source(forward=forward,units=units,origin=(.03,.11,-.04))
                matrix,translation,report=alignment(inspection,_profile(target))
                np.testing.assert_allclose(_points(inspection)@matrix.T+translation,target,rtol=0,atol=2e-14)
                self.assertGreater(np.linalg.det(matrix),0.)
                np.testing.assert_allclose(np.linalg.svd(matrix,compute_uv=False),[1/units]*3,rtol=2e-14)
                self.assertLess(report['maximum_wheel_centre_error_metres'],2e-14)

    def test_translated_source_and_native_origins_do_not_change_fit_shape(self):
        inspection=_source();target=_native_from_source(inspection)
        first=alignment(inspection,_profile(target))
        source_shift=np.array([12.5,-3.25,7.75]);target_shift=np.array([-.2,.7,.4])
        shifted=copy.deepcopy(inspection)
        shifted['wheel_centers']={k:(np.array(v)+source_shift).tolist() for k,v in shifted['wheel_centers'].items()}
        matrix,translation,_=alignment(shifted,_profile(target+target_shift))
        np.testing.assert_allclose(matrix,first[0],rtol=0,atol=2e-15)
        np.testing.assert_allclose(_points(shifted)@matrix.T+translation,target+target_shift,rtol=0,atol=3e-15)

    def test_actual_reviewed_supra_transform_remains_exactly_the_delivered_baseline(self):
        # From v044/supra_recognition_comparison.json; expected binary float64
        # matrix/translation captured by the delivered 0.4.3 alignment function.
        inspection={'forward':'+Z','wheelbase':2.557976007461548,'track':1.5413289070129395,
            'wheel_centers':{
                'FL':[.7573347091674805,.17277294397354126,1.234093189239502],
                'FR':[-.783994197845459,.17277294397354126,1.2340936660766602],
                'RL':[.7573344707489014,.17277294397354126,-1.323882818222046],
                'RR':[-.7839944362640381,.17277294397354126,-1.3238823413848877]}}
        matrix,translation,_=alignment(inspection)
        self.assertEqual(matrix.astype('>f8').tobytes().hex(),
            'bfefe6750559ec3d8000000000000000800000000000000000000000000000003fefe674e27cea0b000000000000000080000000000000008000000000000000bfefe674e27cea0b')
        self.assertEqual(translation.astype('>f8').tobytes().hex(),'bf8b36e585f432533f97fa4e459def08bfa6ea12ddf7f500')

    def test_native_coordinates_override_obsolete_profile_dimension_summaries(self):
        inspection=_source();target=_native_from_source(inspection);profile=_profile(target)
        profile.update(wheelbase=9.,front_track=.7,rear_track=3.,wheel_y=100.)
        matrix,translation,report=alignment(inspection,profile)
        np.testing.assert_allclose(_points(inspection)@matrix.T+translation,target,rtol=0,atol=1e-15)
        self.assertEqual(report['target_wheelbase_metres'],2.55)
        self.assertEqual(report['target_front_track_metres'],1.5)
        self.assertEqual(report['target_rear_track_metres'],1.6)

    def test_fit_does_not_modify_source_profile_or_return_aliases(self):
        inspection=_source();profile=_profile(_native_from_source(inspection))
        saved_source=copy.deepcopy(inspection);saved_profile=copy.deepcopy(profile);saved_default=copy.deepcopy(PROFILE)
        matrix,translation,report=alignment(inspection,profile)
        matrix[:]=0;translation[:]=0;report['source_front_track_metres']=99
        self.assertEqual(inspection,saved_source);self.assertEqual(profile,saved_profile);self.assertEqual(PROFILE,saved_default)

    def test_report_keeps_best_fit_residual_separate_from_physical_compatibility(self):
        inspection=_source();_,_,report=alignment(inspection,_profile(_native_from_source(inspection)))
        self.assertEqual(report['fit_scope'],'wheel_centres_only')
        self.assertTrue(report['dimensional_review_required'])
        self.assertEqual(report['fit_method'],'axis_constrained_four_centre_least_squares')
        self.assertIn('rigid wheel/brake shape',report['policy'])
        self.assertNotIn('physical_compatibility',report)

    def test_missing_malformed_or_nonfinite_source_centres_are_rejected(self):
        target=_native_from_source(_source());profile=_profile(target)
        cases=[None,[1.,2.],[1.,float('nan'),2.],[1.,float('inf'),2.],['1',2.,3.],[True,2.,3.]]
        for value in cases:
            with self.subTest(value=value):
                inspection=_source();inspection['wheel_centers']['FL']=value
                with self.assertRaises(GarageError):alignment(inspection,profile)
        for mode in ('missing','extra'):
            inspection=_source()
            if mode=='missing':inspection['wheel_centers'].pop('RR')
            else:inspection['wheel_centers']['extra']=[0,0,0]
            with self.assertRaises(GarageError):alignment(inspection,profile)

    def test_missing_or_unknown_target_height_is_never_guessed(self):
        inspection=_source();target=_native_from_source(inspection)
        for value in (None,[1.,2.],[-.75,None,-1.275],[-.75,float('nan'),-1.275],[-.75,float('inf'),-1.275],[True,.2,-1.275]):
            with self.subTest(value=value):
                profile=_profile(target);profile['wheel_geometry']['FL']['center_metres']=value
                with self.assertRaises(GarageError):alignment(inspection,profile)
        profile=_profile(target);profile['wheel_geometry'].pop('RR')
        with self.assertRaises(GarageError):alignment(inspection,profile)

    def test_reflected_or_crossed_source_and_target_labels_are_rejected(self):
        for side,swap in itertools.product(('source','target'),(('FL','FR'),('FL','RR'),('FL','RL'))):
            with self.subTest(side=side,swap=swap):
                inspection=_source();profile=_profile(_native_from_source(inspection))
                rows=inspection['wheel_centers'] if side=='source' else profile['wheel_geometry']
                rows[swap[0]],rows[swap[1]]=rows[swap[1]],rows[swap[0]]
                with self.assertRaises(GarageError):alignment(inspection,profile)

    def test_coincident_and_collinear_centres_do_not_create_a_transform(self):
        for side,points in itertools.product(('source','target'),([0.,0.,0.],None)):
            inspection=_source();profile=_profile(_native_from_source(inspection))
            values={k:(points if points is not None else [float(i),0.,0.]) for i,k in enumerate(CORNERS)}
            if side=='source':inspection['wheel_centers']=values
            else:
                for k in CORNERS:profile['wheel_geometry'][k]['center_metres']=values[k]
            with self.assertRaises(GarageError):alignment(inspection,profile)

    def test_stale_saved_measurements_cannot_override_actual_centres(self):
        for field in ('wheelbase','track'):
            inspection=_source();profile=_profile(_native_from_source(inspection));inspection[field]*=1.01
            with self.assertRaisesRegex(GarageError,'no longer matches'):alignment(inspection,profile)

    def test_invalid_saved_dimensions_are_actionable(self):
        for field,value in itertools.product(('wheelbase','track'),(None,0.,-1.,True,'2.55',float('nan'),float('inf'),10**1000)):
            with self.subTest(field=field,value_type=type(value).__name__):
                inspection=_source();profile=_profile(_native_from_source(inspection));inspection[field]=value
                with self.assertRaises(GarageError):alignment(inspection,profile)

    def test_existing_profile_lateral_and_residual_limits_remain_enforced(self):
        inspection=_source();target=_native_from_source(_source(front_track=1.,rear_track=1.))
        with self.assertRaisesRegex(GarageError,'15%'):alignment(inspection,_profile(target))
        # No single body scale can meet both targets; an axle-specific warp
        # would hide this failure, and must not appear in this narrow fix.
        target=_native_from_source(_source(front_track=1.7,rear_track=1.4))
        with self.assertRaisesRegex(GarageError,'residual limit'):alignment(inspection,_profile(target))

    def test_negative_shared_scale_is_rejected_instead_of_reflecting(self):
        inspection=_source(heights=(10.,10.,-10.,-10.))
        target=_native_from_source(inspection);target[:,1]*=-1
        with self.assertRaisesRegex(GarageError,'positive body fit'):alignment(inspection,_profile(target))
