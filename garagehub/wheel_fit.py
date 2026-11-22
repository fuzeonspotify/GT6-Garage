"""Measured source/body/native tyre envelopes and deterministic rim frames.

Measurements describe the existing converter without changing source geometry,
native tyres or handling. No general compatibility tolerance has been reviewed;
even coincident wheel centres do not establish wheel-arch/suspension clearance.
"""
from __future__ import annotations

import numpy as np

from .car_profiles import PROFILE
from .errors import GarageError
from .recognition import CORNERS, axes


def native_wheel_geometry(corner, profile=None):
    """Return a private copy of one reviewed wheel frame in native body metres."""
    profile = PROFILE if profile is None else profile
    if corner not in CORNERS:
        raise GarageError('Unknown wheel corner.', 'wheel_fit')
    row = profile.get('wheel_geometry', {}).get(corner, {})
    try:
        center = np.asarray(row['center_metres'], dtype=float)
        radius = float(row['tyre_radius_metres'])
        width = float(row['tyre_width_metres'])
    except (KeyError, TypeError, ValueError) as exc:
        raise GarageError('The native profile has no reviewed '+corner+' tyre dimensions.', 'wheel_fit') from exc
    if center.shape != (3,) or not np.isfinite(center).all() or not np.isfinite([radius, width]).all() or radius <= 0 or width <= 0:
        raise GarageError('The native profile has invalid '+corner+' tyre dimensions.', 'wheel_fit')
    return {'center_metres': center.tolist(), 'radius_metres': radius, 'width_metres': width}


def _source_tyre(scene, inspection, corner):
    if corner not in CORNERS:
        raise GarageError('Unknown wheel corner.', 'wheel_fit')
    left, up, front = axes(inspection['forward'])
    mapping = {row['id']: row for row in inspection['parts']}
    tyres = [part for part in scene.parts
             if mapping.get(part.id, {}).get('role') == 'tyre'
             and mapping[part.id].get('corner') == corner]
    if not tyres:
        raise GarageError('Assign the '+corner+' tyre geometry to measure its envelope.', 'wheel_fit')
    points = np.concatenate([np.asarray(part.positions, dtype=float) for part in tyres])
    if points.ndim != 2 or points.shape[1] != 3 or not len(points) or not np.isfinite(points).all():
        raise GarageError('The '+corner+' tyre has invalid geometry positions.', 'wheel_fit')
    bounds = np.array([points.min(0), points.max(0)])
    lat = int(np.argmax(np.abs(left)))
    longitudinal = int(np.argmax(np.abs(front)))
    extent = bounds[1]-bounds[0]
    radius = float(max(extent[1], extent[longitudinal])/2)
    width = float(extent[lat])
    if not .0001 < radius < 1000 or not .0001 < width < 1000:
        raise GarageError('The '+corner+' tyre has invalid envelope dimensions.', 'wheel_fit')
    measured = {'center_metres': bounds.mean(0).tolist(),
                'radius_metres': radius, 'width_metres': width,
                'radial_extents_metres': [float(extent[1]), float(extent[longitudinal])],
                'parts': [part.id for part in tyres]}
    return points, measured, (left, up, front)


def rim_frame(scene, inspection, corner, profile=None):
    """Keep the existing rim transform exact; return its independent fit data."""
    _, measured, (left, up, front) = _source_tyre(scene, inspection, corner)
    native = native_wheel_geometry(corner, profile)
    try:
        center = np.asarray(inspection['wheel_centers'][corner], dtype=float)
        source_radius = float(inspection['wheel_radii'][corner])
    except (KeyError, TypeError, ValueError) as exc:
        raise GarageError('Assign the '+corner+' tyre centre and radius before rim conversion.', 'wheel_fit') from exc
    if center.shape != (3,) or not np.isfinite(center).all() or not .0001 < source_radius < 1000:
        raise GarageError('The '+corner+' tyre centre or inferred radius is invalid.', 'wheel_fit')
    radius, width = native['radius_metres'], native['width_metres']
    side = 1 if corner[1] == 'L' else -1
    radial_scale = radius/source_radius
    axial_scale = width/measured['width_metres']
    matrix = np.array([-side*left*axial_scale, up*radial_scale, -side*front*radial_scale])
    details = {'inspection_center_metres': center.tolist(),
               'inspection_radius_metres': source_radius,
               'radial_scale': radial_scale, 'axial_scale': axial_scale,
               'inspection_center_error_metres': float(np.linalg.norm(center-measured['center_metres'])),
               'inspection_radius_difference_metres': source_radius-measured['radius_metres'],
               'coordinate_space': 'native_wheel_local',
               'source_tyre_geometry_preserved': True}
    return center, matrix, np.array([width/2, 0, 0]), details


def tyre_fit_report(scene, inspection, body_matrix=None, body_translation=None, profile=None):
    """Report measurable fit differences without inventing an acceptance cutoff.

The standalone rim exporter can operate without a compatible body fit, so a
failed body alignment is reported explicitly instead of blocking that exporter.
Missing assigned geometry also yields an incomplete report, never a fit claim.
"""
    profile = PROFILE if profile is None else profile
    body_error = None
    if body_matrix is None or body_translation is None:
        try:
            # Local import keeps the shared frame helpers usable by alignment.
            from .car_geometry import alignment
            body_matrix, body_translation, _ = alignment(inspection, profile)
        except GarageError as exc:
            body_error = str(exc)
    if body_error is None:
        body_matrix = np.asarray(body_matrix, dtype=float)
        body_translation = np.asarray(body_translation, dtype=float)
        if body_matrix.shape != (3, 3) or body_translation.shape != (3,) or not np.isfinite(body_matrix).all() or not np.isfinite(body_translation).all() or np.linalg.det(body_matrix) == 0:
            body_error = 'A finite body alignment is required for envelope comparison.'
    rows = []
    for corner in CORNERS:
        native = native_wheel_geometry(corner, profile)
        row = {'corner': corner, 'available': False, 'source': None, 'body': None,
               'native': native, 'differences': None, 'rim_transform': None, 'issues': []}
        try:
            points, measured, _ = _source_tyre(scene, inspection, corner)
            row['source'] = measured
            _, _, _, rim = rim_frame(scene, inspection, corner, profile)
            row['rim_transform'] = rim
            if body_error is None:
                transformed = points@body_matrix.T+body_translation
                bounds = np.array([transformed.min(0), transformed.max(0)])
                extent = bounds[1]-bounds[0]
                body = {'center_metres': bounds.mean(0).tolist(),
                        'radius_metres': float(max(extent[1], extent[2])/2),
                        'width_metres': float(extent[0]),
                        'radial_extents_metres': [float(extent[1]), float(extent[2])]}
                if body['radius_metres'] <= 0 or body['width_metres'] <= 0:
                    raise GarageError('The transformed '+corner+' tyre has a collapsed envelope.', 'wheel_fit')
                row['body'] = body
                row['differences'] = {
                    'center_error_metres': float(np.linalg.norm(np.asarray(body['center_metres'])-native['center_metres'])),
                    'radius_metres': body['radius_metres']-native['radius_metres'],
                    'width_metres': body['width_metres']-native['width_metres'],
                    'radius_ratio': body['radius_metres']/native['radius_metres'],
                    'width_ratio': body['width_metres']/native['width_metres']}
                rim['radial_scale_relative_to_body'] = native['radius_metres']/body['radius_metres']
                rim['axial_scale_relative_to_body'] = native['width_metres']/body['width_metres']
                row['available'] = True
            else:
                row['issues'].append(body_error)
        except GarageError as exc:
            row['issues'].append(str(exc))
        rows.append(row)
    return {'schema': 1, 'profile_id': profile['id'],
            'fit_scope': 'tyre_envelope_diagnostics',
            'status': 'measured' if all(row['available'] for row in rows) else 'incomplete',
            'measurement_basis': 'assigned_tyre_mesh_bounds',
            'difference_convention': 'body_transformed_source_minus_native',
            'dimensional_compatibility': 'unreviewed', 'dimensional_review_required': True,
            'native_tyres_retained': True, 'source_geometry_changed': False,
            'body_transform_available': body_error is None, 'body_transform_error': body_error,
            'policy': 'Review these measurements with the source body and retained native tyres. Wheel-arch clearance, suspension behavior and runtime visibility remain unverified.',
            'corners': rows}


def normal_winding_report(positions, normals, faces):
    """Count authored normal/face disagreement without choosing a repair."""
    points = np.asarray(positions, dtype=float)
    normals = np.asarray(normals, dtype=float)
    faces = np.asarray(faces, dtype=np.int64)
    triangles = points[faces]
    cross = np.cross(triangles[:, 1]-triangles[:, 0], triangles[:, 2]-triangles[:, 0])
    averaged = normals[faces].mean(axis=1)
    dots = np.einsum('ij,ij->i', cross, averaged)
    valid = (np.linalg.norm(cross, axis=1) > 0) & (np.linalg.norm(averaged, axis=1) > 0) & np.isfinite(dots)
    opposed = int(np.count_nonzero(valid & (dots < 0)))
    assessed = int(np.count_nonzero(valid))
    return {'triangles': len(faces), 'assessed_triangles': assessed,
            'aligned_triangles': int(np.count_nonzero(valid & (dots > 0))),
            'opposed_triangles': opposed,
            'perpendicular_triangles': int(np.count_nonzero(valid & (dots == 0))),
            'unassessable_triangles': len(faces)-assessed,
            'opposed_fraction': opposed/assessed if assessed else None,
            'repair_applied': False}


def wheel_source_orientation_report(scene, inspection):
    """Disclose wheel-source orientation and two-sided material review needs.

An authored normal conflict does not choose which side of a surface is intended
to face outward. A double-sided source material also does not establish the
native shader/rasterizer's handling. This report makes no geometry repair or
approval decision; it preserves both source winding and source normals.
"""
    mapping = {row['id']: row for row in inspection['parts']}
    parts = []
    for part in scene.parts:
        assigned = mapping.get(part.id, {})
        role = assigned.get('role')
        if role not in ('rim', 'valve'):
            continue
        winding = normal_winding_report(part.positions, part.normals, part.indices)
        disagreement = winding['opposed_triangles'] > 0
        double_sided = bool(scene.materials[part.material].get('double_sided', False))
        parts.append({'part': part.id, 'name': part.name, 'role': role,
                      'corner': assigned.get('corner'),
                      'source_normal_winding': winding,
                      'source_double_sided': double_sided,
                      'normal_disagreement': disagreement,
                      'review_required': disagreement or double_sided})
    return {'schema': 1, 'parts': parts,
            'normal_disagreement_parts': sum(row['normal_disagreement'] for row in parts),
            'double_sided_parts': sum(row['source_double_sided'] for row in parts),
            'review_required': any(row['review_required'] for row in parts),
            'geometry_changed': False, 'normals_changed': False,
            'native_double_sided_verified': False}
