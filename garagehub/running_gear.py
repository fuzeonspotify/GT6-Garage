"""Requested wheel references and the currently supported inherited native path.

This resolver writes no geometry or database.  Resolved dimensions describe the
fixed profile reference used by the preview, not measured game suspension poses.
Only unchanged running gear is coherent across the existing native build path.
A changed request is retained for review, with every unsupported field named;
it never changes the resolved reference or authorizes a partial native edit.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
from collections.abc import Mapping

import numpy as np

from .car_profiles import ASSETS, PROFILE
from .errors import GarageError
from .stock_race_wheel import STOCK_SHA256, STOCK_SHOWROOM_SHA256


SCHEMA = 1
CAPABILITY_REVISION = 2
CORNERS = ('FL', 'FR', 'RL', 'RR')
AXLES = ('front', 'rear')
AXLE_FIELDS = ('center_y_metres', 'center_z_metres', 'track_metres',
               'tyre_diameter_metres', 'tyre_width_metres')
# These bounds reject implausible/unstable requests; they are not a demonstrated
# native sizing range, clearance test, or handling-compatibility claim.
REQUEST_BOUNDS = {
    'center_y_metres': (-12., 12.),
    'center_z_metres': (-12., 12.),
    'track_metres': (.1, 6.),
    'tyre_diameter_metres': (.05, 4.),
    'tyre_width_metres': (.01, 2.),
}
WHEELBASE_BOUNDS_METRES = (.3, 12.)
_FIELD_LABELS = {
    'center_y_metres': 'wheel reference height',
    'center_z_metres': 'axle reference position',
    'track_metres': 'wheel reference track',
    'tyre_diameter_metres': 'tyre reference diameter',
    'tyre_width_metres': 'tyre reference width',
}


def _need(condition, message, code='running_gear_request'):
    if not condition:
        raise GarageError(message, code)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise GarageError('Running-gear data must contain finite JSON values.',
                          'running_gear_plan') from exc


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _number(value, label, bounds):
    _need(type(value) in (int, float), label+' must be a finite JSON number.')
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise GarageError(label+' exceeds the supported numeric range.',
                          'running_gear_request') from exc
    low, high = bounds
    _need(math.isfinite(result) and low <= result <= high,
          label+' must be between '+str(low)+' and '+str(high)+' metres.')
    return result


def _axle(value, axle):
    _need(isinstance(value, Mapping) and set(value) == set(AXLE_FIELDS),
          'Specify exactly the five '+axle+' wheel-reference dimensions in metres.')
    return {field: _number(value[field], axle.capitalize()+' '+_FIELD_LABELS[field],
                            REQUEST_BOUNDS[field]) for field in AXLE_FIELDS}


def _validate_axle_order(front, rear):
    _need(front['center_z_metres'] < rear['center_z_metres'],
          'The front axle must have a smaller Z reference than the rear axle; front is -Z.')
    wheelbase = rear['center_z_metres'] - front['center_z_metres']
    _need(WHEELBASE_BOUNDS_METRES[0] <= wheelbase <= WHEELBASE_BOUNDS_METRES[1],
          'The derived axle separation must be between 0.3 and 12 metres.')
    return wheelbase


def normalize_running_gear(value=None):
    """Return a private canonical request, never an approval or a native plan.

An omitted/empty request means inherited profile settings.  Schema defaults to
1 on input; a supplied version must be exactly integer 1.  Custom requests must
contain complete front/rear rows.  Wheelbase is derived, never another input.
"""
    value = {} if value is None else value
    _need(isinstance(value, Mapping), 'Running gear must be an object.')
    schema, mode = value.get('schema', SCHEMA), value.get('mode', 'profile')
    _need(type(schema) is int and schema == SCHEMA,
          'The running-gear request uses an unsupported schema.')
    _need(mode in ('profile', 'checked_stock', 'custom_dimensions'),
          'Choose the checked Supra wheels, manual placement or explicit dimensions.')
    allowed = {'schema', 'mode', 'wheel_set'}
    if mode == 'custom_dimensions':
        allowed.update(AXLES)
    elif mode == 'checked_stock':
        allowed.update(('source_tyres', 'tyre_size'))
    _need(not set(value)-allowed, 'Unknown running-gear setting.')
    result = {'schema': SCHEMA, 'mode': mode}
    if 'wheel_set' in value:
        _need(value['wheel_set'] == 'supra_factory', 'Choose an available GT6 wheel set.')
        result['wheel_set'] = value['wheel_set']
    if 'tyre_size' in value:
        _need(value['tyre_size'] in ('game', 'match_source'), 'Choose the GT6 tyre size or match the original tyre size.')
        if value['tyre_size'] == 'game':
            result['tyre_size'] = 'game'
    if 'source_tyres' in value:
        parts = value['source_tyres']
        _need(isinstance(parts, Mapping) and set(parts) <= set(CORNERS)
              and all(isinstance(pid, str) and 0 < len(pid) <= 256 for pid in parts.values())
              and len(set(parts.values())) == len(parts),
              'Select a different source tyre for each corner.')
        if parts:
            result['source_tyres'] = {corner: parts[corner] for corner in CORNERS if corner in parts}
    if mode == 'custom_dimensions':
        _need({'front', 'rear'} <= set(value), 'Custom running gear needs both axle rows.')
        result.update({axle: _axle(value[axle], axle) for axle in AXLES})
        _validate_axle_order(result['front'], result['rear'])
    return result


def _profile_reference():
    """Snapshot and check the server-owned profile; asset bytes authenticate later."""
    profile, assets = copy.deepcopy(PROFILE), copy.deepcopy(ASSETS)
    _need(isinstance(profile.get('id'), str) and bool(profile['id'])
          and isinstance(assets, dict) and bool(assets)
          and all(isinstance(key, str) and isinstance(value, str) and len(value) == 64
                  and all(c in '0123456789abcdef' for c in value)
                  for key, value in assets.items()),
          'The running-gear profile or native asset identities are incomplete.',
          'running_gear_profile')
    geometry = profile.get('wheel_geometry')
    _need(isinstance(geometry, Mapping) and set(geometry) == set(CORNERS),
          'The profile must contain four explicit wheel references.', 'running_gear_profile')
    expected = {'center_metres', 'tyre_radius_metres', 'tyre_width_metres'}
    for corner in CORNERS:
        row = geometry[corner]
        _need(isinstance(row, Mapping) and set(row) == expected,
              'The '+corner+' profile wheel reference is incomplete.', 'running_gear_profile')
        center = row['center_metres']
        _need(isinstance(center, (list, tuple)) and len(center) == 3
              and all(type(v) in (int, float) and math.isfinite(v) for v in center)
              and all(type(row[k]) in (int, float) and math.isfinite(row[k]) and row[k] > 0
                      for k in ('tyre_radius_metres', 'tyre_width_metres')),
              'The '+corner+' profile dimensions are invalid.', 'running_gear_profile')
    resolved = {}
    for axle, left, right in (('front', 'FL', 'FR'), ('rear', 'RL', 'RR')):
        l, r = geometry[left], geometry[right]
        lc, rc = l['center_metres'], r['center_metres']
        _need(lc[0] < 0 < rc[0] and lc[0] == -rc[0] and lc[1:] == rc[1:]
              and l['tyre_radius_metres'] == r['tyre_radius_metres']
              and l['tyre_width_metres'] == r['tyre_width_metres'],
              'The profile requires a separately reviewed asymmetric wheel contract.',
              'running_gear_profile')
        resolved[axle] = _axle({
            'center_y_metres': lc[1], 'center_z_metres': lc[2],
            'track_metres': rc[0]-lc[0],
            'tyre_diameter_metres': l['tyre_radius_metres']*2.,
            'tyre_width_metres': l['tyre_width_metres']}, axle)
    resolved['wheelbase_metres'] = _validate_axle_order(resolved['front'], resolved['rear'])
    # Keep every existing corner value, rather than reconstructing the native
    # defaults through a new rounding, nominal specification, or body transform.
    corners = copy.deepcopy(dict(geometry))
    frames = {}
    for corner in CORNERS:
        row = corners[corner]
        center = np.asarray(row['center_metres'], dtype=float)
        side = 1. if corner[1] == 'L' else -1.
        rotation = np.diag([side, 1., side])
        local_center = np.asarray([float(row['tyre_width_metres']) / 2., 0., 0.])
        shift = center - rotation @ local_center
        frames[corner] = {'reference_local_center_metres': local_center.tolist(),
            'local_to_reference_rotation': rotation.tolist(),
            'local_to_reference_translation_metres': shift.tolist()}
    return profile, assets, resolved, corners, frames


def _requested_corners(request, resolved_corners):
    if request['mode'] != 'custom_dimensions':
        return copy.deepcopy(resolved_corners)
    result = {}
    for corner in CORNERS:
        axle = request['front' if corner[0] == 'F' else 'rear']
        side = -1. if corner[1] == 'L' else 1.
        result[corner] = {
            'center_metres': [side*axle['track_metres']/2., axle['center_y_metres'],
                              axle['center_z_metres']],
            'tyre_radius_metres': axle['tyre_diameter_metres']/2.,
            'tyre_width_metres': axle['tyre_width_metres']}
    return result


def resolve_running_gear(value=None):
    """Resolve only the inherited path; preserve and explain unsupported intent.

``resolved``, ``corners`` and ``frames`` remain fixed reference data even when a
custom request is blocked.  ``requested_corners`` are proposed geometry only.
No nominal database units, tyre-size choices or loaded dimensions are inferred.
"""
    requested = normalize_running_gear(value)
    profile, assets, resolved, corners, frames = _profile_reference()
    blockers = []
    if requested['mode'] == 'custom_dimensions':
        for axle in AXLES:
            for field in AXLE_FIELDS:
                wanted, inherited = requested[axle][field], resolved[axle][field]
                if wanted != inherited:
                    blockers.append({'code': 'running_gear_unsupported',
                        'field': axle+'.'+field, 'requested': wanted, 'resolved': inherited,
                        'capability_revision': CAPABILITY_REVISION,
                        'message': axle.capitalize()+' '+_FIELD_LABELS[field]
                            +' differs from the inherited profile. Coherent native adjustment '
                            'is not implemented; restore the profile value to build.'})
    profile_digest = _digest(profile)
    factory = requested.get('wheel_set') == 'supra_factory'
    wheel_output = {'hq/wheel': STOCK_SHOWROOM_SHA256 if factory else assets['hq/wheel'], 'race/wheel': STOCK_SHA256}
    capabilities = {'revision': CAPABILITY_REVISION, 'profile_id': profile['id'],
        'profile_contract_sha256': profile_digest, 'asset_sha256': assets,
        'wheel_output_sha256': wheel_output,
        'native_policy': 'factory_showroom_and_race_wheels' if factory else 'stock_packed_race_wheel_and_template_showroom',
        'custom_dimension_changes_supported': False, 'request_bounds_metres': REQUEST_BOUNDS,
        'derived_wheelbase_bounds_metres': WHEELBASE_BOUNDS_METRES}
    plan = {'schema': SCHEMA, 'kind': 'inherited_running_gear_plan',
        'requested': requested, 'resolved': resolved, 'corners': corners,
        'requested_corners': _requested_corners(requested, corners), 'frames': frames,
        'native_no_op': not blockers, 'ready': not blockers, 'blockers': blockers,
        'capability_revision': CAPABILITY_REVISION, 'capability_digest': _digest(capabilities),
        'profile_id': profile['id'], 'profile_contract_sha256': profile_digest,
        'asset_sha256': assets, 'wheel_output_sha256': wheel_output,
        'native_policy': capabilities['native_policy'],
        'configuration_policy': 'inherit_selected_destination_unchanged',
        'requires_template_authentication': True,
        'reference_scope': 'Fixed profile geometry reference; not a loaded suspension pose or nominal physical configuration.',
        'current_mount_verified': False, 'runtime_verified': False,
        'physics_changed': False, 'rounding_applied': False}
    plan['plan_digest'] = _digest(plan)
    return plan


def require_inherited_running_gear(plan):
    """Recompute the complete proof and return a fresh allowed no-op plan.

Checking a caller-supplied digest alone is insufficient: a caller can hash a
forged result.  Exact canonical comparison also distinguishes JSON booleans
from numeric lookalikes and rejects extra fields or outdated capabilities.
"""
    _need(isinstance(plan, Mapping) and 'requested' in plan,
          'Request a current running-gear plan before building.', 'running_gear_plan')
    fresh = resolve_running_gear(plan['requested'])
    _need(hmac.compare_digest(_json(dict(plan)), _json(fresh)),
          'The running-gear proof changed or is stale. Review the current assembly.',
          'running_gear_plan')
    if not fresh['native_no_op']:
        raise GarageError(' '.join(row['message'] for row in fresh['blockers']),
                          'running_gear_unsupported', {'blockers': copy.deepcopy(fresh['blockers'])})
    return fresh
