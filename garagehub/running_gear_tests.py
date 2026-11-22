"""Request/proof checks; actual native byte parity is an integration test.

The reference-frame regression reproduces the prior preview mathematics and
compares exact float64 bytes.  These tests make no game-fit or runtime claim.
"""
from __future__ import annotations

import copy
import hashlib
import json
import math
import unittest
from unittest.mock import patch

import numpy as np

from .car_profiles import ASSETS, PROFILE
from .errors import GarageError
from .running_gear import (AXLES, AXLE_FIELDS, CORNERS, normalize_running_gear,
                           require_inherited_running_gear, resolve_running_gear)


def matching_request():
    resolved = resolve_running_gear()['resolved']
    return {'schema': 1, 'mode': 'custom_dimensions',
            **{axle: copy.deepcopy(resolved[axle]) for axle in AXLES}}


def rehash(plan):
    plan.pop('plan_digest', None)
    plan['plan_digest'] = hashlib.sha256(json.dumps(plan, sort_keys=True,
        separators=(',', ':'), ensure_ascii=False, allow_nan=False).encode()).hexdigest()
    return plan


class RunningGearTests(unittest.TestCase):
    def test_default_request_is_canonical_and_does_not_gain_editable_dimensions(self):
        for value in (None, {}, {'mode': 'profile'}, {'schema': 1, 'mode': 'profile'}):
            with self.subTest(value=value):
                self.assertEqual(normalize_running_gear(value), {'schema': 1, 'mode': 'profile'})
        plan = resolve_running_gear()
        self.assertTrue(plan['ready'])
        self.assertTrue(plan['native_no_op'])
        self.assertEqual(plan['blockers'], [])
        self.assertEqual(plan['asset_sha256'], ASSETS)
        self.assertEqual(plan['wheel_output_sha256']['hq/wheel'], ASSETS['hq/wheel'])
        self.assertNotEqual(plan['wheel_output_sha256']['race/wheel'], ASSETS['race/wheel'])
        self.assertEqual(plan['corners'], PROFILE['wheel_geometry'])
        self.assertEqual(plan['requested_corners'], PROFILE['wheel_geometry'])
        self.assertFalse(plan['current_mount_verified'])
        self.assertFalse(plan['runtime_verified'])
        self.assertFalse(plan['physics_changed'])
        self.assertTrue(plan['requires_template_authentication'])
        self.assertEqual(plan['configuration_policy'], 'inherit_selected_destination_unchanged')

    def test_profile_frames_preserve_the_previous_preview_float_bytes(self):
        plan = resolve_running_gear()
        for corner in CORNERS:
            with self.subTest(corner=corner):
                reference = PROFILE['wheel_geometry'][corner]
                center = np.asarray(reference['center_metres'], dtype=float)
                width = float(reference['tyre_width_metres'])
                side = 1. if corner[1] == 'L' else -1.
                rotation = np.diag([side, 1., side])
                local_center = np.asarray([width / 2., 0., 0.])
                shift = center - rotation @ local_center
                frame = plan['frames'][corner]
                self.assertEqual(np.asarray(plan['corners'][corner]['center_metres'], dtype=float).tobytes(),
                                 center.tobytes())
                for key, expected in (('reference_local_center_metres', local_center),
                        ('local_to_reference_rotation', rotation),
                        ('local_to_reference_translation_metres', shift)):
                    self.assertEqual(np.asarray(frame[key], dtype=float).tobytes(), expected.tobytes())
                # Subtracting then re-adding the half-width can cost one ULP;
                # the frame bytes above must still equal the old path exactly.
                np.testing.assert_allclose(rotation @ local_center + shift, center,
                                           rtol=0., atol=np.finfo(float).eps)

    def test_explicit_profile_dimensions_are_native_no_op_but_keep_distinct_intent(self):
        profile, custom = resolve_running_gear(), resolve_running_gear(matching_request())
        self.assertTrue(custom['native_no_op'])
        for key in ('resolved', 'corners', 'requested_corners', 'frames', 'asset_sha256',
                    'capability_revision', 'capability_digest'):
            self.assertEqual(profile[key], custom[key], key)
        self.assertNotEqual(profile['requested'], custom['requested'])
        self.assertNotEqual(profile['plan_digest'], custom['plan_digest'])
        self.assertEqual(require_inherited_running_gear(custom), custom)

    def test_front_and_rear_radii_widths_and_geometric_height_remain_distinct(self):
        plan = resolve_running_gear()
        self.assertEqual(plan['resolved']['wheelbase_metres'], 2.55)
        self.assertEqual(plan['resolved']['front']['tyre_diameter_metres'], .6433)
        self.assertEqual(plan['resolved']['rear']['tyre_diameter_metres'], .6358)
        self.assertEqual(plan['corners']['FL']['tyre_radius_metres'], .32165)
        self.assertEqual(plan['corners']['RL']['tyre_radius_metres'], .3179)
        self.assertEqual(plan['corners']['FL']['tyre_width_metres'], .235)
        self.assertEqual(plan['corners']['RL']['tyre_width_metres'], .255)
        self.assertEqual(plan['resolved']['front']['center_y_metres'], .19565)
        self.assertNotIn('ride_height', json.dumps(plan))
        self.assertNotIn('nominal_outer_radius_metres', json.dumps(plan))

    def test_every_individual_changed_dimension_is_blocked_without_resolving_it(self):
        inherited = resolve_running_gear()
        for axle in AXLES:
            for field in AXLE_FIELDS:
                with self.subTest(axle=axle, field=field):
                    request = matching_request()
                    request[axle][field] += .01
                    plan = resolve_running_gear(request)
                    self.assertFalse(plan['ready'])
                    self.assertFalse(plan['native_no_op'])
                    self.assertEqual(plan['resolved'], inherited['resolved'])
                    self.assertEqual(plan['corners'], inherited['corners'])
                    self.assertEqual(plan['frames'], inherited['frames'])
                    self.assertEqual(len(plan['blockers']), 1)
                    blocker = plan['blockers'][0]
                    self.assertEqual(blocker['field'], axle+'.'+field)
                    self.assertEqual(blocker['requested'], request[axle][field])
                    self.assertEqual(blocker['resolved'], inherited['resolved'][axle][field])
                    self.assertNotEqual(plan['requested_corners'], plan['corners'])
                    with self.assertRaises(GarageError) as error:
                        require_inherited_running_gear(plan)
                    self.assertEqual(error.exception.code, 'running_gear_unsupported')

    def test_all_changed_fields_are_reported_and_no_nearest_default_rounding_occurs(self):
        request = matching_request()
        for axle in AXLES:
            for field in AXLE_FIELDS:
                request[axle][field] = math.nextafter(request[axle][field], math.inf)
        plan = resolve_running_gear(request)
        self.assertEqual({row['field'] for row in plan['blockers']},
                         {axle+'.'+field for axle in AXLES for field in AXLE_FIELDS})
        self.assertEqual(len(plan['blockers']), 10)
        self.assertFalse(plan['rounding_applied'])

    def test_invalid_schema_unknown_or_conflicting_inputs_are_rejected(self):
        bad = [False, 1, [], 'profile', {'schema': True}, {'schema': 2},
               {'mode': 'fit'}, {'mode': []}, {'mode': 'profile', 'front': {}},
               {'mode': 'profile', 'installation': {'mode': 'replace', 'replace_car_id': 82}},
               {'mode': 'custom_dimensions'}, {**matching_request(), 'wheelbase_metres': 2.55},
               {**matching_request(), 'resolved': resolve_running_gear()['resolved']}]
        for value in bad:
            with self.subTest(value=value), self.assertRaises(GarageError):
                normalize_running_gear(value)
        for field in ('wheelbase_metres', 'ride_height_metres', 'radius_metres', 'center_metres'):
            value = matching_request()
            value['front'][field] = 1.
            with self.subTest(field=field), self.assertRaises(GarageError):
                normalize_running_gear(value)
        for axle in AXLES:
            for field in AXLE_FIELDS:
                value = matching_request()
                del value[axle][field]
                with self.subTest(axle=axle, field=field), self.assertRaises(GarageError):
                    normalize_running_gear(value)

    def test_nonfinite_bool_non_numeric_and_out_of_range_dimensions_are_rejected(self):
        for field in AXLE_FIELDS:
            for number in (True, False, None, '1', float('nan'), float('inf'),
                           -float('inf'), 10**400, 20., -20.):
                request = matching_request()
                request['front'][field] = number
                with self.subTest(field=field, value=str(number)), self.assertRaises(GarageError):
                    normalize_running_gear(request)
        for field in ('track_metres', 'tyre_diameter_metres', 'tyre_width_metres'):
            for number in (0., -1., 5e-324):
                request = matching_request()
                request['rear'][field] = number
                with self.subTest(field=field, value=number), self.assertRaises(GarageError):
                    normalize_running_gear(request)

    def test_axle_order_and_derived_separation_are_checked_without_a_second_wheelbase_input(self):
        for front, rear in ((1., 1.), (2., 1.), (0., .1), (-12., 12.)):
            request = matching_request()
            request['front']['center_z_metres'], request['rear']['center_z_metres'] = front, rear
            with self.subTest(front=front, rear=rear), self.assertRaises(GarageError):
                normalize_running_gear(request)

    def test_callers_cannot_modify_input_profile_or_other_resolution_results(self):
        request, profile_before, assets_before = matching_request(), copy.deepcopy(PROFILE), copy.deepcopy(ASSETS)
        request_before = copy.deepcopy(request)
        plan = resolve_running_gear(request)
        fresh = require_inherited_running_gear(plan)
        fresh['corners']['FL']['center_metres'][0] += 100.
        plan['requested']['front']['track_metres'] += 1.
        plan['resolved']['front']['track_metres'] += 2.
        plan['corners']['FL']['center_metres'][0] += 3.
        plan['asset_sha256']['hq/wheel'] = '0'*64
        self.assertEqual(request, request_before)
        self.assertEqual(PROFILE, profile_before)
        self.assertEqual(ASSETS, assets_before)
        self.assertEqual(resolve_running_gear()['corners'], profile_before['wheel_geometry'])

    def test_rehashing_forged_resolved_or_capability_fields_cannot_authorize_them(self):
        changes = [lambda p: p['corners']['FL']['center_metres'].__setitem__(0, 3.),
                   lambda p: p['resolved']['front'].__setitem__('track_metres', 2.),
                   lambda p: p['frames']['FR']['local_to_reference_rotation'][0].__setitem__(0, 1.),
                   lambda p: p['requested_corners']['RR'].__setitem__('tyre_radius_metres', .5),
                   lambda p: p['asset_sha256'].__setitem__('race/wheel', '0'*64),
                   lambda p: p['wheel_output_sha256'].__setitem__('race/wheel', ASSETS['race/wheel']),
                   lambda p: p.__setitem__('capability_revision', 999),
                   lambda p: p.__setitem__('capability_digest', '0'*64),
                   lambda p: p.__setitem__('profile_id', 'another-profile'),
                   lambda p: p.__setitem__('native_no_op', 1),
                   lambda p: p.__setitem__('physics_changed', 0),
                   lambda p: p.__setitem__('extra_authorization', True)]
        for index, change in enumerate(changes):
            plan = resolve_running_gear()
            change(plan)
            with self.subTest(change=index), self.assertRaises(GarageError) as error:
                require_inherited_running_gear(rehash(plan))
            self.assertEqual(error.exception.code, 'running_gear_plan')
        request = matching_request()
        request['front']['track_metres'] += .1
        plan = resolve_running_gear(request)
        plan.update(ready=True, native_no_op=True, blockers=[])
        with self.assertRaises(GarageError):
            require_inherited_running_gear(rehash(plan))

    def test_profile_or_asset_change_invalidates_previously_resolved_proof(self):
        plan = resolve_running_gear()
        for module, change in (('garagehub.running_gear.PROFILE', {'label': 'Changed profile'}),
                               ('garagehub.running_gear.ASSETS', {'hq/wheel': 'a'*64})):
            with self.subTest(module=module), patch.dict(module, change), self.assertRaises(GarageError):
                require_inherited_running_gear(plan)

    def test_changed_or_matching_requests_never_choose_nominal_tyre_edits_for_any_destination(self):
        request = matching_request()
        for changed in (False, True):
            if changed:
                request['front']['tyre_diameter_metres'] += .1
            plan = resolve_running_gear(request)
            self.assertEqual(plan['configuration_policy'], 'inherit_selected_destination_unchanged')
            self.assertFalse(plan['physics_changed'])
            self.assertNotIn('tyre_size_plan', plan)
            self.assertNotIn('insertions', plan)
            self.assertNotIn('redirects', plan)


if __name__ == '__main__':
    unittest.main()
