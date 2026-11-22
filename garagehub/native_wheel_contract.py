"""Read-only ownership inventory for the authenticated Supra-derived wheels.

This describes drawing resources and fixed local geometry. It does not establish
the engine's physical tyre owner, live axle transforms, or sizing order. In
particular, race WheelBlurShadow geometry must never be called a physical tyre.
No game data, model commands, database rows, or source geometry are modified.
"""
from __future__ import annotations

from collections import defaultdict
import math
import struct

import numpy as np

from .car_profiles import ASSETS, PROFILE_ID
from .errors import GarageError
from .native import MDL3
from .util import sha256


SCHEMA = 'garagehub.native_wheel_contract.v1'
CORNERS = ('FL', 'FR', 'RL', 'RR')


def _need(value, message):
    if not value:
        raise GarageError(message, 'native_wheel_contract')


def _span(m, pointer, length, label):
    _need(type(pointer) is int and pointer >= 0xe4 and length > 0,
          label + ' has no valid metadata allocation.')
    return m.span(pointer, length, m.cut)


def _bounds(m, pointer):
    points = np.frombuffer(_span(m, pointer, 96, 'Wheel bounds'), dtype='>f4').reshape(8, 3)
    _need(np.isfinite(points).all(), 'Wheel bounds contain a nonfinite coordinate.')
    return {'offset': pointer, 'bytes': 96, 'min': points.min(0).tolist(),
            'max': points.max(0).tolist(), 'encoding': 'ordered_eight_vertex_hexahedron'}, points


def _shader(m, material):
    ms = m.u32(0x44)
    _span(m, ms, 24, 'Wheel material header')
    _need(m.data[ms + 4] == 1 and 0 <= material < m.u16(ms),
          'Wheel material bank does not match the reviewed profile.')
    at = m.u32(ms + 8) + material * 52
    _span(m, at, 52, 'Wheel material')
    definition = m.u16(at + 4)
    _need(definition < m.u16(ms + 2), 'Wheel shader definition leaves its table.')
    md = m.u32(ms + 12) + definition * 40
    _span(m, md, 40, 'Wheel material definition')
    reference = m.u32(md + 20)
    _span(m, reference, 8, 'Wheel shader reference')
    return {'material': material, 'material_name': m.string(m.u32(at)),
            'shader': m.string(m.u32(reference)), 'shader_key': m.u32(reference + 4)}


def _role(lod, sid, shader):
    if lod == 'hq':
        role, expected = (('profile_hq_tyre_mesh', 'Flex') if sid < 4 else
                          ('wheel_shadow', 'Shadow') if sid < 8 else
                          ('rim', 'Flex') if sid % 2 == 0 else ('valve', 'Flex'))
    else:
        role, expected = (('wheel_shadow', 'Shadow') if sid >= 12 else
                          ('rim_blur_shadow', 'WheelBlurShadow') if sid % 3 == 0 else
                          ('rim_and_valve', 'Flex') if sid % 3 == 1 else
                          ('rim_blur', 'WheelBlur'))
    _need(shader == expected, 'Wheel shape role and native shader binding disagree.')
    return role


def _direct(m, sp, layout, sid, references):
    nv, vp, ni, ip = (m.u32(sp + offset) for offset in (8, 12, 20, 24))
    _need(0 < nv <= 65536 and ni > 0 and ni % 3 == 0 and m.u16(sp + 38) == ni // 3,
          'Ordinary wheel geometry has invalid counts.')
    stride = layout['stride']
    _span(m, vp, nv * stride, 'Wheel vertices')
    index_data = _span(m, ip, ni * 2, 'Wheel indices')
    _need(int(np.frombuffer(index_data, dtype='>u2').max()) < nv,
          'Wheel triangle references a missing vertex.')
    position = next((field for field in layout['fields'] if field['name'] == 'position'), None)
    _need(position and (position['components'], position['format'], position['stream']) == (3, 2, 0)
          and position['offset'] + 12 <= stride, 'Unsupported ordinary wheel position layout.')
    xyz = np.ndarray((nv, 3), dtype='>f4', buffer=m.data,
                     offset=vp + position['offset'], strides=(stride, 4))
    _need(np.isfinite(xyz).all(), 'Ordinary wheel geometry has nonfinite positions.')
    for kind, pointer, length in (('ordinary_vertices', vp, nv * stride),
                                  ('ordinary_indices', ip, ni * 2)):
        references.append({'shape': sid, 'kind': kind, 'offset': pointer, 'bytes': length})
    return {'storage': 'ordinary_inline', 'vertex_offset': vp, 'vertex_count': nv,
            'index_offset': ip, 'index_count': ni, 'stride': stride,
            'fields': layout['fields'],
            'local_mesh_bounds': {'min': xyz.min(0).tolist(), 'max': xyz.max(0).tolist()},
            'local_radial_envelope_metres': float(np.linalg.norm(xyz[:, 1:].astype(float), axis=1).max())}


def _packed(m, sp, sid, references):
    _need(m.s16(sp + 2) == -1 and not any(m.u32(sp + offset) for offset in (8, 12, 20, 24)),
          'Packed wheel shape mixes ordinary geometry fields.')
    rp = m.u32(sp + 44)
    _span(m, rp, 56, 'Wheel packed reference')
    hp = m.u32(0xd0)
    _need(_span(m, hp, 52, 'Wheel packed header')[:4] == b'PMSH',
          'Wheel packed header is missing.')
    mesh_id = m.u32(rp + 0x34)
    _need(mesh_id < m.u16(hp + 16), 'Wheel packed mesh index leaves its table.')
    mp = m.u32(hp + 24) + 48 * mesh_id
    _span(m, mp, 48, 'Wheel packed mesh')
    decl_id, layout_id = m.u16(mp), m.u16(mp + 2)
    _need(decl_id < m.u16(hp + 22) and layout_id < m.u16(hp + 20),
          'Wheel packed vertex declaration or bit layout leaves its table.')
    dp = m.u32(hp + 36) + decl_id * 16
    declaration = _span(m, dp, 16, 'Wheel packed vertex declaration')
    packed_count, raw_count, packed_stride, raw_stride = declaration[:4]
    _need((packed_count, raw_count, packed_stride, raw_stride) == (1, 3, 16, 12),
          'Wheel packed vertex declaration is not the reviewed inline layout.')
    ep, names = m.u32(dp + 8), m.u32(dp + 12)
    _span(m, ep, 16, 'Wheel packed vertex fields')
    _span(m, names, 16, 'Wheel packed vertex names')
    elements = []
    for i in range(4):
        elements.append({'name': m.string(m.u32(names + 4 * i)), 'packed': i == 0,
                         'field': list(m.span(ep + 4 * i, 4))})
    expected = [('position', [0, 0, 3, 2]), ('normal', [1, 0, 1, 6]),
                ('colorSet1', [2, 4, 4, 4]), ('map12', [3, 8, 2, 3])]
    _need([(row['name'], row['field']) for row in elements] == expected,
          'Wheel packed position/normal/colour/UV ownership changed.')
    la = m.u32(hp + 32) + 8 * layout_id
    _span(m, la, 8, 'Wheel packed bit layout array')
    _need(m.u32(la) == 1, 'Wheel packed bit layout count changed.')
    lp = m.u32(la + 4)
    _span(m, lp, 48, 'Wheel packed bit layout')
    _need(m.u32(lp) == 48 and m.span(lp + 4, 8) == bytes((16, 16, 16, 0, 16, 32, 48, 48)),
          'Wheel packed position bit allocation changed.')
    scale = struct.unpack_from('>3f', m.data, lp + 16)
    offset = struct.unpack_from('>3f', m.data, lp + 32)
    _need(all(math.isfinite(v) for v in scale + offset), 'Wheel packed XYZ layout is nonfinite.')
    references.append({'shape': sid, 'kind': 'packed_position_layout', 'offset': lp, 'bytes': 48})
    _need(m.data[mp + 6] == 1, 'Wheel packed component count changed.')
    sd, bounds_pointer = m.u32(mp + 16), m.u32(mp + 20)
    _span(m, sd, 48, 'Wheel smooth mesh descriptor')
    pointers = struct.unpack_from('>8I', m.data, sd)
    tail = struct.unpack_from('>7H2B', m.data, sd + 32)
    pv_count, rv_count = tail[1], tail[3]
    _need(pv_count > 0 and rv_count == pv_count and tail[7] == 11,
          'Wheel packed vertex/index descriptor changed.')
    _need(not any(pointers[index] for index in (1, 2, 4, 7)),
          'Wheel packed descriptor has an unreviewed extension.')
    fields = [('packed_positions', 0, (pv_count * 48 + 7) // 8),
              ('raw_normals_colour_uv', 3, rv_count * raw_stride),
              ('packed_indices', 5, tail[5] * 16),
              ('smooth_mesh_commands', 6, tail[6] * 16)]
    payloads = []
    for kind, index, length in fields:
        pointer = pointers[index]
        _span(m, pointer, length, 'Wheel ' + kind)
        row = {'shape': sid, 'kind': kind, 'offset': pointer, 'bytes': length}
        references.append(row)
        payloads.append({key: value for key, value in row.items() if key != 'shape'})
    shape_bounds = _span(m, m.u32(sp + 40), 96, 'Wheel shape bounds')
    soa = _span(m, bounds_pointer, 96, 'Wheel packed component bounds')
    # Two blocks of four vertices: xxxx yyyy zzzz, then the second four.
    words = struct.unpack('>24I', shape_bounds)
    expected_soa = [words[(block * 4 + vertex) * 3 + axis]
                    for block in range(2) for axis in range(3) for vertex in range(4)]
    _need(soa == struct.pack('>24I', *expected_soa), 'Wheel packed and shape bounds disagree.')
    return {'storage': 'packed_inline', 'reference_offset': rp,
            'mesh_index_field_offset': rp + 0x34, 'mesh_index': mesh_id, 'mesh_offset': mp,
            'declaration_offset': dp, 'elements': elements,
            'bit_layout_offset': lp, 'bit_layout_id': layout_id,
            'position_scale_xyz': list(scale), 'position_offset_xyz': list(offset),
            'smooth_descriptor_offset': sd, 'packed_vertex_count': pv_count,
            'raw_vertex_count': rv_count, 'payloads': payloads,
            'component_bounds_offset': bounds_pointer, 'component_bounds_match': True,
            'decoded_surface_or_physical_tyre_dimensions': None}


def _aliases(references):
    groups = defaultdict(list)
    for row in references:
        groups[(row['kind'], row['offset'])].append(row)
    result = []
    for (kind, pointer), rows in sorted(groups.items()):
        owners = sorted({row['shape'] for row in rows})
        if len(owners) > 1:
            result.append({'kind': kind, 'offset': pointer, 'shapes': owners,
                           'referenced_byte_lengths': sorted({row['bytes'] for row in rows})})
    return result


def _models(m, lod, shapes):
    result = []
    for mid in range(8):
        at = m.u32(0x30) + 48 * mid
        _span(m, at, 48, 'Wheel model')
        _need(m.u16(at + 18) == 8, 'Wheel model bounding hexahedron changed.')
        pointer, length = m.u32(at + 24), m.u32(at + 28)
        program = _span(m, pointer, length, 'Wheel model commands')
        if mid >= 4:
            sid = mid if lod == 'hq' else mid + 8
            context = mid if lod == 'hq' else mid + 4
            expected = bytes((59, sid, context, 0))
            calls = [(0, sid, context, 'shadow')]
        elif lod == 'hq':
            ids = (mid, 8 + 2 * mid, 9 + 2 * mid)
            expected = b''.join(bytes((59, sid, mid)) for sid in ids) + b'\0'
            calls = [(3 * i, sid, mid, 'stationary_reference') for i, sid in enumerate(ids)]
        else:
            sid, context = 3 * mid, 2 * mid
            expected = (bytes((59, sid, context)) + bytes.fromhex('05000002000400070908')
                        + bytes((59, sid + 1, context + 1, 9, 3, 59, sid + 2, context + 1, 0)))
            calls = [(0, sid, context, 'before_branch'),
                     (13, sid + 1, context + 1, 'rim_branch'),
                     (18, sid + 2, context + 1, 'blur_branch')]
        _need(program == expected, 'Wheel draw/state commands changed from the reviewed profile.')
        origin = struct.unpack_from('>3f', m.data, at + 4)
        radius = struct.unpack_from('>f', m.data, at)[0]
        _need(all(math.isfinite(v) for v in origin + (radius,)) and radius > 0,
              'Wheel model origin/radius is invalid.')
        bound, _ = _bounds(m, m.u32(at + 20))
        result.append({'model': mid, 'corner': CORNERS[mid % 4], 'offset': at,
                       'origin': list(origin), 'radius_field': radius, 'bounds': bound,
                       'program_offset': pointer, 'program_length': length,
                       'program_sha256': sha256(program),
                       'draws': [{'command_offset': off, 'shape': sid, 'context': ctx,
                                  'state': state, 'role': shapes[sid]['role'],
                                  'storage': shapes[sid]['geometry']['storage']}
                                 for off, sid, ctx, state in calls],
                       'runtime_branch_threshold_verified': False})
    return result


def _asset(raw, lod):
    m = MDL3(raw)
    validation = m.validate()
    _need((m.u16(0x10), m.u16(0x14)) == (8, 16), 'Wheel model/shape inventory changed.')
    layouts, references, shapes = m.fvfs(), [], []
    for sid in range(16):
        sp = m.u32(0x38) + 48 * sid
        binding = _shader(m, m.s16(sp + 4))
        role = _role(lod, sid, binding['shader'])
        bound, _ = _bounds(m, m.u32(sp + 40))
        fid = m.s16(sp + 2)
        if fid >= 0:
            _need(fid < len(layouts) and not m.u32(sp + 44), 'Ordinary wheel has a packed extension.')
            geometry = _direct(m, sp, layouts[fid], sid, references)
        else:
            geometry = _packed(m, sp, sid, references)
        shapes.append({'shape': sid, 'offset': sp, 'flags': m.u16(sp),
                       'role': role, 'binding': binding, 'bounds': bound, 'geometry': geometry,
                       'physical_tyre_measurement': False})
    return {'sha256': sha256(raw), 'bytes': len(raw), 'metadata_extent': m.cut,
            'native_structure_passed': validation['passed'], 'models': _models(m, lod, shapes),
            'shapes': shapes, 'shared_payloads': _aliases(references),
            'alias_scope': 'Equal typed start offsets referenced by the sixteen active shapes only; '
                           'partial overlaps and unreferenced native packed meshes are not classified.',
            'shared_data_reclaim_or_edit_permission': False}


def audit_native_wheel_contract(hq_wheel: bytes, race_wheel: bytes) -> dict:
    """Inspect exact owned profile wheel bytes without writing or authoring data.

    Unknown, mutable, corrupt, or structurally inconsistent inputs raise
    GarageError. Hash authentication happens for both assets before parsing.
    The serializable result deliberately does not certify full wheel-size or
    chassis adaptation, even when every drawing-resource check passes.
    """
    supplied = {'hq': hq_wheel, 'race': race_wheel}
    for lod, raw in supplied.items():
        _need(type(raw) is bytes, 'Wheel ownership inspection requires immutable file bytes.')
        if sha256(raw) != ASSETS[lod + '/wheel']:
            raise GarageError('The ' + lod + '/wheel file does not match the reviewed vehicle profile.',
                              'template_identity')
    assets = {lod: _asset(raw, lod) for lod, raw in supplied.items()}
    return {'schema': SCHEMA, 'profile_id': PROFILE_ID, 'read_only': True,
            'assets': assets, 'runtime_verified': False,
            'capabilities': {'authenticated_wheel_draw_inventory': True,
                             'fixed_hq_tyre_mesh_envelopes': True,
                             'race_physical_tyre_owner_identified': False,
                             'native_axle_mount_owner_identified': False,
                             'runtime_dimension_scaling_order_verified': False,
                             'complete_wheel_size_authoring': False,
                             'complete_chassis_adaptation': False},
            'role_correction': 'Race shapes 0, 3, 6 and 9 use WheelBlurShadow; their bounds '
                               'are not physical tyre dimensions.',
            'limits': [
                {'code': 'race_tyre_owner_unresolved',
                 'message': 'The race wheel assets identify rims, blur and shadows. The separate '
                            'physical/rendered tyre owner and its size binding are not established.'},
                {'code': 'axle_mount_owner_unresolved',
                 'message': 'Fixed preview centres are not measured engine axle transforms. Local '
                            'Y/Z vertex offsets cannot substitute for moving a spinning axle.'},
                {'code': 'runtime_sizing_order_unverified',
                 'message': 'Database tyre specifications, native geometry and engine sizing must '
                            'be reconciled before changing dimensions; automatic scaling is unverified.'},
                {'code': 'body_mechanics_outside_inventory',
                 'message': 'Body-owned brakes, suspension, tyre-related body/shadow geometry, global '
                            'tyre assets and current suspension pose are outside this wheel inventory.'},
                {'code': 'native_transform_commands_unverified',
                 'message': 'No verified GT6 whole-wheel transform command is available here. '
                            'PS2 command semantics do not establish the PS3 command contract.'},
                {'code': 'shared_payload_ownership_limited',
                 'message': 'Shared references are evidence for preserving ownership, not permission '
                            'to overwrite or reclaim their allocations.'},
            ]}
