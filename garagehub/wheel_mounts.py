"""Rigid wheel-local placement without deriving size from unrelated geometry.

A captured mount describes one pose, not the complete suspension or tyre
contract.  Translation is allowed only along the spin axis: moving a rotating
vertex off that axis would make it orbit instead of correcting body alignment.
"""
from __future__ import annotations

import numpy as np

from .errors import GarageError
from .recognition import CORNERS, axes


def rigid_axle_mount(inspection, corner, native_local_to_body):
    """Preserve source dimensions and lateral centre in a reference mount pose.

The source body convention is the existing native lab's rigid forward/up frame
centred laterally and longitudinally between the four tyres.  The measured
native matrix is used only to place the source reference along local X and to
report the remaining body-to-axle error.  Its suspension/camber/steering state
is not baked into the source vertices.
"""
    if corner not in CORNERS:
        raise GarageError('Unknown wheel corner.', 'wheel_mount')
    try:
        centers = np.array([inspection['wheel_centers'][k] for k in CORNERS], dtype=float)
        mount = np.asarray(native_local_to_body, dtype=float)
    except (KeyError, TypeError, ValueError) as exc:
        raise GarageError('Four source centres and a measured native wheel frame are required.', 'wheel_mount') from exc
    if centers.shape != (4, 3) or not np.isfinite(centers).all():
        raise GarageError('Source wheel centres must be four finite 3D points.', 'wheel_mount')
    if mount.shape != (3, 4) or not np.isfinite(mount).all():
        raise GarageError('The native reference mount must be a finite 3 by 4 matrix.', 'wheel_mount')
    rotation = mount[:, :3]
    # Float32 capture constants have small rounding errors.  This is a numeric
    # rigid-matrix check, not a physical fit tolerance.
    if not np.allclose(rotation.T@rotation, np.eye(3), rtol=0, atol=2e-5) or abs(float(np.linalg.det(rotation))-1) > 2e-5:
        raise GarageError('The reference mount is scaled, sheared or reflected; review its coordinate contract.', 'wheel_mount')
    if abs(float(rotation[0, 0])) < .5:
        raise GarageError('The reference spin axis is not lateral enough for an axial-only fit.', 'wheel_mount')
    left, up, front = axes(inspection['forward'])
    center = centers[CORNERS.index(corner)]
    origin = centers.mean(0)*np.array([1., 0., 1.])
    body_matrix = np.array([-left, up, -front])
    target = (center-origin)@body_matrix.T
    side = 1 if corner[1] == 'L' else -1
    source_to_local = np.array([-side*left, up, -side*front])
    offset = np.array([(target[0]-mount[0, 3])/rotation[0, 0], 0., 0.])
    fitted = rotation@offset+mount[:, 3]
    return center.copy(), source_to_local, offset, {
        'corner': corner,
        'fit_policy': 'preserve_source_dimensions_axial_translation_only',
        'radial_scale': 1., 'axial_scale': 1.,
        'axial_offset_metres': float(offset[0]),
        'radial_offset_metres': [0., 0.],
        'source_center_metres': center.tolist(),
        'source_center_in_native_body_frame': target.tolist(),
        'reference_mount_local_to_body': mount.tolist(),
        'fitted_center_in_reference_body_pose': fitted.tolist(),
        'source_body_minus_fitted_center_metres': (target-fitted).tolist(),
        'source_to_local_rotation': source_to_local.tolist(),
        'source_dimensions_preserved': True,
        'body_alignment_applied': False,
        'suspension_or_physics_changed': False,
        'mount_pose_is_acceptance': False,
    }
