"""Reject unreviewed native stream/inline ownership collisions in RX-8 labs.

Zero vertex/index stream pointers do NOT mean an inactive binding: RX-8
rows 0..39 carry packed-mesh descriptors and two bounds layouts. Preserve
their opaque fields and shared payloads; these experiments remain NOT INSTALLABLE.
"""
from __future__ import annotations
import struct
from .errors import GarageError
from .native import MDL3
from .body_streams import groups
from .util import sha256
from .packed_streams import audit_packed_streams

def parse_cpu_rows(cpu: bytes, slots, *, shape_count=102, count_required=88):
    if len(cpu)<32:
        raise GarageError('RX-8 CPU stream header is truncated.','rx8_stream_owner')
    count=struct.unpack_from('>H',cpu,20)[0]
    table=struct.unpack_from('>I',cpu,24)[0]
    if count!=count_required or table<32 or table+32*count>len(cpu) or table%16:
        raise GarageError('RX-8 shape binding table layout changed.','rx8_stream_owner')
    rows={}
    for i in range(count):
        row=struct.unpack_from('>8I',cpu,table+i*32)
        sid=row[0]
        if sid in rows or sid>=shape_count:
            raise GarageError('Duplicated or out-of-range streamed shape ID.','rx8_stream_owner')
        rows[sid]=row
    chosen=tuple(slots)
    if not chosen or len(set(chosen))!=len(chosen) or any(not isinstance(s,int) or s<0 or s>=40 for s in chosen):
        raise GarageError('Unknown or repeated RX-8 original static slot.','rx8_stream_owner')
    metadata={}
    for sid in chosen:
        row=rows.get(sid)
        if row is None:
            raise GarageError('RX-8 target slot has no reviewed stream binding.','rx8_stream_owner')
        # An isolated nonzero vertex or index pointer is also a collision.
        if row[1] or row[2]:
            raise GarageError('RX-8 original static slot already has live geometry in body_s. Refusing dual ownership.','rx8_stream_owner')
        metadata[str(sid)]=list(row[3:])
    return {
        'selected_slots':list(chosen),'stream_rows':count,
        'live_vertex_index_conflicts':0,
        'slots_with_nonzero_auxiliary_metadata':[sid for sid in chosen if any(metadata[str(sid)])],
        'auxiliary_metadata_fields':metadata,
        'auxiliary_metadata_unreviewed':any(any(v) for v in metadata.values()),
        'actual_game_render_verified':False,
        'installable':False
    }

def check_native_stream_ownership(model:bytes,stream:bytes,slots):
    m=MDL3(model)
    if m.u16(0x14)!=102:
        raise GarageError('Unreviewed RX-8 shape count.','rx8_stream_owner')
    main=[g for g in groups(model,stream) if g['label']=='//0']
    if len(main)!=1:
        raise GarageError('Expected exactly one RX-8 main stream.','rx8_stream_owner')
    cpu=b''.join(r['raw'] for r in main[0]['records'][:main[0]['na']])
    result=parse_cpu_rows(cpu,slots,shape_count=m.u16(0x14))
    for sid in result['selected_slots']:
        sp=m.u32(0x38)+sid*48
        if m.u32(sp+12) or m.u32(sp+24):
            raise GarageError('Original RX-8 MDL3 already owns inline geometry for this slot.','rx8_stream_owner')
    packed=audit_packed_streams(model,cpu,selected_slots=result['selected_slots'])
    if not packed['model_binding_consistent']:
        raise GarageError('Original RX-8 packed stream descriptors, bounds or PMSH references disagree.','rx8_stream_owner')
    if set(packed['selected_packed_slots'])!=set(result['selected_slots']):
        raise GarageError('Original RX-8 target slot no longer has its reviewed packed-stream binding.','rx8_stream_owner')
    result.update(main_cpu_bytes=len(cpu),original_stream_sha256=sha256(stream),
                  checked_original_inline_slots=len(result['selected_slots']),
                  original_stream_retained=True,packed_stream_analysis=packed,
                  auxiliary_layout_partially_mapped=True)
    return result
