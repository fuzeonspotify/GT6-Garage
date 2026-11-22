"""Final-byte validation for this profile's generated ordinary body draws.

The supported emitter writes 24-byte interleaved vertices and uint16 triangle
lists into the //0 CPU stream, after its reviewed 108160-byte native prefix.
This guard does not decode inherited packed meshes, secondary vertex arrays,
alternate geometry groups, opaque native state, or runtime loading behavior.
"""
from __future__ import annotations

import struct
import numpy as np

from .body_streams import groups
from .errors import GarageError
from .native import MDL3, corners
from .util import sha256


_PREFIX_BYTES = 108160
_FIELDS = {'position': (0, 3, 2, 0), 'normal': (12, 1, 6, 0),
           'map12': (16, 2, 2, 0)}


def _need(condition, message, shape=None):
    if not condition:
        label = f'Generated body shape {shape}' if shape is not None else 'Generated body stream'
        raise GarageError(f'{label}: {message} Rebuild with the reviewed body profile; '
                          'this body candidate was not accepted.',
                          'ordinary_stream_contract', {'shape': shape})


def _integer(value):
    return isinstance(value, int) and not isinstance(value, bool)


def _span(data, start, size, label, shape=None):
    _need(0 <= start <= len(data)-size and size >= 0,
          f'{label} leaves its serialized allocation.', shape)
    return data[start:start+size]


def validate_generated_streams(model, stream, emitted_shapes):
    """Validate final serialized bytes against emitted streamed-shape receipts.

Each receipt is one Editor.shape/finish record with id, streamed=True,
vertices, triangles, material, fvf and vertex/index SHA256. These receipts
identify the generated subset; they do not authorize inspecting another
native geometry representation as if it used this emitter's contract.
Inputs are read-only. The return value is evidence of these structural checks,
not visual or runtime acceptance.
"""
    try:
        return _validate(bytes(model), bytes(stream), emitted_shapes)
    except GarageError:
        raise
    except (KeyError, TypeError, ValueError, IndexError, OverflowError, struct.error) as exc:
        raise GarageError('Generated body stream could not be validated from its final bytes. '
                          'Rebuild with the reviewed body profile; this body candidate was not accepted.',
                          'ordinary_stream_contract') from exc


def _validate(model, stream, emitted_shapes):
    emitted = list(emitted_shapes)
    _need(bool(emitted), 'no emitted streamed shapes were supplied for validation.')
    selected = []
    for row in emitted:
        _need(isinstance(row, dict) and _integer(row.get('id')),
              'an emitted shape receipt has no integer ID.')
        sid = row['id']
        _need(row.get('streamed') is True, 'the selected receipt is not a streamed draw.', sid)
        for key in ('vertices', 'triangles', 'material', 'fvf'):
            _need(_integer(row.get(key)), f'the emitted {key} receipt is invalid.', sid)
        for key in ('vertex_sha256', 'index_sha256'):
            value = row.get(key)
            _need(isinstance(value, str) and len(value) == 64
                  and all(c in '0123456789abcdef' for c in value),
                  f'the emitted {key} receipt is invalid.', sid)
        selected.append(sid)
    _need(len(set(selected)) == len(selected), 'the emitted shape list contains duplicate IDs.')

    m = MDL3(model)
    shape_count, shape_table = m.u16(0x14), m.u32(0x38)
    m.span(shape_table, shape_count*48, m.cut)
    layouts = m.fvfs()
    parsed = groups(model, stream)
    mains = [g for g in parsed if g['label'] == '//0']
    _need(len(mains) == 1, 'exactly one reviewed //0 main stream is required.')
    main = mains[0]
    cpu = b''.join(r['raw'] for r in main['records'][:main['na']])
    gpu_bytes = sum(len(r['raw']) for r in main['records'][main['na']:])
    _need(len(cpu) >= _PREFIX_BYTES, 'the CPU allocation is smaller than its reviewed prefix.')
    u32 = lambda at: struct.unpack('>I', _span(cpu, at, 4, 'CPU header'))[0]
    u16 = lambda at: struct.unpack('>H', _span(cpu, at, 2, 'CPU header'))[0]
    _need((u32(4), u32(8), u32(12)) == (len(cpu), len(cpu)+gpu_bytes, gpu_bytes),
          'the declared CPU/GPU allocation differs from the decoded spans.')
    count, textures, table, texture_table = u16(20), u16(22), u32(24), u32(28)
    _need(table == 32 and table+count*32 <= _PREFIX_BYTES
          and texture_table+textures*16 <= _PREFIX_BYTES,
          'the binding/texture tables leave the reviewed native prefix.')
    bindings = {}
    for i in range(count):
        values = struct.unpack('>8I', _span(cpu, table+i*32, 32, 'binding table'))
        bindings.setdefault(values[0], []).append(values)

    ranges = []
    checks = []
    for expected in emitted:
        sid = expected['id']
        _need(0 <= sid < shape_count, 'the ID leaves the serialized shape table.', sid)
        candidates = bindings.get(sid, [])
        _need(len(candidates) == 1, 'exactly one main-stream binding is required.', sid)
        binding = candidates[0]
        _need(not any(binding[4:]), 'the binding contains fields outside the ordinary emitter contract.', sid)
        at = shape_table+sid*48
        _need(m.u16(at) in (0x1809, 0x1819, 0x3809, 0x3819),
              'the flags differ from the supported triangle-list emitter.', sid)
        _need(not any(model[at+6:at+8]+model[at+16:at+20]+model[at+28:at+38])
              and m.u32(at+44) == 0,
              'the shape contains extension or packed-reference data outside this emitter.', sid)
        _need(m.u32(at+12) == m.u32(at+24) == m.u32(at+40) == 0,
              'streamed geometry unexpectedly also has direct metadata pointers.', sid)
        fid, material = m.s16(at+2), m.s16(at+4)
        _need(fid == expected['fvf'] and material == expected['material'] and 0 <= fid < len(layouts),
              'the FVF/material differs from the emitted receipt.', sid)
        layout = layouts[fid]
        fields = {f['name']: (f['offset'], f['components'], f['format'], f['stream'])
                  for f in layout['fields']}
        _need(layout['stride'] == 24 and len(layout['fields']) == len(_FIELDS) and fields == _FIELDS,
              'only the reviewed 24-byte position/packed-normal/UV stream-0 layout is supported.', sid)
        nv, ni, nt = m.u32(at+8), m.u32(at+20), m.u16(at+38)
        _need(0 < nv <= 65535 and 0 < nt <= 20000 and ni == nt*3,
              'the serialized vertex/index/triangle counts are invalid.', sid)
        _need(nv == expected['vertices'] and nt == expected['triangles'],
              'the serialized counts differ from the emitted receipt.', sid)
        vp, ip, bp = binding[1:4]
        payloads = (('vertices', vp, nv*24, 128), ('indices', ip, ni*2, 16),
                    ('bounds', bp, 96, 16))
        buffers = {}
        for kind, pointer, size, alignment in payloads:
            _need(pointer >= _PREFIX_BYTES and pointer % alignment == 0,
                  f'{kind} points into the native prefix or violates emitter alignment.', sid)
            buffers[kind] = _span(cpu, pointer, size, kind, sid)
            for start, end, owner, other_kind in ranges:
                _need(pointer+size <= start or pointer >= end,
                      f'{kind} overlaps generated shape {owner} {other_kind}.', sid)
            ranges.append((pointer, pointer+size, sid, kind))
        vertex, index, bounds = buffers['vertices'], buffers['indices'], buffers['bounds']
        indices = np.frombuffer(index, dtype='>u2')
        _need(int(indices.max()) < nv, 'an index refers beyond the emitted vertex count.', sid)
        points = np.ndarray((nv,3), dtype='>f4', buffer=vertex, strides=(24,4))
        uv = np.ndarray((nv,2), dtype='>f4', buffer=vertex, offset=16, strides=(24,4))
        _need(np.isfinite(points).all(), 'the serialized positions contain NaN or infinity.', sid)
        _need(np.isfinite(uv).all(), 'the serialized UVs contain NaN or infinity.', sid)
        box = np.frombuffer(bounds, dtype='>f4').reshape(8,3)
        _need(np.isfinite(box).all(), 'the serialized bounds contain NaN or infinity.', sid)
        lo, hi = box.min(0), box.max(0)
        ordered = np.frombuffer(corners([lo,hi]), dtype='>f4').reshape(8,3)
        _need(np.array_equal(box, ordered), 'the eight bounding corners are not in emitter order.', sid)
        # Numeric float32 rounding allowance only, not a physical fit tolerance.
        tolerance = 4*np.finfo(np.float32).eps*max(1., float(np.max(np.abs(points))))
        _need(np.all(lo <= points.min(0)+tolerance) and np.all(hi >= points.max(0)-tolerance),
              'the serialized bounds do not enclose the emitted positions.', sid)
        vertex_hash, index_hash = sha256(vertex), sha256(index)
        _need(vertex_hash == expected['vertex_sha256'] and index_hash == expected['index_sha256'],
              'the final geometry bytes differ from the emitted receipt.', sid)
        checks.append({'shape':sid,'fvf':fid,'vertices':nv,'triangles':nt,
                       'vertex_pointer':vp,'index_pointer':ip,'bounds_pointer':bp,
                       'vertex_sha256':vertex_hash,'index_sha256':index_hash,
                       'positions_finite':True,'uv_finite':True,
                       'indices_in_range':True,'ordered_bounds_enclose_positions':True})
    return {'schema':1,'passed':True,'group':'//0','shapes_checked':len(checks),
            'shape_ids':selected,'checks':checks,'main_cpu_bytes':len(cpu),
            'other_main_bindings_not_validated':sorted(set(bindings)-set(selected)),
            'other_groups_geometry_not_validated':[g['label'] for g in parsed if g is not main],
            'scope':'Final serialized bytes of the explicitly emitted ordinary body shapes only; inherited, packed, secondary-array and alternate-group geometry are outside scope.',
            'inputs_changed':False,'runtime_verified':False}
