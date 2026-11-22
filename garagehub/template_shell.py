"""Explicitly reviewed body-on-template native assembly route.

A rigid shell uses uniform placement with authored lamps or an original supplied
light kit; exact source faces may be explicitly excluded with a recorded reason.
The authenticated template supplies stock packed race wheels, showroom wheels, mechanical programs,
interior and driving configuration. Existing authored-component production
approval remains separate from this bounded assembly contract.
No source axle measurements, suspension mounts or collision fields are invented.
Native serialization success is not runtime or complete-car acceptance.
"""
from __future__ import annotations

import copy
import hashlib
import hmac
import json
import math
import re
import shutil
import time
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from .car_profiles import PROFILE, ASSETS, validate_template
from .errors import GarageError
from .material_help import BODY_DETAIL, OPTICS, REIMPORT, alpha_steps, finish_steps, material_error
from .stock_race_wheel import restore_stock_race_wheel, restore_stock_showroom_wheel
from .glb import GLB, Scene, Part
from .install_targets import installation, selection_actions
from .mechanical_parts import _part, _geometry_hash
from .mechanical_policy import require_static_compatible
from .recognition import CORNER_ROLES, axes, infer, _mechanical_names, component_name_role
from .scene_coordinates import coordinate_settings, COORDINATE_SYSTEM, prepare_scene
from .util import atomic_json, hash_file, sha256
from .template_lights import normalize_lighting, lamp_kit, verify_prepared_mounts, PART_PREFIX
from .surface_realization import opaque_two_sided_plan, verify_native_opaque_culling
from .running_gear import (normalize_running_gear, resolve_running_gear,
                           require_inherited_running_gear)
from .wheel_check import (check_exclusions, checked_wheel_files, dimensional_check,
                          inspect_body_visibility, needs_native_check, require_wheel_check)


SCHEMA = 2
ALGORITHM_REVISION = 6
STATIC_ROLES = frozenset(('body', 'paint', 'glass', 'headlight', 'taillight',
                          'brakelight', 'interior'))
MAXIMUM_POSITION_METRES = 12.


def _fail(message, code='template_shell'):
    raise GarageError(message, code)


def _json(value):
    try:
        return json.dumps(value, sort_keys=True, separators=(',', ':'),
                          ensure_ascii=False, allow_nan=False).encode('utf-8')
    except (TypeError, ValueError, OverflowError) as exc:
        raise GarageError('Template-shell inputs must contain finite JSON data.',
                          'template_shell') from exc


def _digest(value):
    return hashlib.sha256(_json(value)).hexdigest()


def _is_digest(value):
    return (isinstance(value, str) and len(value) == 64
            and all(c in '0123456789abcdef' for c in value))


def _number(value, label, *, positive=False):
    if (isinstance(value, (bool, np.bool_))
            or not isinstance(value, (int, float, np.integer, np.floating))):
        _fail(label+' must be a finite number.', 'shell_placement')
    try:
        value = float(value)
    except (ValueError, OverflowError) as exc:
        raise GarageError(label+' exceeds the numeric range.', 'shell_placement') from exc
    if not math.isfinite(value) or (positive and value <= 0):
        _fail(label+' must be finite'+(' and positive.' if positive else '.'),
              'shell_placement')
    return value


def _placement(value):
    if (not isinstance(value, Mapping) or
            not {'scale', 'translation_metres'} <= set(value) or
            set(value)-{'scale', 'translation_metres', 'flip_front', 'driver'}):
        _fail('Specify one uniform scale and a three-component translation in metres.',
              'shell_placement')
    shift = value['translation_metres']
    if not isinstance(shift, (list, tuple)) or len(shift) != 3:
        _fail('Body translation must contain exactly X, Y and Z in metres.', 'shell_placement')
    flip = value.get('flip_front', False)
    if type(flip) is not bool:
        _fail('Flip front must be an explicit boolean choice.', 'shell_placement')
    from .driver_fit import normalize_driver
    driver = normalize_driver(value.get('driver'))
    return {'scale': _number(value['scale'], 'Uniform body scale', positive=True),
            'translation_metres': [_number(x, 'Body translation') for x in shift],
            'flip_front': flip, **({'driver':driver} if driver is not None else {})}


def _dispositions(value):
    value = {} if value is None else value
    if not isinstance(value, Mapping):
        _fail('Source dispositions must be an object keyed by component.', 'shell_coverage')
    result = {}
    for pid, decision in value.items():
        if (not isinstance(pid, str) or not pid or len(pid) > 256 or
                not isinstance(decision, Mapping) or
                set(decision) != {'disposition', 'reason'} or
                decision.get('disposition') != 'excluded' or
                not isinstance(decision.get('reason'), str) or
                not decision['reason'].strip() or len(decision['reason']) > 2000):
            _fail('An excluded component needs an explicit exclusion and reason.', 'shell_coverage')
        result[pid] = {'disposition': 'excluded', 'reason': decision['reason'].strip()}
    return result


def normalize_shell_settings(value=None):
    """Validate saved/UI settings without approving source geometry or a build."""
    value = {} if value is None else value
    if not isinstance(value, Mapping) or set(value)-{'placement', 'dispositions', 'lighting', 'running_gear'}:
        _fail('Unknown template-shell setting.', 'shell_settings')
    return {'placement': _placement(value.get('placement',
                {'scale': 1., 'translation_metres': [0., 0., 0.]})),
            'dispositions': _dispositions(value.get('dispositions')),
            'lighting': normalize_lighting(value.get('lighting')),
            'running_gear': normalize_running_gear(value.get('running_gear'))}


def _reference(running_gear_plan):
    if (not isinstance(PROFILE.get('id'), str) or not PROFILE['id']
            or not isinstance(PROFILE.get('label'), str) or not ASSETS or any(
            not isinstance(k, str) or not _is_digest(v) for k, v in ASSETS.items())):
        _fail('The native template contract is incomplete.')
    gear = require_inherited_running_gear(running_gear_plan)
    contract = {'profile_id': PROFILE['id'], 'profile_label': PROFILE['label'],
        'profile_contract_sha256': _digest(PROFILE), 'asset_sha256': copy.deepcopy(ASSETS),
        'coordinate_system': COORDINATE_SYSTEM, 'forward': '-Z',
        'wheel_geometry': copy.deepcopy(gear['corners']),
        'running_gear_plan_digest': gear['plan_digest'],
        'source_dependent': False, 'current_mount_verified': False,
        'requires_template_authentication': True,
        'scope': 'Fixed reviewed template references; not loaded suspension or clearance evidence.'}
    contract['reference_digest'] = _digest(contract)
    return contract


def _wheel_animations_only(original, project):
    """A spinning source wheel can be discarded with its entire node subtree."""
    animations = original.document.get('animations', [])
    if not animations:
        return True
    nodes = original.document.get('nodes', [])
    excluded = (project.get('template_shell_settings') or {}).get('dispositions', {})
    roles = project.get('overrides', {})
    removed = {p.id for p in original.parts if roles.get(p.id, {}).get('role') == 'ignore'
               and excluded.get(p.id, {}).get('disposition') == 'excluded'}
    targets = set()
    for animation in animations:
        channels = animation.get('channels') if isinstance(animation, dict) else None
        if not isinstance(channels, list) or not channels:
            return False
        for channel in channels:
            target = channel.get('target') if isinstance(channel, dict) else None
            if not isinstance(target,dict) or target.get('path') not in ('rotation','translation','scale'):
                return False
            index = target.get('node')
            if type(index) is not int or not 0 <= index < len(nodes):
                return False
            targets.add(index)
    active = set()
    pending = list(targets)
    while pending:
        index = pending.pop()
        if index in active:
            continue
        active.add(index)
        pending.extend(nodes[index].get('children', []))
    affected = {p.id for p in original.parts if p.node in active}
    return bool(affected) and affected <= removed


def _inputs(scene, project):
    """Verify raw provenance, exact coordinate preparation and fresh inspection.

    The first prototype accepts original, unpartitioned rigid parts. A prepared
    Scene cannot substitute arbitrary modified arrays while retaining an old
    GLB fingerprint, or silently apply a second unit conversion.
    """
    if not isinstance(scene, Scene) or not isinstance(project, Mapping):
        _fail('Supply the prepared source scene and its reviewed project.')
    if (not _is_digest(scene.digest) or not isinstance(scene.raw, bytes)
            or sha256(scene.raw) != scene.digest
            or project.get('source_sha256') != scene.digest):
        _fail('The source fingerprint does not match its original GLB bytes.', 'source_changed')
    coordinates = coordinate_settings(project.get('source_up_axis', '+Y'),
                                      project.get('metres_per_unit', 1.))
    if project.get('wheel_partitions', []) != []:
        _fail('This developer route requires an unpartitioned body-only source; '
              'export reviewed rigid body parts as a separate model.', 'shell_source')
    source = GLB(scene.raw)
    original = source.scene()
    if not _wheel_animations_only(original, project):
        _fail('Bake the intended rigid pose before using this body-only route; source animation is not transferred.',
              'shell_source')
    for mesh in original.document.get('meshes', []):
        for primitive in mesh.get('primitives', []):
            if primitive.get('targets'):
                _fail('Bake morph-target geometry before using this body-only route.', 'shell_source')
            attributes = primitive.get('attributes', {})
            if any(key.startswith(('JOINTS_', 'WEIGHTS_')) for key in attributes):
                _fail('Source vertex colors or joint attributes need a supported conversion route.',
                      'shell_source')
            for semantic, accessor in attributes.items():
                if semantic.startswith('COLOR_'):
                    colour = source.accessor(accessor)
                    position_count = source.doc['accessors'][attributes['POSITION']]['count']
                    if (colour.shape not in ((position_count, 3), (position_count, 4))
                            or not np.all(colour == 1)):
                        _fail('Bake the source vertex colours into a base-colour texture before exporting this car.', 'shell_source')
    expected = prepare_scene(original, **coordinates)
    if (_json(scene.materials) != _json(expected.materials)
            or _json(scene.document) != _json(expected.document)):
        _fail('Prepared materials or metadata differ from the original source.', 'source_changed')
    supplied = [_part(p) for p in scene.parts]
    if (not supplied or len({p.id for p in supplied}) != len(supplied)
            or [p.id for p in supplied] != [p.id for p in expected.parts]):
        _fail('Prepared source part identities differ from the original model.', 'source_changed')
    hashes = []
    for actual, wanted in zip(supplied, expected.parts):
        digest = _geometry_hash(actual)
        if digest != _geometry_hash(wanted) or actual.extras != wanted.extras:
            _fail('Prepared geometry does not reproduce the declared source axes and units: '
                  +actual.id+'.', 'source_changed')
        hashes.append({'id': actual.id, 'geometry_sha256': digest})
    forward = project.get('forward')
    axes(forward)
    overrides = project.get('overrides', {})
    if (not isinstance(overrides, Mapping) or set(overrides)-{p.id for p in supplied}):
        _fail('Source assignments reference an unknown component.', 'shell_source')
    inspection = infer(scene, forward, overrides)
    if _json(project.get('inspection')) != _json(inspection):
        _fail('The component inspection is stale or does not match the current source.',
              'shell_inspection')
    require_static_compatible(inspection)
    if project.get('approved_roles') is not True:
        _fail('Review every component as rigid body geometry before approving this route.',
              'shell_source_review')
    if project.get('template_inheritance_approved') is not True:
        _fail('Review the inherited wheels, chassis, interior, shadows and driving behavior.',
              'shell_inheritance')
    choice = installation(project.get('installation'))
    actions = selection_actions(choice)
    if actions:
        _fail(' '.join(actions), 'installation_choice')
    return inspection, {'source_sha256': scene.digest,
        'prepared_geometry_sha256': _digest(hashes), 'prepared_parts': hashes,
        'materials_sha256': _digest(scene.materials),
        'document_sha256': _digest(scene.document), 'coordinates': coordinates,
        'forward': forward, 'inspection': copy.deepcopy(inspection),
        'overrides': copy.deepcopy(dict(overrides)), 'wheel_partitions': [],
        'wheelbase_metres': project.get('wheelbase_metres'), 'installation': choice}


def _coverage(scene, inspection, dispositions):
    dispositions = _dispositions(dispositions)
    parts = {p.id: p for p in scene.parts}
    if not isinstance(dispositions, Mapping) or set(dispositions)-set(parts):
        _fail('A source disposition references an unknown component.', 'shell_coverage')
    decisions = {}
    for pid, value in dispositions.items():
        if (not isinstance(value, Mapping) or set(value) != {'disposition', 'reason'}
                or value.get('disposition') != 'excluded'
                or not isinstance(value.get('reason'), str) or not value['reason'].strip()
                or len(value['reason']) > 2000):
            _fail('An ignored component needs an explicit exclusion and reason: '+pid+'.',
                  'shell_coverage')
        decisions[pid] = {'disposition': 'excluded', 'reason': value['reason'].strip()}
    result = []
    for row in inspection['parts']:
        part = parts[row['id']]
        role = row['role']
        label = re.sub(r'[^a-z0-9]+', ' ', (part.name+' '+scene.materials[part.material]['name']).lower())
        tagged = part.extras.get('gt6_role')
        named_role = component_name_role(part.name,scene.materials[part.material]['name'])
        # Names/tags are evidence requiring review, never permission to bake a
        # moving part into the shell by overriding it to Body or Ignore.
        mechanical_evidence = (role in CORNER_ROLES or tagged in CORNER_ROLES
                or named_role in CORNER_ROLES or _mechanical_names(label)
                or row.get('role_review_required') or row.get('mechanical_role_proposals'))
        if mechanical_evidence and not (role == 'ignore' and part.id in decisions):
            _fail('This body-only route does not accept source wheels or mechanical parts: '
                  +part.id+'. Export a reviewed wheel-free shell.', 'shell_mechanical')
        if role == 'ignore':
            if part.id not in decisions:
                _fail('Record why this source component is excluded: '+part.id+'.', 'shell_coverage')
            status, route, reason = 'excluded', None, decisions[part.id]['reason']
        elif role in STATIC_ROLES:
            if part.id in decisions:
                _fail('Assign Ignore before explicitly excluding '+part.id+'.', 'shell_coverage')
            status, route, reason = 'emit', 'body_static', None
        else:
            _fail('The source role has no static route: '+part.id+'.', 'shell_coverage')
        result.append({'id': part.id, 'role': role, 'material': part.material,
            **{key:True for key in ('use_native_optics','use_native_finish') if row.get(key)},
            'source_vertices': len(part.positions), 'source_triangles': len(part.indices),
            'source_faces': {'kind': 'all_original_part_faces', 'first': 0,
                             'count': len(part.indices)},
            'prepared_part_sha256': _geometry_hash(part),
            'disposition': status, 'route': route, 'reason': reason,
            'mechanical_evidence_excluded': bool(mechanical_evidence and status == 'excluded')})
    totals = {key: sum(row['source_triangles'] for row in result if row['disposition'] == key)
              for key in ('emit', 'excluded')}
    if not totals['emit'] or sum(totals.values()) != scene.triangles:
        _fail('Source triangle accounting is incomplete.', 'shell_coverage')
    return result, totals, decisions


def _materials(scene, coverage):
    """Check source-only native limitations before any candidate folder exists."""
    from .car_materials import Materials
    from .surface_realization import native_finish_substitutions
    converter = Materials(scene, None)
    checked = set()
    for row in coverage:
        if row['disposition'] != 'emit':
            continue
        material = scene.materials[row['material']]
        originals = scene.document.get('materials', [])
        source = originals[row['material']] if row['material'] < len(originals) else {}
        replaced = set(native_finish_substitutions(source)) if row.get('use_native_finish') else set()
        pbr = source.get('pbrMetallicRoughness', {})
        unsupported = [name for name in ('normalTexture', 'occlusionTexture', 'emissiveTexture')
                       if name in source and name not in replaced]
        if 'metallicRoughnessTexture' in pbr and 'metallicRoughnessTexture' not in replaced:
            unsupported.append('metallicRoughnessTexture')
        extensions = source.get('extensions', {})
        unsupported_extensions = set(extensions)-{'KHR_materials_transmission'}-replaced
        if unsupported or unsupported_extensions:
            features = sorted(set(unsupported) | unsupported_extensions)
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'uses effects this GT6 material cannot carry ('+', '.join(features)+').',
                                 finish_steps(features))
        if 'KHR_materials_transmission' in extensions:
            transmission = extensions['KHR_materials_transmission']
            if (not isinstance(transmission, Mapping)
                    or set(transmission)-{'transmissionFactor'} or row['role'] != 'glass'):
                raise material_error(scene, row['material'], row['id'], 'shell_material',
                                     'uses a transmission setting that GT6 glass cannot reproduce.',
                                     ['For window glass, use one Transmission Weight value in Blender and '
                                      'disconnect any texture from that input. For solid panels, set Weight to 0.',
                                      REIMPORT, OPTICS])
        if (row['role'] not in ('headlight', 'taillight', 'brakelight')
                and 'emissiveFactor' not in replaced
                and any(float(v) != 0. for v in source.get('emissiveFactor', [0., 0., 0.]))):
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'has a glow assigned to a surface outside the lamp controls.',
                                 [OPTICS, 'For solid bodywork, set Emission Strength to 0 on a copy of this material in Blender.', REIMPORT])
        if material.get('alpha_mode') == 'MASK':
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'uses an alpha cutout that the GT6 material cannot draw.', alpha_steps(material, row['role']))
        if material.get('normal_map') and 'normalTexture' not in replaced:
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'uses a normal map that the GT6 material cannot carry.', finish_steps(['normalTexture']))
        if row['role'] == 'paint' and material.get('base_texture') is not None:
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'has a colour texture on panels assigned to factory Paint.', [BODY_DETAIL])
        if row['role'] == 'paint' and material.get('alpha_mode') == 'BLEND':
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'uses transparency on panels assigned to factory Paint.', alpha_steps(material, 'paint'), check_alpha=True)
        # Lamp base surfaces are grouped as Body by the shared serializer. Its
        # opaque palette branch precedes transparency handling, so a reviewed
        # transparent source cannot silently take that branch in this route.
        if (row['role'] in ('body', 'interior', 'headlight', 'taillight', 'brakelight')
                and material.get('alpha_mode') == 'BLEND'
                and material.get('base_texture') is None and material['metallic'] < .4):
            raise material_error(scene, row['material'], row['id'], 'shell_material',
                                 'uses transparency on a surface that would turn solid in GT6.',
                                 alpha_steps(material, row['role']))
        if material.get('base_texture') is not None and row['material'] not in checked:
            converter.source_image(material)
            checked.add(row['material'])


def _assembled(scene, project, coverage, matrix, translation, lighting):
    """Create a transient compilation scene; original source accounting is separate."""
    generated, receipt = lamp_kit(scene, coverage, matrix, translation, lighting)
    receipt['generated_part_ids'] = [part['id'] for part in generated]
    if not generated:
        return scene, project, receipt
    if any(part.id.startswith(PART_PREFIX) for part in scene.parts):
        _fail('Source component identity collides with the supplied-light namespace.', 'shell_coverage')
    assembled = copy.deepcopy(scene)
    material_ids = {}
    document_materials = assembled.document.setdefault('materials', [])
    # The GLB loader adds an implicit Default material after authored records.
    # Match that normalized index without changing original input metadata.
    while len(document_materials) < len(assembled.materials):
        mat = assembled.materials[len(document_materials)]
        document_materials.append({'name': mat['name'], 'pbrMetallicRoughness': {
            'baseColorFactor': list(mat['color']), 'metallicFactor': mat['metallic'],
            'roughnessFactor': mat['roughness']}})
    inverse = np.linalg.inv(matrix)
    for part in generated:
        color = tuple(part['color'])
        if color not in material_ids:
            mid = len(assembled.materials)
            material_ids[color] = mid
            material = {'id': mid, 'name': 'Supplied lamp '+part['role'],
                'color': list(color), 'metallic': 0., 'roughness': .45,
                'alpha_mode': 'OPAQUE', 'alpha_cutoff': .5, 'double_sided': False,
                'transmission': 0., 'emissive': [0., 0., 0.],
                'base_texture': None, 'normal_map': False}
            assembled.materials.append(material)
            document_materials.append({'name': material['name'], 'pbrMetallicRoughness': {
                'baseColorFactor': list(color), 'metallicFactor': 0., 'roughnessFactor': .45}})
        positions = (part['positions']-translation) @ inverse.T
        normals = part['normals'] @ matrix
        normals /= np.linalg.norm(normals, axis=1)[:, None]
        assembled.parts.append(Part(part['id'], part['name'], -1, -1,
            material_ids[color], positions, normals, part['uv'].copy(), part['faces'].copy(),
            extras={'gt6_role': part['role'], 'generated_by': 'gt6_supplied_lamp_kit',
                    'generated_kit_revision': receipt['kit_revision']}, warnings=[]))
    derived = copy.deepcopy(project)
    overrides = {row['id']: {'role': row['role'], 'corner': row.get('corner'),
                             **{key:True for key in ('use_native_optics','use_native_finish') if row.get(key)}}
                 for row in project['inspection']['parts']}
    overrides.update({part['id']: {'role': part['role'], 'corner': None}
                      for part in generated})
    derived['inspection'] = infer(assembled, project['forward'], overrides)
    derived['assembly_source_part_ids'] = [part.id for part in scene.parts]
    derived['generated_part_ids'] = receipt['generated_part_ids']
    receipt['geometry_sha256'] = _digest([
        {'id': part['id'], 'role': part['role'],
         'positions': part['positions'].tolist(), 'normals': part['normals'].tolist(),
         'faces': part['faces'].tolist()} for part in generated])
    return assembled, derived, receipt


def assemble_shell(scene, project, review):
    """Shared preview/compiler assembly from a fresh source-bound review.

    A browser-supplied matrix or generated-part list cannot enter this helper.
    Geometry is rederived from original input and normalized current settings.
    """
    if not isinstance(review, Mapping) or not _is_digest(review.get('review_digest')):
        _fail('Request a current shell review before assembling the preview.', 'shell_stale')
    fresh = review_shell(scene, project, review.get('placement'),
                         review.get('dispositions'), review.get('lighting'),
                         review.get('running_gear'))
    if (not fresh['available'] or not _is_digest(fresh['review_digest'])
            or not hmac.compare_digest(fresh['review_digest'], review['review_digest'])):
        _fail('Source or assembly settings changed before preview generation.', 'shell_stale')
    return _assembled(scene, project, fresh['coverage'], np.asarray(fresh['matrix']),
                      np.asarray(fresh['translation_metres']), fresh['lighting'])


def review_shell(scene, project, placement, dispositions=None, lighting=None, running_gear=None):
    """Review without writing files; invalid inputs return an explicit blocker."""
    report = {'schema': SCHEMA, 'algorithm_revision': ALGORITHM_REVISION,
        'scope': 'template_shell_developer_review', 'available': False,
        'ready_for_candidate': False, 'within_profile_limits': False,
        'blockers': [], 'review_digest': None, 'placement': None,
        'matrix': None, 'translation_metres': None, 'scale_xyz': None,
        'reference': None, 'source': None, 'coverage': [], 'lighting': None,
        'running_gear': None, 'running_gear_plan': None,
        'triangle_dispositions': {'emit': 0, 'excluded': 0}, 'approved': False,
        'source_anchors': {'status': 'unmeasured', 'wheel_centers': {},
                           'maximum_wheel_centre_error_metres': None},
        'maximum_wheel_centre_error_metres': None,
        'physics_changed': False, 'source_geometry_changed': False,
        'runtime_verified': False, 'production_enabled': False,
        'source_review_scope': 'Component approval is a manual review of rigid source surfaces. '
            'Unknown fused geometry is not algorithmically certified wheel-free.',
        'limitations': ['Explicit body placement is not measured axle alignment or physical clearance.',
            'Choose source lamps or the supplied light kit; supplied mounts require actual body surfaces.',
            'Glass uses the reviewed native optical-family approximation; exact glTF transmission is not reproduced.',
            'Template running gear, interior, driver, shadows and driving configuration are inherited.',
            'Native appearance, collision, steering, suspension and repeated loading require game inspection.']}
    try:
        spec = _placement(placement)
        light_settings = normalize_lighting(lighting)
        gear = resolve_running_gear(running_gear)
        report.update(placement=spec, lighting=light_settings,
                      running_gear=gear['requested'], running_gear_plan=gear)
        if not gear['ready']:
            report['blockers'].extend(copy.deepcopy(gear['blockers']))
            _json(report)
            return report
        inspection, inputs = _inputs(scene, project)
        coverage, totals, decisions = _coverage(scene, inspection, dispositions)
        surface_plan = opaque_two_sided_plan(scene, inspection)
        _materials(scene, coverage)
        reference = _reference(gear)
        left, up, front = axes(inputs['forward'])
        with np.errstate(over='ignore', invalid='ignore'):
            matrix = np.asarray([-left, up, -front], dtype=float)*spec['scale']
            if spec['flip_front']:
                matrix = np.diag([-1., 1., -1.]) @ matrix
        translation = np.asarray(spec['translation_metres'], dtype=float)
        if not np.isfinite(matrix).all() or not 0 < np.linalg.det(matrix) < float('inf'):
            _fail('The explicit body transform exceeds the finite proper-transform range.', 'shell_placement')
        selected = {row['id']: row for row in coverage if row['disposition'] == 'emit'}
        mapped = {}
        for part in scene.parts:
            if part.id not in selected:
                continue
            with np.errstate(over='ignore', invalid='ignore'):
                points = part.positions.astype(float)@matrix.T+translation
            if not np.isfinite(points).all() or np.max(np.abs(points)) > MAXIMUM_POSITION_METRES:
                _fail('Placed body geometry exceeds the current 12-metre coordinate bound.', 'shell_placement')
            mapped[part.id] = points
        if light_settings['mode'] == 'source':
            headlights = [mapped[row['id']] for row in coverage
                          if row['disposition'] == 'emit' and row['role'] == 'headlight']
            if not headlights:
                _fail('Assign source headlamp surfaces on both sides of the placed body or choose supplied lights.', 'shell_lamps')
            head = np.vstack(headlights)
            if not (np.any(head[:, 0] < 0) and np.any(head[:, 0] > 0)):
                _fail('Placed source headlamps must contain both left and right emitting surfaces.', 'shell_lamps')
            if not any(row['disposition'] == 'emit' and row['role'] == 'taillight' for row in coverage):
                _fail('Assign source running/brake tail-lamp surfaces or choose supplied lights.', 'shell_lamps')
        elif any(row['disposition'] == 'emit' and row['role'] in
                 ('headlight', 'taillight', 'brakelight') for row in coverage):
            _fail('Exclude existing source lamp surfaces with a reason before choosing supplied lights, '
                  'or use the model lights to avoid duplicate lamps.', 'shell_lamps')
        assembled, assembly_project, generated = _assembled(
            scene, project, coverage, matrix, translation, light_settings)
        fit = {'fit_method': 'explicit_uniform_static_body_placement',
            'fit_scope': 'static_body_only_unmeasured_anchors',
            'uniform_scale': spec['scale'], 'lateral_fit_ratio': 1.,
            'maximum_wheel_centre_error_metres': None,
            'source_anchor_measurements_available': False,
            'reference_digest': reference['reference_digest'],
            'dimensional_review_required': True,
            'policy': 'Declared axes, explicit uniform body scale and metre translation; '
                      'no inferred wheelbase, measured source axles or wheel-local offsets.'}
        report.update(available=True, ready_for_candidate=True, within_profile_limits=True,
            placement=spec, lighting=light_settings, generated_lighting=generated,
            matrix=matrix.tolist(), translation_metres=translation.tolist(),
            scale_xyz=[spec['scale']]*3, reference=reference,
            source={k: inputs[k] for k in ('source_sha256', 'prepared_geometry_sha256',
                                          'materials_sha256', 'coordinates', 'forward')},
            coverage=coverage, triangle_dispositions=totals, dispositions=decisions, fit=fit,
            opaque_two_sided_realization=surface_plan,
            source_parts=len(scene.parts), source_triangles=scene.triangles,
            all_source_parts_accounted=True, source_wheels_required=False,
            wheel_conversion={'policy': gear['native_policy'],
                              'wheel_output_sha256': gear['wheel_output_sha256'],
                              'source_rims_resized': False, 'source_tyres_measured': False})
        # Exercise the real source geometry/budget path before approval, with
        # no native template access or filesystem output. Missing source tyres
        # remain an unavailable advisory report, never fabricated measurements.
        from .car_geometry import prepare_parts
        from .shell_setup import current_wheel_fit
        source_fit = current_wheel_fit(scene, project, {'placement': spec, 'running_gear': gear['requested']})
        check = dimensional_check(source_fit, gear)
        report['source_wheel_fit'] = source_fit
        report['wheel_check'] = check_exclusions(check, source_fit, coverage)
        budgets = {}
        mount_checks = {}
        for lod in ('hq', 'race'):
            parts, _, _ = prepare_parts(assembled, assembly_project['inspection'], lod,
                fit_solution=(matrix, translation, report))
            budgets[lod] = {'triangles': sum(len(p['faces']) for p in parts),
                            'vertices': sum(len(p['positions']) for p in parts)}
            mount_checks[lod] = verify_prepared_mounts(parts, generated)
        if needs_native_check(report) and check['messages']:
            # The body transform can still be rendered for a diagnostic preview.
            report['ready_for_candidate'] = False
            report['blockers'].append({'code': 'wheel_fit', 'message':
                'Resolve the wheel fit before exporting. '+ ' '.join(check['messages'])})
        report['prepared_budgets'] = budgets
        report['lamp_mount_checks'] = mount_checks
        report['review_digest'] = _digest({'schema': SCHEMA,
            'algorithm_revision': ALGORITHM_REVISION, 'inputs': inputs,
            'reference': reference, 'placement': spec, 'coverage': coverage,
            'dispositions': decisions, 'matrix': report['matrix'],
            'translation_metres': report['translation_metres'],
            'lighting': light_settings, 'generated_lighting': generated,
            'running_gear_plan': gear,
            'wheel_check': check,
            'opaque_two_sided_realization': surface_plan})
    except GarageError as exc:
        report.update(available=False, ready_for_candidate=False, within_profile_limits=False,
                      review_digest=None, approved=False)
        blocker = {'code': exc.code, 'message': str(exc)}
        if isinstance(exc.detail, dict) and 'material_help' in exc.detail:
            blocker['material_help'] = exc.detail['material_help']
        report['blockers'].append(blocker)
    _json(report)
    return report


def _require_review(report):
    if not report.get('ready_for_candidate') or not _is_digest(report.get('review_digest')):
        _fail(' '.join(row['message'] for row in report.get('blockers', []))
              or 'Review the complete current source and explicit body placement.', 'shell_review')


def approve_shell(scene, project, placement, dispositions, review_digest, confirmed, lighting=None,
                  running_gear=None):
    if confirmed is not True:
        _fail('Explicitly confirm the source accounting, body placement and inherited vehicle behavior.',
              'shell_confirmation')
    if not _is_digest(review_digest):
        _fail('Request a current template-shell review before confirming.', 'shell_stale')
    report = review_shell(scene, project, placement, dispositions, lighting, running_gear)
    _require_review(report)
    if not hmac.compare_digest(report['review_digest'], review_digest):
        _fail('The source, placement, template contract or destination changed after review.', 'shell_stale')
    return {'schema': SCHEMA, 'placement': copy.deepcopy(report['placement']),
            'lighting': copy.deepcopy(report['lighting']),
            'running_gear': copy.deepcopy(report['running_gear']),
            'dispositions': copy.deepcopy(report['dispositions']),
            'review_digest': report['review_digest']}


def resolve_shell(scene, project, decision):
    if (not isinstance(decision, Mapping)
            or set(decision) != {'schema', 'placement', 'dispositions', 'review_digest',
                                 'lighting', 'running_gear'}
            or type(decision.get('schema')) is not int or decision['schema'] != SCHEMA
            or not _is_digest(decision.get('review_digest'))):
        _fail('The approved template-shell decision is missing or invalid.', 'shell_stale')
    report = review_shell(scene, project, decision['placement'], decision['dispositions'],
                         decision['lighting'], decision['running_gear'])
    _require_review(report)
    if not hmac.compare_digest(report['review_digest'], decision['review_digest']):
        _fail('The source, placement, template contract or destination changed after approval.', 'shell_stale')
    report['approved'] = True
    return np.asarray(report['matrix']), np.asarray(report['translation_metres']), report


def validate_native_shell_surfaces(template, review):
    """Authenticate opaque culling before using generated reverse-facing surfaces."""
    plan = review['opaque_two_sided_realization']
    if not plan['part_ids']:
        return {}
    root = Path(template['root'])
    return {lod: verify_native_opaque_culling((root/lod/'body').read_bytes(), lod)
            for lod in ('hq', 'race')}


def plan_shell(scene, project, template_root, decision):
    _, _, review = resolve_shell(scene, project, decision)
    gear = require_inherited_running_gear(review['running_gear_plan'])
    template = validate_template(Path(template_root))
    native_surfaces = validate_native_shell_surfaces(template, review)
    verified_wheels = None
    if needs_native_check(review):
        from .template_shell_preview import decode_template_wheels
        verified_wheels = checked_wheel_files(template['root'], gear)
        wheels, _ = decode_template_wheels((Path(template['root'])/'hq/wheel').read_bytes(), gear)
        matrix = np.asarray(review['matrix'])
        translation = np.asarray(review['translation_metres'])
        assembled, assembly_project, _ = _assembled(scene, project, review['coverage'], matrix,
                                                    translation, review['lighting'])
        review['wheel_check'] = inspect_body_visibility(assembled, assembly_project, review, wheels, verified_wheels)
        require_wheel_check(review['wheel_check'])
    return {'schema': SCHEMA, 'kind': 'template-shell-developer-plan',
            'ready': True, 'profile_id': PROFILE['id'], 'template': template,
            'review': review, 'running_gear_plan': gear,
            'native_surface_validation': native_surfaces,
            'verified_wheel_files': verified_wheels,
            'production_enabled': False, 'runtime_verified': False,
            'identity_and_package_checked': False}


def check_shell_body_streams(scene,project,review,template_root,cancel=None):
    """Run the same native body writers as a build, without committing a project or ZIP."""
    from .car_builder import _prepare_body_streams
    template=Path(validate_template(Path(template_root))['root'])
    matrix=np.asarray(review['matrix']);translation=np.asarray(review['translation_metres'])
    assembled,prepared,generated=_assembled(scene,project,review['coverage'],matrix,translation,review['lighting'])
    result={}
    for lod in ('hq','race'):
        _,_,report,parts=_prepare_body_streams(assembled,prepared,template,lod,cancel,(matrix,translation,review))
        verify_prepared_mounts(parts,generated)
        stream=report['body_stream']
        result[lod]={'triangles':report['static_triangles'],'used_stream_bytes':stream['used_cpu_bytes'],
                     'available_stream_bytes':stream['main_cpu_bytes_before'],'body_bytes':report['texture_loading']['body_bytes']}
    return result


def build_shell(scene, project, template_root, output, decision, cancel=None, progress=None):
    """Build eight native assets; never install, launch or approve a game car."""
    from .car_builder import _build_body_with_fit, fit_projectors
    from .car_geometry import check_cancel
    start = time.time()
    # The public developer API has no Store lock. Freeze caller-owned mutable
    # inputs and recompute all provenance/approval checks on this one snapshot.
    scene, project, decision = copy.deepcopy((scene, project, decision))
    plan = plan_shell(scene, project, template_root, decision)
    review = plan['review']
    gear = require_inherited_running_gear(plan['running_gear_plan'])
    solution = (np.asarray(review['matrix']), np.asarray(review['translation_metres']), review)
    assembled, assembly_project, generated = _assembled(
        scene, project, review['coverage'], solution[0], solution[1], review['lighting'])
    template, output = Path(plan['template']['root']), Path(output)
    if output.exists() or output.is_symlink() or template.resolve() in output.resolve().parents:
        _fail('Use a fresh candidate folder outside the owned native template.', 'shell_output')
    check_cancel(cancel)
    notify = progress or (lambda message: None)
    output.mkdir(parents=True)
    try:
        root = output/PROFILE['model_path']
        root.mkdir(parents=True)
        body = {}
        hqparts = None
        for lod in ('hq', 'race'):
            check_cancel(cancel)
            notify('Building explicit '+lod+' static body and reviewed lights')
            folder = root/lod
            folder.mkdir()
            body[lod], parts = _build_body_with_fit(assembled, assembly_project, template, lod,
                folder/'body', cancel, fit_solution=solution)
            if lod == 'hq':
                hqparts = parts
            wheel = template/lod/'wheel'
            if lod == 'race':
                (folder/'wheel').write_bytes(restore_stock_race_wheel(wheel.read_bytes()))
            elif gear['requested'].get('wheel_set') == 'supra_factory':
                (folder/'wheel').write_bytes(restore_stock_showroom_wheel(wheel.read_bytes()))
            else:
                shutil.copyfile(wheel, folder/'wheel')
            if hash_file(folder/'wheel') != gear['wheel_output_sha256'][lod+'/wheel']:
                _fail('The emitted wheel differs from the reviewed native asset.', 'shell_native')
        info, projectors = fit_projectors((template/'info').read_bytes(), hqparts)
        driver_report=None
        if review['placement'].get('driver') is not None:
            from .driver_fit import fit_driver
            info,driver_report=fit_driver(info,review['placement']['driver'])
        (root/'info').write_bytes(info)
        shutil.copyfile(template/'interior', root/'interior')
        if hash_file(root/'interior') != ASSETS['interior']:
            _fail('Inherited interior bytes differ from the authenticated template.', 'shell_native')
        check_cancel(cancel)
        validate_template(template)
        hashes = {p.relative_to(output).as_posix(): hash_file(p)
                  for p in root.rglob('*') if p.is_file()}
        expected = {PROFILE['model_path']+'/'+rel for rel in ASSETS}
        if set(hashes) != expected:
            _fail('The template-shell native asset set is incomplete.', 'shell_native')
        emitted = {row['id'] for row in review['coverage'] if row['disposition'] == 'emit'}
        generated_ids = set(generated['generated_part_ids'])
        for lod, detail in body.items():
            covered = set(detail['source_components_covered'])
            if covered != emitted | generated_ids:
                _fail('Serialized '+lod+' body differs from the reviewed source inventory.', 'shell_coverage')
            detail['assembly_components_covered'] = sorted(covered)
            detail['source_components_covered'] = sorted(covered & emitted)
            detail['generated_components_covered'] = sorted(covered & generated_ids)
            detail['generated_component_ownership'] = 'original_supplied_lamp_kit'
        def wheel_report(lod):
            resource = lod+'/wheel'
            policy = 'stock_packed_race_wheel' if lod == 'race' else (
                'stock_packed_showroom_wheel' if gear['requested'].get('wheel_set') == 'supra_factory' else 'inherited_unchanged')
            return {'policy': policy,
                    'source_sha256': ASSETS[resource],
                    'sha256': hashes[PROFILE['model_path']+'/'+resource],
                    'source_rims_resized': False, 'source_tyres_measured': False,
                    'runtime_verified': False}
        receipt = {'schema': SCHEMA, 'kind': 'template-shell-native-candidate',
            'passed': True, 'profile_id': PROFILE['id'], 'model_path': PROFILE['model_path'],
            'car_id': PROFILE['car_id'], 'name': project.get('name', 'Custom body'),
            'source_sha256': scene.digest, 'source_license': project.get('license', {}),
            'source_parts': len(scene.parts), 'source_triangles': scene.triangles,
            'source_orientation': review['source'], 'body': body,
            'race_wheels': wheel_report('race'), 'showroom_wheels': wheel_report('hq'),
            'wheel_check': review['wheel_check'], 'verified_wheel_files': plan['verified_wheel_files'],
            'projectors': projectors, 'driver_fit':driver_report, 'source_virtual_assets': hashes,
            'geometry_review': review, 'coverage': review['coverage'],
            'running_gear_plan': gear,
            'native_surface_validation': plan['native_surface_validation'],
            'lighting': review['lighting'], 'generated_lighting': generated,
            'lamp_mount_checks': review['lamp_mount_checks'],
            'triangle_dispositions': review['triangle_dispositions'],
            'all_source_parts_accounted': True,
            'inherited': [*PROFILE['inherited'], 'Stock packed on-track wheels and native showroom wheels'],
            'limitations': [*PROFILE['limits'], *review['limitations'],
                           'Native feasibility result; game appearance and mechanical behavior require acceptance.'],
            'production_enabled': False, 'installable': False,
            'runtime_verified': False, 'runtime_tested': False, 'physical_ps3_tested': False,
            'game_installation_changed': False, 'additional_database_entry': False,
            'physics_changed': False, 'source_geometry_changed': False,
            'seconds': time.time()-start}
        atomic_json(output/'build_receipt.json', receipt)
        notify('Developer native candidate complete; runtime acceptance is still required')
        return receipt
    except Exception as exc:
        # This fresh folder belongs exclusively to this build. Retain a failure
        # receipt, never an apparently usable half-built native asset tree.
        shutil.rmtree(output)
        output.mkdir(parents=True)
        atomic_json(output/'FAILED.json', {'schema': SCHEMA,
            'kind': 'template-shell-native-failure', 'release_ready': False,
            'installable': False, 'partial_native_assets_removed': True,
            'game_installation_changed': False, 'reason': str(exc)})
        raise
