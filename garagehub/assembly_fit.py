"""Read-only four-corner fit in the prepared Y-up, metre coordinate contract.

This module does not call or replace car_geometry.alignment. A best-fit rigid
transform is a diagnostic, not a geometry policy, physical-fit acceptance, or a
native rotating-mount edit. Input centres keep their explicit corner labels.
"""
from __future__ import annotations

from collections.abc import Mapping
import copy
import json
import math

import numpy as np

from .errors import GarageError
from .recognition import CORNERS, axes
from .scene_coordinates import COORDINATE_SYSTEM


# Dimensionless arithmetic guards, not vehicle-fit tolerances in metres.
_RANK_EPS = 64 * np.finfo(np.float64).eps
_MATRIX_EPS = 2e-6  # Allows rounding of a supplied float32 rigid matrix.


def _fail(message):
    raise GarageError(message, 'assembly_fit')


def _array(value, shape, label):
    try:
        raw = np.asarray(value)
        if raw.dtype.kind not in 'iuf':
            _fail(label + ' must contain real numbers, not text or booleans.')
        # Avoid silently accepting bools mixed into an otherwise numeric list.
        if not isinstance(value, np.ndarray) and any(isinstance(x, (bool, np.bool_)) for x in np.asarray(value, dtype=object).flat):
            _fail(label + ' must not contain booleans.')
        result = np.array(raw, dtype=np.float64, copy=True)
    except (TypeError, ValueError, OverflowError) as exc:
        if isinstance(exc, GarageError):
            raise
        raise GarageError(label + ' must contain finite numeric coordinates.', 'assembly_fit') from exc
    if result.shape != shape or not np.isfinite(result).all():
        _fail(label + ' must have shape ' + str(shape) + ' and contain only finite numbers.')
    return result


def _positive(value, label):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        _fail(label + ' must be a positive finite number in metres.')
    try:
        result = float(value)
    except (ValueError, OverflowError) as exc:
        raise GarageError(label + ' exceeds the numeric range.', 'assembly_fit') from exc
    if not math.isfinite(result) or result <= 0:
        _fail(label + ' must be a positive finite number in metres.')
    return result


def _ratio(value, reference):
    result = value / reference
    if not math.isfinite(result):
        _fail('The entered or measured dimension ratio exceeds the numeric range; review source units and target dimensions.')
    return result


def _frame(forward, label):
    if not isinstance(forward, str) or forward not in ('+Z', '-Z', '+X', '-X'):
        _fail(label + ' needs an explicit +Z, -Z, +X or -X forward direction.')
    return axes(forward)


def _centred(points, label):
    with np.errstate(over='ignore', invalid='ignore'):
        center = np.sum(points / len(points), axis=0)
        relative = points - center
    if not np.isfinite(center).all() or not np.isfinite(relative).all():
        _fail(label + ' exceeds the numeric range; review the prepared units and origin.')
    span = float(np.max(np.abs(relative)))
    if span == 0:
        _fail(label + ' are coincident; four distinct wheel centres are required.')
    normalized = relative / span
    singular = np.linalg.svd(normalized, compute_uv=False)
    rank = int(np.count_nonzero(singular > singular[0] * _RANK_EPS))
    return center, normalized, rank


def _cross2(a, b):
    return float(a[0] * b[1] - a[1] * b[0])


def _corners(values, forward, label):
    if not isinstance(values, Mapping) or set(values) != set(CORNERS):
        _fail(label + ' must contain exactly FL, FR, RL and RR; assign labels explicitly.')
    points = _array([values[k] for k in CORNERS], (4, 3), label)
    left, _, front = _frame(forward, label)
    _, normalized, rank = _centred(points, label)
    if rank < 2:
        _fail(label + ' are collinear or numerically degenerate; wheelbase and tracks must be measurable.')
    for i in range(4):
        for j in range(i):
            if math.hypot(*(normalized[i] - normalized[j])) <= _RANK_EPS:
                _fail(label + ' include coincident or numerically indistinguishable corners.')
    lateral, longitudinal = normalized @ left, normalized @ front
    if min(lateral[0] - lateral[1], lateral[2] - lateral[3],
           longitudinal[0] - longitudinal[2], longitudinal[1] - longitudinal[3]) <= _RANK_EPS:
        _fail(label + ' labels conflict with the declared left/front axes. Correct reflected or crossed assignments; labels will not be reordered.')
    # Reject crossing opposite edges of the labelled horizontal perimeter.
    plane = np.column_stack((lateral, longitudinal))[[0, 1, 3, 2]]
    for a, b, c, d in ((plane[0], plane[1], plane[2], plane[3]),
                       (plane[1], plane[2], plane[3], plane[0])):
        ab, cd = b - a, d - c
        crosses = (_cross2(ab, c - a) * _cross2(ab, d - a) < -_RANK_EPS
                   and _cross2(cd, a - c) * _cross2(cd, b - c) < -_RANK_EPS)
        if crosses:
            _fail(label + ' form crossed corner assignments; review the declared labels.')
    return points, rank


def _measure(points, forward):
    left, _, front = _frame(forward, 'Wheel layout')
    with np.errstate(over='ignore', invalid='ignore'):
        front_middle = (points[0] / 2 + points[1] / 2)
        rear_middle = (points[2] / 2 + points[3] / 2)
        dimensions = {
            'wheelbase_metres': float((front_middle - rear_middle) @ front),
            'front_track_metres': float((points[0] - points[1]) @ left),
            'rear_track_metres': float((points[2] - points[3]) @ left),
            'left_wheelbase_metres': float((points[0] - points[2]) @ front),
            'right_wheelbase_metres': float((points[1] - points[3]) @ front),
        }
    if any(not math.isfinite(x) or x <= 0 for x in dimensions.values()):
        _fail('Wheel dimensions are degenerate or exceed the numeric range; review the prepared centres.')
    return dimensions


def _proper_fit(source, target):
    cs, s, source_rank = _centred(source, 'Source centres')
    ct, t, target_rank = _centred(target, 'Target centres')
    u, singular, vt = np.linalg.svd(s.T @ t)
    # A planar quartet is valid. Rank one cannot define a stable orientation.
    if np.count_nonzero(singular > singular[0] * _RANK_EPS) < 2:
        _fail('The paired centres do not determine a stable rigid orientation; review their assignments.')
    correction = np.eye(source.shape[1])
    corrected = bool(np.linalg.det(vt.T @ u.T) < 0)
    if corrected:
        correction[-1, -1] = -1
    rotation = vt.T @ correction @ u.T
    with np.errstate(over='ignore', invalid='ignore'):
        translation = ct - rotation @ cs
        mapped = source @ rotation.T + translation
        residuals = mapped - target
    if not all(np.isfinite(v).all() for v in (rotation, translation, mapped, residuals)):
        _fail('The rigid comparison exceeds the numeric range; review the prepared units and origins.')
    norms = [math.hypot(*row) for row in residuals]
    rms = math.hypot(*norms) / math.sqrt(len(norms))
    if not math.isfinite(rms):
        _fail('The rigid comparison residuals exceed the numeric range.')
    fit = {
        'rotation': rotation.tolist(), 'translation_metres': translation.tolist(),
        'scale': 1.0, 'determinant': float(np.linalg.det(rotation)),
        'determinant_correction_applied': corrected,
        'source_point_rank': source_rank, 'target_point_rank': target_rank,
        'rms_corner_error_metres': rms, 'maximum_corner_error_metres': max(norms),
        'fit_tolerance_metres': None, 'compatibility_assessed': False,
    }
    return fit, mapped, residuals, norms


def _by_corner(points):
    return {corner: points[i].tolist() for i, corner in enumerate(CORNERS)}


def assembly_fit_report(inspection, target_contract, *, declared_wheelbase_metres=None):
    """Compare already prepared centres without scaling, relabelling or edits.

    ``inspection`` supplies ``forward`` and ``wheel_centers`` from the prepared
    scene. Units/axes must already have been applied by ``prepare_scene``.
    ``target_contract`` supplies ``kind``, ``forward`` and either:

    * ``explicit_centres``: four ``wheel_centers_metres`` in a declared target
      body frame. A rigid diagnostic is returned, never applied.
    * ``nominal_layout``: ``wheelbase_metres``, ``front_track_metres`` and
      ``rear_track_metres``. Only an abstract, symmetric XZ layout is compared.
      Target height/origin are unknown; ``rigid_fit`` is None and no usable 3D
      transform is manufactured from tyre radius, ride height or source Y.

    Optional ``provenance`` is copied into the target report. An optional
    entered wheelbase is compared to the measured source, never used as scale.
    """
    if not isinstance(inspection, Mapping) or not isinstance(target_contract, Mapping):
        _fail('Assembly fitting requires a source inspection and an explicit target contract.')
    for value, label in ((inspection, 'Source inspection'), (target_contract, 'Target contract')):
        if value.get('coordinate_system', COORDINATE_SYSTEM) != COORDINATE_SYSTEM:
            _fail(label + ' must use the prepared right-handed, Y-up, metre contract.')
    forward = inspection.get('forward')
    source, source_rank = _corners(inspection.get('wheel_centers'), forward, 'Source centres')
    source_dimensions = _measure(source, forward)
    target_forward = target_contract.get('forward')
    left, _, front = _frame(target_forward, 'Target contract')
    kind = target_contract.get('kind')
    if kind not in ('explicit_centres', 'nominal_layout'):
        _fail('Choose an explicit_centres or nominal_layout target contract.')
    provenance = copy.deepcopy(target_contract.get('provenance'))
    try:
        json.dumps(provenance, allow_nan=False)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GarageError('Target provenance must be finite JSON data.', 'assembly_fit') from exc
    declared = None if declared_wheelbase_metres is None else _positive(declared_wheelbase_metres, 'Entered wheelbase')
    entered = {
        'metres': declared, 'measured_prepared_metres': source_dimensions['wheelbase_metres'],
        'entered_minus_measured_metres': None if declared is None else declared - source_dimensions['wheelbase_metres'],
        'entered_to_measured_ratio': None if declared is None else _ratio(declared, source_dimensions['wheelbase_metres']),
        'used_to_scale_geometry': False,
    }
    report = {
        'schema': 1, 'kind': 'whole_assembly_fit_diagnostic',
        'coordinate_system': COORDINATE_SYSTEM, 'corner_order': list(CORNERS),
        'dimension_measurement_scope': 'Wheelbase and tracks are projections along each declared front/left axis, consistent with prepared inspection. They are not pose-invariant pair distances; a rigid comparison may remove pose differences without changing size.',
        'source': {'forward': forward, 'wheel_centers_metres': _by_corner(source),
                   'dimensions': source_dimensions, 'point_rank': source_rank,
                   'entered_wheelbase': entered,
                   'scope': 'Centres from the already prepared scene; original units are neither inferred nor converted here. Tyre assignments are not independently authenticated.'},
        'target': {'kind': kind, 'forward': target_forward, 'provenance': provenance},
        'rigid_fit': None, 'planar_fit': None,
        'transform_applied': False, 'source_geometry_changed': False,
        'physics_changed': False, 'suspension_or_mounts_changed': False,
        'physical_compatibility': 'unverified', 'runtime_verified': False,
        'limits': [
            'Wheel-centre coincidence does not establish tyre dimensions, body or wheel-arch clearance, suspension travel, steering clearance or physical compatibility.',
            'Four centre points do not authenticate body handedness or a native rotating frame. Coplanar centres do not determine the wheel-plane normal sign.',
            'A single explicitly chosen transform may be applied to geometry already in one common prepared body frame. It does not establish native per-corner pre-spin mounts.',
            'Do not apply body-frame residuals as local off-axis shifts to spinning wheels or rotors; local shifts rotate with the assembly. Caliper and rotating routes remain separate.',
            'No fit threshold, geometry adaptation, source unit correction or physics edit is selected by this diagnostic.',
        ],
    }
    if kind == 'explicit_centres':
        target, target_rank = _corners(target_contract.get('wheel_centers_metres'), target_forward, 'Target centres')
        target_dimensions = _measure(target, target_forward)
        fit, mapped, residuals, norms = _proper_fit(source, target)
        fit.update(source_space='prepared_body', target_space='target_body',
                   method='proper_orthogonal_least_squares_xyz', residual_scope='xyz',
                   convention='target_position = source_position @ rotation.T + translation_metres')
        report['rigid_fit'] = fit
        report['status'] = 'explicit_centres_compared'
        report['target'].update(wheel_centers_metres=_by_corner(target), dimensions=target_dimensions,
                                target_y_known=True, point_rank=target_rank,
                                origin_scope='Explicit target body-frame centres supplied by the caller; height provenance is not inferred.')
        report['corner_residuals'] = {
            k: {'fitted_center_metres': mapped[i].tolist(), 'residual_metres': residuals[i].tolist(),
                'distance_metres': norms[i], 'scope': 'xyz'} for i, k in enumerate(CORNERS)
        }
    else:
        if 'wheel_centers_metres' in target_contract or any(k in target_contract for k in ('center_y_metres', 'target_y_metres', 'wheel_center_y_metres')):
            _fail('A nominal_layout has unknown target Y. Supply complete explicit_centres to compare a reviewed body-relative height.')
        target_dimensions = {key: _positive(target_contract.get(key), 'Nominal ' + key)
                             for key in ('wheelbase_metres', 'front_track_metres', 'rear_track_metres')}
        # Y=0 is used only inside the temporary XZ construction and is never
        # reported as a native centre or supplied to a 3D fit.
        wb, ft, rt = (target_dimensions[k] for k in ('wheelbase_metres', 'front_track_metres', 'rear_track_metres'))
        nominal = np.array([left * ft / 2 + front * wb / 2, -left * ft / 2 + front * wb / 2,
                            left * rt / 2 - front * wb / 2, -left * rt / 2 - front * wb / 2])
        fit, mapped, residuals, norms = _proper_fit(source[:, (0, 2)], nominal[:, (0, 2)])
        fit.update(method='proper_yaw_only_least_squares_xz', residual_scope='xz_only',
                   axes=['X', 'Z'], usable_as_3d_transform=False,
                   convention='comparison_xz = source_xz @ rotation.T + translation_metres; target Y remains unknown')
        report['planar_fit'] = fit
        report['status'] = 'nominal_layout_compared_height_unknown'
        report['target'].update(dimensions=target_dimensions, target_y_known=False,
                                wheel_centers_metres={k: [float(nominal[i, 0]), None, float(nominal[i, 2])] for i, k in enumerate(CORNERS)},
                                origin_scope='Abstract symmetric nominal layout, centred between axles in XZ; native body origin and all target Y values are unknown.')
        report['corner_residuals'] = {
            k: {'fitted_center_metres': [float(mapped[i, 0]), None, float(mapped[i, 1])],
                'residual_metres': [float(residuals[i, 0]), None, float(residuals[i, 1])],
                'distance_metres': norms[i], 'scope': 'xz_only'} for i, k in enumerate(CORNERS)
        }
        report['limits'].append('Nominal XZ comparison permits yaw and horizontal translation only; pitch, roll, vertical placement, loaded tyre radius and ride height are unresolved.')
    report['dimensional_mismatches'] = {
        key: {'source_metres': source_dimensions[key], 'target_metres': target_dimensions[key],
              'source_minus_target_metres': source_dimensions[key] - target_dimensions[key],
              'source_to_target_ratio': _ratio(source_dimensions[key], target_dimensions[key])}
        for key in ('wheelbase_metres', 'front_track_metres', 'rear_track_metres')
    }
    return report


def apply_assembly_transform(positions, *, transform, normals=None, coordinate_space='prepared_body'):
    """Return independent position/normal arrays for an explicitly chosen fit.

    The caller must supply positions for every attachment in the SAME prepared
    body frame and deliberately pass a complete 3D rigid transform. This does
    not convert wheel-local/pre-spin geometry or edit pivots, physics or native
    mount matrices. Triangle indices and authored normal lengths are untouched.
    """
    if coordinate_space != 'prepared_body':
        _fail('Apply the shared assembly transform only in the prepared body frame. Wheel-local or rotating-frame offsets require a separately verified native mount route.')
    if not isinstance(transform, Mapping):
        _fail('An explicit complete 3D rigid transform is required; a diagnostic does not apply itself.')
    if transform.get('source_space', 'prepared_body') != 'prepared_body' or transform.get('usable_as_3d_transform', True) is not True:
        _fail('This comparison does not provide an applicable prepared-body 3D transform.')
    if 'scale' in transform and _positive(transform['scale'], 'Rigid scale') != 1.0:
        _fail('A whole-assembly rigid transform must not scale geometry.')
    rotation = _array(transform.get('rotation'), (3, 3), 'Assembly rotation')
    translation = _array(transform.get('translation_metres'), (3,), 'Assembly translation')
    determinant = float(np.linalg.det(rotation))
    if not np.allclose(rotation.T @ rotation, np.eye(3), atol=_MATRIX_EPS, rtol=0) or abs(determinant - 1) > _MATRIX_EPS:
        _fail('The supplied assembly matrix is scaled, sheared or reflected; supply a proper rigid rotation.')
    try:
        shape = np.asarray(positions).shape
    except (TypeError, ValueError) as exc:
        raise GarageError('Assembly positions must be an N by 3 numeric array.', 'assembly_fit') from exc
    if len(shape) != 2 or shape[1] != 3:
        _fail('Assembly positions must be an N by 3 numeric array.')
    points = _array(positions, shape, 'Assembly positions')
    vectors = None if normals is None else _array(normals, shape, 'Assembly normals')
    with np.errstate(over='ignore', invalid='ignore'):
        moved = points @ rotation.T + translation
        turned = None if vectors is None else vectors @ rotation.T
    if not np.isfinite(moved).all() or (turned is not None and not np.isfinite(turned).all()):
        _fail('The explicit assembly transform produces non-finite geometry.')
    return moved, turned
