"""Read-only audit of packed MDL3 car-stream bindings and shared payloads.

The PMSH reference and smooth-mesh descriptor layouts come from PDTools at
2a9637d8c36100e4c211d73cd8140a9d690578d2. The CPU-row mapping and the bounds
transpose were independently checked against both hash-pinned RX-8 donors.
The complete car-stream loader is not documented by those sources. In
particular, row +0x10 remains opaque and vertex payload lengths are not inferred
from neighbouring allocations. This module never clears or frees data.
"""
from __future__ import annotations

from collections import Counter, defaultdict
import math
import struct

from .errors import GarageError
from .native import MDL3
from .util import sha256


REFERENCE_COMMIT = '2a9637d8c36100e4c211d73cd8140a9d690578d2'
REFERENCE_BASE = f'https://github.com/Nenkai/PDTools/blob/{REFERENCE_COMMIT}/PDTools.Files/Models/PS3/ModelSet3/'
REFERENCES = {
    'packed_reference': REFERENCE_BASE + 'Shapes/MDL3ShapePackedMeshRef.cs',
    'packed_header': REFERENCE_BASE + 'PackedMesh/PackedMeshHeader.cs',
    'packed_mesh': REFERENCE_BASE + 'PackedMesh/PMSHMesh.cs',
    'smooth_mesh_descriptor': REFERENCE_BASE + 'PackedMesh/PMSHSmoothMeshData.cs',
}


def _require(condition, message):
    if not condition:
        raise GarageError(message, 'native_packed_stream')


def audit_packed_streams(model: bytes, cpu: bytes, *, selected_slots=None):
    """Describe packed bindings, typed ranges, and cross-shape pointer aliases.

    Malformed tables/ranges raise GarageError. A well-formed stream that no
    longer matches the model returns explicit ``issues``: this permits comparing
    an isolated inline conversion with its donor stream without accepting it.
    Bounds equality is checked only for one-component bindings, the layout
    verified on the reviewed donors; multi-component bounds remain unverified.
    Shared vertex pointers have unknown byte lengths and are never reclaimable.
    """
    m = MDL3(model)
    shape_count, shape_table = m.u16(0x14), m.u32(0x38)
    m.span(shape_table, shape_count * 48, m.cut)
    _require(len(cpu) >= 32, 'Packed CPU stream header is truncated.')
    cpu_size, combined_size, gpu_size = struct.unpack_from('>III', cpu, 4)
    _require(cpu_size == len(cpu) and combined_size == cpu_size + gpu_size,
             'Packed CPU/GPU allocation sizes disagree with the decoded stream.')
    count, texture_count = struct.unpack_from('>HH', cpu, 20)
    table, texture_table = struct.unpack_from('>II', cpu, 24)
    _require(table >= 32 and table % 16 == 0 and table + count * 32 <= len(cpu),
             'Packed shape binding table leaves the CPU stream.')
    if texture_count:
        _require(texture_table >= 32 and texture_table + texture_count * 16 <= len(cpu),
                 'Packed texture binding table leaves the CPU stream.')
    rows = {}
    for index in range(count):
        row = struct.unpack_from('>8I', cpu, table + index * 32)
        _require(row[0] < shape_count and row[0] not in rows,
                 'Packed stream has an invalid or duplicated shape ID.')
        rows[row[0]] = row

    selected = None if selected_slots is None else tuple(selected_slots)
    if selected is not None:
        _require(len(set(selected)) == len(selected)
                 and all(type(sid) is int and sid in rows for sid in selected),
                 'Selected packed-stream slots are missing or repeated.')
    selected_set = set(rows) if selected is None else set(selected)
    classifications = {}
    packed_rows = []
    references = []
    issues = []
    packed_header = None

    def issue(sid, code, message):
        issues.append({'shape': sid, 'code': code, 'message': message})

    def span(offset, length, label):
        # Zero is the null offset. Do not validate it as a readable CPU address.
        _require(offset >= 32 and length >= 0 and offset + length <= len(cpu),
                 f'{label} leaves the CPU stream.')
        return cpu[offset:offset + length]

    def reference(sid, component, field, offset, length=None):
        if not offset:
            _require(not length, f'Shape {sid} {field} has a size but no pointer.')
            return
        span(offset, max(1, length or 0), f'Shape {sid} {field}')
        references.append({'shape': sid, 'component': component, 'field': field,
                           'offset': offset, 'byte_length': length,
                           'full_extent_known': length is not None})

    def native_mesh(sid):
        nonlocal packed_header
        sp = shape_table + sid * 48
        fid, vertex, index, ref = m.s16(sp + 2), m.u32(sp + 12), m.u32(sp + 24), m.u32(sp + 44)
        if fid != -1:
            issue(sid, 'ordinary_fvf_with_packed_binding',
                  'The stream still has packed data while the model uses an ordinary FVF.')
        if vertex or index:
            issue(sid, 'inline_geometry_with_packed_binding',
                  'The model has inline geometry while its stream still has packed data.')
        if not ref:
            issue(sid, 'missing_packed_reference',
                  'The stream has packed data but the model has no PMSH reference.')
            return None
        # PDTools reads 12 floats, an additional word, THEN the mesh index.
        # The older public overview's +0x30 index is not this donor's layout.
        m.span(ref, 0x38, m.cut)
        mesh_index = m.u32(ref + 0x34)
        if packed_header is None:
            hp = m.u32(0xd0)
            _require(hp > 0 and m.span(hp, 4, m.cut) == b'PMSH',
                     'Packed stream has no valid native PMSH header.')
            m.span(hp, 0x34, m.cut)
            mesh_count, mesh_table = m.u16(hp + 16), m.u32(hp + 24)
            m.span(mesh_table, mesh_count * 48, m.cut)
            packed_header = {'offset': hp, 'mesh_count': mesh_count, 'mesh_table': mesh_table}
        _require(mesh_index < packed_header['mesh_count'],
                 f'Shape {sid} PMSH reference index leaves its native table.')
        mp = packed_header['mesh_table'] + mesh_index * 48
        return {'reference_offset': ref, 'index_field_offset': 0x34,
                'mesh_index': mesh_index, 'mesh_offset': mp,
                'component_count': model[mp + 6]}

    for sid, row in rows.items():
        if not any(row[1:]):
            classifications[sid] = 'empty_binding'
            continue
        if not any(row[4:]):
            classifications[sid] = 'ordinary_streamed' if row[1] and row[2] else 'other_unpacked_binding'
            continue
        classifications[sid] = 'packed_streamed'
        _require(not row[1] and not row[2],
                 f'Shape {sid} mixes ordinary stream pointers and packed metadata.')
        _require(all(row[3:7]) and 1 <= row[7] <= 255,
                 f'Shape {sid} has an incomplete packed-stream binding.')
        bounds, opaque, descriptor_table, soa_bounds, component_count = row[3:]
        reference(sid, None, 'shape_bounds_aos', bounds, 96)
        # The 16-byte block is observed, not fully decoded. Do not infer its
        # extent or treat its words as relocatable CPU pointers.
        reference(sid, None, 'opaque_binding_0x10', opaque)
        opaque_bytes = span(opaque, 16, f'Shape {sid} opaque binding block')
        reference(sid, None, 'smooth_mesh_descriptors', descriptor_table, component_count * 48)
        reference(sid, None, 'component_bounds_soa', soa_bounds, component_count * 96)
        aos = span(bounds, 96, f'Shape {sid} AoS bounds')
        soa = span(soa_bounds, component_count * 96, f'Shape {sid} SoA bounds')
        _require(all(math.isfinite(x) for x in struct.unpack('>24f', aos)),
                 f'Shape {sid} AoS bounds contain a nonfinite number.')
        _require(all(math.isfinite(x) for x in struct.unpack('>' + 'f' * (24 * component_count), soa)),
                 f'Shape {sid} SoA bounds contain a nonfinite number.')
        bounds_match = None
        if component_count == 1:
            words = struct.unpack('>24I', aos)
            expected = [words[(block * 4 + vertex) * 3 + axis]
                        for block in range(2) for axis in range(3) for vertex in range(4)]
            bounds_match = struct.pack('>24I', *expected) == soa
            if not bounds_match:
                issue(sid, 'bounds_pair_mismatch',
                      'Single-component AoS and SoA bounds differ after the verified transpose.')
        native = native_mesh(sid)
        if native is not None and native['component_count'] != component_count:
            issue(sid, 'native_component_count_mismatch',
                  'The stream component count differs from the referenced native PMSH mesh.')
        descriptors = []
        for component in range(component_count):
            at = descriptor_table + component * 48
            pointers = struct.unpack_from('>8I', cpu, at)
            tail = struct.unpack_from('>7H2B', cpu, at + 32)
            packed_count, nonpacked_count = tail[1], tail[3]
            _require(not packed_count or pointers[0],
                     f'Shape {sid} packed vertices have a count but no pointer.')
            _require(not nonpacked_count or pointers[3],
                     f'Shape {sid} non-packed vertices have a count but no pointer.')
            index_bytes, command_bytes = tail[5] * 16, tail[6] * 16
            for field, offset, length in (
                ('packed_vertex', pointers[0], None),
                ('unknown_descriptor_0x04', pointers[1], None),
                ('unknown_descriptor_0x08', pointers[2], None),
                ('nonpacked_vertex', pointers[3], None),
                ('unknown_descriptor_0x10', pointers[4], None),
                ('packed_index', pointers[5], index_bytes),
                ('packed_commands', pointers[6], command_bytes),
            ):
                reference(sid, component, field, offset, length)
            descriptors.append({'component': component, 'offset': at,
                                'packed_vertex_count': packed_count,
                                'nonpacked_vertex_count': nonpacked_count,
                                'index_pointer': pointers[5], 'index_bytes': index_bytes,
                                'commands_pointer': pointers[6], 'commands_bytes': command_bytes,
                                'face_index_bits': tail[7],
                                'uninterpreted_pointer_fields': [pointers[1], pointers[2], pointers[4]],
                                'uninterpreted_word_0x1c': pointers[7]})
        packed_rows.append({'shape': sid, 'component_count': component_count,
                            'bounds_aos_pointer': bounds, 'bounds_soa_pointer': soa_bounds,
                            'single_component_bounds_match': bounds_match,
                            'opaque_binding_pointer': opaque,
                            'opaque_binding_prefix_bytes': 16,
                            'opaque_binding_prefix_sha256': sha256(opaque_bytes),
                            'native_packed_mesh': native, 'descriptors': descriptors})

    by_target = defaultdict(list)
    for ref in references:
        by_target[ref['offset']].append(ref)
    shared = []
    for offset, refs in sorted(by_target.items()):
        owners = sorted({ref['shape'] for ref in refs})
        if len(owners) > 1:
            shared.append({'offset': offset, 'shapes': owners, 'references': refs,
                           'selected_and_retained': bool(set(owners) & selected_set)
                           and bool(set(owners) - selected_set)})
    selected_packed = [row['shape'] for row in packed_rows if row['shape'] in selected_set]
    return {
        'schema': 'garagehub.packed_stream_audit.v1',
        'validation_scope': 'Packed bindings only; ordinary geometry is classified but not validated here.',
        'reference_graph_complete': False,
        'alias_detection_scope': 'Shared target offsets in packed bindings; partial overlaps are not classified.',
        'cpu_bytes': len(cpu), 'binding_count': count,
        'binding_class_counts': dict(sorted(Counter(classifications.values()).items())),
        'binding_classes': {str(sid): kind for sid, kind in classifications.items()},
        'packed_binding_count': len(packed_rows), 'selected_packed_slots': selected_packed,
        'packed_rows': packed_rows, 'payload_references': references,
        'shared_payload_targets': shared,
        'selected_to_retained_shared_targets': [x['offset'] for x in shared if x['selected_and_retained']],
        'issues': issues, 'model_binding_consistent': not issues,
        'all_payload_extents_known': False,
        'payload_reclamation_allowed': False,
        'opaque_binding_semantics_reviewed': False,
        'multi_component_bounds_verified': False,
        'source_references': REFERENCES,
        'actual_game_render_verified': False, 'installable': False,
    }
