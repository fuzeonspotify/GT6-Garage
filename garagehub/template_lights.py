"""Original supplied lamp geometry, mounted on reviewed rigid source surfaces.

This is a bounded reusable kit, not extracted donor bodywork. Mounts come from
ray intersections with the placed body; a missing mounting surface is a blocker.
Native light switching/projector serialization remains the shared compiler's job.
"""
from __future__ import annotations

import math
from collections.abc import Mapping

import numpy as np

from .errors import GarageError


KIT_REVISION = 3
PART_PREFIX = '__gt6_supplied_lamp_'
SUPPLIED_ENDS = {'source': (), 'supplied_front': ('front',),
                 'supplied_rear': ('rear',), 'supplied': ('front', 'rear')}
RATIOS = {'front_height_ratio': .42, 'rear_height_ratio': .50,
          'half_spacing_ratio': .32}


def normalize_lighting(value=None):
    if value is None:
        value = {'mode': 'source'}
    if not isinstance(value, Mapping) or not isinstance(value.get('mode'), str) or value['mode'] not in SUPPLIED_ENDS:
        raise GarageError('Choose the model lights or the supplied light kit.', 'shell_lighting')
    mode = value['mode']
    allowed = {'mode'} if mode == 'source' else {'mode', *RATIOS}
    if set(value)-allowed:
        raise GarageError('Unknown supplied-light setting.', 'shell_lighting')
    result = {'mode': mode}
    if SUPPLIED_ENDS[mode]:
        for key, default in RATIOS.items():
            number = value.get(key, default)
            if (isinstance(number, (bool, np.bool_)) or
                    not isinstance(number, (int, float, np.integer, np.floating))):
                raise GarageError('Supplied-light placement must use finite ratios.', 'shell_lighting')
            try:
                number = float(number)
            except (ValueError, OverflowError) as exc:
                raise GarageError('Supplied-light placement exceeds the numeric range.',
                                  'shell_lighting') from exc
            if not math.isfinite(number):
                raise GarageError('Supplied-light placement must use finite ratios.', 'shell_lighting')
            low, high = (.15, .40) if key == 'half_spacing_ratio' else (.15, .80)
            if not low <= number <= high:
                raise GarageError('Supplied-light '+key.replace('_', ' ')+
                                  ' must be between '+str(low)+' and '+str(high)+'.',
                                  'shell_lighting')
            result[key] = number
    return result


def _surface_triangles(scene, coverage, matrix, translation):
    permitted = {row['id'] for row in coverage
                 if row['disposition'] == 'emit' and row['role'] in ('body', 'paint')}
    surfaces = []
    points = []
    for part in scene.parts:
        if part.id not in permitted:
            continue
        placed = part.positions.astype(float) @ matrix.T + translation
        triangles = placed[part.indices]
        surfaces.append((part.id, triangles))
        points.append(placed)
    if not points:
        raise GarageError('Supplied lights need an emitted Body or Paint surface to attach to.',
                          'shell_light_mount')
    return surfaces, np.array([np.vstack(points).min(0), np.vstack(points).max(0)])


def _ray(surfaces, x, y, direction):
    """Intersect an axis-parallel ray with triangles, retaining source identity."""
    found = []
    for part_id, tri in surfaces:
        origin = tri[:, 0]
        a, b = tri[:, 1]-origin, tri[:, 2]-origin
        determinant = a[:, 0]*b[:, 1]-a[:, 1]*b[:, 0]
        cross = np.cross(a, b)
        length = np.linalg.norm(cross, axis=1)
        valid = (length > 0) & (np.abs(determinant) > 1e-15)
        # A nearly horizontal/sideways surface is not a front/rear lamp mount.
        valid &= np.abs(cross[:, 2]) >= .35*length
        ids = np.flatnonzero(valid)
        if not len(ids):
            continue
        delta_x, delta_y = x-origin[ids, 0], y-origin[ids, 1]
        u = (delta_x*b[ids, 1]-delta_y*b[ids, 0])/determinant[ids]
        v = (a[ids, 0]*delta_y-a[ids, 1]*delta_x)/determinant[ids]
        inside = (u >= -1e-9) & (v >= -1e-9) & (u+v <= 1+1e-9)
        for index, lu, lv in zip(ids[inside], u[inside], v[inside]):
            z = origin[index, 2]+lu*a[index, 2]+lv*b[index, 2]
            found.append((float(z), part_id, int(index)))
    if not found:
        return None
    return min(found, key=lambda item: item[0]) if direction < 0 else max(found, key=lambda item: item[0])


def _outline(x, y, width, height):
    """Eight-corner rounded-rectangle approximation; counterclockwise in XY."""
    a, b, cut = width/2, height/2, min(width, height)*.14
    return np.array([[x-a+cut, y-b], [x+a-cut, y-b], [x+a, y-b+cut],
                     [x+a, y+b-cut], [x+a-cut, y+b], [x-a+cut, y+b],
                     [x-a, y+b-cut], [x-a, y-b+cut]])


def _flat_mesh(polygons):
    vertices, normals, faces = [], [], []
    for polygon in polygons:
        polygon = np.asarray(polygon, dtype=float)
        normal = np.cross(polygon[1]-polygon[0], polygon[2]-polygon[0])
        size = np.linalg.norm(normal)
        if size <= 1e-12:
            raise GarageError('A supplied-light surface is degenerate.', 'shell_light_mount')
        normal /= size
        start = len(vertices)
        vertices.extend(polygon)
        normals.extend([normal]*len(polygon))
        faces.extend([[start, start+i, start+i+1] for i in range(1, len(polygon)-1)])
    return {'positions': np.asarray(vertices, dtype=float),
            'normals': np.asarray(normals, dtype=float),
            'uv': np.full((len(vertices), 2), .5),
            'faces': np.asarray(faces, dtype=np.int64)}


def _lamp(label, direction, xy, hits, width, height, color, role):
    outer_xy = _outline(xy[0], xy[1], width, height)
    inner_xy = _outline(xy[0], xy[1], width*.78, height*.64)
    surface_z = np.array([hit[0] for hit in hits])
    if np.ptp(surface_z) > .12:
        raise GarageError('The '+label+' mount bends too sharply for the supplied light. '
                          'Adjust its placement or use authored lamp surfaces.', 'shell_light_mount')
    front_z = (surface_z.min() if direction < 0 else surface_z.max()) + direction*.014
    outer = np.column_stack((outer_xy, np.full(8, front_z)))
    inner = np.column_stack((inner_xy, np.full(8, front_z+direction*.001)))
    # The back edge penetrates the mounting surface by 2.5 mm deliberately;
    # it cannot float at an unrelated global bounding-box extremum.
    back = np.column_stack((outer_xy, surface_z-direction*.0025))
    polygons = []
    for i in range(8):
        j = (i+1) % 8
        polygons.extend([[outer[i], outer[j], inner[j], inner[i]],
                         [outer[i], back[i], back[j], outer[j]]])
    # A curved mounting surface may make the rear cap nonplanar. Individual
    # fan triangles retain exact outward face normals and complete coverage.
    rear_center = back.mean(0)
    for i in range(8):
        polygons.append([rear_center, back[(i+1) % 8], back[i]])
    if direction < 0:
        polygons = [list(reversed(polygon)) for polygon in polygons]
    housing = _flat_mesh(polygons)
    lens = inner.copy()
    lens[:, 2] += direction*.0005
    if direction < 0:
        lens = lens[::-1]
    lens = _flat_mesh([lens])
    parts = []
    for suffix, mesh, part_role, shade in (
            ('housing', housing, 'body', [.025, .030, .035, 1.]),
            ('lens', lens, role, color)):
        parts.append({'id': PART_PREFIX+label+'_'+suffix,
                      'name': 'Supplied '+label.replace('_', ' ')+' '+suffix,
                      'role': part_role, 'color': shade, **mesh})
    receipt = {'name': label, 'role': role, 'outward_native_z': direction,
               'center_metres': [float(xy[0]), float(xy[1]), float(front_z+direction*.0015)],
               'width_metres': width, 'height_metres': height,
               'attachment_penetration_metres': .0025,
               'mount_samples': [{'position_metres': [float(p[0]), float(p[1]), hit[0]],
                                  'source_part_id': hit[1], 'source_triangle': hit[2]}
                                 for p, hit in zip(outer_xy, hits)]}
    return parts, receipt


def _mount_geometry(scene, coverage, matrix, translation):
    surfaces, bounds = _surface_triangles(scene, coverage, matrix, translation)
    extent, center = bounds[1]-bounds[0], bounds.mean(0)
    if extent[0] < .25 or extent[1] < .12 or extent[2] < .4:
        raise GarageError('The placed body is too small or flat for the supplied light kit. '
                          'Review source units and model size.', 'shell_light_mount')
    width = float(np.clip(extent[0]*.14, .04, .28))
    height = float(np.clip(extent[1]*.075, .025, .11))
    # The nine rays for a lamp can only intersect faces whose XY envelopes
    # touch its rectangle. Keep original triangle indices for the mount receipt.
    indexed = [(pid, tri, tri[:, :, :2].min(1), tri[:, :, :2].max(1))
               for pid, tri in surfaces if len(tri)]
    return bounds, extent, center, width, height, indexed


def _mount_pair(geometry, end, ratio, spacing):
    bounds, extent, center, width, height, indexed = geometry
    direction, role, color = ((-1, 'headlight', [.70, .76, .82, 1.]) if end == 'front'
                              else (1, 'taillight', [.55, .012, .008, 1.]))
    parts, mounts, half_depths = [], [], []
    y = float(bounds[0, 1]+ratio*extent[1])
    for side, sign in (('left', -1), ('right', 1)):
        x = float(center[0]+sign*spacing*extent[0])
        if x*sign <= width/2:
            raise GarageError('Center the body so supplied left and right lights stay on '
                              'their corresponding vehicle sides.', 'shell_light_mount')
        outline = _outline(x, y, width, height)
        # _ray permits a barycentric tolerance at shared triangle edges.
        pad = max(1., float(extent.max())) * 4e-9
        low, high = outline.min(0)-pad, outline.max(0)+pad
        candidates, face_ids = [], {}
        for pid, triangles, lower, upper in indexed:
            ids = np.flatnonzero((lower[:, 0] <= high[0]) & (upper[:, 0] >= low[0])
                                 & (lower[:, 1] <= high[1]) & (upper[:, 1] >= low[1]))
            if len(ids):
                candidates.append((pid, triangles[ids]))
                face_ids[pid] = ids
        hits = [_ray(candidates, px, py, direction) for px, py in [*outline, (x, y)]]
        if any(hit is None for hit in hits):
            raise GarageError('No complete body mounting surface under the supplied '+end+' '+side+
                              ' light. Adjust light height/spacing or use model lights.',
                              'shell_light_mount')
        hits = [(z, pid, int(face_ids[pid][i])) for z, pid, i in hits]
        center_hit = hits.pop()
        depths = [(hit[0]-center[2])*direction for hit in [center_hit, *hits]]
        if min(depths) <= 0:
            raise GarageError('The supplied '+end+' '+side+' light reaches the opposite half '
                              'of the body instead of its own end. Adjust light height/spacing '
                              'or provide a complete mounting surface.', 'shell_light_mount')
        half_depths.extend(depths)
        if max(abs(hit[0]-center_hit[0]) for hit in hits) > .12:
            raise GarageError('The supplied '+end+' '+side+' light spans an opening or sharp '
                              'surface change. Adjust its placement or use model lights.',
                              'shell_light_mount')
        generated, mount = _lamp(end+'_'+side, direction, (x, y), hits,
                                 width, height, color, role)
        mount['center_mount_sample'] = {'position_metres': [x, y, center_hit[0]],
            'source_part_id': center_hit[1], 'source_triangle': center_hit[2]}
        mount['minimum_body_half_depth_metres'] = float(min(depths))
        parts.extend(generated)
        mounts.append(mount)
    return parts, mounts, half_depths


def find_lamp_settings(scene, coverage, matrix, translation, lighting):
    """Search only the ends that need a pair, starting at the player's settings."""
    original = normalize_lighting(lighting)
    if original['mode'] == 'source':
        return original
    geometry = _mount_geometry(scene, coverage, matrix, translation)
    ends = SUPPLIED_ENDS[original['mode']]

    def nearest(value, low, high):
        grid = {i / 1000 for i in range(10*low, 10*high+1, 25)}
        grid.update(round(value + i*.025, 8) for i in range(-26,27)
                    if low/100 <= round(value+i*.025,8) <= high/100)
        return [value, *sorted(grid-{value}, key=lambda v: (round(abs(v-value), 12), v))]

    best, best_cost = None, float('inf')
    width, height = geometry[1][:2]
    for spacing in nearest(original['half_spacing_ratio'], 15, 40):
        # Minimize the summed squared movement of the four lamp centres in
        # metres. Keeping the original spacing must not force both pairs to
        # the bottom of the body when a small lateral move would suffice.
        cost = len(ends)*(width*(spacing-original['half_spacing_ratio']))**2
        if cost >= best_cost:
            break
        result = {**original, 'half_spacing_ratio':spacing}
        for end in ends:
            key = end+'_height_ratio'
            del result[key]
            for ratio in nearest(original[key], 15, 80):
                movement = (height*(ratio-original[key]))**2
                if cost+movement >= best_cost:
                    break
                try:
                    _mount_pair(geometry, end, ratio, spacing)
                except GarageError as exc:
                    if exc.code != 'shell_light_mount':
                        raise
                else:
                    result[key] = ratio
                    cost += movement
                    break
            if key not in result:
                break
        else:
            best, best_cost = result, cost
    if best is not None:
        return best
    raise GarageError('No pair of '+' and '.join(ends)+' lamp mounts passed the surface checks '
                      'at the sampled positions. Adjust the lamp settings or supply '
                      'authored lamp surfaces.', 'shell_light_mount')


def lamp_kit(scene, coverage, matrix, translation, lighting):
    """Return deterministic native-coordinate parts and explicit generated ownership."""
    settings = normalize_lighting(lighting)
    ends = SUPPLIED_ENDS[settings['mode']]
    if not ends:
        return [], {'mode': 'source', 'generated_parts': [], 'generated_triangles': 0}
    geometry = _mount_geometry(scene, coverage, matrix, translation)
    bounds, _, center, _, _, _ = geometry
    parts, mounts, half_depths = [], [], []
    for end in ends:
        generated, pair, depths = _mount_pair(geometry, end, settings[end+'_height_ratio'],
                                              settings['half_spacing_ratio'])
        parts.extend(generated)
        mounts.extend(pair)
        half_depths.extend(depths)
    for part in parts:
        if (not np.isfinite(part['positions']).all() or np.max(np.abs(part['positions'])) > 12
                or not np.isfinite(part['normals']).all()):
            raise GarageError('Supplied lamps exceed the finite native coordinate range.',
                              'shell_light_mount')
    return parts, {'mode': settings['mode'], 'kit_revision': KIT_REVISION,
        'ownership': 'generated_original_lamp_kit', 'settings': settings,
        'body_mount_bounds_metres': bounds.tolist(), 'mounts': mounts,
        'mount_half_check': {'passed': True, 'axis': 'native_z',
            'body_midpoint_metres': float(center[2]), 'front_direction': -1,
            'rear_direction': 1, 'samples': len(half_depths),
            'minimum_depth_metres': float(min(half_depths)),
            'scope': 'Each lamp center and rim sample stays on its corresponding body half.'},
        'generated_parts': [{'id': p['id'], 'role': p['role'],
                              'triangles': len(p['faces']), 'vertices': len(p['positions'])}
                             for p in parts],
        'generated_triangles': sum(len(p['faces']) for p in parts),
        'source_triangle_count_changed': False,
        'native_emitter_recipe': 'shared_headlamp_running_brake_switches',
        'projectors': ('fit_existing_native_projectors_to_generated_front_lens_surfaces'
                       if 'front' in ends else 'fit_existing_native_projectors_to_source_front_lens_surfaces'),
        'runtime_verified': False,
        'limits': ['Axis-aligned fixed light kit; no turn signals, reverse lamps or damage animation.',
                   'Mounting intersections are geometric evidence, not native lighting or game visibility acceptance.']}


def verify_prepared_mounts(prepared, receipt):
    """A reduced LOD must still support the exact reviewed generated housings."""
    if receipt['mode'] == 'source':
        return {'required': False}
    surfaces = [(part['id'], part['positions'][part['faces']]) for part in prepared
                if part['role'] in ('body', 'paint') and not part['id'].startswith(PART_PREFIX)]
    maximum = 0.
    for mount in receipt['mounts']:
        direction = mount['outward_native_z']
        for sample in [*mount['mount_samples'], mount['center_mount_sample']]:
            x, y, z = sample['position_metres']
            current = _ray(surfaces, x, y, direction)
            if current is None:
                raise GarageError('Geometry reduction removed a supplied-light attachment surface. '
                                  'Use a lower-detail source or authored lights.', 'shell_light_mount')
            residual = abs(current[0]-z)
            maximum = max(maximum, residual)
            if residual > .0015:
                raise GarageError('Geometry reduction moved a supplied-light attachment by more than '
                                  '1.5 mm. Use a lower-detail source or authored lights.',
                                  'shell_light_mount')
    return {'required': True, 'passed': True, 'samples': sum(len(m['mount_samples']) for m in receipt['mounts']),
            'centres_checked': len(receipt['mounts']),
            'maximum_surface_shift_metres': maximum, 'maximum_allowed_shift_metres': .0015,
            'scope': 'Emitted LOD attachment surface, not collision or game clearance.'}
