"""Nonmutating, reviewed bootstrap for a combined homogeneous tyre group.

This uses occupied *triangle* projection intervals, not a mesh-name whitelist,
the model origin, forced clustering or a connected-component count. Two clear
bands along each declared horizontal axis can propose four corner regions.
The existing lossless partitioner must independently produce those exact face
sets before a proposal is returned. Ambiguous geometry is left unchanged.

The caller still needs to confirm that all selected surfaces are tyres. The
derived centres describe source envelopes, not proven mechanical rotation hubs,
native mounts or physics. No source file, project or native asset is written.
"""
from __future__ import annotations

import hashlib
import json

import numpy as np

from .errors import GarageError
from .mechanical_parts import (
    _child_ids, _geometry_hash, _identity, _part, partition_wheel_corners,
)
from .recognition import CORNERS, axes
from .scene_coordinates import coordinate_description


_CHUNK = 65536
_GAP_EPS = 64 * np.finfo(np.float64).eps
_METHOD = 'separated_triangle_projection_bands'


def _fail(message):
    raise GarageError(message, 'wheel_discovery')


def _json_hash(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                                     allow_nan=False).encode('utf-8')).hexdigest()


def _prepared_source(part, source_part, source_up_axis, metres_per_unit):
    """Bind the exact existing prepare_scene result to explicit settings."""
    description = coordinate_description(source_up_axis, metres_per_unit)
    source = _part(part if source_part is None else source_part)
    if source_part is None and (source_up_axis != '+Y' or metres_per_unit != 1.0):
        _fail('Supply the canonical source component to verify nondefault source axes or units before discovering tyre corners.')
    if _identity(part) != _identity(source):
        _fail('The prepared component identity or counts differ from its canonical source.')
    if not np.array_equal(part.indices, source.indices) or not np.array_equal(part.uv, source.uv):
        _fail('Prepared topology or UVs differ from the canonical source. Reload the unchanged source before discovering tyre corners.')
    if source_up_axis == '+Y' and metres_per_unit == 1.0:
        if not np.array_equal(part.positions, source.positions) or not np.array_equal(part.normals, source.normals):
            _fail('Prepared geometry does not match the declared source axes and units. Refresh the prepared model and try again.')
    else:
        rotation = np.asarray(description['source_to_y_up_rotation'], dtype=np.float64)
        for start in range(0, len(source.positions), _CHUNK):
            end = start + _CHUNK
            # These are prepare_scene's exact numeric operations, including
            # its float32 publication. No fit margin authorizes local edits.
            with np.errstate(over='ignore', invalid='ignore'):
                expected = (source.positions[start:end].astype(np.float64)
                            @ rotation.T * description['metres_per_unit']).astype(np.float32)
                normals = (source.normals[start:end].astype(np.float64)
                           @ rotation.T).astype(np.float32)
            if (not np.isfinite(expected).all() or not np.isfinite(normals).all()
                    or not np.array_equal(part.positions[start:end], expected)
                    or not np.array_equal(part.normals[start:end], normals)):
                _fail('Prepared geometry does not match the declared source axes and units. Refresh the prepared model and try again.')
    preparation = {key: description[key] for key in (
        'source_up_axis', 'metres_per_unit', 'coordinate_system',
        'source_to_y_up_rotation')}
    return source, preparation


def _projection(part, direction, label):
    """Return whole-triangle low/high-band owners and a compact measurement."""
    axis = int(np.flatnonzero(direction)[0])
    sign = float(direction[axis])
    lower = np.empty(len(part.indices), dtype=np.float64)
    upper = np.empty(len(part.indices), dtype=np.float64)
    for start in range(0, len(part.indices), _CHUNK):
        end = start + _CHUNK
        values = np.asarray(part.positions[part.indices[start:end], axis],
                            dtype=np.float64) * sign
        lower[start:end] = values.min(axis=1)
        upper[start:end] = values.max(axis=1)
    low = float(lower.min())
    high = float(upper.max())
    with np.errstate(over='ignore', invalid='ignore'):
        span = high - low
    if not np.isfinite(span) or span <= 0:
        _fail('The selected surfaces have no numerically usable ' + label + ' separation. Check the source axes and select a complete tyre or tread group.')
    # Normalization makes the arithmetic guard independent of source units.
    # It only merges numerical-scale interval gaps; it never moves geometry.
    lo = (lower - low) / span
    hi = (upper - low) / span
    order = np.argsort(lo, kind='stable')
    lo_sorted = lo[order]
    max_hi = np.maximum.accumulate(hi[order])
    starts = np.r_[0, np.flatnonzero(lo_sorted[1:] - max_hi[:-1] > _GAP_EPS) + 1]
    count = len(starts)
    if count != 2:
        _fail('The selected surfaces form ' + str(count) + ' occupied ' + label
              + ' bands; two unambiguous bands are required. Select a complete tyre or tread group first, or separate/assign the source components manually. Sidewall-only or bridged geometry may need established tyre centres.')
    ends = np.r_[starts[1:] - 1, len(order) - 1]
    max_upper = np.maximum.accumulate(upper[order])
    bands = [[float(lower[order[start]]), float(max_upper[end])]
             for start, end in zip(starts, ends)]
    separator = bands[0][1] / 2 + bands[1][0] / 2
    if not bands[0][1] < separator < bands[1][0]:
        _fail('The tyre separation gap is below the usable numeric resolution. Review the source coordinates; no faces were divided.')
    owners = np.full(len(lower), -1, dtype=np.int8)
    owners[upper < separator] = 0
    owners[lower > separator] = 1
    if np.any(owners < 0):
        _fail('A triangle crosses the proposed tyre separation. Separate the source surfaces; no triangle was split or dropped.')
    return owners, {
        'axis': ('+' if sign > 0 else '-') + 'XYZ'[axis],
        'direction': [int(value) for value in direction],
        'occupied_bands_metres': bands,
        'separator_metres': separator,
        'band_triangle_counts': [int(np.count_nonzero(owners == i)) for i in (0, 1)],
        'normalized_arithmetic_gap_guard': _GAP_EPS,
    }


def discover_tyre_corners(part, *, forward, known_ids=(), source_part=None,
                          source_up_axis='+Y', metres_per_unit=1.0):
    """Return four Parts, existing replay provenance, and a review report.

    ``part`` is one explicitly selected, homogeneous tyre surface group in the
    prepared Y-up scene. ``forward`` is the player's declared prepared front
    axis, never an automatically selected longer bounding-box direction.
    ``source_part`` is the canonical source Part before coordinate preparation;
    supply it together with the exact ``source_up_axis``/``metres_per_unit``
    whenever preparation is nondefault. Pass all scene IDs in ``known_ids``.

    The returned decision uses the existing wheel_corner_partition schema and
    stores original face membership. Persist it only after the player confirms
    the four groups and the tyre role. The report's input/decision hashes bind
    geometry and preparation, not a whole uploaded file or project settings;
    the caller must also bind source-file hashes, assignments and project state
    when accepting a reviewed proposal. This function performs no I/O.
    """
    try:
        return _discover(part, forward=forward, known_ids=known_ids,
                         source_part=source_part, source_up_axis=source_up_axis,
                         metres_per_unit=metres_per_unit)
    except GarageError as exc:
        if exc.code == 'wheel_discovery':
            raise
        raise GarageError(str(exc), 'wheel_discovery') from exc


def _discover(part, *, forward, known_ids, source_part, source_up_axis, metres_per_unit):
    part = _part(part)
    if not isinstance(forward, str) or forward not in ('+Z', '-Z', '+X', '-X'):
        _fail('Choose the model front direction (+Z, -Z, +X or -X) before discovering tyre corners.')
    if isinstance(known_ids, (str, bytes)):
        _fail('Known component identities must be a collection of part IDs.')
    try:
        known_ids = list(known_ids)
    except TypeError as exc:
        raise GarageError('Known component identities must be a collection of part IDs.', 'wheel_discovery') from exc
    child_ids = _child_ids(part, known_ids)
    source, preparation = _prepared_source(part, source_part, source_up_axis, metres_per_unit)
    left, _, front = axes(forward)
    lateral, lateral_report = _projection(part, left, 'left/right')
    longitudinal, longitudinal_report = _projection(part, front, 'front/rear')
    owners = (1 - longitudinal) * 2 + (1 - lateral)
    centres = {}
    regions = []
    face_sets = []
    for index, corner in enumerate(CORNERS):
        faces = np.flatnonzero(owners == index)
        if not len(faces):
            _fail('The selected group has no complete triangles at ' + corner + '. Select a group containing all four tyres or assign separate components individually.')
        used = np.unique(part.indices[faces])
        points = np.asarray(part.positions[used], dtype=np.float64)
        bounds = np.array([points.min(axis=0), points.max(axis=0)])
        centre = bounds[0] / 2 + bounds[1] / 2
        with np.errstate(over='ignore', invalid='ignore'):
            extent = bounds[1] - bounds[0]
        if not np.isfinite(centre).all() or not np.isfinite(extent).all():
            _fail('A proposed tyre envelope exceeds the usable numeric range. Check the source coordinates.')
        centres[corner] = centre.tolist()
        regions.append({'corner': corner, 'id': child_ids[index],
                        'triangles': len(faces), 'vertices': len(used),
                        'bounds_metres': bounds.tolist(),
                        'envelope_dimensions_metres': extent.tolist()})
        face_sets.append(faces)
    # This validates explicit labels/layout, canonical preparation, nearest-
    # centre ownership, full face coverage and exact triangle attributes using
    # the same emitter/replay contract as already reviewed manual partitions.
    children, decision = partition_wheel_corners(
        part, {'forward': forward, 'wheel_centers': centres}, 'tyre',
        known_ids=known_ids, source_part=source)
    for expected, group in zip(face_sets, decision['groups']):
        if not np.array_equal(expected, group['source_face_indices']):
            _fail('The clear-gap proposal and nearest-centre partition disagree. Review this asymmetric or offset geometry manually; no corner assignment was saved.')
    proof = {
        'schema': 1, 'method': _METHOD, 'method_revision': 1,
        'canonical_source': {**_identity(source), 'geometry_sha256': _geometry_hash(source)},
        'prepared_source': {**_identity(part), 'geometry_sha256': _geometry_hash(part)},
        'preparation': preparation,
        'forward': forward,
        'known_part_ids_sha256': _json_hash(sorted(known_ids)),
        'known_part_count': len(known_ids),
    }
    report = {
        'schema': 1, 'kind': 'tyre_corner_discovery', 'method': _METHOD,
        'status': 'review_required', 'role': 'tyre', 'corner_order': list(CORNERS),
        'input_proof': proof, 'input_sha256': _json_hash(proof),
        'decision_sha256': _json_hash(decision),
        'projections': {'left': lateral_report, 'forward': longitudinal_report},
        'wheel_centers_metres': centres, 'corners': regions,
        'checks': {'all_source_triangles_retained_once': True,
                   'triangle_positions_normals_uv_winding_unchanged': True,
                   'existing_partition_face_membership_agrees': True,
                   'declared_preparation_verified': True},
        'scope': {
            'role_confirmation_required': True,
            'centres': 'Bounds midpoints of the selected referenced tyre surfaces in the declared prepared metre coordinates; not proven rotation hubs.',
            'selection': 'One homogeneous tyre surface group; semantic role requires player review.',
            'input_proof': 'Canonical/prepared component geometry and declared preparation only. The caller must also authenticate original upload bytes and current project assignments/settings.',
            'unknown': ['physical tyre dimensions', 'native mounting frames', 'rotating assembly fit', 'replacement physics compatibility'],
            'native_output_verified': False,
        },
        'geometry_unchanged': True, 'runtime_verified': False,
    }
    return children, decision, report
