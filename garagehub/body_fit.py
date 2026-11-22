"""Explicit static-body placement against the current owned native profile.

Reviewing a body transform does not approve a complete vehicle. The existing
profile separately resizes rims to its retained native tyres; source mechanical
roles, unsupported materials, identity routing and runtime acceptance retain
their own gates. Original source arrays and bytes are never changed here.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
from collections.abc import Mapping

import numpy as np

from .car_profiles import PROFILE, ASSETS
from .errors import GarageError
from .glb import Scene
from .install_targets import installation
from .mechanical_parts import _geometry_hash, _part
from .recognition import CORNERS, axes
from .scene_coordinates import COORDINATE_SYSTEM, coordinate_settings
from .wheel_fit import native_wheel_geometry, rim_frame


POLICIES = ('keep_proportions', 'adapt_body')
LABELS = {'keep_proportions': 'Keep original body size and shape',
          'adapt_body': 'Adapt body to template'}
SCHEMA = 1
# This is the existing profile's candidate bound, not a clearance or physical
# compatibility threshold. Adaptation still delegates to alignment's own gates.
MAXIMUM_CORNER_ERROR_METRES = .06


def _fail(message, code='body_fit'):
    raise GarageError(message, code)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise GarageError('Body-fit inputs must contain finite JSON data.', 'body_fit') from exc


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _is_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _policy(value):
    if not isinstance(value, str) or value not in POLICIES:
        _fail('Choose Keep original body size and shape or Adapt body to template.')
    return value


def _reference():
    """Return an independent, source-independent copy of the profile contract.

This report does not read template files. Production planning/building must
still run validate_template against these exact asset hashes. Nominal selected
database dimensions and historical RX8 captures cannot replace these centres.
"""
    if not isinstance(PROFILE.get('id'), str) or not PROFILE['id']:
        _fail('The supported native profile has no stable identity.')
    if not ASSETS or any(not isinstance(k, str) or not _is_digest(v)
                         for k, v in ASSETS.items()):
        _fail('The supported native profile has no exact asset contract.')
    geometry = {}
    for corner in CORNERS:
        row = native_wheel_geometry(corner, PROFILE)
        geometry[corner] = {'center_metres': row['center_metres'],
                            'tyre_radius_metres': row['radius_metres'],
                            'tyre_width_metres': row['width_metres']}
    contract = {'schema': SCHEMA, 'algorithm_revision': 1,
        'profile_id': PROFILE['id'], 'profile_label': PROFILE.get('label', ''),
        'title_id': PROFILE.get('title_id'), 'game_update': PROFILE.get('game_update'),
        'coordinate_system': COORDINATE_SYSTEM, 'forward': '-Z',
        'wheel_geometry': geometry, 'asset_sha256': copy.deepcopy(ASSETS),
        'candidate_limits': {'maximum_corner_error_metres': MAXIMUM_CORNER_ERROR_METRES,
                             'adaptation_lateral_to_uniform_ratio': [.85, 1.15],
                             'scope': 'Existing supported-profile candidate limits; not physical-fit or clearance acceptance.'},
        'requires_template_authentication': True, 'current_mount_verified': False,
        'source_dependent': False,
        'scope': 'Fixed reviewed native-profile body-frame centres and exact donor assets. This read-only report does not authenticate a folder or establish current suspension pose, rendered tyres or clearance.'}
    contract['reference_digest'] = _digest(contract)
    return contract


def _source_inputs(scene, project):
    if not isinstance(scene, Scene) or not isinstance(project, Mapping):
        _fail('Review a prepared source model and its saved project before choosing body fit.')
    if not _is_digest(scene.digest):
        _fail('The source model has no valid original fingerprint.')
    saved_source = project.get('source_sha256')
    if saved_source is not None and saved_source != scene.digest:
        _fail('The saved source fingerprint differs from the prepared model.', 'source_changed')
    inspection = project.get('inspection')
    if not isinstance(inspection, Mapping) or not isinstance(inspection.get('parts'), list):
        _fail('The current component inspection is missing.')
    records = inspection['parts']
    if not all(isinstance(row, Mapping) and isinstance(row.get('id'), str) for row in records):
        _fail('The current component identities are invalid.')
    identities = [row['id'] for row in records]
    parts = [_part(part) for part in scene.parts]
    if (not parts or len(set(identities)) != len(identities)
            or len({part.id for part in parts}) != len(parts)
            or set(identities) != {part.id for part in parts}):
        _fail('The component inspection does not match the current prepared source.')
    geometry = [{'id': part.id, 'geometry_sha256': _geometry_hash(part)} for part in parts]
    coordinates = coordinate_settings(project.get('source_up_axis', '+Y'),
                                      project.get('metres_per_unit', 1.))
    inputs = {'source_sha256': scene.digest, 'prepared_geometry_sha256': _digest(geometry),
        'inspection': copy.deepcopy(dict(inspection)), 'coordinates': coordinates,
        'forward': project.get('forward', inspection.get('forward')),
        'wheel_partitions': copy.deepcopy(project.get('wheel_partitions', [])),
        'overrides': copy.deepcopy(project.get('overrides', {})),
        'wheelbase_metres': project.get('wheelbase_metres'),
        'installation': installation(project.get('installation'))}
    _json(inputs)
    return inputs


def _wheels(scene, inspection):
    corners = []
    for corner in CORNERS:
        row = {'corner': corner, 'available': False,
               'radial_scale': None, 'axial_scale': None, 'issue': None}
        try:
            _, _, _, fit = rim_frame(scene, inspection, corner, PROFILE)
            row.update(available=True, radial_scale=fit['radial_scale'],
                       axial_scale=fit['axial_scale'])
        except GarageError as exc:
            row['issue'] = str(exc)
        corners.append(row)
    return {'policy': 'existing_profile_radial_and_axial_resize',
        'native_tyres_retained': True, 'source_rim_rigidity_guaranteed': False,
        'body_choice_changes_wheel_conversion': False, 'corners': corners,
        'scope': 'The existing rim converter independently resizes radius and width to retained native tyres. These body choices do not preserve every rim dimension, add mechanical-role support, change physics or approve tyre/arch clearance.'}


def body_fit_review(scene, project, policy):
    """Return a finite JSON review, never approval or changed source geometry.

The original-size policy uses the declared up/front frame and centroid
translation only: it does not add a least-squares pitch/roll tilt. Adaptation
uses the existing production alignment exactly, including its current limits.
Both policies retain those existing source/profile validation gates.
"""
    policy = _policy(policy)
    report = {'schema': SCHEMA, 'scope': 'static_body_fit_review',
        'policy': policy, 'label': LABELS[policy], 'available': False,
        'within_profile_limits': False, 'issue': None, 'issue_code': None,
        'review_digest': None, 'reference': None, 'source': None,
        'matrix': None, 'translation_metres': None, 'scale_xyz': None,
        'scale_change_percent_xyz': None,
        'maximum_wheel_centre_error_metres': None, 'corner_residuals': {},
        'fit': {}, 'wheel_conversion': None, 'approved': False,
        'physics_changed': False, 'source_geometry_changed': False,
        'body_transform_applied': False, 'runtime_verified': False,
        'mechanical_roles_supported_by_this_choice': False}
    try:
        reference = _reference()
        report['reference'] = reference
        inputs = _source_inputs(scene, project)
        report['source'] = {key: inputs[key] for key in ('source_sha256', 'prepared_geometry_sha256')}
        inspection = project['inspection']
        # Local import keeps this reusable by car_geometry's production caller.
        from .car_geometry import alignment
        adapted_matrix, adapted_translation, adapted_fit = alignment(inspection, PROFILE)
        points = np.array([inspection['wheel_centers'][c] for c in CORNERS], dtype=float)
        targets = np.array([reference['wheel_geometry'][c]['center_metres'] for c in CORNERS], dtype=float)
        if policy == 'adapt_body':
            matrix, translation, fit = adapted_matrix, adapted_translation, copy.deepcopy(adapted_fit)
            scale = float(fit['uniform_scale'])
            scales = np.array([scale*fit['lateral_fit_ratio'], scale, scale])
        else:
            left, up, front = axes(inspection['forward'])
            matrix = np.array([-left, up, -front])
            translation = np.mean(targets, axis=0)-matrix@np.mean(points, axis=0)
            scales = np.ones(3)
            fit = {'uniform_scale': 1., 'lateral_fit_ratio': 1.,
                   'fit_method': 'declared_axes_rigid_centroid_placement',
                   'fit_scope': 'wheel_centres_only', 'dimensional_review_required': True,
                   'policy': 'The static body retains its prepared size and shape; only the declared proper orientation and centroid translation are applied. No fitted pitch/roll tilt or per-axis scaling is introduced.'}
        mapped = points@matrix.T+translation
        residuals = mapped-targets
        errors = np.hypot.reduce(residuals, axis=1)
        if not all(np.isfinite(x).all() for x in (matrix, translation, scales, mapped, residuals, errors)):
            _fail('The body fit exceeds the finite numeric range; review source units.')
        maximum = float(errors.max())
        fit['maximum_wheel_centre_error_metres'] = maximum
        fit['body_policy'] = policy
        fit['reference_digest'] = reference['reference_digest']
        wheels = _wheels(scene, inspection)
        report.update(available=True, within_profile_limits=maximum <= MAXIMUM_CORNER_ERROR_METRES,
            matrix=matrix.tolist(), translation_metres=translation.tolist(),
            scale_xyz=scales.tolist(), scale_change_percent_xyz=((scales-1.)*100.).tolist(),
            maximum_wheel_centre_error_metres=maximum, fit=fit, wheel_conversion=wheels,
            corner_residuals={corner: {'mapped_center_metres': mapped[i].tolist(),
                'reference_center_metres': targets[i].tolist(),
                'residual_metres': residuals[i].tolist(), 'distance_metres': float(errors[i])}
                for i, corner in enumerate(CORNERS)})
        report['review_digest'] = _digest({'schema': SCHEMA, 'policy': policy,
            'inputs': inputs, 'reference_digest': reference['reference_digest'],
            'matrix': report['matrix'], 'translation_metres': report['translation_metres'],
            'wheel_conversion': wheels})
        if not report['within_profile_limits']:
            report.update(issue="Keeping the original body size leaves wheel centres beyond this profile's existing 0.06 m candidate limit. Review the source or choose an eligible explicit body adaptation.",
                          issue_code='body_fit_limit')
    except GarageError as exc:
        report.update(issue=str(exc), issue_code=exc.code)
    _json(report)
    return report


def approve_body_fit(scene, project, policy, review_digest, confirmed):
    """Validate an explicit current review and return only its portable choice."""
    if confirmed is not True:
        _fail('Review the body transform and independently resized rims, then explicitly confirm this choice.', 'body_fit_confirmation')
    if not _is_digest(review_digest):
        _fail('Review the current body fit before confirming it.', 'body_fit_stale')
    report = body_fit_review(scene, project, policy)
    if not report['available'] or not report['within_profile_limits']:
        _fail(report['issue'] or 'This body fit is not eligible for the selected profile.')
    if not hmac.compare_digest(review_digest, report['review_digest']):
        _fail('The source, preparation, destination or native profile changed. Review the body fit again.', 'body_fit_stale')
    return {'schema': SCHEMA, 'policy': policy, 'review_digest': report['review_digest']}


def resolve_body_fit(scene, project, require_approved=True):
    """Recompute a saved body choice for production; never trust saved matrices.

The optional unapproved mode is for internal diagnostics of an explicit policy
only. It does not choose a policy for an old project or authorize a build.
"""
    choice = project.get('body_fit') if isinstance(project, Mapping) else None
    if not isinstance(choice, Mapping):
        _fail('Review and confirm how the body should fit the vehicle template.', 'body_fit_required')
    if require_approved:
        if (set(choice) != {'schema', 'policy', 'review_digest'}
                or type(choice.get('schema')) is not int or choice['schema'] != SCHEMA
                or not _is_digest(choice.get('review_digest'))):
            _fail('The saved body-fit choice is incomplete. Review and confirm it again.', 'body_fit_stale')
    report = body_fit_review(scene, project, choice.get('policy'))
    if not report['available'] or not report['within_profile_limits']:
        _fail(report['issue'] or 'This body fit is not eligible for the selected profile.')
    if require_approved and not hmac.compare_digest(choice['review_digest'], report['review_digest']):
        _fail('The source, preparation, destination or native profile changed. Review the body fit again.', 'body_fit_stale')
    report['approved'] = bool(require_approved)
    return np.array(report['matrix'], dtype=float), np.array(report['translation_metres'], dtype=float), report
