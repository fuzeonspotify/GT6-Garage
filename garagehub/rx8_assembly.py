"""Compose one source-accounted RX8 assembly candidate from original assets.

This is a non-installable authoring/inspection path, not a production profile.
One original editor owns each body asset. Source static/lamp draws and the
authenticated rotor/caliper suffix are composed together. Every source part
has an emitted, inherited, explicitly excluded, or blocked disposition.

The only fit policy currently implemented is deliberately diagnostic: retain
the audited source dimensions/reference convention and expose the body-to-hub
residual. It supplies no current ride height, physics edit or new local offset.
"""
from __future__ import annotations

import copy
import json
import struct
from collections import defaultdict
from collections.abc import Mapping

import numpy as np

from .errors import GarageError
from .glb import Scene
from .native import MDL3
from .native_editor import Editor
from .util import sha256
from .recognition import ROLES, CORNERS, CORNER_ROLES, axes
from .mechanical_parts import _part, _geometry_hash
from .assembly_fit import assembly_fit_report
from .car_geometry import batches, switch, evaluate
from .car_materials import Materials
from .body_streams import groups as stream_groups
from .donor_profiles import RX8_ASSETS, RX8_ID
from .rx8_body_materials import RX8BodyMaterials
from .rx8_lamp_lab import emitter_recipe
from .rx8_mechanical_lab import (reviewed_rx8_routes, historical_frame_contract,
                                 PROGRAM_HASHES)
from .mechanical_native import append_dynamic_components, prepare_component, _layout
from .rx8_wheel_adapter import (REFERENCE_MOUNTS, REFERENCE_EVIDENCE,
                                shadow_reference_geometry)
from .wheel_mounts import rigid_axle_mount


POLICY = 'historical_reference_only'
STATIC_ROLES = frozenset(('body', 'paint', 'interior', 'glass', 'headlight',
                         'taillight', 'brakelight'))
MECHANICAL_ROLES = frozenset(('brake_rotor', 'brake_caliper'))
RIM_ROLES = frozenset(('rim', 'valve'))
LAMP_ROLES = frozenset(('headlight', 'taillight', 'brakelight'))
SUFFIX_START = {'hq': {0: 204, 5: 207}, 'race': {0: 215, 5: 207}}
DRAW_BUDGET = 18000


def _need(condition, message):
    if not condition:
        raise GarageError(message, 'assembly_contract')


def _digest_json(value):
    return sha256(json.dumps(value, sort_keys=True, separators=(',', ':'),
                             allow_nan=False).encode('utf-8'))


def _inventory(scene, inspection):
    _need(isinstance(scene, Scene) and isinstance(inspection, Mapping),
          'Use the current prepared source scene and its reviewed inspection.')
    rows = inspection.get('parts')
    _need(isinstance(rows, list) and all(isinstance(row, Mapping) for row in rows),
          'The source inspection inventory is missing.')
    ids = [row.get('id') for row in rows]
    _need(all(isinstance(pid, str) and pid for pid in ids) and len(set(ids)) == len(ids),
          'Inspection component identities are missing or duplicated.')
    parts = {part.id: _part(part) for part in scene.parts}
    _need(len(parts) == len(scene.parts) == len(rows) and set(parts) == set(ids),
          'Inspection identities do not match the current prepared source.')
    records = {row['id']: row for row in rows}
    for pid, part in parts.items():
        row = records[pid]
        _need(row.get('role') in ROLES, 'Unknown source role for '+pid+'.')
        _need(row.get('corner') is None or row.get('corner') in CORNERS,
              'Unknown source corner for '+pid+'.')
        _need(part.material < len(scene.materials) and isinstance(scene.materials[part.material], Mapping),
              'Source material identity is missing for '+pid+'.')
        normals = np.asarray(part.normals, float)
        _need(np.allclose(np.sum(normals*normals, axis=1), 1., rtol=0, atol=2e-4),
              'Source normals must already be normalized: '+pid+'.')
    assembly_fit_report(inspection, {'kind': 'explicit_centres',
        'forward': inspection.get('forward'),
        'wheel_centers_metres': inspection.get('wheel_centers')})
    for corner in CORNERS:
        tyres = [part for pid, part in parts.items()
                 if records[pid]['role'] == 'tyre' and records[pid].get('corner') == corner]
        _need(bool(tyres), 'Assign source tyre geometry at '+corner+' before assembly authoring.')
        # Match recognition's float32 bounds/mean arithmetic exactly.
        bounds = np.array([np.min([part.positions.min(0) for part in tyres], axis=0),
                           np.max([part.positions.max(0) for part in tyres], axis=0)])
        measured = bounds.mean(0)
        numeric = 256*np.finfo(float).eps*max(1., float(np.max(np.abs(bounds))))
        _need(np.allclose(measured, inspection['wheel_centers'][corner], rtol=0, atol=numeric),
              'Saved tyre/hub centre differs from current geometry at '+corner+'.')
    return parts, records


def _frames(inspection, fit_decision):
    _need(isinstance(fit_decision, Mapping)
          and set(fit_decision) <= {'policy', 'review_note'}
          and fit_decision.get('policy') == POLICY,
          'Choose the explicit historical_reference_only diagnostic policy; no fitted mount policy is implemented.')
    _need(isinstance(fit_decision.get('review_note', ''), str), 'Fit review note must be text.')
    centers = np.asarray([inspection['wheel_centers'][c] for c in CORNERS], float)
    left, up, front = axes(inspection['forward'])
    rotation = np.array([-left, up, -front])
    origin = centers.mean(0)*np.array([1., 0., 1.])
    mechanical = historical_frame_contract(inspection)
    wheels = {}
    residuals = {}
    for corner in CORNERS:
        center, basis, offset, report = rigid_axle_mount(inspection, corner, REFERENCE_MOUNTS[corner])
        expected = (center-origin)@rotation.T
        _need(np.array_equal(expected, report['source_center_in_native_body_frame']),
              'The shared body and wheel source-frame conventions disagree.')
        wheels[corner] = {'units': 'metres', 'source_center_metres': center.tolist(),
            'source_to_local_rotation': basis.tolist(), 'axial_offset_metres': float(offset[0]),
            'radial_offset_metres': [0., 0.], 'reference_local_to_body': copy.deepcopy(REFERENCE_MOUNTS[corner]),
            'evidence': copy.deepcopy(REFERENCE_EVIDENCE), **report}
        residuals[corner] = {'wheel': report['source_body_minus_fitted_center_metres']}
        for role in MECHANICAL_ROLES:
            frame = mechanical[role+':'+corner]
            _need(np.array_equal(expected, frame['source_center_in_body_frame']),
                  'The shared body and mechanical source-frame conventions disagree.')
            residuals[corner][role] = frame['source_body_minus_fitted_center_metres']
    return {'policy': POLICY, 'review_note': fit_decision.get('review_note', ''),
        'body_source_origin_metres': origin.tolist(), 'body_source_to_native_rotation': rotation.tolist(),
        'wheel_frames': wheels, 'mechanical_frames': mechanical,
        'source_body_minus_reference_hub_metres': residuals,
        'historically_observed_reference': True, 'current_mount_verified': False,
        'nominal_target_y_used': False, 'body_fit_solved': False,
        'new_alignment_offsets_applied': False, 'source_dimensions_preserved': True,
        'physics_changed': False,
        'scope': 'Existing audited rigid local bases and axial terms only. The source body retains its own dimensions and XZ-centred frame. Historical reference residuals remain unresolved; this is not current ride-height, tyre or motion acceptance.'}


def assembly_plan(scene, inspection, *, fit_decision, dispositions=None):
    """Account for the complete prepared scene without native or filesystem writes.

``dispositions`` records caller-reviewed ``blocked`` or ``excluded`` components
with a nonempty reason (and optional provenance). It can identify unresolved
suspension without embedding a source name or mesh ID in the compiler.
"""
    parts, records = _inventory(scene, inspection)
    frames = _frames(inspection, fit_decision)
    dispositions = {} if dispositions is None else dispositions
    _need(isinstance(dispositions, Mapping) and set(dispositions) <= set(parts),
          'Component dispositions reference an unknown source part.')
    for pid, decision in dispositions.items():
        _need(isinstance(decision, Mapping)
              and set(decision) <= {'disposition', 'reason', 'provenance'}
              and decision.get('disposition') in ('blocked', 'excluded')
              and isinstance(decision.get('reason'), str) and decision['reason'].strip(),
              'A reviewed component disposition needs an explicit status and reason: '+pid+'.')
    coverage = []
    issues = []

    def issue(code, message, pids=()):
        issues.append({'code': code, 'parts': list(pids), 'message': message})

    issue('historical_fit_unresolved', 'The source body and historical native hubs are not fitted. Inspect the declared per-corner residuals.')
    issue('native_runtime_unverified', 'Combined native loading, material appearance, steering, spin, suspension and repeated-entry behavior are unverified.')
    issue('native_inheritance_unverified', 'Original tyres, tyre shadows/blur, driver/interior, body shadow and physics are retained; their compatibility with this source is unverified.')
    for pid, part in parts.items():
        row, material = records[pid], scene.materials[part.material]
        role, corner = row['role'], row.get('corner')
        item = {'id': pid, 'role': role, 'corner': corner, 'material': int(part.material),
            'source_triangles': len(part.indices), 'source_vertices': len(part.positions),
            'prepared_part_sha256': _geometry_hash(part), 'disposition': 'emit', 'route': None,
            'reason': None, 'material_limitations': []}
        if pid in dispositions:
            item.update(copy.deepcopy(dict(dispositions[pid])))
        elif row.get('role_review_required') or row.get('mechanical_role_proposals'):
            item.update(disposition='blocked', reason='A source motion/role proposal still requires explicit review.')
        elif role == 'ignore':
            item.update(disposition='blocked', reason='An ignored source component needs a recorded exclusion reason.')
        elif role in CORNER_ROLES and corner not in CORNERS:
            item.update(disposition='blocked', reason='This motion role needs one reviewed wheel corner.')
        elif role == 'tyre':
            item.update(disposition='inherited', route='native_tyre',
                reason='The selected diagnostic policy retains native tyres; this source tyre supplies hub/envelope measurements only. Rendered/loaded tyre compatibility remains unverified.')
        elif material.get('alpha_mode', 'OPAQUE') == 'MASK':
            item.update(disposition='blocked', reason='Native alpha-cutout semantics are not supported by this assembly profile.')
        elif role in MECHANICAL_ROLES | RIM_ROLES and (
                material.get('alpha_mode', 'OPAQUE') != 'OPAQUE' or material.get('transmission', 0.) > .1):
            item.update(disposition='blocked', reason='Transparent/cutout moving surfaces need a reviewed material-preserving native route; this surface is not emitted or flattened.')
        elif role in LAMP_ROLES and (
                material.get('alpha_mode', 'OPAQUE') != 'OPAQUE' or material.get('transmission', 0.) > .1):
            item.update(disposition='blocked', reason='This transparent lamp needs a reviewed nonemissive base and switched-emitter recipe.')
        else:
            item['route'] = 'body_static' if role in STATIC_ROLES else 'body_mechanical' if role in MECHANICAL_ROLES else 'wheel_rim'
        if material.get('normal_map'):
            item['material_limitations'].append('Source normal-map transfer is unimplemented.')
        if material.get('double_sided'):
            item['material_limitations'].append('Native two-sided rendering of this source material is unverified.')
        if material.get('alpha_mode', 'OPAQUE') == 'BLEND' or role == 'glass':
            item['material_limitations'].append('Native opacity, sorting and glass optics remain unverified.')
        if role == 'paint':
            item['material_limitations'].append('Source paint appearance and native palette behavior remain unverified.')
        coverage.append(item)
    # The reviewed brake suffix has one existing call per motion role/corner.
    # Distinct materials are never merged, silently omitted or made static.
    mechanical = defaultdict(list)
    for item in coverage:
        if item['disposition'] == 'emit' and item['route'] == 'body_mechanical':
            mechanical[item['role'], item['corner']].append(item)
    for key, items in mechanical.items():
        materials = {item['material'] for item in items}
        if len(materials) > 1 or sum(item['source_triangles'] for item in items) > 20000 or sum(item['source_vertices'] for item in items) > 65535:
            for item in items:
                item.update(disposition='blocked', route=None,
                    reason='The current reviewed mechanical call supports one bounded compatible material group; a multi-draw native route is required.')
    for item in coverage:
        if item['disposition'] == 'blocked':
            issue('source_component_blocked', item['reason'], [item['id']])
        for message in item['material_limitations']:
            issue('material_capability_unverified', message, [item['id']])
    for corner in CORNERS:
        if not any(item['role'] == 'rim' and item['corner'] == corner and item['disposition'] == 'emit' for item in coverage):
            issue('source_rim_incomplete', 'No supported source rim is emitted at '+corner+'.')
    for role in ('headlight', 'taillight'):
        if not any(item['role'] == role and item['disposition'] == 'emit' for item in coverage):
            issue('source_lamp_incomplete', 'The '+role+' source state is absent or blocked.')
    totals = {status: sum(item['source_triangles'] for item in coverage if item['disposition'] == status)
              for status in ('emit', 'inherited', 'excluded', 'blocked')}
    _need(sum(totals.values()) == scene.triangles, 'Source disposition accounting lost or duplicated triangles.')
    return {'schema': 1, 'kind': 'source_accounted_native_assembly_plan', 'profile': RX8_ID,
        'source_sha256': scene.digest,
        'prepared_geometry_sha256': _digest_json([{k: item[k] for k in ('id', 'prepared_part_sha256')} for item in coverage]),
        'source_parts': len(coverage), 'source_triangles': scene.triangles,
        'coverage': coverage, 'triangle_dispositions': totals,
        'all_source_parts_accounted': True, 'source_geometry_changed': False,
        'fit': frames, 'blockers': issues, 'ready_for_production': False,
        'full_source_geometry_emitted': totals['blocked'] == totals['excluded'] == totals['inherited'] == 0,
        'installable': False, 'runtime_verified': False, 'full_car_import_accepted': False}


def _program(model, mid):
    at = model.u32(0x30)+48*mid
    return model.span(model.u32(at+24), model.u32(at+28))


def _current_program(editor, mid):
    at = editor.m.u32(0x30)+48*mid
    pointer, size = struct.unpack_from('>II', editor.b, at+24)
    _need(0 <= pointer <= len(editor.b)-size, 'Authored program leaves its editor allocation.')
    return bytes(editor.b[pointer:pointer+size])


def _draws(ids, context):
    _need(all(type(sid) is int and 0 <= sid <= 255 for sid in ids), 'Draw shape index exceeds the reviewed byte operand.')
    return b''.join(bytes((59, sid, context)) for sid in ids)


def _lamp_program(lights, context):
    command = switch(7, b'', _draws(lights['headlamp'], context))+switch(
        0, switch(7, b'', _draws(lights['running'], context)), _draws(lights['brake'], context))
    table = {}
    for night in (0, 1):
        for brake in (0, 1):
            expected = (lights['headlamp'] if night else [])+(
                lights['brake'] if brake else lights['running'] if night else [])
            _need(evaluate(command+b'\0', night, brake) == expected, 'Combined source lamp state table is inconsistent.')
            table[f'night{night}_brake{brake}'] = expected
    return command, table


def compose_body_program(original, patched, lod, mid, opaque, glass, lights):
    """Replace only authenticated static regions; retain patched mechanics.

The race model-0 outer selector 0x72 is retained, with its two explicit branch
targets and default long jump relocated to the new terminal End instruction.
"""
    _need(lod in SUFFIX_START and mid in (0, 5), 'Unsupported RX8 body program.')
    _need(sha256(original) == PROGRAM_HASHES[lod][str(mid)] and len(patched) == len(original),
          'The composed body needs authenticated original-length mechanical programs.')
    cut = SUFFIX_START[lod][mid]
    _need(patched[:cut] == original[:cut] and patched[-1:] == b'\0',
          'A mechanical authoring pass changed the static prefix or terminator.')
    context = 17 if mid == 5 else 0
    driver = original[:3] if mid == 5 else b''
    _need(not driver or driver == bytes((59, 96, 17)), 'Original driver context changed.')
    lamp, states = _lamp_program(lights, context)
    prefix = driver+_draws(opaque, context)+lamp+_draws(glass, context)
    suffix = patched[cut:]
    command = prefix+suffix
    wrapper = 0
    if lod == 'race' and mid == 0:
        _need(original[:11] == bytes.fromhex('05007202000501790a0176'),
              'Original race body visibility wrapper changed.')
        payload = command[:-1]
        _need(len(payload)+3 <= 65535, 'Combined race body exceeds the preserved long-branch budget.')
        command = b'\x05\x00\x72\x02'+struct.pack('>HH', 5, len(payload)+3)+b'\x0a'+struct.pack('>H', len(payload))+payload+b'\0'
        wrapper = 11
        _need(6+struct.unpack_from('>H', command, 4)[0] == wrapper and
              8+struct.unpack_from('>H', command, 6)[0] == len(command)-1 and
              11+struct.unpack_from('>H', command, 9)[0] == len(command)-1,
              'Race body visibility/default branch relocation failed.')
    start = wrapper+len(prefix)
    _need(command[start:] == suffix, 'Composed command lost the patched mechanical suffix.')
    return command, {'model': mid, 'context': context, 'source_static_shapes': list(opaque)+list(glass),
        'lamp_states': states, 'driver_call_preserved': bool(driver),
        'mechanical_suffix_start_before': cut, 'mechanical_suffix_start_after': start,
        'mechanical_suffix_sha256': sha256(suffix), 'mechanical_suffix_bytes_preserved': True,
        'race_visibility_wrapper_preserved': lod == 'race' and mid == 0,
        'program_sha256': sha256(command)}


def wheel_state_calls(program, state):
    """Bounded decoder for this writer's reviewed wheel switch, including default."""
    _need(type(state) is int, 'Wheel state must be an integer.')
    at, calls = 0, []
    for _ in range(len(program)+1):
        _need(0 <= at < len(program), 'Wheel branch target leaves the program.')
        opcode = program[at]
        if opcode == 0:
            _need(at == len(program)-1, 'Wheel program reached an unexpected embedded End.')
            return calls
        if opcode == 59:
            _need(at+3 <= len(program), 'Truncated wheel draw.')
            calls.append((program[at+1], program[at+2])); at += 3
        elif opcode == 9:
            _need(at+2 <= len(program), 'Truncated wheel jump.')
            at += 2+program[at+1]
        elif opcode == 5:
            _need(program[at:at+4] == bytes((5, 0, 0, 2)) and at+8 <= len(program),
                  'Unreviewed wheel state selector.')
            if state in (0, 1):
                entry = at+4+state*2
                at = entry+2+struct.unpack_from('>H', program, entry)[0]
            else:
                at += 8
        else:
            raise GarageError('Unreviewed wheel command in composed program.', 'assembly_contract')
    raise GarageError('Wheel state program did not terminate.', 'assembly_contract')


def compose_wheel_program(original, lod, corner_index, shapes):
    _need(lod in ('hq', 'race') and 0 <= corner_index < 4, 'Unknown wheel route.')
    context = corner_index if lod == 'hq' else corner_index*2+1
    main = corner_index if lod == 'hq' else corner_index*3
    if lod == 'hq':
        _need(original == bytes((59, main, context, 0)), 'Original HQ wheel program changed.')
        result = _draws(shapes, context)+b'\0'
        high = None
    else:
        _need(len(original) == 22 and original[:15] == bytes((5, 0, 0, 2, 0, 4, 0, 7, 9, 11, 59, main, context, 9, 6))
              and original[-1:] == b'\0', 'Original race wheel state program changed.')
        high = wheel_state_calls(original, 1)
        _need(len(high) == 2 and wheel_state_calls(original, 0) == [(main, context)]
              and wheel_state_calls(original, 2) == [], 'Original wheel branch targets are inconsistent.')
        result = switch(0, _draws(shapes, context), original[15:21])+b'\0'
        _need(wheel_state_calls(result, 1) == high and wheel_state_calls(result, 2) == [],
              'High-speed or default wheel route changed.')
    _need(wheel_state_calls(result, 0) == [(sid, context) for sid in shapes], 'Source rim draws were lost.')
    return result, {'context': context, 'source_shapes': list(shapes),
        'original_high_speed_calls': high, 'high_speed_calls_preserved': lod == 'race',
        'default_to_end_preserved': lod == 'race', 'program_sha256': sha256(result)}


def _body_part(part, frame, role):
    rotation = np.asarray(frame['body_source_to_native_rotation'])
    origin = np.asarray(frame['body_source_origin_metres'])
    return {'id': part.id, 'role': role, 'material': part.material,
        'positions': (np.asarray(part.positions, float)-origin)@rotation.T,
        'normals': np.asarray(part.normals, float)@rotation.T,
        'uv': np.asarray(part.uv, float).copy(), 'faces': np.asarray(part.indices, np.int64).copy()}


def _emit(editor, parts, recipe, label, *, frame_kind):
    _layout(editor, recipe['fvf'])
    if recipe.get('constant_uv') is not None:
        for part in parts:
            part['native_uv'] = np.tile(recipe['constant_uv'], (len(part['positions']), 1))
    result, points = [], []
    for vb, ib, positions, pids in batches(parts, DRAW_BUDGET):
        sid = editor.shape(vb, ib, positions, recipe['fvf'], recipe['native_material'], label,
                           transparent=recipe.get('transparent', False), streamed=False)
        result.append({'shape': sid, 'source_parts': pids, 'frame_kind': frame_kind,
            'vertices': len(positions), 'triangles': len(ib)//6,
            'native_material': recipe['native_material'], 'native_fvf': recipe['fvf'],
            'vertex_sha256': sha256(vb), 'index_sha256': sha256(ib)})
        points.append(positions)
    return result, points


def _native_check(original, result, shape_records, changed_programs, *, model_points=None):
    before, after = MDL3(original), MDL3(result)
    validation = after.validate()
    _need(result[after.cut:after.cut+len(original)-before.cut] == original[before.cut:],
          'An original GPU payload was changed by combined authoring.')
    for sid in range(before.u16(0x14)):
        _need(before.span(before.u32(0x38)+48*sid, 48) == after.span(after.u32(0x38)+48*sid, 48),
              'Combined authoring modified an original packed/native shape descriptor.')
    layouts = after.fvfs()
    for row in shape_records:
        at = after.u32(0x38)+48*row['shape']
        _need(after.s16(at+2) >= 0 and after.u32(at+44) == 0,
              'An appended direct shape acquired an opaque packed binding.')
        nv, ni = after.u32(at+8), after.u32(at+20)
        stride = layouts[after.s16(at+2)]['stride']
        _need(stride == 24 and nv == row['vertices'] and ni == row['triangles']*3
              and after.s16(at+4) == row['native_material']
              and sha256(after.span(after.u32(at+12), nv*stride)) == row['vertex_sha256']
              and sha256(after.span(after.u32(at+24), ni*2)) == row['index_sha256'],
              'A serialized combined source shape differs from its encoded attributes.')
    for mid in range(before.u16(0x10)):
        old_at, new_at = before.u32(0x30)+48*mid, after.u32(0x30)+48*mid
        _need(before.span(old_at+32, 16) == after.span(new_at+32, 16), 'Native model VM entry state changed.')
        if mid not in changed_programs:
            _need(_program(before, mid) == _program(after, mid), 'An unrelated native model program changed.')
            continue
        expected = changed_programs[mid]
        _need(_program(after, mid) == expected['program'], 'Final serialized program differs from its composition plan.')
        q = after.u32(0xb0)+64*mid
        material_ids = set(struct.unpack('>'+'H'*after.u16(q+48), after.span(after.u32(q), after.u16(q+48)*2)))
        _need(set(expected['materials']) <= material_ids, 'Final model lost a referenced appended material.')
        if model_points is not None:
            bounds = np.frombuffer(after.span(after.u32(new_at+20), 96), dtype='>f4').reshape(8, 3)
            points = model_points[mid]
            epsilon = 4*np.finfo(np.float32).eps*np.maximum(1., np.max(np.abs(points), axis=0))
            _need(np.all(points.min(0) >= bounds.min(0)-epsilon)
                  and np.all(points.max(0) <= bounds.max(0)+epsilon),
                  'Final model bounds lost source body or mechanical sweep extrema.')
    _need(before.u32(0x60) == after.u32(0x60) and before.span(before.u32(0x64), before.u32(0x60))
          == after.span(after.u32(0x64), after.u32(0x60)), 'Native VM bytecode changed.')
    return validation


def _build_body(scene, inspection, donor, stream, lod, plan):
    routes = reviewed_rx8_routes(donor, lod)
    _need(sha256(stream) == RX8_ASSETS[lod+'/body_s'], 'Original body stream fingerprint changed.')
    original_groups = stream_groups(donor, stream)
    editor = Editor(donor)
    _need(editor.m.u16(0x14) == 102 and editor.banks == 2, 'Unknown original RX8 body structure.')
    material_builder = RX8BodyMaterials(scene, editor, lod, passive_source_lenses=True)
    lookup = {part.id: part for part in scene.parts}
    emitted = [row for row in plan['coverage'] if row['disposition'] == 'emit']
    static, lamp_records, all_points = [], [], []
    opaque, glass = [], []
    lights = {'headlamp': [], 'running': [], 'brake': []}
    merged = {}
    for row in emitted:
        if row['route'] != 'body_static':
            continue
        part = lookup[row['id']]
        recipe = material_builder.recipe(part.material, row['role'])
        record = _body_part(part, plan['fit'], row['role'])
        key = (recipe['native_material'], recipe['fvf'], recipe['transparent'])
        merged.setdefault(key, (recipe, []))[1].append(record)
    for key, (recipe, records) in sorted(merged.items(), key=lambda item: (item[0][2], item[0][0])):
        rows, points = _emit(editor, records, recipe, 'assembly_static', frame_kind='source_body_reference')
        static.extend(rows); all_points.extend(points)
        (glass if recipe['transparent'] else opaque).extend(row['shape'] for row in rows)
    for row in emitted:
        if row['route'] != 'body_static' or row['role'] not in LAMP_ROLES:
            continue
        part = lookup[row['id']]
        kinds = ('headlamp',) if row['role'] == 'headlight' else ('running', 'brake') if row['role'] == 'taillight' else ('brake',)
        for kind in kinds:
            recipe = emitter_recipe(material_builder, part.material, kind)
            record = _body_part(part, plan['fit'], row['role'])
            record['positions'] += record['normals']*.00075
            rows, points = _emit(editor, [record], recipe, 'assembly_lamp_'+kind, frame_kind='source_body_reference')
            for item in rows:
                item.update(kind=kind, base_source_ownership='intentional_lamp_state_redraw', normal_offset_metres=.00075)
            lamp_records.extend(rows); all_points.extend(points)
            lights[kind].extend(item['shape'] for item in rows)
    components, mechanical_bounds = [], []
    for row in emitted:
        if row['route'] != 'body_mechanical':
            continue
        part = lookup[row['id']]
        recipe = material_builder.recipe(part.material, 'body')
        item = {'id': part.id, 'role': row['role'], 'corner': row['corner'],
            'positions': part.positions, 'normals': part.normals, 'uv': part.uv, 'faces': part.indices,
            'native_material': recipe['native_material'], 'native_fvf': recipe['fvf'],
            'transparent': recipe['transparent']}
        if recipe['constant_uv'] is not None:
            item['native_uv'] = np.tile(recipe['constant_uv'], (len(part.positions), 1))
        components.append(item)
        frame = plan['fit']['mechanical_frames'][row['role']+':'+row['corner']]
        mechanical_bounds.append(prepare_component(item, frame)['body_reference_points'])
    mechanics = append_dynamic_components(editor, components, routes, plan['fit']['mechanical_frames']) if components else {
        'shapes': [], 'programs': [], 'source_triangles': 0, 'native_triangles': 0}
    all_points.extend(mechanical_bounds)
    _need(bool(static) and bool(all_points), 'A combined assembly candidate needs source static geometry.')
    # Editor.model() reads the ORIGINAL model material set/bounds each time.
    # The final write must receive the full union, including mechanical sweeps.
    shapes = static+lamp_records+mechanics['shapes']
    material_ids = {row['native_material'] for row in shapes}
    points = np.vstack(all_points)
    program_checks, final_programs = [], {}
    for mid in (0, 5):
        original, patched = _program(editor.m, mid), _current_program(editor, mid)
        allowed = {entry['offset'] for report in mechanics['programs'] if report['model'] == mid for entry in report['shape_operands']}
        _need(len(original) == len(patched) and all(a == b or offset in allowed for offset, (a, b) in enumerate(zip(original, patched))),
              'Intermediate mechanical composition changed more than authenticated shape operands.')
        program, check = compose_body_program(original, patched, lod, mid, opaque, glass, lights)
        editor.model(mid, program, material_ids, points)
        final_programs[mid] = {'program': program, 'materials': sorted(material_ids)}
        check['mechanical_draws'] = [{**entry, 'composed_operand_offset': check['mechanical_suffix_start_after']+
            entry['offset']-check['mechanical_suffix_start_before']} for report in mechanics['programs']
            if report['model'] == mid for entry in report['shape_operands']]
        program_checks.append(check)
    data, resources = editor.finish()
    validation = _native_check(donor, data, shapes, final_programs, model_points={0: points, 5: points})
    output_groups = stream_groups(data, stream)
    _need([(g['label'], [(r['flags'], r['stored']) for r in g['records']]) for g in output_groups] ==
          [(g['label'], [(r['flags'], r['stored']) for r in g['records']]) for g in original_groups],
          'The combined candidate changed an original body stream group.')
    for check in program_checks:
        program = _program(MDL3(data), check['model'])
        for entry in check['mechanical_draws']:
            at = entry['composed_operand_offset']
            _need(program[at-1:at+2] == bytes((59, entry['new_shape'], entry['context_preserved'])),
                  'Serialized mechanical draw lost its native context during static-prefix composition.')
    expected = sum(row['source_triangles'] for row in emitted if row['route'] in ('body_static', 'body_mechanical'))
    _need(sum(row['triangles'] for row in static+mechanics['shapes']) == expected,
          'Combined body/mechanical source triangle coverage differs from the plan.')
    return data, {'native_sha256': sha256(data), 'original_body_sha256': sha256(donor),
        'body_s_sha256': sha256(stream), 'body_s_unchanged': True,
        'static_shapes': static, 'lamp_redraw_shapes': lamp_records, 'mechanical': mechanics,
        'programs': program_checks, 'base_source_triangles': expected,
        'lamp_redraw_triangles': sum(row['triangles'] for row in lamp_records),
        'source_material_recipes': material_builder.report, 'native_resources': resources,
        'original_shape_descriptors_preserved': True, 'original_gpu_payload_preserved': True,
        'native_vm_and_unrelated_models_preserved': True, 'final_material_and_sweep_union_verified': True,
        'native_validation': validation}


def _build_wheel(scene, donor, lod, plan):
    _need(sha256(donor) == RX8_ASSETS[lod+'/wheel'], 'Original RX8 wheel fingerprint changed.')
    editor = Editor(donor)
    _need((editor.m.u16(0x14), editor.m.u16(0x10), editor.banks) ==
          ((8, 8, 1) if lod == 'hq' else (16, 8, 1)), 'Unknown original wheel structure.')
    original_shadows = shadow_reference_geometry(editor, lod)
    source_materials = Materials(scene, editor)
    lookup = {part.id: part for part in scene.parts}
    emitted = [row for row in plan['coverage'] if row['disposition'] == 'emit' and row['route'] == 'wheel_rim']
    shape_records, program_checks, final_programs = [], [], {}
    images = {}
    for ci, corner in enumerate(CORNERS):
        frame = plan['fit']['wheel_frames'][corner]
        center = np.asarray(frame['source_center_metres'])
        basis = np.asarray(frame['source_to_local_rotation'])
        offset = np.array([frame['axial_offset_metres'], 0., 0.])
        _need(np.linalg.det(basis) > 0 and frame['radial_offset_metres'] == [0., 0.],
              'Wheel geometry needs the shared proper rigid local frame.')
        template = ci if lod == 'hq' else ci*2
        sampler_keys = dict(editor.texture_keys(template))
        diffuse = sampler_keys.get('diffuseMapSampler')
        _need(diffuse is not None and sampler_keys.get('ambientMapSampler') == diffuse,
              'Original wheel diffuse/ambient aliases changed.')
        new_shapes, corner_points, materials = [], [], set()
        for row in emitted:
            if row['corner'] != corner:
                continue
            part = lookup[row['id']]
            source_material = scene.materials[part.material]
            if part.material not in images:
                image = source_materials.source_image(source_material)
                image.putalpha(255)  # glTF OPAQUE ignores source texture alpha.
                images[part.material] = editor.image(image, 'assembly_rim_source_'+str(part.material))
            bindings = {name: images[part.material] for name, pid in sampler_keys.items() if pid == diffuse}
            native_material = editor.clone_material(template, 'assembly_'+corner+'_'+part.id, bindings)
            recipe = {'native_material': native_material, 'fvf': editor.fvf(template), 'transparent': False,
                      'constant_uv': (.5, .5) if source_material['base_texture'] is None else None}
            record = {'id': part.id, 'positions': (np.asarray(part.positions, float)-center)@basis.T+offset,
                'normals': np.asarray(part.normals, float)@basis.T,
                'uv': np.asarray(part.uv, float).copy(), 'faces': np.asarray(part.indices, np.int64).copy()}
            records, points = _emit(editor, [record], recipe, 'assembly_rim_'+corner, frame_kind='native_wheel_local')
            for item in records:
                item.update(corner=corner, role=row['role'], frame=copy.deepcopy(frame))
            shape_records.extend(records); corner_points.extend(points)
            new_shapes.extend(item['shape'] for item in records); materials.add(native_material)
        original = _program(editor.m, ci)
        # A missing/blocked corner emits no low-speed source draw and stays a
        # visible capability blocker. It never silently presents the donor rim.
        program, check = compose_wheel_program(original, lod, ci, new_shapes)
        if not corner_points:
            corner_points = [np.zeros((1, 3))]
        editor.model(ci, program, materials, np.vstack(corner_points))
        final_programs[ci] = {'program': program, 'materials': sorted(materials)}
        program_checks.append({'corner': corner, **check})
    data, resources = editor.finish()
    validation = _native_check(donor, data, shape_records, final_programs)
    _need(shadow_reference_geometry(Editor(data), lod) == original_shadows,
          'Retained native wheel-shadow geometry/materials changed.')
    expected = sum(row['source_triangles'] for row in emitted)
    _need(sum(row['triangles'] for row in shape_records) == expected, 'Source rim/valve triangles were lost.')
    return data, {'native_sha256': sha256(data), 'original_wheel_sha256': sha256(donor),
        'shapes': shape_records, 'programs': program_checks, 'base_source_triangles': expected,
        'original_shape_descriptors_preserved': True, 'original_gpu_payload_preserved': True,
        'native_shadow_geometry_preserved': True, 'race_high_speed_and_default_routes_preserved': lod == 'race',
        'source_dimensions_preserved': True, 'native_resources': resources, 'native_validation': validation,
        'rendered_tyre_fit_verified': False, 'race_mount_verified': False}


def build_assembly_lod(scene, inspection, donor_body, donor_stream, donor_wheel, lod, *,
                       fit_decision, dispositions=None):
    """Return ({body, body_s, wheel}, receipt) for one isolated diagnostic LOD.

Inputs are original owned donor bytes. No filesystem writes, game calls,
database edits, source-name matching or production-profile changes occur.
Blocked source surfaces are enumerated and never converted to another role.
"""
    _need(lod in ('hq', 'race'), 'Choose HQ or race assembly LOD.')
    for name, value in (('body', donor_body), ('body_s', donor_stream), ('wheel', donor_wheel)):
        _need(isinstance(value, bytes) and sha256(value) == RX8_ASSETS[lod+'/'+name],
              'Use the exact original RX8 '+lod+'/'+name+' asset, not a prior lab output.')
    plan = assembly_plan(scene, inspection, fit_decision=fit_decision, dispositions=dispositions)
    body, body_receipt = _build_body(scene, inspection, donor_body, donor_stream, lod, plan)
    wheel, wheel_receipt = _build_wheel(scene, donor_wheel, lod, plan)
    total = body_receipt['base_source_triangles']+wheel_receipt['base_source_triangles']
    _need(total == plan['triangle_dispositions']['emit'], 'Combined LOD output differs from complete source accounting.')
    assets = {'body': body, 'body_s': donor_stream, 'wheel': wheel}
    receipt = {'schema': 1, 'kind': 'combined_rx8_assembly_diagnostic', 'lod': lod, 'passed': True,
        'plan': plan, 'body': body_receipt, 'wheel': wheel_receipt,
        'asset_sha256': {name: sha256(data) for name, data in assets.items()},
        'emitted_base_source_triangles': total, 'all_source_parts_accounted': True,
        'body_and_mechanical_draws_composed_together': True,
        'static_and_wheel_writers_share_frame_contract': True,
        'fit_contract_sha256': _digest_json(plan['fit']),
        'scope': 'Source-accounted combined native diagnostic. Passed means serialization/ownership checks passed, not solved fit, complete material/motion support or game acceptance.',
        'ready_for_production': False, 'installable': False, 'runtime_verified': False,
        'full_car_import_accepted': False, 'physics_changed': False, 'game_launched': False}
    return assets, receipt


def build_assembly(scene, inspection, original_assets, *, fit_decision, dispositions=None):
    """Return all eight native files and one receipt; inherited assets are explicit."""
    _need(isinstance(original_assets, Mapping) and set(original_assets) == set(RX8_ASSETS),
          'Supply the complete eight original owned RX8 assets.')
    for name, digest in RX8_ASSETS.items():
        _need(isinstance(original_assets[name], bytes) and sha256(original_assets[name]) == digest,
              'Original RX8 asset identity changed: '+name+'.')
    output = {name: original_assets[name] for name in ('info', 'interior')}
    lods = {}
    for lod in ('hq', 'race'):
        assets, receipt = build_assembly_lod(scene, inspection, original_assets[lod+'/body'],
            original_assets[lod+'/body_s'], original_assets[lod+'/wheel'], lod,
            fit_decision=fit_decision, dispositions=dispositions)
        output.update({lod+'/'+name: data for name, data in assets.items()}); lods[lod] = receipt
    _need(lods['hq']['fit_contract_sha256'] == lods['race']['fit_contract_sha256'],
          'HQ and race used different source assembly contracts.')
    return output, {'schema': 1, 'kind': 'combined_rx8_assembly_diagnostic', 'passed': True,
        'source_sha256': scene.digest, 'original_asset_sha256': dict(RX8_ASSETS),
        'output_asset_sha256': {name: sha256(data) for name, data in output.items()},
        'inherited_unchanged_assets': ['info', 'interior', 'hq/body_s', 'race/body_s'],
        'info_projectors_and_cockpit_fit_verified': False, 'lods': lods,
        'installable': False, 'ready_for_production': False, 'runtime_verified': False,
        'full_car_import_accepted': False, 'physics_changed': False, 'game_launched': False}
