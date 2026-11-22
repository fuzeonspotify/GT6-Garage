"""Suggest reversible supplied-wheel settings from the current prepared model."""
from __future__ import annotations

import copy
import re
from pathlib import Path

import numpy as np

from .errors import GarageError
from .recognition import CORNERS, axes
from .running_gear import resolve_running_gear
from .surface_realization import native_finish_substitutions
from .template_lights import find_lamp_settings
from .template_shell import _digest, normalize_shell_settings
from .wheel_check import (MIN_RIM_VISIBLE, MIN_TYRE_VISIBLE, centered_scale_limits, checked_wheel_files,
                          dimensional_check, sample_native_faces, visibility_for_body)


def _wheel_sections(part, orientation, origin, grouped):
    """Partition a wheel-only primitive by actual faces, including a complete axle."""
    points = part.positions @ orientation.T
    if not grouped:
        return [points]
    side = (points[:, 0] >= origin[0]).astype(np.int8)
    axle = (points[:, 2] >= origin[2]).astype(np.int8)
    owners = side + 2*axle
    for start in range(0, len(part.indices), 65536):
        block = owners[part.indices[start:start+65536]]
        if np.any(block != block[:, :1]):
            return []
    return [points[owners == corner] for corner in np.unique(owners)]


def _round_wheel(points, origin, car_size, directions, picked, axle_named=False):
    low, high = points.min(axis=0), points.max(axis=0)
    centre = (low+high)/2
    x, y, z = centre
    width, height, length = high-low
    car_width, car_height, car_length = car_size
    diameter = max(height, length)
    if (not .035*car_length <= diameter <= .49*car_length
            or min(height, length) < (.75 if picked else .83)*diameter or not 0 < width < .85*diameter
            or abs(x-origin[0]) < .21*car_width or abs(z-origin[2]) < (.12 if axle_named else .15)*car_length
            or y > origin[1]+.08*car_height):
        return None
    # Multiple projected support directions reject square mudflaps and boxes.
    yz = points[:, 1:] - centre[1:]
    radial = np.array([np.max(yz @ direction) for direction in directions])
    if radial.min() < (.75 if picked else .80)*diameter/2 or radial.max() > 1.10*diameter/2:
        return None
    corner = ('F' if z < origin[2] else 'R')+('L' if x < origin[0] else 'R')
    return corner, centre, diameter, (low[0], high[0])


def _wheel_groups(scene, project, orientation):
    # Work in the same X lateral, Y up, -Z front frame used for serialization.
    # Re-evaluate the original geometry of excluded wheels on a reopened project.
    bounds = scene.bounds
    origin = bounds.mean(0) @ orientation.T
    car_width, car_height, car_length = (bounds[1]-bounds[0]) @ np.abs(orientation).T
    groups = {corner: [] for corner in CORNERS}
    chosen = (project.get('template_shell_settings') or {}).get('running_gear', {}).get('source_tyres', {})
    by_id = {pid: corner for corner, pid in chosen.items()}
    if min(car_width, car_height, car_length) <= 0:
        return {}
    angles = np.arange(0, 2*np.pi, np.pi/8)
    directions = np.column_stack((np.cos(angles), np.sin(angles)))
    for part in scene.parts:
        override = project.get('overrides', {}).get(part.id, {}).get('role')
        tag = part.extras.get('gt6_role')
        picked = part.id in by_id
        if ((not picked and override not in (None, 'ignore', 'rim', 'tyre'))
                or tag not in (None, 'rim', 'tyre')):
            continue
        label = (part.name+' '+scene.materials[part.material]['name']).casefold().replace('_', ' ')
        if re.search(r'steer|arch|fender|liner|wheel.?well|caliper|brake|rotor|spare|hubcap', label):
            continue
        x, _, z = part.center @ orientation.T
        grouped = abs(x-origin[0]) < .21*car_width or abs(z-origin[2]) < .15*car_length
        if grouped and not (picked or tag in ('tyre','rim') or re.search(r'wheel|tyre|tire|tread', label)):
            continue
        rim_only = (override == 'rim' or tag == 'rim' or bool(re.search(r'\brims?\b|alloy', label)))
        axle_named = bool(re.search(r'(?:wheel|tire|tyre)[\W_]*(?:front|back|rear)|(?:front|back|rear)[\W_]*(?:wheel|tyre|tire)',label))
        sections = _wheel_sections(part, orientation, origin, grouped)
        measured = [_round_wheel(points, origin, (car_width, car_height, car_length), directions, picked, axle_named)
                    for points in sections]
        # Dropping a shared component removes every face. Accept the original
        # only when every section has a complete, distinct wheel envelope.
        if not measured or any(row is None for row in measured) or len({row[0] for row in measured}) != len(measured):
            continue
        for corner, center, diameter, lateral in measured:
            groups[corner].append((part.id, center, diameter, rim_only, lateral))
    if any(not rows for rows in groups.values()):
        return {}
    wheels = {}
    for corner, rows in groups.items():
        # Prefer the largest complete radial section, never a mean of hub,
        # valve and tyre centres. Separate nonconcentric candidates are ambiguous.
        largest = max(rows, key=lambda row: (row[2], not row[3]))
        if corner in chosen:
            candidates = [row for row in rows if row[0] == chosen[corner]]
            if not candidates or candidates[0][2] < .98*largest[2]:
                return {}
            _, center, diameter, rim_only, _ = candidates[0]
        else:
            _, center, diameter, rim_only, _ = largest
        if any(np.linalg.norm(other[1:]-center[1:]) > .10*diameter
               for _, other, _, _, _ in rows):
            return {}
        limits = [row[4] for row in rows]
        wheels[corner] = {'center': center, 'diameter': float(diameter),
                          'lateral_bounds': (float(np.min(limits)), float(np.max(limits))),
                          'rim_only': rim_only, 'parts': [row[0] for row in rows]}
    for left, right in (('FL','FR'), ('RL','RR')):
        a, b = wheels[left], wheels[right]
        if (min(a['diameter'], b['diameter']) < .80*max(a['diameter'], b['diameter'])
                or np.linalg.norm(a['center'][1:]-b['center'][1:]) > .25*max(a['diameter'], b['diameter'])):
            return {}
    return wheels


def _inside_wheel_envelopes(part, wheels, rotation):
    """Every face must fit wholly within one measured wheel, even for grouped brakes."""
    if not wheels:
        return False
    points = part.positions @ rotation.T
    owner = np.full(len(points), -1, dtype=np.int8)
    for index, wheel in enumerate(wheels.values()):
        low, high = wheel['lateral_bounds']
        radius = wheel['diameter'] / 2
        tolerance = .01 * radius
        radial = points[:, 1:] - wheel['center'][1:]
        inside = ((points[:, 0] >= low - tolerance) & (points[:, 0] <= high + tolerance)
                  & (np.einsum('ij,ij->i', radial, radial) <= (radius + tolerance)**2))
        if np.any(inside & (owner != -1)):
            return False
        owner[inside] = index
    for start in range(0, len(part.indices), 65536):
        corners = owner[part.indices[start:start + 65536]]
        if np.any(corners < 0) or np.any(corners != corners[:, :1]):
            return False
    return True


def _frame(project, placement):
    left, up, front = axes(project['forward'])
    rotation = np.array([-left, up, -front])
    if placement['flip_front']:
        rotation = np.diag([-1., 1., -1.]) @ rotation
    return rotation


def _tail_spare(part, wheels, rotation):
    if not wheels or not re.fullmatch(r'(?:(?:spare[ _.-]*)?(?:wheel|tyre|tire)[ _.-]*(?:back|rear|spare)?|spare)(?:[ _.-]*static)?',part.name,flags=re.I):
        return False
    left,right=(wheels[c] for c in ('RL','RR'))
    midpoint=(left['center']+right['center'])/2
    diameter=(left['diameter']+right['diameter'])/2
    points=part.positions @ rotation.T
    low,high=points.min(0),points.max(0)
    center=(low+high)/2
    width,height,depth=high-low
    if (center[2]<midpoint[2]+.5*diameter or abs(center[0]-midpoint[0])>.2*diameter
            or not .75*diameter<=width<=1.25*diameter or not .88*width<=height<=1.12*width
            or not 0<depth<.8*width):
        return False
    angles=np.arange(0,2*np.pi,np.pi/8)
    support=(points[:,:2]-center[:2]) @ np.stack([np.cos(angles),np.sin(angles)])
    radii=support.max(0)
    return bool(radii.min()>=.42*width and radii.max()<=.57*width)


def replacement_ids(scene, project, wheels, rotation):
    measured = {pid for wheel in wheels.values() for pid in wheel['parts']}
    parts = {part.id: part for part in scene.parts}
    for row in project['inspection']['parts']:
        pid = row['id']
        mechanical = (row['role'] in ('rim', 'tyre', 'valve', 'brake_rotor', 'brake_caliper')
                      or (pid not in project.get('overrides', {}) and row.get('mechanical_role_proposals')))
        if pid not in measured and ((mechanical and _inside_wheel_envelopes(parts[pid], wheels, rotation))
                or ((mechanical or row['role']=='ignore') and _tail_spare(parts[pid], wheels, rotation))):
            measured.add(pid)
    return measured


def stock_defaults(settings=None):
    """Part assignments and a driver's pose belong to their own car."""
    if settings is None:
        return {'wheel_set':'supra_factory','tyre_size':'game','placement':None}
    normalized = normalize_shell_settings(settings)
    gear = normalized['running_gear']
    placement = normalized['placement']
    return {'wheel_set':'supra_factory','tyre_size':gear.get('tyre_size','match_source'),
            'placement':{key:copy.deepcopy(placement[key]) for key in ('scale','translation_metres','flip_front')}}


def prepare_stock_replacement(scene, project, inspection, defaults=None):
    """Set up a fresh import using the original hubs as guides for the game's wheels."""
    settings = normalize_shell_settings(project.get('template_shell_settings'))
    settings['running_gear'] = {'schema': 1, 'mode': 'checked_stock', 'tyre_size': 'game', 'wheel_set':'supra_factory'}
    if defaults and defaults.get('tyre_size') == 'match_source':
        settings['running_gear'].pop('tyre_size')
    saved = defaults.get('placement') if defaults else None
    if saved:
        settings['placement'] = normalize_shell_settings({'placement':saved})['placement']
    inputs = {**project, 'inspection': inspection, 'template_shell_settings': settings}
    rotation = _frame(inputs, settings['placement'])
    wheels = _wheel_groups(scene, inputs, rotation)
    if not saved:
        _fit(wheels, resolve_running_gear(settings['running_gear']), settings['placement'], 'wheelbase')
    else:
        project['stock_placement_mode'] = 'saved_placement'
    for pid in sorted(replacement_ids(scene, inputs, wheels, rotation)):
        project['overrides'][pid] = {'role':'ignore', 'corner':None}
        settings['dispositions'][pid] = {'disposition':'excluded', 'reason':'Replaced by the GT6 wheel set.'}
    project.update(assembly_mode='template_shell', template_shell_settings=settings, approved_roles=False,
                   stock_setup_pending=True)
    project.pop('template_shell_decision', None)


def _fit(wheels, gear, placement, mode):
    if not wheels:
        return None
    source = np.array([wheels[c]['center'] for c in CORNERS])
    native = np.array([gear['corners'][c]['center_metres'] for c in CORNERS])
    base = float(source[2:,2].mean()-source[:2,2].mean())
    if mode == 'wheelbase':
        placement['scale'] = gear['resolved']['wheelbase_metres']/base
    scale = placement['scale']
    if mode in ('wheelbase', 'keep_size'):
        placement['translation_metres'] = (native.mean(0)-scale*source.mean(0)).tolist()
    delta = source*scale+placement['translation_metres']-native
    measurements = []
    for i, corner in enumerate(CORNERS):
        wheel = wheels[corner]
        source_diameter = wheel['diameter']*scale
        supplied_diameter = 2*gear['corners'][corner]['tyre_radius_metres']
        measurements.append({'corner': corner, 'parts': wheel['parts'],
            'source_envelope_metres': source_diameter, 'rim_only': wheel['rim_only'],
            'supplied_tyre_diameter_metres': supplied_diameter,
            'supplied_over_source': None if wheel['rim_only'] else supplied_diameter/source_diameter,
            'center_offset_metres': delta[i].tolist()})
    return {'source_wheelbase_metres': base, 'placed_wheelbase_metres':base*scale,
            'profile_wheelbase_metres': gear['resolved']['wheelbase_metres'],
            'corners': measurements, 'scale_limits': centered_scale_limits(source, wheels, gear),
            'scope': 'Static source mesh bounds and supplied profile references.'}


def current_wheel_fit(scene, project, settings):
    placement = dict(settings['placement'])
    groups = _wheel_groups(scene, {**project, 'template_shell_settings':settings}, _frame(project, placement))
    return _fit(groups, resolve_running_gear(settings['running_gear']), placement, 'current')


def _exterior_optic(scene, row, project, rotation, longitudinal_bounds):
    role = row['role']
    material = scene.materials[row['material']]
    if role in ('glass', 'headlight', 'taillight', 'brakelight'):
        return role if material['double_sided'] or any(material['emissive']) else None
    if (role not in ('body','paint') or row['id'] in project.get('overrides', {})
            or not any(material['emissive']) or material['alpha_mode'] != 'OPAQUE'):
        return None
    # Only classify a complete, localized emissive group. The preview lets the
    # player inspect signals, marker lights and lenses before accepting roles.
    if re.search(r'chrome|exhaust|mirror|door.?lock|badge|emblem|interior|trim', row['material_name'], re.I):
        return None
    low, high = longitudinal_bounds
    z = np.asarray(row['bounds']) @ rotation[2]
    if max(z) <= low+.28*(high-low):
        return 'headlight'
    if min(z) >= high-.28*(high-low):
        return 'taillight'
    return None


def _find_placement(scene, coverage, rotation, wheels, gear, placement, native_root):
    """Search small uniform corrections to the wheelbase fit before proposing edits."""
    from .template_shell_preview import decode_template_wheels
    included = {row['id'] for row in coverage if row['disposition'] == 'emit'}
    base_parts = [(part.id, part.positions @ rotation.T, part.indices) for part in scene.parts
                  if part.id in included]
    matched = placement['scale']
    source_mid = np.mean([wheels[c]['center'] for c in CORNERS], axis=0)
    native_mid = np.mean([gear['corners'][c]['center_metres'] for c in CORNERS], axis=0)
    limits = centered_scale_limits(np.asarray([wheels[c]['center'] for c in CORNERS]), wheels, gear)
    interval = limits['hubs' if gear['requested'].get('tyre_size') == 'game' else 'both'] if limits else None
    if not interval:
        return {'matched': False, 'factor': None, 'placements_sampled': 0}
    low, high = interval
    # Preserve the established placements first. Also try the nearest viable
    # size and interior points when a thin feasible interval falls between them.
    coarse = [matched*f for f in (1., .99, 1.01, .98, 1.02, .97, 1.03)]
    extra = np.linspace(low, high, 17)
    extra = sorted(extra, key=lambda scale: abs(scale-matched))
    scales = dict.fromkeys([*coarse, *extra])
    samples = None
    tested = 0
    for scale in scales:
        scale = float(scale)
        if scale < low-1e-12 or scale > high+1e-12:
            continue
        proposed = dict(placement, scale=scale, translation_metres=(native_mid-scale*source_mid).tolist())
        fit = _fit(wheels, gear, proposed, 'current')
        if dimensional_check(fit, gear)['status'] != 'compatible':
            continue
        if samples is None:
            meshes, _ = decode_template_wheels((Path(native_root)/'hq/wheel').read_bytes(), gear)
            samples = sample_native_faces(meshes, gear)
        parts = [{'id':pid, 'positions':positions*scale+proposed['translation_metres'], 'faces':faces}
                 for pid, positions, faces in base_parts]
        rows = visibility_for_body(parts, gear, samples)
        tested += 1
        if all(row['rim']['fraction'] >= MIN_RIM_VISIBLE and row['tyre']['fraction'] >= MIN_TYRE_VISIBLE
               for row in rows.values()):
            placement.update(proposed)
            return {'matched': True, 'factor': scale/matched, 'placements_sampled': tested, 'source_visibility': rows}
    return {'matched': False, 'factor': None, 'placements_sampled': tested}


def suggest_setup(scene, project, sizing, *, template_root=None):
    if project.get('assembly_mode') != 'template_shell':
        raise GarageError('Choose supplied wheels to find an assembly setup.', 'assembly_mode')
    if sizing not in ('keep_size', 'wheelbase', 'checked_fit', 'saved_placement'):
        raise GarageError('Choose whether to keep the current scale or match the supplied wheelbase.')
    settings = normalize_shell_settings(project.get('template_shell_settings'))
    gear = resolve_running_gear(settings['running_gear'])
    if not gear['ready']:
        raise GarageError(gear['blockers'][0]['message'], 'running_gear_unsupported')
    placement = settings['placement']
    rotation = _frame(project, placement)
    wheels = _wheel_groups(scene, project, rotation)
    fit = _fit(wheels, gear, placement, 'wheelbase' if sizing == 'checked_fit' else sizing)
    replaced = replacement_ids(scene, project, wheels, rotation)
    overrides = copy.deepcopy(project.get('overrides', {}))
    exclusions = []
    optics = []
    finishes = []
    materials = scene.document.get('materials', [])
    coverage = []
    has_lamps = False
    longitudinal_bounds = sorted(scene.bounds @ rotation[2])
    for part in project['inspection']['parts']:
        pid, role = part['id'], part['role']
        if pid in replaced and (role != 'ignore' or pid in settings['dispositions']):
            exclusions.append({'id': pid, 'name': part['name']})
            overrides[pid] = {'role': 'ignore', 'corner': None}
            settings['dispositions'][pid] = {'disposition': 'excluded',
                'reason': 'Replaced with the supplied native wheel assembly in the reviewed setup.'}
            role = 'ignore'
        elif role != 'ignore':
            optical_role = _exterior_optic(scene, part, project, rotation, longitudinal_bounds)
            if optical_role is not None:
                role = optical_role
                overrides[pid] = {'role': role, 'corner': None, 'use_native_optics': True}
                optics.append({'id':pid, 'name':part['name'], 'material':part['material_name'], 'role':role})
            if (role in ('body','paint','interior','headlight','taillight','brakelight','glass')
                    and part['material'] < len(materials)):
                effects = native_finish_substitutions(materials[part['material']])
                if effects:
                    overrides.setdefault(pid, {'role':role, 'corner':None})['use_native_finish'] = True
                    finishes.append({'id':pid, 'material':part['material_name'], 'role':role, 'effects':effects})
        has_lamps |= role in ('headlight', 'taillight', 'brakelight')
        coverage.append({'id': pid, 'role': role, 'disposition': 'excluded' if role=='ignore' else 'emit'})
    notes = []
    wheel_files, file_error, search = None, None, None
    if gear['requested']['mode'] == 'checked_stock':
        if template_root:
            from .car_profiles import validate_template
            try:
                template = validate_template(Path(template_root))
                wheel_files = checked_wheel_files(template['root'], gear)
                if sizing == 'checked_fit' and wheels:
                    search = _find_placement(scene, coverage, rotation, wheels, gear, placement, template['root'])
                    fit = _fit(wheels, gear, placement, 'current')
            except GarageError as exc:
                file_error = str(exc)
        else:
            file_error = 'Choose the compatible starting package in Setup to check both GT6 wheel files.'
    if not wheels:
        notes.append('Four separate wheel envelopes could not be measured. Set body placement in the assembled preview.')
    # Use authored lenses when present. An incomplete set stays visible for
    # review. Never silently remove authored lenses to make a kit succeed.
    if not has_lamps:
        lights = settings['lighting'] if settings['lighting']['mode']=='supplied' else {'mode':'supplied'}
        try:
            settings['lighting'] = find_lamp_settings(scene, coverage, rotation*placement['scale'],
                np.asarray(placement['translation_metres']), lights)
        except GarageError as exc:
            if exc.code != 'shell_light_mount':
                raise
            notes.append(str(exc))
            settings['lighting'] = lights
    else:
        settings['lighting'] = {'mode':'source'}
        roles = {row['role'] for row in coverage if row['disposition']=='emit'}
        if not {'headlight','taillight'} <= roles:
            notes.append('Only part of the source lamp set was recognized. Assign the missing lamp surfaces or review exclusions for supplied lights.')
    changes = {'template_shell_settings': normalize_shell_settings(settings), 'overrides': overrides}
    setup = {'id': project['id'], 'sizing': sizing, 'changes': changes, 'exclusions': exclusions, 'optics':optics,
             'finishes': finishes, 'fit': fit, 'notes': notes}
    setup['wheel_check'] = dimensional_check(fit, gear)
    setup['wheel_check'].update(wheel_files=wheel_files, search=search, wheel_file_error=file_error)
    # Includes untouched source assignments, coordinate choices, approval and
    # destination so a second browser tab cannot apply an out-of-date proposal.
    setup['token'] = _digest({'project': project, 'setup': setup, 'gear': gear['plan_digest']})
    return setup
