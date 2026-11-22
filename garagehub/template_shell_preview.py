"""Read-only assembled shell preview with authenticated native wheel meshes.

The wheel mesh is decoded from the user's exact owned HQ wheel asset. Its
placement is the profile's fixed reference pose, not a sampled game suspension
pose. Preview materials approximate colors; native shaders are not executed.
No proprietary mesh data is bundled, generated or substituted by this module.
"""
from __future__ import annotations

import copy
import hmac
import json
import struct
from collections.abc import Mapping
from pathlib import Path

import numpy as np

from .car_geometry import prepare_parts
from .car_profiles import ASSETS, PROFILE, validate_template
from .errors import GarageError
from .glb import Part, Scene
from .native import MDL3
from .preview import build_preview
from .recognition import CORNERS
from .util import sha256
from .running_gear import resolve_running_gear, require_inherited_running_gear


_FIELDS = {'position': (0, 3, 2, 0), 'normal': (12, 1, 6, 0),
           'map12': (16, 2, 2, 0), 'colorSet1': (24, 4, 4, 0)}
_MATERIALS = (
    ('Supplied native tyre — preview color', [.025, .03, .032, 1.], 0., .9),
    ('Supplied native rim — preview color', [.42, .46, .49, 1.], .8, .3),
    ('Supplied native valve — preview color', [.13, .14, .15, 1.], .25, .55),
)


def _need(value, message):
    if not value:
        raise GarageError(message, 'template_shell_preview')


def _normals(packed):
    components = []
    for shift, bits, step in ((0, 11, 32), (11, 11, 32), (22, 10, 64)):
        values = ((packed >> shift) & ((1 << bits) - 1)).astype(np.int32)
        values = np.where(values >= (1 << (bits - 1)), values - (1 << bits), values)
        components.append(values.astype(float) * (step / 32767.))
    normals = np.column_stack(components)
    lengths = np.linalg.norm(normals, axis=1)
    _need(np.all(lengths > 0), 'The supplied wheel contains an empty normal.')
    return normals / lengths[:,None]


def _factory_rim(model, shape):
    """Decode the pinned Supra HQ rim's two stationary packed draw batches."""
    from .native_wheel_contract import _packed
    at = model.u32(0x38) + 48*shape
    mesh = _packed(model, at, shape, [])
    streams = {row['kind']:model.span(row['offset'],row['bytes'],model.cut) for row in mesh['payloads']}
    q = np.frombuffer(streams['packed_positions'],dtype='>u2').reshape(-1,3)
    points = (q.astype(float)/32768+2)*mesh['position_scale_xyz']+mesh['position_offset_xyz']
    count = len(points)
    combined = streams['raw_normals_colour_uv']
    packed = np.ndarray((count,),dtype='>u4',buffer=combined,strides=(12,)).astype(np.uint32)
    uv = np.ndarray((count,2),dtype='>f2',buffer=combined,offset=8,strides=(12,2)).astype(float)
    # All four original rims share two draw commands of 128 and 47
    # four-triangle groups, with absolute 11-bit vertex indices.
    nindices = (128+47)*4*3
    indices = streams['packed_indices']
    bits = np.unpackbits(np.frombuffer(indices,dtype=np.uint8))[:nindices*11].reshape(-1,11)
    faces = (bits @ (1 << np.arange(10,-1,-1))).astype(np.uint32).reshape(-1,3)
    _need(count == 701 and int(faces.max()) < count and np.isfinite(points).all() and np.isfinite(uv).all(),
          'The factory showroom rim differs from its reviewed packed layout.')
    return {'shape':shape,'native_material':model.s16(at+4),'positions':points,'normals':_normals(packed),
            'faces':faces,'uv':uv,'vertex_sha256':sha256(streams['packed_positions']+combined),
            'index_sha256':sha256(indices)}


def _direct_shape(model, shape):
    """Decode one complete ordinary triangle-list shape, without guessing."""
    _need(type(shape) is int and 0 <= shape < model.u16(0x14),
          'A supplied-wheel draw references an absent native shape.')
    at = model.u32(0x38) + 48 * shape
    model.span(at, 48, model.cut)
    fid = model.s16(at + 2)
    layouts = model.fvfs()
    _need(model.u16(at) == 0x1809 and 0 <= fid < len(layouts),
          'The supplied-wheel preview does not support this native shape encoding.')
    _need(not any(model.span(at + 6, 2) + model.span(at + 16, 4)
                  + model.span(at + 28, 10)) and model.u32(at + 44) == 0,
          'The supplied-wheel shape has an unreviewed extension or stream reference.')
    layout = layouts[fid]
    fields = {field['name']: (field['offset'], field['components'],
                              field['format'], field['stream'])
              for field in layout['fields']}
    _need(layout['stride'] == 28 and fields == _FIELDS
          and len(layout['fields']) == len(_FIELDS),
          'The supplied-wheel preview requires the reviewed position/normal/UV/color layout.')
    count, vp = model.u32(at + 8), model.u32(at + 12)
    index_count, ip = model.u32(at + 20), model.u32(at + 24)
    _need(vp > 0 and ip > 0 and 0 < count <= 65535 and 0 < index_count <= 60000
          and index_count % 3 == 0 and model.u16(at + 38) == index_count // 3,
          'The supplied-wheel triangle or vertex count is invalid.')
    model.span(vp, count * 28, model.cut)
    model.span(ip, index_count * 2, model.cut)
    points = np.ndarray((count, 3), dtype='>f4', buffer=model.data,
                        offset=vp, strides=(28, 4)).astype(np.float64)
    packed = np.ndarray((count,), dtype='>u4', buffer=model.data,
                        offset=vp + 12, strides=(28,)).astype(np.uint32)
    normals = _normals(packed)
    faces = np.frombuffer(model.span(ip, index_count * 2, model.cut),
                          dtype='>u2').astype(np.uint32).reshape(-1, 3)
    _need(np.isfinite(points).all() and np.isfinite(normals).all()
          and int(faces.max()) < count,
          'The supplied-wheel geometry contains invalid positions, normals or indices.')
    # The viewer does not sample native texture/vertex color data. Keep the
    # decoded UVs available as geometry provenance, while explicitly exposing
    # the approximation in the preview's material scope.
    uv = np.ndarray((count, 2), dtype='>f4', buffer=model.data,
                    offset=vp + 16, strides=(28, 4)).astype(np.float64)
    _need(np.isfinite(uv).all(), 'The supplied-wheel geometry has invalid UVs.')
    return {'shape': shape, 'native_material': model.s16(at + 4),
            'positions': points, 'normals': normals, 'uv': uv, 'faces': faces,
            'vertex_sha256': sha256(model.span(vp, count * 28, model.cut)),
            'index_sha256': sha256(model.span(ip, index_count * 2, model.cut))}


def decode_template_wheels(raw, running_gear_plan=None):
    """Return real tyre/rim/valve geometry in four fixed reference frames.

    Authentication and the complete stationary HQ draw list are mandatory.
    Shadows and high-speed blur are intentionally outside this static preview.
    The returned metadata never certifies loaded wheel mounting or clearance.
    """
    _need(isinstance(raw, bytes) and sha256(raw) == ASSETS['hq/wheel'],
          'The supplied-wheel preview needs the exact reviewed native wheel asset.')
    gear = require_inherited_running_gear(
        resolve_running_gear() if running_gear_plan is None else running_gear_plan)
    model = MDL3(raw)
    model.validate()
    factory = None
    if gear['requested'].get('wheel_set') == 'supra_factory':
        from .stock_race_wheel import restore_stock_showroom_wheel
        factory = MDL3(restore_stock_showroom_wheel(raw))
    _need(model.u16(0x10) == 8 and model.u16(0x14) == 16,
          'The supplied-wheel model/shape inventory differs from the reviewed profile.')
    components, proof = [], []
    for ci, corner in enumerate(CORNERS):
        shape_ids = (ci, 8 + 2 * ci, 9 + 2 * ci)
        expected = b''.join(bytes((59, sid, ci)) for sid in shape_ids) + b'\0'
        at = model.u32(0x30) + ci * 48
        program = model.span(model.u32(at + 24), model.u32(at + 28), model.cut)
        _need(program == expected,
              'The supplied-wheel stationary program differs from the reviewed complete draw list.')
        reference = gear['corners'][corner]
        center = np.asarray(reference['center_metres'], dtype=float)
        width = float(reference['tyre_width_metres'])
        _need(center.shape == (3,) and np.isfinite(center).all()
              and np.isfinite(width) and width > 0,
              'The supplied-wheel reference frame is invalid.')
        # Inverse of the production rim_frame convention: the tyre center is
        # +width/2 on local X; right wheels have a proper 180-degree Y rotation.
        frame = gear['frames'][corner]
        rotation = np.asarray(frame['local_to_reference_rotation'], dtype=float)
        local_center = np.asarray(frame['reference_local_center_metres'], dtype=float)
        shift = np.asarray(frame['local_to_reference_translation_metres'], dtype=float)
        component_shapes = [('tyre',ci),('rim',ci)] if factory else list(zip(('tyre','rim','valve'),shape_ids))
        for material_id, (role, sid) in enumerate(component_shapes):
            decoded = _factory_rim(factory, sid) if factory and role == 'rim' else _direct_shape(model, sid)
            guide = bool(factory and role == 'tyre')
            points = decoded['positions'] @ rotation.T + shift
            normals = decoded['normals'] @ rotation.T
            components.append({**decoded, 'positions': points, 'normals': normals,
                'corner': corner, 'role': role, 'preview_material': material_id, 'tyre_guide':guide,
                'local_to_reference_rotation': rotation.tolist(),
                'local_to_reference_translation_metres': shift.tolist(),
                'reference_center_metres': center.tolist()})
            proof.append({'shape': sid, 'corner': corner, 'role': role, 'tyre_guide':guide,
                'vertices': len(points), 'triangles': len(decoded['faces']),
                'vertex_sha256': decoded['vertex_sha256'],
                'index_sha256': decoded['index_sha256'],
                'native_material': decoded['native_material'],
                'reference_local_center_metres': local_center.tolist(),
                'local_to_reference_rotation': rotation.tolist(),
                'local_to_reference_translation_metres': shift.tolist(),
                'local_bounds_metres': [decoded['positions'].min(0).tolist(),
                                         decoded['positions'].max(0).tolist()],
                'reference_bounds_metres': [points.min(0).tolist(), points.max(0).tolist()]})
    return components, {'native_asset': 'hq/wheel', 'sha256': sha256(factory.data if factory else raw),
        'running_gear_plan_digest': gear['plan_digest'], 'native_no_op': True,
        'complete_stationary_draw_coverage': True, 'draws': proof,
        'native_reference_pose': True, 'current_mount_verified': False,
        'scope': ('Factory showroom rims and profile tyre guides at the four stock hubs. GT6 supplies the tyres in game.'
                  if factory else 'Exact owned HQ tyre/rim/valve geometry in fixed profile reference frames.'),
        'omitted_native_components': ['wheel shadows', 'race spin blur', 'interior',
                                      'driver', 'body-owned mechanical assemblies']}


def _unique_id(base, used):
    name, suffix = base, 0
    while name in used:
        suffix += 1
        name = base + '_' + str(suffix)
    used.add(name)
    return name


def build_template_shell_preview(scene, project, review, template_root, limit=800000):
    """Return GGV1 bytes and matching viewer records from a fresh assembly.

    Preview is allowed before saved approval. It recomputes source review and
    authenticates the owned template; returned geometry grants no build or game
    acceptance. The caller should still serialize access to project mutations.
    """
    from .template_shell import review_shell, assemble_shell, validate_native_shell_surfaces
    _need(type(limit) is int and limit > 0, 'The assembled preview needs a positive triangle target.')
    _need(isinstance(review, Mapping) and isinstance(review.get('review_digest'), str),
          'Request a current assembly review before opening its preview.')
    fresh = review_shell(scene, project, review.get('placement'), review.get('dispositions'),
                         lighting=review.get('lighting'), running_gear=review.get('running_gear'))
    _need(fresh.get('available') is True and isinstance(fresh.get('review_digest'), str)
          and hmac.compare_digest(fresh['review_digest'], review['review_digest']),
          'The source, placement, lighting or destination changed. Refresh the assembled preview.')
    template = validate_template(Path(template_root))
    surface_state = validate_native_shell_surfaces(template, fresh)
    gear = require_inherited_running_gear(fresh['running_gear_plan'])
    from .wheel_check import checked_wheel_files, inspect_body_visibility, needs_native_check
    wheel_files = (checked_wheel_files(template['root'], gear)
                   if needs_native_check(fresh) else None)
    wheels, native = decode_template_wheels((Path(template['root']) / 'hq/wheel').read_bytes(), gear)
    assembled, assembly_project, generated = assemble_shell(scene, project, fresh)
    prepared, preparation, _ = prepare_parts(assembled, assembly_project['inspection'], 'hq',
        fit_solution=(np.asarray(fresh['matrix']), np.asarray(fresh['translation_metres']), fresh))
    check = (inspect_body_visibility(assembled, assembly_project, fresh, wheels, wheel_files, prepared)
             if wheel_files else fresh['wheel_check'])
    source_ids = {part.id for part in scene.parts}
    source_lookup = {part.id: part for part in assembled.parts}
    materials = copy.deepcopy(assembled.materials)
    realizations = {row['id']: row['surface_realization'] for row in preparation
                    if row.get('surface_realization')}
    records, parts = {}, []
    for row in prepared:
        source = source_lookup[row['id']]
        origin = 'source' if row['id'] in source_ids else 'supplied_lamp_kit'
        parts.append(Part(row['id'], source.name, source.node, source.primitive, row['material'],
            np.asarray(row['positions'], np.float32), np.asarray(row['normals'], np.float32),
            np.asarray(row['uv'], np.float32), np.asarray(row['faces'], np.uint32),
            copy.deepcopy(source.extras), source.warnings.copy()))
        records[row['id']] = {'id': row['id'], 'name': source.name, 'role': row['role'],
            'corner': None, 'material': row['material'], 'component_origin': origin,
            'source_part_id': row['id'] if origin == 'source' else None,
            'is_supplied_component': origin != 'source', 'moves_with_body': True,
            'is_reference_guide': False, 'scope': 'shared_native_hq_static_preparation'}
        if row['id'] in realizations:
            records[row['id']]['surface_realization'] = copy.deepcopy(realizations[row['id']])
            # The preview geometry already includes separate front/back faces.
            # This copied material is a native approximation, not the original.
            materials[row['material']]['double_sided'] = False
    _need(bool(parts), 'The assembled preview has no prepared static surfaces.')
    used = {part.id for part in assembled.parts}
    native_material_offset = len(materials)
    for label, color, metallic, roughness in _MATERIALS:
        materials.append({'id': len(materials), 'name': label, 'color': color,
            'metallic': metallic, 'roughness': roughness, 'base_texture': None,
            'double_sided': False})
    for row in wheels:
        identifier = _unique_id('__gt6_supplied_' + row['corner'] + '_' + row['role'], used)
        material = native_material_offset + row['preview_material']
        name = row['corner'] + (' tyre size guide' if row.get('tyre_guide') else ' supplied ' + row['role'])
        parts.append(Part(identifier, name, 0, 0, material,
            np.asarray(row['positions'], np.float32), np.asarray(row['normals'], np.float32),
            np.asarray(row['uv'], np.float32), row['faces'].copy()))
        records[identifier] = {'id': identifier, 'name': name, 'role': row['role'],
            'corner': row['corner'], 'material': material, 'component_origin': 'native_template',
            'source_part_id': None, 'native_asset': 'hq/wheel', 'native_shape': row['shape'],
            'is_supplied_component': True, 'moves_with_body': False,
            'is_reference_guide': bool(row.get('tyre_guide')), 'actual_native_geometry': not row.get('tyre_guide'),
            'native_reference_pose': True, 'current_mount_verified': False,
            'scope': 'authenticated_native_geometry_fixed_reference_pose',
            'reference_center_metres': row['reference_center_metres']}
    combined = Scene(parts, materials, {}, b'', scene.digest, [])
    # Shared native preparation already budgets these surfaces. A second QEM
    # pass on coincident opposite faces could merge/cancel their split normals.
    effective_limit = max(limit, combined.triangles) if realizations else limit
    raw = build_preview(combined, effective_limit)
    size, vertex_count, index_count = struct.unpack_from('<III', raw, 4)
    header = json.loads(raw[16:16 + size])
    vertices = np.frombuffer(raw, '<f4', vertex_count * 6, 16 + size).reshape(-1, 6)
    indices = np.frombuffer(raw, '<u4', index_count, 16 + size + vertex_count * 24)
    metadata = []
    for mesh in header['meshes']:
        used_indices = indices[mesh['index_start']:mesh['index_start'] + mesh['index_count']]
        points = vertices[np.unique(used_indices), :3].astype(float)
        bounds = np.array([points.min(0), points.max(0)])
        record = copy.deepcopy(records[mesh['id']])
        record.update(center=bounds.mean(0).tolist(), bounds=bounds.tolist(),
                      triangles=mesh['preview_triangles'], surface_simplified=mesh['surface_simplified'])
        metadata.append(record)
        mesh.update(component_origin=record['component_origin'],
                    is_supplied_component=record['is_supplied_component'],
                    moves_with_body=record['moves_with_body'])
        if record.get('surface_realization'):
            mesh['surface_realization'] = copy.deepcopy(record['surface_realization'])
    header.update(scope='template_shell_assembled_preview', template_shell_digest=fresh['review_digest'],
        reference_digest=fresh['reference']['reference_digest'],
        running_gear_plan_digest=gear['plan_digest'], running_gear_native_no_op=gear['native_no_op'],
        preview_only=True, build_approved=False, runtime_verified=False,
        source_inputs_changed=False, source_original_triangles=scene.triangles,
        source_static_triangles=sum(row['source_triangles'] for row in fresh['coverage']
                                    if row['disposition'] == 'emit'),
        native_prepared_static_triangles=sum(len(row['faces']) for row in prepared),
        static_preparation=preparation, generated_components=generated,
        native_surface_state_validation=surface_state,
        generated_backface_triangles=sum(row['generated_backface_triangles']
                                         for row in realizations.values()),
        actual_native_wheel_geometry=True, native_wheel_geometry=native, wheel_check=check,
        native_reference_pose=True, current_mount_verified=False,
        native_wheel_preview_triangles=sum(row['triangles'] for row in metadata
                                           if row['component_origin'] == 'native_template'),
        native_wheel_geometry_simplified=any(row['surface_simplified'] for row in metadata
                                            if row['component_origin'] == 'native_template'),
        material_scope='Approximate preview colors and lighting; native game shaders and textures are not rendered.',
        preview_scope='Prepared body and lamp surfaces with actual supplied HQ wheel geometry in a fixed reference pose. '
                      'Interior, driver, body-owned mechanics and current suspension are not shown.',
        triangle_target=limit, effective_triangle_target=effective_limit,
        additional_preview_simplification_allowed=not bool(realizations),
        preview_simplification_policy='Retain already-budgeted split front/back geometry.'
            if realizations else 'Complete surfaces with optional additional preview QEM.')
    info_path = Path(template['root']) / 'info'
    if info_path.is_file() or fresh['placement'].get('driver') is not None:
        from .driver_fit import fit_driver
        from .cabin_fit import suggest_cabin
        _,header['driver_fit']=fit_driver(info_path.read_bytes(),fresh['placement'].get('driver'))
        def cabin_parts():
            for row in prepared:
                if row['id'] in source_ids:
                    part = source_lookup[row['id']]
                    label = part.name+' '+assembled.materials[part.material]['name']
                    yield label, part.id, row['role'], row['positions'], row['faces']
        header['driver_suggestion'] = suggest_cabin(cabin_parts(), header['driver_fit']['reference_targets'])
    encoded = json.dumps(header, separators=(',', ':'), allow_nan=False).encode()
    encoded += b' ' * (-len(encoded) % 4)
    return (b'GGV1' + struct.pack('<III', len(encoded), vertex_count, index_count) + encoded
            + raw[16 + size:]), metadata
