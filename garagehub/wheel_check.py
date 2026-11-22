"""Player-facing fit checks for the Supra wheel pair tested in GT6 1.22."""
import math
import copy
from pathlib import Path

import numpy as np

from .errors import GarageError
from .recognition import CORNERS
from .stock_race_wheel import restore_stock_race_wheel, restore_stock_showroom_wheel
from .util import sha256


WHEEL_SET = "Toyota Supra RZ ’97"
# Product fit limits in the fixed, straight-ahead reference pose. They do
# not describe the game's suspension travel or a physical clearance envelope.
DIAMETER_RANGE = (.85, 1.15)
MAX_HUB_RADIAL_FRACTION = .15
MIN_RIM_VISIBLE = .70
MIN_TYRE_VISIBLE = .55


def centered_scale_limits(source, wheels, gear):
    """Solve the diameter and hub limits for a body centred between the axles."""
    corners = gear['corners']
    native = np.asarray([corners[name]['center_metres'] for name in CORNERS])
    source_offsets = source - source.mean(axis=0)
    native_offsets = native - native.mean(axis=0)
    diameter = [0., math.inf]
    hubs = [0., math.inf]
    for index, name in enumerate(CORNERS):
        wheel, ref = wheels[name], corners[name]
        if wheel['rim_only'] or wheel['diameter'] <= 0:
            diameter = [1., 0.]
        else:
            ratio = 2 * ref['tyre_radius_metres'] / wheel['diameter']
            diameter[0] = max(diameter[0], ratio / DIAMETER_RANGE[1])
            diameter[1] = min(diameter[1], ratio / DIAMETER_RANGE[0])
        # A hub in side view must stay inside a circle, while its lateral
        # offset must stay in an interval. Each constraint bounds the scale.
        for axes, limit in (([1, 2], MAX_HUB_RADIAL_FRACTION * ref['tyre_radius_metres']),
                            ([0], ref['tyre_width_metres'] / 2)):
            direction, target = source_offsets[index, axes], native_offsets[index, axes]
            length_sq = float(direction @ direction)
            if length_sq == 0:
                if np.linalg.norm(target) > limit:
                    hubs = [1., 0.]
                continue
            midpoint = float(direction @ target) / length_sq
            residual = target - midpoint * direction
            slack = limit**2 - float(residual @ residual)
            if slack < 0:
                hubs = [1., 0.]
                continue
            half_span = math.sqrt(slack / length_sq)
            hubs[0] = max(hubs[0], midpoint - half_span)
            hubs[1] = min(hubs[1], midpoint + half_span)
    low, high = max(diameter[0], hubs[0]), min(diameter[1], hubs[1])
    def interval(bounds):
        return [float(n) for n in bounds] if bounds[0] <= bounds[1] and all(map(math.isfinite, bounds)) else None
    return {'tyres': interval(diameter), 'hubs': interval(hubs), 'both': interval([low, high])}


def dimensional_check(fit, gear):
    game_size = gear.get('requested', {}).get('tyre_size') == 'game'
    rows = []
    messages = []
    for wheel in (fit or {}).get('corners', []):
        corner = wheel['corner']
        native = gear['corners'][corner]
        dx, dy, dz = wheel['center_offset_metres']
        diameter = wheel['supplied_over_source']
        radial_limit = MAX_HUB_RADIAL_FRACTION * native['tyre_radius_metres']
        lateral_limit = native['tyre_width_metres'] / 2
        radial = math.hypot(dy, dz)
        issues = []
        if diameter is None and not game_size:
            issues.append('Only the rim was measured. Select the outer tyre or tread.')
        elif not game_size and not DIAMETER_RANGE[0] <= diameter <= DIAMETER_RANGE[1]:
            issues.append(f'Supplied diameter is {diameter:.0%} of the source tyre. Accepted range is 85–115%.')
        if radial > radial_limit:
            issues.append(f'The axle is {radial*1000:.0f} mm from the source hub in side view. Limit {radial_limit*1000:.0f} mm.')
        if abs(dx) > lateral_limit:
            issues.append(f'The lateral offset is {abs(dx)*1000:.0f} mm. Limit {lateral_limit*1000:.0f} mm.')
        rows.append({'corner': corner, 'source_diameter_metres': None if diameter is None else wheel['source_envelope_metres'],
                     'native_diameter_metres': wheel['supplied_tyre_diameter_metres'],
                     'diameter_ratio': diameter, 'side_offset_metres': radial,
                     'lateral_offset_metres': abs(dx), 'issues': issues})
        messages.extend(corner+' · '+issue for issue in issues)
    if not rows:
        messages.append('Four outer tyres could not be measured. Check source axes and select one tyre component per corner. For a body-only model, use manual placement.')
    complete = len(rows) == 4 and (game_size or all(row['diameter_ratio'] is not None for row in rows))
    return {'schema': 1, 'wheel_set': WHEEL_SET, 'corners': rows,
            **({'tyre_size':'game'} if game_size else {}),
            **({'scale_limits':fit['scale_limits']} if fit and fit.get('scale_limits') else {}),
            'status': 'incompatible' if any((game_size or row['diameter_ratio'] is not None) and row['issues'] for row in rows)
                      else 'unmeasured' if not complete else 'compatible',
            'messages': messages, 'visibility': {}, 'limits': {
                'diameter_ratio': list(DIAMETER_RANGE), 'side_offset_tyre_radius_fraction': MAX_HUB_RADIAL_FRACTION,
                'lateral_offset_tyre_width_fraction': .5,
                'minimum_rim_visibility': MIN_RIM_VISIBLE, 'minimum_tyre_visibility': MIN_TYRE_VISIBLE}}


def _samples():
    # Uniform samples over the rim face and sidewall annulus in the YZ plane.
    # Using pixel centres off the axes avoids seams/vertices in common models.
    axis = (np.arange(18) + .5) / 9 - 1
    points = np.array([(y, z) for y in axis for z in axis])
    radius = np.linalg.norm(points, axis=1)
    return {'rim': points[radius <= .63], 'tyre': points[(radius >= .78) & (radius <= .94)]}


def _faces_near_wheel(parts, center, radius, side):
    faces = []
    owners = []
    for index, part in enumerate(parts):
        positions = part['positions']
        for start in range(0, len(part['faces']), 32768):
            triangles = positions[part['faces'][start:start+32768]]
            low, high = triangles.min(axis=1), triangles.max(axis=1)
            near = ((high[:, 1] >= center[1]-radius) & (low[:, 1] <= center[1]+radius)
                    & (high[:, 2] >= center[2]-radius) & (low[:, 2] <= center[2]+radius))
            near &= (high[:, 0] if side == 1 else -low[:, 0]) >= side*center[0]
            if near.any():
                faces.append(triangles[near])
                owners.append(np.full(np.count_nonzero(near), index, dtype=np.int32))
    if not faces:
        return np.empty((0, 3, 3)), np.empty(0, dtype=np.int32)
    return np.concatenate(faces).astype(float), np.concatenate(owners)


def _outer_depth(points, triangles, side):
    """Depth of the nearest triangle along each ray from outside the car."""
    result = np.full(len(points), -np.inf)
    hit_faces = np.full(len(points), -1, dtype=int)
    a = triangles[:, 0]
    b, c = triangles[:, 1]-a, triangles[:, 2]-a
    det = b[:, 1]*c[:, 2] - c[:, 1]*b[:, 2]
    good = np.abs(det) > 1e-12
    face_ids = np.flatnonzero(good)
    a, b, c, det = a[good], b[good], c[good], det[good]
    for index, (y, z) in enumerate(points):
        dy, dz = y-a[:, 1], z-a[:, 2]
        u = (dy*c[:, 2]-dz*c[:, 1]) / det
        v = (b[:, 1]*dz-b[:, 2]*dy) / det
        inside = (u >= -1e-8) & (v >= -1e-8) & (u+v <= 1+1e-8)
        x = a[inside, 0] + u[inside]*b[inside, 0] + v[inside]*c[inside, 0]
        if len(x):
            nearest = int(np.argmax(side*x))
            result[index] = side*x[nearest]
            hit_faces[index] = face_ids[inside][nearest]
    return result, hit_faces


def sample_native_faces(wheels, gear):
    """Only score rays that hit the actual spokes or rubber on the visible side."""
    rings = _samples()
    result = {}
    for corner in CORNERS:
        wheel = gear['corners'][corner]
        center = np.asarray(wheel['center_metres'])
        radius = wheel['tyre_radius_metres']
        side = -1 if corner[1] == 'L' else 1
        components = {part['role']: part for part in wheels if part['corner'] == corner}
        result[corner] = {}
        for role, samples in rings.items():
            points = center[1:] + samples*radius
            depth = {}
            for name, mesh in components.items():
                depth[name], _ = _outer_depth(points, mesh['positions'][mesh['faces']], side)
            front = np.maximum.reduce(list(depth.values()))
            hit = np.isfinite(depth[role]) & (depth[role] >= front)
            if np.count_nonzero(hit) < 6:
                raise GarageError('Too few visible native '+role+' samples at '+corner+'.', 'wheel_assets')
            result[corner][role] = (points[hit], depth[role][hit])
    return result


def check_exclusions(check, fit, coverage):
    included = {item['id'] for item in coverage if item['disposition'] == 'emit'}
    for wheel in (fit or {}).get('corners', []):
        duplicated = included.intersection(wheel['parts'])
        if duplicated:
            check['messages'].append(wheel['corner']+' · Exclude the source wheel parts replaced by this set: '+', '.join(sorted(duplicated))+'.')
            check['status'] = 'incompatible'
    return check


def needs_native_check(review):
    return (review['running_gear']['mode'] == 'checked_stock'
            or review['wheel_check']['status'] != 'unmeasured')


def visibility_for_body(parts, gear, samples):
    rows = {}
    for corner in CORNERS:
        ref = gear['corners'][corner]
        side = -1 if corner[1] == 'L' else 1
        center = np.array(ref['center_metres'])
        inside = center.copy()
        inside[0] -= side * ref['tyre_width_metres']
        triangles, owners = _faces_near_wheel(parts, inside, ref['tyre_radius_metres'], side)
        counts = rows[corner] = {}
        for role in ('rim', 'tyre'):
            points, wheel_depth = samples[corner][role]
            body_depth, hit = _outer_depth(points, triangles, side)
            blocked = body_depth >= wheel_depth-.0001
            visible = len(points)-int(np.count_nonzero(blocked))
            indices, totals = np.unique(owners[hit[blocked]], return_counts=True)
            covers = [{'part': parts[i]['id'], 'samples': int(total)} for i, total in
                      sorted(zip(indices, totals), key=lambda item: -int(item[1]))]
            counts[role] = {'visible': visible, 'tested': len(points), 'fraction': visible/len(points), 'occluders': covers}
    return rows


def inspect_body_visibility(assembled, assembly_project, review, wheels, hashes, hq_parts=None):
    """Use the authenticated HQ wheel surfaces against each emitted body LOD."""
    from .car_geometry import prepare_parts
    gear = review['running_gear_plan']
    check = copy.deepcopy(review['wheel_check'])
    check['wheel_files'] = hashes
    samples = sample_native_faces(wheels, gear)
    fit = (np.asarray(review['matrix']), np.asarray(review['translation_metres']), review)
    for lod in ('hq', 'race'):
        parts = hq_parts if lod == 'hq' else None
        if parts is None:
            parts, _, _ = prepare_parts(assembled, assembly_project['inspection'], lod, fit_solution=fit)
        rows = check['visibility'][lod] = visibility_for_body(parts, gear, samples)
        for corner, counts in rows.items():
            for role, limit in (('rim', MIN_RIM_VISIBLE), ('tyre', MIN_TYRE_VISIBLE)):
                fraction = counts[role]['fraction']
                if fraction < limit:
                    location = 'Garage' if lod == 'hq' else 'Race'
                    names = ', '.join(row['part'] for row in counts[role]['occluders'][:3])
                    check['messages'].append(f'{corner} · {location} body leaves {fraction:.0%} of the sampled {role} face visible. Minimum {limit:.0%}. Inspect {names} in Source components.')
    check['passed'] = check['status'] == 'compatible' and not check['messages']
    if check['status'] == 'compatible' and not check['passed']:
        check['status'] = 'incompatible'
    return check


def require_wheel_check(check):
    if not check or check.get('passed') is not True:
        reason = ' '.join(check['messages']) if check else 'Run the wheel check again.'
        raise GarageError('Resolve the wheel fit before exporting. '+reason, 'wheel_fit')


def checked_wheel_files(template_root, gear):
    """Read the two owned assets and reproduce the race wheel before export."""
    root = Path(template_root)
    try:
        hq = (root/'hq/wheel').read_bytes()
        if gear['requested'].get('wheel_set') == 'supra_factory':
            hq = restore_stock_showroom_wheel(hq)
        race = restore_stock_race_wheel((root/'race/wheel').read_bytes())
    except OSError as exc:
        raise GarageError('Choose the compatible starting package to check both wheel files.', 'wheel_assets') from exc
    hashes = {'hq/wheel': sha256(hq), 'race/wheel': sha256(race)}
    if hashes != gear['wheel_output_sha256']:
        raise GarageError('The garage or race wheel differs from the race-tested set. Choose a compatible starting package.', 'wheel_assets')
    return hashes
