"""Expose source units, reviewed native centres and nominal DB data separately.

These reports never replace the existing profile's applied body/rim transforms.
The reference-wheelbase tolerance covers the UI's three decimal places; it is
not a physical compatibility or native wheel-mount acceptance threshold.
"""
from __future__ import annotations

from .assembly_fit import assembly_fit_report
from .car_profiles import PROFILE
from .errors import GarageError
from .wheel_fit import native_wheel_geometry


REFERENCE_WHEELBASE_TOLERANCE_METRES = .001


def profile_assembly_review(inspection, declared_wheelbase_metres=None, profile=None):
    profile = PROFILE if profile is None else profile
    contract = {
        'kind': 'explicit_centres', 'forward': '-Z',
        'wheel_centers_metres': {corner: native_wheel_geometry(corner, profile)['center_metres']
                                for corner in ('FL', 'FR', 'RL', 'RR')},
        'provenance': {'kind': 'reviewed_native_profile', 'profile_id': profile['id'],
                       'scope': 'Hash-pinned profile body frame; current runtime mount state is unverified.'},
    }
    try:
        report = assembly_fit_report(inspection, contract,
                                     declared_wheelbase_metres=declared_wheelbase_metres)
    except GarageError as exc:
        return {'available': False, 'issue': str(exc), 'transform_applied': False,
                'runtime_verified': False}
    report['available'] = True
    return report


def reference_wheelbase_review(assembly):
    if not assembly.get('available'):
        return {'available': False, 'consistent': None, 'action': None,
                'scope': 'Enter a reference and review four tyre corners to compare source units.'}
    values = assembly['source']['entered_wheelbase']
    difference = values['entered_minus_measured_metres']
    consistent = None if difference is None else abs(difference) <= REFERENCE_WHEELBASE_TOLERANCE_METRES
    action = None
    if consistent is False:
        action = ('The entered reference wheelbase differs from the prepared model. '
                  'Review the tyre assignments and source units; use Set units from wheelbase '
                  'to apply the reference explicitly, or correct the reference value.')
    return {**values, 'available': True, 'consistent': consistent, 'action': action,
            'comparison_tolerance_metres': REFERENCE_WHEELBASE_TOLERANCE_METRES,
            'scope': 'Reference/source consistency at the UI millimetre precision; not a target fit tolerance.'}


def selected_target_assembly_review(inspection, target_geometry, declared_wheelbase_metres=None):
    chassis = target_geometry.get('physical', {}).get('chassis', {})
    nominal = chassis.get('nominal')
    if not nominal:
        return {'available': False, 'issue': 'Selected target chassis dimensions are incomplete.',
                'transform_applied': False, 'runtime_verified': False}
    contract = {'kind': 'nominal_layout', 'forward': '-Z', **nominal,
                'provenance': {'kind': 'selected_target_specdb_nominal',
                               'database_sha256': target_geometry['database']['sha256'],
                               'target': target_geometry['target'],
                               'selection': target_geometry['selection']['CHASSIS'],
                               'chassis_record': chassis.get('record'),
                               'native_height_and_origin_known': False}}
    try:
        report = assembly_fit_report(inspection, contract,
                                     declared_wheelbase_metres=declared_wheelbase_metres)
    except GarageError as exc:
        return {'available': False, 'issue': str(exc), 'transform_applied': False,
                'runtime_verified': False}
    report['available'] = True
    return report
