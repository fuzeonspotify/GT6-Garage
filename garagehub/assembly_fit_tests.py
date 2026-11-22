"""Whole-assembly measurements, explicit frames and nonmutating rigid fits."""
from __future__ import annotations

import copy
from dataclasses import replace
import itertools
import json
import math
import unittest

import numpy as np

from .assembly_fit import assembly_fit_report, apply_assembly_transform
from .errors import GarageError
from .glb import Part, Scene
from .recognition import CORNERS, axes, infer
from .scene_coordinates import UP_AXES, COORDINATE_SYSTEM, coordinate_description, prepare_scene


def layout(wheelbase=2.8, front_track=1.6, rear_track=1.7, forward='+Z', height=.35):
    left, up, front = axes(forward)
    return {corner: (left * (1 if corner[1] == 'L' else -1)
                     * (front_track if corner[0] == 'F' else rear_track) / 2
                     + front * (1 if corner[0] == 'F' else -1) * wheelbase / 2
                     + up * height).tolist() for corner in CORNERS}


def inspection(centers=None, forward='+Z'):
    return {'forward': forward, 'wheel_centers': layout(forward=forward) if centers is None else centers,
            # Deliberately untrusted cached summaries: recompute from centres.
            'track': 900, 'wheelbase': 800}


def explicit(centers=None, forward='+Z'):
    return {'kind': 'explicit_centres', 'forward': forward,
            'wheel_centers_metres': layout(forward=forward) if centers is None else centers,
            'provenance': {'scope': 'test fixture, explicit body-frame centres'}}


def nominal(wheelbase=2.8, front_track=1.6, rear_track=1.7, forward='+Z'):
    return {'kind': 'nominal_layout', 'forward': forward, 'wheelbase_metres': wheelbase,
            'front_track_metres': front_track, 'rear_track_metres': rear_track,
            'provenance': {'scope': 'test nominal dimensions; height unknown'}}


def rotate_x(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[1., 0., 0.], [0., c, -s], [0., s, c]])


def rotate_y(angle):
    c, s = math.cos(angle), math.sin(angle)
    return np.array([[c, 0., s], [0., 1., 0.], [-s, 0., c]])


def transformed(centers, rotation, translation):
    return {k: (rotation @ np.asarray(v) + translation).tolist() for k, v in centers.items()}


def scene_for_centres(centers):
    # Symmetric authored boxes give exact bounds centres; corner overrides make
    # the test about prepared coordinates rather than the recognition heuristic.
    offsets = np.array(list(itertools.product((-.04, .04), (-.08, .08), (-.08, .08))))
    triangles = np.array([[0, 1, 2], [1, 3, 2]], dtype=np.uint32)
    parts = [Part(k, k + ' tyre', i, 0, 0,
                  (np.asarray(centers[k]) + offsets).astype(np.float32),
                  np.tile([0., 1., 0.], (8, 1)).astype(np.float32),
                  np.zeros((8, 2), np.float32), triangles.copy()) for i, k in enumerate(CORNERS)]
    material = {'id': 0, 'name': 'rubber', 'color': [.02, .02, .02, 1.],
                'metallic': 0., 'alpha_mode': 'OPAQUE', 'transmission': 0., 'base_texture': None}
    return Scene(parts, [material], {}, b'fixture', 'prepared-fixture', [])


class AssemblyFitTests(unittest.TestCase):
    def test_separate_front_rear_tracks_ignore_cached_average(self):
        report = assembly_fit_report(inspection(), nominal(front_track=1.5, rear_track=1.9))
        dimensions = report['source']['dimensions']
        self.assertAlmostEqual(dimensions['wheelbase_metres'], 2.8)
        self.assertAlmostEqual(dimensions['front_track_metres'], 1.6)
        self.assertAlmostEqual(dimensions['rear_track_metres'], 1.7)
        mismatch = report['dimensional_mismatches']
        self.assertAlmostEqual(mismatch['front_track_metres']['source_minus_target_metres'], .1)
        self.assertAlmostEqual(mismatch['rear_track_metres']['source_minus_target_metres'], -.2)
        self.assertEqual(report['corner_order'], list(CORNERS))
        self.assertIsNone(report['rigid_fit'])

    def test_rigid_fit_recovers_known_rotation_translation_without_scale(self):
        source = layout()
        rotation = rotate_y(math.pi / 2) @ rotate_x(.14)
        translation = np.array([2.3, -.4, 5.2])
        target = transformed(source, rotation, translation)
        report = assembly_fit_report(inspection(source), explicit(target, '+X'))
        fit = report['rigid_fit']
        np.testing.assert_allclose(fit['rotation'], rotation, atol=2e-15)
        np.testing.assert_allclose(fit['translation_metres'], translation, atol=2e-15)
        self.assertAlmostEqual(fit['determinant'], 1.)
        self.assertEqual(fit['scale'], 1.)
        self.assertLess(fit['maximum_corner_error_metres'], 3e-15)
        self.assertFalse(report['transform_applied'])
        self.assertFalse(fit['compatibility_assessed'])
        self.assertIsNone(fit['fit_tolerance_metres'])
        # Target-body pitch changes the projected wheelbase while the shared
        # proper transform still fits exactly without changing any distance.
        self.assertAlmostEqual(report['target']['dimensions']['wheelbase_metres'], 2.8 * math.cos(.14))
        self.assertGreater(report['dimensional_mismatches']['wheelbase_metres']['source_minus_target_metres'], 0.)
        self.assertIn('projections', report['dimension_measurement_scope'])

    def test_declared_wheelbase_is_not_source_unit_correction(self):
        source = inspection(layout(.255, .16, .17))
        source['wheelbase'] = 2.55
        target = explicit(layout(2.55, 1.6, 1.7))
        before = copy.deepcopy(source)
        report = assembly_fit_report(source, target, declared_wheelbase_metres=2.55)
        entered = report['source']['entered_wheelbase']
        self.assertAlmostEqual(entered['measured_prepared_metres'], .255)
        self.assertAlmostEqual(entered['entered_to_measured_ratio'], 10.)
        self.assertAlmostEqual(entered['entered_minus_measured_metres'], 2.295)
        self.assertFalse(entered['used_to_scale_geometry'])
        self.assertEqual(report['rigid_fit']['scale'], 1.)
        self.assertGreater(report['rigid_fit']['maximum_corner_error_metres'], 1.)
        self.assertEqual(source, before)
        self.assertEqual(report['physical_compatibility'], 'unverified')

    def test_actual_historical_f50_reference_retains_known_dimensional_residual(self):
        # Independent actual-byte/reference analysis, documented in
        # docs/evidence/v043/rx8_nominal_tyre_reference.json. This is historical
        # body-relative Y evidence, not loaded ride height or runtime acceptance.
        source = {
            'FL': [-.8347450494766235, .331118643283844, -1.2991852760314941],
            'FR': [.8347450494766235, .331118643283844, -1.2991852760314941],
            'RL': [-.8317311406135559, .3492395877838135, 1.2991852760314941],
            'RR': [.8317311406135559, .3492395877838135, 1.2991852760314941],
        }
        target = explicit(layout(2.7, 1.5, 1.51, '-Z', .1985), '-Z')
        target['provenance'] = {'scope': 'historical body-relative pose only; current Y unverified'}
        report = assembly_fit_report(inspection(source, '-Z'), target)
        fit = report['rigid_fit']
        self.assertAlmostEqual(fit['maximum_corner_error_metres'], .09879600077426165, places=12)
        np.testing.assert_allclose(report['corner_residuals']['FL']['residual_metres'],
                                   [-.08474504947662354, 0., .050783130645840835], atol=2e-15)
        np.testing.assert_allclose(report['corner_residuals']['RR']['residual_metres'],
                                   [.0767311406135559, 0., -.050783130645840835], atol=2e-15)
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(report['physics_changed'])
        self.assertEqual(report['target']['provenance'], target['provenance'])

    def test_nominal_layout_never_guesses_y_from_source_radius_or_ride_height(self):
        source = layout()
        source['FL'][1] += .02
        source['FR'][1] += .02
        target = nominal()
        target.update(nominal_tyre_radius_metres=.32985, ride_height_metres=.125)
        first = assembly_fit_report(inspection(source), target)
        raised = {k: [v[0], v[1] + 20, v[2]] for k, v in source.items()}
        second = assembly_fit_report(inspection(raised), target)
        self.assertIsNone(first['rigid_fit'])
        self.assertFalse(first['target']['target_y_known'])
        self.assertFalse(first['planar_fit']['usable_as_3d_transform'])
        self.assertEqual(first['planar_fit'], second['planar_fit'])
        self.assertEqual(first['corner_residuals'], second['corner_residuals'])
        for corner in CORNERS:
            self.assertIsNone(first['target']['wheel_centers_metres'][corner][1])
            self.assertIsNone(first['corner_residuals'][corner]['residual_metres'][1])
            self.assertEqual(first['corner_residuals'][corner]['scope'], 'xz_only')
        with self.assertRaises(GarageError):
            apply_assembly_transform([[0., 0., 0.]], transform=first['planar_fit'])

    def test_nominal_yaw_fit_respects_independent_target_forward_axes(self):
        source = transformed(layout(), np.eye(3), np.array([4., .7, -2.]))
        report = assembly_fit_report(inspection(source), nominal(forward='-X'))
        fit = report['planar_fit']
        self.assertAlmostEqual(fit['determinant'], 1.)
        self.assertLess(fit['maximum_corner_error_metres'], 2e-15)
        self.assertEqual(fit['axes'], ['X', 'Z'])
        self.assertIsNone(report['rigid_fit'])

    def test_all_prepared_up_axes_forward_axes_and_units_retain_measured_metres(self):
        for forward, up_axis, unit in itertools.product(('+Z', '-Z', '+X', '-X'), UP_AXES, (1., .01, .001)):
            with self.subTest(forward=forward, up_axis=up_axis, unit=unit):
                desired = scene_for_centres(layout(forward=forward))
                rotation = np.array(coordinate_description(up_axis)['source_to_y_up_rotation'])
                raw_parts = [replace(p, positions=(p.positions @ rotation / unit).astype(np.float32),
                                     normals=(p.normals @ rotation).astype(np.float32)) for p in desired.parts]
                raw = replace(desired, parts=raw_parts)
                original = [p.positions.tobytes() for p in raw.parts]
                prepared = prepare_scene(raw, up_axis, unit)
                inferred = infer(prepared, forward, {k: {'role': 'tyre', 'corner': k} for k in CORNERS})
                report = assembly_fit_report(inferred, explicit(forward=forward))
                dimensions = report['source']['dimensions']
                np.testing.assert_allclose([dimensions['wheelbase_metres'], dimensions['front_track_metres'], dimensions['rear_track_metres']], [2.8, 1.6, 1.7], atol=3e-7, rtol=0)
                self.assertLess(report['rigid_fit']['maximum_corner_error_metres'], 3e-7)
                self.assertEqual([p.positions.tobytes() for p in raw.parts], original)

    def test_reflected_or_crossed_source_labels_are_rejected_without_reordering(self):
        bad_sets = []
        reflected = layout()
        for point in reflected.values():
            point[0] *= -1
        bad_sets.append(reflected)
        for a, b in (('FL', 'FR'), ('FL', 'RR'), ('FR', 'RL'), ('FL', 'RL')):
            swapped = layout()
            swapped[a], swapped[b] = swapped[b], swapped[a]
            bad_sets.append(swapped)
        for centers in bad_sets:
            with self.subTest(centers=centers):
                before = copy.deepcopy(centers)
                with self.assertRaisesRegex(GarageError, 'labels|crossed|degenerate'):
                    assembly_fit_report(inspection(centers), explicit())
                self.assertEqual(centers, before)

    def test_reflected_target_labels_and_wrong_forward_are_rejected(self):
        reflected = layout()
        for point in reflected.values():
            point[2] *= -1
        with self.assertRaises(GarageError):
            assembly_fit_report(inspection(), explicit(reflected))
        with self.assertRaises(GarageError):
            assembly_fit_report(inspection(), explicit(layout(), '-Z'))
        with self.assertRaises(GarageError):
            assembly_fit_report(inspection(layout(), '-Z'), explicit())

    def test_nonplanar_mirror_has_positive_determinant_and_retains_residual(self):
        source = layout()
        source['FL'][1] += .3
        source['RR'][1] += .1
        target = {k: [v[0], -v[1], v[2]] for k, v in source.items()}
        report = assembly_fit_report(inspection(source), explicit(target))
        fit = report['rigid_fit']
        self.assertEqual(fit['source_point_rank'], 3)
        self.assertTrue(fit['determinant_correction_applied'])
        self.assertAlmostEqual(fit['determinant'], 1.)
        self.assertGreater(fit['maximum_corner_error_metres'], .05)
        self.assertFalse(fit['compatibility_assessed'])

    def test_planar_perfect_fit_does_not_claim_handedness_or_physical_compatibility(self):
        report = assembly_fit_report(inspection(), explicit())
        self.assertEqual(report['rigid_fit']['source_point_rank'], 2)
        self.assertLess(report['rigid_fit']['maximum_corner_error_metres'], 1e-14)
        self.assertEqual(report['physical_compatibility'], 'unverified')
        self.assertTrue(any('Coplanar centres' in x for x in report['limits']))
        self.assertFalse(report['runtime_verified'])
        self.assertFalse(report['suspension_or_mounts_changed'])

    def test_modest_axle_skew_stays_measured_not_threshold_rejected(self):
        source = layout()
        source['FL'][2] += .1
        source['RR'][0] += .03
        report = assembly_fit_report(inspection(source), explicit())
        dimensions = report['source']['dimensions']
        self.assertAlmostEqual(dimensions['left_wheelbase_metres'], 2.9)
        self.assertAlmostEqual(dimensions['right_wheelbase_metres'], 2.8)
        self.assertGreater(report['rigid_fit']['maximum_corner_error_metres'], .01)
        self.assertIsNone(report['rigid_fit']['fit_tolerance_metres'])

    def test_missing_extra_malformed_and_nonfinite_centres_raise_actionable_error(self):
        missing = layout(); del missing['FL']
        extra = layout(); extra['XX'] = [0., 0., 0.]
        cases = [None, [], missing, extra]
        for bad in (float('nan'), float('inf'), float('-inf'), None, '0.8', True):
            broken = layout(); broken['FL'][0] = bad; cases.append(broken)
        wrong_shape = layout(); wrong_shape['FL'] = [1., 2.]; cases.append(wrong_shape)
        for centers in cases:
            with self.subTest(centers=centers):
                with self.assertRaises(GarageError) as context:
                    assembly_fit_report({'forward': '+Z', 'wheel_centers': centers}, explicit())
                self.assertEqual(context.exception.code, 'assembly_fit')
                with self.assertRaises(GarageError):
                    assembly_fit_report(inspection(), explicit(centers) if centers is not None else {'kind': 'explicit_centres', 'forward': '+Z'})

    def test_degenerate_coincident_and_collinear_centres_are_rejected(self):
        coincident = {k: [0., .3, 0.] for k in CORNERS}
        collinear = {k: [float(i), .3, float(i)] for i, k in enumerate(CORNERS)}
        duplicate = layout(); duplicate['FR'] = duplicate['FL'].copy()
        almost_line = {'FL': [1e-16, .3, 1.], 'FR': [-1e-16, .3, 1.],
                       'RL': [1e-16, .3, -1.], 'RR': [-1e-16, .3, -1.]}
        for centers in (coincident, collinear, duplicate, almost_line):
            with self.subTest(centers=centers):
                with self.assertRaises(GarageError):
                    assembly_fit_report(inspection(centers), explicit())

    def test_invalid_frame_units_nominal_dimensions_and_ambiguous_height_are_rejected(self):
        for forward in ('+Y', '+Q', None, 1, [], {}):
            with self.subTest(forward=forward):
                with self.assertRaises(GarageError):
                    assembly_fit_report(inspection(forward=forward) if isinstance(forward, str) and forward in ('+Z', '-Z', '+X', '-X') else {'forward': forward, 'wheel_centers': layout()}, explicit())
                target = nominal(); target['forward'] = forward
                with self.assertRaises(GarageError):
                    assembly_fit_report(inspection(), target)
        for value in (0, -1, True, '2.8', None, float('nan'), float('inf')):
            with self.subTest(value=value):
                with self.assertRaises(GarageError):
                    assembly_fit_report(inspection(), nominal(wheelbase=value))
                if value is not None:
                    with self.assertRaises(GarageError):
                        assembly_fit_report(inspection(), nominal(), declared_wheelbase_metres=value)
        wrong_units = inspection(); wrong_units['coordinate_system'] = 'centimetres'
        with self.assertRaises(GarageError):
            assembly_fit_report(wrong_units, explicit())
        target = nominal(); target['center_y_metres'] = .1985
        with self.assertRaisesRegex(GarageError, 'unknown target Y'):
            assembly_fit_report(inspection(), target)
        target = explicit(); target['wheel_centers_metres']['FL'][1] = None
        with self.assertRaises(GarageError):
            assembly_fit_report(inspection(), target)

    def test_diagnostic_is_serializable_and_does_not_alias_inputs_or_provenance(self):
        source, target = inspection(), explicit()
        target['provenance']['row_ids'] = [76, 16654]
        old_source, old_target = copy.deepcopy(source), copy.deepcopy(target)
        report = assembly_fit_report(source, target)
        json.dumps(report, allow_nan=False)
        self.assertEqual(source, old_source)
        self.assertEqual(target, old_target)
        report['source']['wheel_centers_metres']['FL'][0] = 999
        report['target']['provenance']['row_ids'].append(999)
        self.assertEqual(source, old_source)
        self.assertEqual(target, old_target)

    def test_finite_extreme_dimensions_cannot_emit_infinite_ratios(self):
        tiny = inspection(layout(2.8e-310, 1.6e-310, 1.7e-310, height=0.))
        with self.assertRaisesRegex(GarageError, 'numeric range'):
            assembly_fit_report(tiny, nominal(), declared_wheelbase_metres=2.8)
        with self.assertRaisesRegex(GarageError, 'numeric range'):
            assembly_fit_report(inspection(), nominal(2.8e-310, 1.6e-310, 1.7e-310))

    def test_shared_body_transform_preserves_attachment_distances_normals_and_winding(self):
        rotation = rotate_y(.4) @ rotate_x(.2)
        translation = np.array([1.2, -.7, .6])
        transform = {'rotation': rotation, 'translation_metres': translation, 'scale': 1.}
        # Body panel, wheel-local datum already expressed in the SAME body
        # frame, and an arbitrary attached point all receive the same map.
        points = np.array([[0., 0., 0.], [.8, 0., 0.], [0., 1., 0.], [.75, .3, -1.4], [.76, .3, -1.4]])
        normals = np.tile([0., 0., 2.], (len(points), 1))
        originals = (points.tobytes(), normals.tobytes(), rotation.tobytes(), translation.tobytes())
        moved, turned = apply_assembly_transform(points, transform=transform, normals=normals)
        np.testing.assert_allclose(moved, points @ rotation.T + translation)
        np.testing.assert_allclose(turned, normals @ rotation.T)
        for i, j in itertools.combinations(range(len(points)), 2):
            self.assertAlmostEqual(np.linalg.norm(moved[i] - moved[j]), np.linalg.norm(points[i] - points[j]))
        np.testing.assert_allclose(np.linalg.norm(turned, axis=1), 2.)
        face_normal = np.cross(moved[1] - moved[0], moved[2] - moved[0])
        self.assertGreater(float(face_normal @ turned[0]), 0.)
        self.assertFalse(np.shares_memory(moved, points))
        self.assertFalse(np.shares_memory(turned, normals))
        self.assertEqual(originals, (points.tobytes(), normals.tobytes(), rotation.tobytes(), translation.tobytes()))

    def test_application_requires_explicit_transform_and_rejects_local_rotating_frames(self):
        identity = {'rotation': np.eye(3), 'translation_metres': [0., 0., 0.]}
        with self.assertRaises(TypeError):
            apply_assembly_transform([[0., 0., 0.]])
        with self.assertRaises(GarageError):
            apply_assembly_transform([[0., 0., 0.]], transform=None)
        for space in ('wheel_local', 'rotating_local', 'pre_spin', 'target_body'):
            with self.subTest(space=space):
                with self.assertRaisesRegex(GarageError, 'prepared body frame'):
                    apply_assembly_transform([[0., 1., 0.]], transform=identity, coordinate_space=space)
        # Explicitly demonstrate why an off-axis local offset is not an
        # invariant body displacement: a 90-degree X spin turns Y into Z.
        np.testing.assert_allclose(rotate_x(math.pi / 2) @ [0., .1, 0.], [0., 0., .1], atol=1e-16)

    def test_application_rejects_scale_shear_reflection_and_nonfinite_geometry(self):
        matrices = (np.diag([-1., 1., 1.]), np.diag([1.1, 1.1, 1.1]),
                    np.array([[1., .05, 0.], [0., 1., 0.], [0., 0., 1.]]))
        for rotation in matrices:
            with self.subTest(rotation=rotation):
                with self.assertRaises(GarageError):
                    apply_assembly_transform([[0., 0., 0.]], transform={'rotation': rotation, 'translation_metres': [0., 0., 0.]})
        with self.assertRaises(GarageError):
            apply_assembly_transform([[0., 0., 0.]], transform={'rotation': np.eye(3), 'translation_metres': [0., 0., 0.], 'scale': 2.})
        for points, normals in (([[float('nan'), 0., 0.]], None), ([[0., 0.]], None),
                                ([[0., 0., 0.]], [[0., float('inf'), 0.]]),
                                ([[0., 0., 0.]], [[0., 1., 0.], [0., 1., 0.]])):
            with self.subTest(points=points, normals=normals):
                with self.assertRaises(GarageError):
                    apply_assembly_transform(points, normals=normals, transform={'rotation': np.eye(3), 'translation_metres': [0., 0., 0.]})

    def test_explicit_fit_application_is_optional_and_leaves_inspection_unchanged(self):
        source = inspection()
        before = copy.deepcopy(source)
        target = explicit(transformed(source['wheel_centers'], np.eye(3), np.array([.2, -.1, .4])))
        report = assembly_fit_report(source, target)
        points = np.array([source['wheel_centers'][k] for k in CORNERS])
        moved, normals = apply_assembly_transform(points, transform=report['rigid_fit'])
        np.testing.assert_allclose(moved, [target['wheel_centers_metres'][k] for k in CORNERS])
        self.assertIsNone(normals)
        self.assertEqual(source, before)
        self.assertFalse(report['transform_applied'])
        self.assertFalse(report['source_geometry_changed'])


if __name__ == '__main__':
    unittest.main()
