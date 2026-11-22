"""Append rigid mechanical meshes to explicitly reviewed native draw routes.

This authoring helper does not infer a motion group or native pivot. A caller
supplies exact original program hashes/call offsets and separate rotor/caliper
frames. Only a three-byte draw's shape operand changes. Original packed shape
descriptors, streams, VM state, branch widths and draw contexts remain owned by
the donor. The result is an unverified native candidate, never install approval.
"""
from __future__ import annotations

import copy
import math
import numbers
import struct
import numpy as np

from .errors import GarageError
from .native import pack_normal
from .util import sha256

ROLES = {'brake_rotor': 'wheel_spin', 'brake_caliper': 'upright_nonspin'}
CORNERS = ('FL', 'FR', 'RL', 'RR')


def _need(condition, message):
    if not condition:
        raise GarageError(message, 'mechanical_native')


def _int(value, label, minimum=0, maximum=65535):
    _need(isinstance(value, numbers.Integral) and not isinstance(value, (bool, np.bool_))
          and minimum <= value <= maximum, label+' is outside its reviewed integer range.')
    return int(value)


def _array(value, shape, label):
    try:
        out = np.asarray(value, dtype=float)
    except (TypeError, ValueError, OverflowError) as exc:
        raise GarageError(label+' must contain finite numeric values.', 'mechanical_native') from exc
    _need(out.shape == shape and np.isfinite(out).all(), label+' must have the expected finite dimensions.')
    return out


def _rotation(value, label, tolerance=1e-10):
    out = _array(value, (3, 3), label)
    _need(np.allclose(out.T@out, np.eye(3), rtol=0, atol=tolerance)
          and abs(float(np.linalg.det(out))-1.) <= tolerance,
          label+' must be a proper rigid rotation; scale, shear and reflection are not accepted.')
    return out


def _frame(value, role, corner):
    _need(isinstance(value, dict) and value.get('pivot_kind') == 'tyre_hub_center',
          'Mechanical components need a reviewed tyre/hub centre, not a component bounding-box centre.')
    _need(value.get('motion_intent') == ROLES[role], 'Rotor and caliper motion frames cannot be interchanged.')
    _need(value.get('units') == 'metres', 'Mechanical frames must use prepared metres.')
    _need(isinstance(value.get('evidence'), dict) and bool(value['evidence']),
          'Mechanical frame provenance is required.')
    center = _array(value.get('source_center_metres'), (3,), 'Source tyre/hub centre')
    rotation = _rotation(value.get('source_to_local_rotation'), 'Source-to-native local basis')
    axial = value.get('axial_offset_metres')
    _need(isinstance(axial, numbers.Real) and not isinstance(axial, (bool, np.bool_))
          and math.isfinite(float(axial)), 'Axial offset must be finite.')
    radial = _array(value.get('radial_offset_metres', [0., 0.]), (2,), 'Additional radial offset')
    _need(np.array_equal(radial, [0., 0.]),
          'A body alignment error cannot become an added Y/Z translation in a mechanical local frame.')
    mount = _array(value.get('reference_local_to_body'), (3, 4), 'Historical native reference frame')
    _rotation(mount[:, :3], 'Historical native reference basis', 2e-5)
    return center, rotation, np.array([float(axial), 0., 0.]), mount


def _layout(editor, index):
    row = editor.fvf_rows[index]
    _need(len(row) == 120 and row[25] == 24 and row[24] == 3,
          'Mechanical encoding requires the reviewed 24-byte position/normal/UV layout.')
    pointer = struct.unpack_from('>I', row, 8)[0]
    _need(0 < pointer <= len(editor.b)-24, 'Mechanical vertex fields leave native metadata.')
    fields = {}
    for at in range(pointer, pointer+24, 8):
        name_pointer = struct.unpack_from('>I', editor.b, at)[0]
        _need(0 < name_pointer < len(editor.b), 'Mechanical vertex field name leaves metadata.')
        end = editor.b.find(b'\0', name_pointer, min(name_pointer+1024, len(editor.b)))
        _need(end >= 0, 'Mechanical vertex field name is unterminated.')
        try:
            name = editor.b[name_pointer:end].decode('utf-8')
        except UnicodeError as exc:
            raise GarageError('Mechanical vertex field name is invalid.', 'mechanical_native') from exc
        _need(name not in fields, 'Mechanical vertex fields are duplicated.')
        fields[name] = tuple(editor.b[at+4:at+8])
    _need(fields == {'position': (0, 3, 2, 0), 'normal': (12, 1, 6, 0), 'map12': (16, 2, 2, 0)},
          'Mechanical encoding requires direct stream-zero float3 positions, packed normals and float2 UVs.')


def prepare_component(part, frame):
    """Rigidly express one authored component about its tyre/hub, retaining UVs.

Authored off-axis caliper/rotor vertices are retained. The restriction is on
additional alignment translation, not on legitimate source geometry.
"""
    _need(isinstance(part, dict), 'Mechanical source component must be an explicit record.')
    role, corner = part.get('role'), part.get('corner')
    _need(role in ROLES and corner in CORNERS, 'Each mechanical component needs an explicit role and corner.')
    _need(isinstance(part.get('id'), str) and bool(part['id']), 'Mechanical source identity is missing.')
    center, rotation, offset, mount = _frame(frame, role, corner)
    try:
        positions = np.asarray(part['positions'], dtype=float)
        normals = np.asarray(part['normals'], dtype=float)
        uv = np.asarray(part['uv'], dtype=float)
        faces = np.asarray(part['faces'])
    except (KeyError, TypeError, ValueError, OverflowError) as exc:
        raise GarageError('Mechanical source attributes are missing or invalid.', 'mechanical_native') from exc
    _need(positions.ndim == 2 and positions.shape[1:] == (3,) and len(positions) > 0,
          'Mechanical source positions must be a nonempty XYZ array.')
    _need(normals.shape == positions.shape and uv.shape == (len(positions), 2)
          and all(np.isfinite(a).all() for a in (positions, normals, uv)),
          'Mechanical vertex attributes must be complete and finite.')
    _need(np.all(np.abs(normals) <= 1.0001) and np.allclose(np.sum(normals*normals, axis=1), 1., rtol=0, atol=2e-4),
          'Mechanical source normals must already be normalized.')
    _need(faces.ndim == 2 and faces.shape[1:] == (3,) and len(faces) > 0
          and np.issubdtype(faces.dtype, np.integer) and not np.issubdtype(faces.dtype, np.bool_),
          'Mechanical topology must contain complete integer-indexed triangles.')
    _need(np.min(faces) >= 0 and np.max(faces) < len(positions), 'Mechanical triangle references an absent vertex.')
    with np.errstate(over='ignore', invalid='ignore'):
        local = (positions-center)@rotation.T+offset
        normal = normals@rotation.T
        body_reference = local@mount[:, :3].T+mount[:, 3]
    _need(all(np.isfinite(a).all() for a in (local, normal, body_reference)),
          'Mechanical transform exceeds the numeric range.')
    _need(np.all(np.abs(normal) <= 1.0001), 'Mechanical source normals must be normalized before export.')
    # A mechanical mesh can rotate through all phases. Include its complete
    # local-X sweep in the reference-pose culling bounds, not just one phase.
    radial = np.hypot(local[:, 1], local[:, 2])
    _need(np.isfinite(radial).all(), 'Mechanical swept envelope exceeds the numeric range.')
    radius = float(radial.max())
    swept = np.array([[x, y, z] for x in (float(local[:, 0].min()), float(local[:, 0].max()))
                      for y in (-radius, radius) for z in (-radius, radius)])
    if role == 'brake_caliper':
        swept = local
    with np.errstate(over='ignore', invalid='ignore'):
        bound_points = swept@mount[:, :3].T+mount[:, 3]
    _need(np.isfinite(bound_points).all(), 'Mechanical bound transform exceeds the numeric range.')
    result = dict(part, positions=local, normals=normal, uv=uv.copy(), faces=faces.copy(),
                  body_reference_points=bound_points)
    # Native UV overrides represent material sampling only; source UV remains
    # in the receipt and is never silently replaced in this geometry helper.
    if 'native_uv' in part:
        result['native_uv'] = _array(part['native_uv'], (len(local), 2), 'Native material UV').copy()
    return result


def _encode_group(parts):
    """One native material/draw per reviewed route; no omitted faces or reduction."""
    signature = {(p['native_material'], p['native_fvf'], bool(p.get('transparent', False))) for p in parts}
    _need(len(signature) == 1,
          'One native dynamic route cannot silently merge different materials; provide a reviewed multi-draw route.')
    vertex = bytearray()
    indices, points = [], []
    lookup = {}
    for part in parts:
        uv = part.get('native_uv', part['uv'])
        for face in part['faces']:
            for index in face:
                key = (part['id'], int(index))
                if key not in lookup:
                    _need(len(points) < 65535, 'Mechanical route exceeds the native vertex limit; no triangles were dropped.')
                    lookup[key] = len(points)
                    point, normal, texcoord = part['positions'][index], part['normals'][index], uv[index]
                    try:
                        vertex.extend(struct.pack('>3f', *point)+pack_normal(normal)+struct.pack('>2f', *texcoord))
                    except (OverflowError, struct.error) as exc:
                        raise GarageError('Mechanical attributes exceed float32 native encoding.', 'mechanical_native') from exc
                    points.append(point)
                indices.append(lookup[key])
    _need(0 < len(indices)//3 <= 20000, 'Mechanical route exceeds the per-draw triangle limit; no triangles were dropped.')
    material, fvf, transparent = next(iter(signature))
    return bytes(vertex), np.array(indices, dtype='>u2').tobytes(), np.array(points), material, fvf, transparent


def append_dynamic_components(editor, sourceparts, reviewedroutes, framecontract):
    """Append geometry and redirect exact reviewed calls, returning a receipt.

`reviewedroutes` contains original `asset_sha256`, `programs` (model ID string
to SHA256), and `components` with role, corner, original_shape and calls
({model, offset, context}). `framecontract` maps 'role:corner' to explicit
hub-centred rigid frames. Only groups present in sourceparts are redirected.
The editor must still refer to the original reviewed programs.
"""
    _need(isinstance(reviewedroutes, dict) and reviewedroutes.get('asset_sha256') == sha256(editor.raw),
          'Mechanical native routes do not match the exact original asset.')
    _need(isinstance(reviewedroutes.get('evidence'), dict) and bool(reviewedroutes['evidence']),
          'Native dynamic route provenance is required.')
    _need(isinstance(sourceparts, (list, tuple)) and bool(sourceparts) and isinstance(framecontract, dict),
          'Explicit source mechanical groups and frame contracts are required.')
    _need(not editor.finished, 'Native editor was already finalized.')
    routes = reviewedroutes.get('components')
    _need(isinstance(routes, list), 'Reviewed dynamic component routes are missing.')
    route_map = {}
    for route in routes:
        _need(isinstance(route, dict), 'Invalid native dynamic route.')
        key = (route.get('role'), route.get('corner'))
        _need(key[0] in ROLES and key[1] in CORNERS and key not in route_map,
              'Native dynamic route has an unknown or duplicate role/corner.')
        route_map[key] = route
    groups, seen = {}, set()
    for part in sourceparts:
        _need(isinstance(part, dict) and isinstance(part.get('id'), str) and part['id'] not in seen,
              'Source mechanical identities must be unique.')
        seen.add(part['id'])
        key = (part.get('role'), part.get('corner'))
        _need(key in route_map, 'This mechanical role/corner has no reviewed native dynamic route.')
        frame_key = ':'.join(key)
        _need(frame_key in framecontract, 'A mechanical tyre/hub frame is missing.')
        native_material = _int(part.get('native_material'), 'Native material', maximum=32766)
        material_count = editor.bank_size+sum(not row.get('replacement') for row in editor.new_materials)
        _need(native_material < material_count, 'Mechanical source references an absent native material.')
        native_fvf = _int(part.get('native_fvf'), 'Native vertex layout', maximum=len(editor.fvf_rows)-1)
        _layout(editor, native_fvf)
        prepared = prepare_component(dict(part, native_material=native_material, native_fvf=native_fvf), framecontract[frame_key])
        groups.setdefault(key, []).append(prepared)
    original_count = editor.m.u16(0x14)
    _need(len(editor.shape_rows)+len(groups) <= 256, 'Mechanical shapes exceed byte-sized native draw operands.')
    programs, changes, occupied, encoded = {}, {}, set(), {}
    for key, parts in groups.items():
        route = route_map[key]
        old_shape = _int(route.get('original_shape'), 'Original mechanical shape', maximum=original_count-1)
        calls = route.get('calls')
        _need(isinstance(calls, list) and bool(calls), 'Mechanical route has no reviewed native call.')
        for call in calls:
            _need(isinstance(call, dict), 'Invalid mechanical native call.')
            mid = _int(call.get('model'), 'Native model', maximum=editor.m.u16(0x10)-1)
            at = editor.m.u32(0x30)+48*mid
            pointer, size = editor.m.u32(at+24), editor.m.u32(at+28)
            original = editor.m.span(pointer, size, editor.m.cut)
            expected_hash = reviewedroutes.get('programs', {}).get(str(mid))
            _need(expected_hash == sha256(original), 'Original native dynamic program changed.')
            # A different authoring pass must not be overwritten by a stale
            # original-program plan, even if editor.raw still has the old hash.
            current_pointer, current_size = struct.unpack_from('>II', editor.b, at+24)
            _need(current_size == size and 0 <= current_pointer <= len(editor.b)-size
                  and bytes(editor.b[current_pointer:current_pointer+size]) == original,
                  'Native program already changed; compose and review its dynamic routes before relinking.')
            offset = _int(call.get('offset'), 'Native call offset', maximum=max(0, size-3))
            context = _int(call.get('context'), 'Native draw context', maximum=255)
            _need(offset+3 <= size and original[offset:offset+3] == bytes((59, old_shape, context)),
                  'Reviewed rotor/caliper call no longer matches its original shape and context.')
            cell = (mid, offset+1)
            _need(cell not in occupied, 'Two mechanical groups cannot own the same native draw operand.')
            occupied.add(cell)
            programs.setdefault(mid, original)
            changes.setdefault(mid, []).append((offset+1, key))
        encoded[key] = _encode_group(parts)
    # All source, frame, layout and original-program checks complete before
    # any native shape, material set, pointer or program mutation occurs.
    old_shapes = [bytes(row) for row in editor.shape_rows[:original_count]]
    emitted, shape_ids = [], {}
    for key, parts in groups.items():
        vb, ib, points, material, fvf, transparent = encoded[key]
        role, corner = key
        sid = editor.shape(vb, ib, points, fvf, material, 'mechanical_'+role+'_'+corner,
                           transparent=transparent, streamed=False)
        shape_ids[key] = sid
        emitted.append({'role': role, 'corner': corner, 'motion_intent': ROLES[role],
                        'shape': sid, 'original_shape': route_map[key]['original_shape'],
                        'source_parts': [p['id'] for p in parts], 'triangles': len(ib)//6,
                        'vertices': len(points), 'native_material': material,
                        'vertex_sha256': sha256(vb), 'index_sha256': sha256(ib),
                        'frame': copy.deepcopy(framecontract[':'.join(key)])})
    program_reports = []
    for mid, original in programs.items():
        program = bytearray(original)
        mids, bounds = set(), []
        operands = []
        for offset, key in changes[mid]:
            program[offset] = shape_ids[key]
            mids.add(encoded[key][3])
            bounds.extend(p['body_reference_points'] for p in groups[key])
            operands.append({'offset': offset, 'old_shape': original[offset], 'new_shape': program[offset],
                             'role': key[0], 'corner': key[1], 'context_preserved': original[offset+1]})
        allowed = {row['offset'] for row in operands}
        _need(len(program) == len(original) and all(a == b or i in allowed for i, (a, b) in enumerate(zip(original, program))),
              'Mechanical relinking changed native command state or branch layout.')
        editor.model(mid, bytes(program), mids, np.vstack(bounds))
        program_reports.append({'model': mid, 'before_sha256': sha256(original), 'after_sha256': sha256(program),
                                'bytes': len(program), 'shape_operands': operands,
                                'only_shape_operands_changed': True})
    _need(old_shapes == [bytes(row) for row in editor.shape_rows[:original_count]],
          'Original packed/donor shape descriptors were modified.')
    return {'schema': 1, 'kind': 'explicit-native-mechanical-candidate', 'shapes': emitted,
            'programs': program_reports, 'source_triangles': sum(len(p['faces']) for p in sourceparts),
            'native_triangles': sum(row['triangles'] for row in emitted),
            'original_shape_descriptors_preserved': True, 'original_streams_modified': False,
            'native_vm_and_branch_state_preserved': True, 'source_dimensions_preserved': True,
            'new_radial_alignment_offset_metres': [0., 0.], 'physics_or_mounts_changed': False,
            'runtime_verified': False, 'installable': False,
            'route_evidence': copy.deepcopy(reviewedroutes['evidence'])}
