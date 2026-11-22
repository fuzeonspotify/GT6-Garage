"""Explicit source-axis and unit conversion for the prepared vehicle scene.

Transforms are proper rotations followed by positive uniform scale. They do not
reflect geometry, alter source bytes, weld vertices or change triangle winding.
"""
from __future__ import annotations

import math
from dataclasses import replace

import numpy as np

from .errors import GarageError
from .glb import Scene


UP_AXES = ('+Y', '+Z', '-Z', '+X', '-X', '-Y')
COORDINATE_SYSTEM = 'Right-handed, Y-up, metres'
# Each matrix maps the declared source up vector to +Y. Quarter turns preserve
# a useful remaining axis; +Z-up uses the common (x,y,z) -> (x,z,-y) convention.
_ROTATIONS = {
    '+Y': ((1, 0, 0), (0, 1, 0), (0, 0, 1)),
    '+Z': ((1, 0, 0), (0, 0, 1), (0, -1, 0)),
    '-Z': ((1, 0, 0), (0, 0, -1), (0, 1, 0)),
    '+X': ((0, -1, 0), (1, 0, 0), (0, 0, 1)),
    '-X': ((0, 1, 0), (-1, 0, 0), (0, 0, 1)),
    '-Y': ((1, 0, 0), (0, -1, 0), (0, 0, -1)),
}


def coordinate_settings(source_up_axis='+Y', metres_per_unit=1.0):
    if not isinstance(source_up_axis, str) or source_up_axis not in UP_AXES:
        raise GarageError('Choose the source up axis: +Y, +Z, -Z, +X, -X or -Y.', 'source_coordinates')
    if (isinstance(metres_per_unit, bool) or not isinstance(metres_per_unit, (int, float))
            or not 1e-6 <= metres_per_unit <= 1e6 or not math.isfinite(metres_per_unit)):
        raise GarageError('Metres per source unit must be a finite number between 0.000001 and 1000000.', 'source_coordinates')
    return {'source_up_axis': source_up_axis, 'metres_per_unit': float(metres_per_unit)}


def coordinate_description(source_up_axis='+Y', metres_per_unit=1.0):
    settings = coordinate_settings(source_up_axis, metres_per_unit)
    return {**settings, 'coordinate_system': COORDINATE_SYSTEM,
            'source_to_y_up_rotation': [list(row) for row in _ROTATIONS[source_up_axis]],
            'geometry_rule': 'prepared_position = metres_per_unit * rotation * source_world_position',
            'normal_rule': 'prepared_normal = rotation * source_world_normal',
            'source_bytes_unchanged': True}


def prepare_scene(scene: Scene, source_up_axis='+Y', metres_per_unit=1.0):
    settings = coordinate_settings(source_up_axis, metres_per_unit)
    scale = settings['metres_per_unit']
    if source_up_axis == '+Y' and scale == 1.0:
        # Keep the reviewed default path byte-identical, including all float
        # buffers, unusual signed zeros, materials and fingerprints.
        return scene
    rotation = np.asarray(_ROTATIONS[source_up_axis], dtype=np.float64)
    parts = []
    for part in scene.parts:
        positions = (part.positions.astype(np.float64) @ rotation.T * scale).astype(np.float32)
        normals = (part.normals.astype(np.float64) @ rotation.T).astype(np.float32)
        if not np.isfinite(positions).all() or not np.isfinite(normals).all():
            raise GarageError('The chosen axes or units produce non-finite geometry. Check the source scale.', 'source_coordinates')
        parts.append(replace(part, positions=positions, normals=normals,
                             warnings=part.warnings.copy()))
    warning = f'Prepared geometry uses {source_up_axis} as source up and {scale:g} metres per source unit; original model bytes are unchanged.'
    return replace(scene, parts=parts, warnings=[*scene.warnings, warning])
