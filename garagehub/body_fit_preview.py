"""Static body-fit preview with fixed, explicitly labelled reference envelopes.

This uses freshly recomputed fit-review data supplied by the Store. It cannot
approve a fit, produce a native tyre, or establish the current suspension pose.
Input scenes, assignments and reviewed reference data remain unchanged.
"""
from __future__ import annotations

import copy
import json
import math
import struct
from collections.abc import Mapping
from dataclasses import replace

import numpy as np

from .errors import GarageError
from .glb import Scene
from .preview import build_preview
from .recognition import CORNERS


_STATIC_ROLES = frozenset(('body', 'paint', 'glass', 'headlight', 'taillight',
                           'brakelight', 'interior'))
_EXCLUDED_ROLES = frozenset(('rim', 'tyre', 'valve', 'brake_rotor',
                             'brake_caliper', 'ignore'))
_GUIDE_PREFIX = '__gt6_body_fit_reference_'
_RING_SEGMENTS = 64


def _fail(message):
    raise GarageError(message, 'body_fit_preview')


def _array(value, shape, label):
    try:
        original = np.asarray(value)
        if original.dtype.kind not in 'fiu':
            _fail(label + ' must contain finite numbers.')
        result = np.array(original, dtype=np.float64, copy=True)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GarageError(label + ' must contain finite numbers.', 'body_fit_preview') from exc
    if result.shape != shape or not np.isfinite(result).all():
        _fail(label + ' has invalid dimensions or non-finite values.')
    return result


def _digest(value, label):
    if (not isinstance(value, str) or len(value) != 64
            or any(character not in '0123456789abcdef' for character in value)):
        _fail('Reopen the current fit review: ' + label + ' is missing or invalid.')
    return value


def _positive(value, label):
    if isinstance(value, (bool, np.bool_)) or not isinstance(value, (int, float, np.integer, np.floating)):
        _fail(label + ' must be a positive finite measurement.')
    try:
        number = float(value)
    except (ValueError, OverflowError) as exc:
        raise GarageError(label + ' must be a positive finite measurement.', 'body_fit_preview') from exc
    if not math.isfinite(number) or number <= 0:
        _fail(label + ' must be a positive finite measurement.')
    return number


def _published(values, label):
    with np.errstate(over='ignore', invalid='ignore'):
        result = np.asarray(values, dtype='<f4')
    if not np.isfinite(result).all():
        _fail(label + ' exceeds the finite preview coordinate range. Review source units.')
    return result


def _envelope(center, radius, width):
    """Two thin side rings and four axial ribbons, inside the fixed envelope."""
    points, normals, faces = [], [], []
    thickness = min(radius * .0125, width * .04)
    for side in (-1, 1):
        start = len(points)
        for index in range(_RING_SEGMENTS):
            angle = 2 * math.pi * index / _RING_SEGMENTS
            radial = np.array([0., math.cos(angle), math.sin(angle)])
            for distance in (radius, radius - thickness):
                points.append(center + [side * width / 2, 0., 0.] + radial * distance)
                normals.append([float(side), 0., 0.])
        for index in range(_RING_SEGMENTS):
            current = start + 2 * index
            following = start + 2 * ((index + 1) % _RING_SEGMENTS)
            pair = ((current, following, current + 1),
                    (following, following + 1, current + 1))
            faces.extend(pair if side > 0 else tuple((a, c, b) for a, b, c in pair))
    half_angle = min(.0125, thickness / radius)
    for index in range(4):
        angle = index * math.pi / 2
        start = len(points)
        for axial, theta in ((-width / 2, angle - half_angle),
                             (width / 2, angle - half_angle),
                             (width / 2, angle + half_angle),
                             (-width / 2, angle + half_angle)):
            radial = np.array([0., math.cos(theta), math.sin(theta)])
            points.append(center + [axial, 0., 0.] + radius * radial)
            normals.append(radial)
        faces.extend(((start, start + 2, start + 1), (start, start + 3, start + 2)))
    return (np.asarray(points, dtype=float), np.asarray(normals, dtype=float),
            np.asarray(faces, dtype=np.uint32))


def build_body_fit_preview(scene, inspection, review, limit=800000):
    """Return ``(GGV1 bytes, viewer part records)`` without changing inputs.

    ``review`` must be freshly computed by the caller. ``available`` permits
    visual review even when ``within_profile_limits`` is false. The returned
    preview grants no approval. Guides use the fixed -Z-forward profile's tyre
    envelopes, not source tyres or current loaded/native rendered geometry.

    The limit is a static-surface simplification target, as in build_preview;
    complete small surfaces and reference guides are never dropped to hit it.
    """
    if not isinstance(scene, Scene) or not isinstance(inspection, Mapping):
        _fail('Prepare and inspect the model before opening the body-fit preview.')
    if type(limit) is not int or limit <= 0:
        _fail('The body-fit preview needs a positive integer triangle target.')
    if not isinstance(review, Mapping) or review.get('available') is not True:
        _fail('Review the source wheel groups and size before opening the body-fit preview.')
    digest = _digest(review.get('review_digest'), 'fit review digest')
    matrix = _array(review.get('matrix'), (3, 3), 'Body-fit matrix')
    translation = _array(review.get('translation_metres'), (3,), 'Body-fit translation')
    try:
        singular = np.linalg.svd(matrix, compute_uv=False)
        sign, determinant_log = np.linalg.slogdet(matrix)
        if (not np.isfinite(singular).all() or not np.isfinite(determinant_log) or sign == 0
                or singular[-1] <= singular[0] * 128 * np.finfo(float).eps):
            _fail('The body-fit matrix is singular or unstable. Reopen the fit review.')
        inverse = np.linalg.inv(matrix)
    except np.linalg.LinAlgError as exc:
        raise GarageError('The body-fit matrix cannot be inverted. Reopen the fit review.',
                          'body_fit_preview') from exc
    reference = review.get('reference')
    if not isinstance(reference, Mapping) or reference.get('forward') != '-Z':
        _fail('The body-fit preview requires a reviewed -Z-forward reference frame.')
    reference_digest = _digest(reference.get('reference_digest'), 'reference digest')
    profile_id = reference.get('profile_id')
    if not isinstance(profile_id, str) or not profile_id:
        _fail('The body-fit preview has no reviewed template identity.')
    wheel_geometry = reference.get('wheel_geometry')
    if not isinstance(wheel_geometry, Mapping) or set(wheel_geometry) != set(CORNERS):
        _fail('The body-fit preview needs all four fixed reference tyre envelopes.')

    rows = inspection.get('parts')
    if not isinstance(rows, list) or any(not isinstance(row, Mapping) for row in rows):
        _fail('Review the component assignments before opening the body-fit preview.')
    source_ids = [part.id for part in scene.parts]
    row_ids = [row.get('id') for row in rows]
    if (any(not isinstance(value, str) or not value for value in source_ids + row_ids)
            or len(set(source_ids)) != len(source_ids) or len(set(row_ids)) != len(row_ids)
            or set(source_ids) != set(row_ids)):
        _fail('The source components and saved assignments differ. Reopen the model review.')
    assignments = {row['id']: row for row in rows}
    transformed, excluded = [], []
    for part in scene.parts:
        role = assignments[part.id].get('role')
        if role in _EXCLUDED_ROLES:
            excluded.append(part.id)
            continue
        if role not in _STATIC_ROLES:
            _fail('Review the role of ' + part.id + ' before opening the body-fit preview.')
        shape = np.shape(part.positions)
        if len(shape) != 2 or shape[1] != 3 or shape[0] == 0:
            _fail('Static component ' + part.id + ' has no valid preview vertices.')
        points = _array(part.positions, shape, 'Static positions for ' + part.id)
        normals = _array(part.normals, shape, 'Static normals for ' + part.id)
        faces = np.asarray(part.indices)
        if (faces.ndim != 2 or faces.shape[1] != 3 or not len(faces)
                or faces.dtype.kind not in 'iu' or faces.min() < 0 or faces.max() >= len(points)):
            _fail('Static component ' + part.id + ' has invalid triangle indices.')
        if type(part.material) is not int or not 0 <= part.material < len(scene.materials):
            _fail('Static component ' + part.id + ' has no valid preview material.')
        # Position rows use A.T; normal rows use inv(A), i.e. inverse transpose
        # in column-vector convention. Normalize after nonuniform body scaling.
        with np.errstate(over='ignore', invalid='ignore', divide='ignore'):
            moved = points @ matrix.T + translation
            turned = normals @ inverse
            lengths = np.hypot.reduce(turned, axis=1)
            if not np.isfinite(lengths).all() or np.any(lengths <= 0):
                _fail('Static component ' + part.id + ' has invalid transformed normals.')
            turned = turned / lengths[:, None]
        faces = faces.astype(np.uint32, copy=True)
        if sign < 0:
            faces = faces[:, [0, 2, 1]].copy()
        transformed.append(replace(part, positions=_published(moved, 'Transformed model'),
                                   normals=_published(turned, 'Transformed normals'), indices=faces,
                                   uv=part.uv.copy(), extras=copy.deepcopy(part.extras),
                                   warnings=part.warnings.copy()))
    if not transformed:
        _fail('Assign at least one static body component to preview its fit. Wheels and brakes are shown separately.')

    materials = copy.deepcopy(scene.materials)
    guide_material = len(materials)
    materials.append({'id': guide_material, 'name': 'Reference tyre-envelope guides',
                      'color': [.08, .95, .28, 1.], 'metallic': 0., 'roughness': 1.,
                      'base_texture': None, 'double_sided': True})
    guides, guide_rows = [], {}
    used_ids = set(source_ids)
    for corner in CORNERS:
        row = wheel_geometry[corner]
        if not isinstance(row, Mapping):
            _fail('The ' + corner + ' reference envelope is invalid.')
        center = _array(row.get('center_metres'), (3,), corner + ' reference centre')
        radius = _positive(row.get('tyre_radius_metres'), corner + ' reference radius')
        width = _positive(row.get('tyre_width_metres'), corner + ' reference width')
        points, normals, faces = _envelope(center, radius, width)
        base = _GUIDE_PREFIX + corner
        identifier, suffix = base, 0
        while identifier in used_ids:
            suffix += 1
            identifier = base + '_' + str(suffix)
        used_ids.add(identifier)
        guides.append((identifier, _published(points, 'Reference envelope'),
                       _published(normals, 'Reference guide normals'), faces))
        guide_rows[identifier] = {'id': identifier, 'name': corner + ' reference tyre envelope',
            'role': 'body', 'corner': corner, 'material': guide_material,
            'is_reference_guide': True, 'reference_center_metres': center.tolist(),
            'tyre_radius_metres': radius, 'tyre_width_metres': width,
            'scope': 'fixed_reference_envelope_guide', 'native_tyre_rendered': False,
            'current_load_state_verified': False}
    guide_triangles = sum(len(item[3]) for item in guides)
    body_scene = Scene(transformed, materials, {}, b'', scene.digest, [])
    raw = build_preview(body_scene, max(1, limit - guide_triangles))
    header_bytes, vertex_count, index_count = struct.unpack_from('<III', raw, 4)
    header = json.loads(raw[16:16 + header_bytes])
    body_vertices = np.frombuffer(raw, dtype='<f4', count=vertex_count * 6,
                                  offset=16 + header_bytes).reshape(-1, 6)
    body_indices = np.frombuffer(raw, dtype='<u4', count=index_count,
                                 offset=16 + header_bytes + vertex_count * 24)
    vertices, indices = [body_vertices], [body_indices]
    for identifier, points, normals, faces in guides:
        vertices.append(np.column_stack((points, normals)).astype('<f4'))
        indices.append((faces.reshape(-1) + vertex_count).astype('<u4'))
        header['meshes'].append({'id': identifier, 'material': guide_material,
            'index_start': index_count, 'index_count': int(faces.size),
            'preview_triangles': len(faces), 'surface_simplified': False,
            'is_reference_guide': True})
        vertex_count += len(points)
        index_count += faces.size
    vertices = np.concatenate(vertices).astype('<f4')
    indices = np.concatenate(indices).astype('<u4')
    if not np.isfinite(vertices).all():
        _fail('The body-fit preview contains non-finite geometry after simplification.')
    parts_metadata = []
    static_by_id = {part.id: part for part in transformed}
    for mesh in header['meshes']:
        referenced = indices[mesh['index_start']:mesh['index_start'] + mesh['index_count']]
        positions = vertices[np.unique(referenced), :3].astype(float)
        bounds = np.array([positions.min(0), positions.max(0)])
        if mesh['id'] in guide_rows:
            record = dict(guide_rows[mesh['id']])
        else:
            original = static_by_id[mesh['id']]
            record = {'id': original.id, 'name': original.name,
                      'role': assignments[original.id]['role'], 'corner': None,
                      'material': original.material, 'is_reference_guide': False,
                      'scope': 'transformed_static_source_preview'}
        record.update(center=bounds.mean(0).tolist(), bounds=bounds.tolist(),
                      triangles=mesh['preview_triangles'],
                      surface_simplified=mesh['surface_simplified'])
        parts_metadata.append(record)
    bounds = np.array([vertices[:, :3].min(0), vertices[:, :3].max(0)], dtype=float)
    header.update(vertices=len(vertices), indices=len(indices), bounds=bounds.tolist(),
        preview_triangles=len(indices) // 3, preview_only=True, runtime_verified=False,
        build_approved=False, source_inputs_changed=False,
        scope='body_fit_preview_with_reference_envelopes', body_fit_digest=digest,
        reference_digest=reference_digest, reference_profile_id=profile_id,
        original_source_triangles=scene.triangles, source_static_triangles=body_scene.triangles,
        body_preview_triangles=len(body_indices) // 3, reference_guide_triangles=guide_triangles,
        excluded_source_part_ids=excluded, triangle_target=limit,
        reference_guide_style='thin side rings and axial ribbons',
        reference_guide_scope='Fixed profile tyre-envelope guides, not rendered native tyres or current loaded geometry.',
        geometry_policy='Complete static source surfaces with optional QEM simplification; fixed reference guides; no triangle skipping.')
    encoded = json.dumps(header, separators=(',', ':'), allow_nan=False).encode('utf-8')
    encoded += b' ' * (-len(encoded) % 4)
    result = (b'GGV1' + struct.pack('<III', len(encoded), len(vertices), len(indices))
              + encoded + vertices.tobytes() + indices.tobytes())
    return result, parts_metadata
