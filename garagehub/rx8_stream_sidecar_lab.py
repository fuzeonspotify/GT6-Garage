"""Non-installable RX-8 stream-sidecar A/B experiment.

A replacement inline shape has no vertex/index entry in the original stream,
but the original RX-8 still has auxiliary (bounds/indirection) references.
This tool builds a separate, *unverified* variant with those references
cleared. Neither variant is approved for game installation.
"""
from __future__ import annotations
import struct,zlib
from .errors import GarageError
from .native import MDL3
from .body_streams import groups,decode
from .rx8_stream_guard import parse_cpu_rows,check_native_stream_ownership
from .util import sha256

def clear_auxiliary(cpu:bytes,slots):
    report=parse_cpu_rows(cpu,slots)
    clean=bytearray(cpu)
    table=struct.unpack_from('>I',cpu,24)[0]
    requested=set(report['selected_slots'])
    touched=[]
    for i in range(report['stream_rows']):
        at=table+i*32
        sid=struct.unpack_from('>I',cpu,at)[0]
        if sid not in requested:continue
        before=bytes(clean[at+12:at+32])
        clean[at+12:at+32]=bytes(20)
        touched.append({'shape':sid,'previous_aux_sha256':sha256(before),'had_auxiliary':any(before)})
    if len(touched)!=len(requested):
        raise GarageError('Not all reviewed stream slots were cleared.','rx8_stream_sidecar')
    return bytes(clean),touched

def _encode(raw:bytes,old_flags:int):
    if old_flags>>31:
        obj=zlib.compressobj(9,zlib.DEFLATED,-15)
        stored=obj.compress(raw)+obj.flush()
        flags=len(stored)|0x80000000
    else:
        stored=raw
        flags=len(raw)
    if len(stored)>=2**31 or decode(stored,len(raw),bool(flags>>31))!=raw:
        raise GarageError('Sidecar lab compressed payload failed roundtrip.','rx8_stream_sidecar')
    return stored,flags

def make_sidecar_variant(built:bytes,donor_body:bytes,donor_stream:bytes,slots):
    ownership=check_native_stream_ownership(donor_body,donor_stream,slots)
    original=groups(donor_body,donor_stream)
    main=next(g for g in original if g['label']=='//0')
    cpu=b''.join(r['raw'] for r in main['records'][:main['na']])
    new_cpu,touched=clear_auxiliary(cpu,slots)
    if new_cpu==cpu:
        raise GarageError('No auxiliary sidecar change to test.','rx8_stream_sidecar')
    chunks=[];offset=0
    for row in main['records'][:main['na']]:
        size=len(row['raw'])
        chunks.append(new_cpu[offset:offset+size])
        offset+=size
    if offset!=len(cpu):raise GarageError('Stream chunk partition changed.','rx8_stream_sidecar')
    modified=bytearray(built)
    main_records={id(rec):chunks[i] for i,rec in enumerate(main['records'][:main['na']])}
    # The parser has already validated that the original records cover exactly
    # the file, without gaps or overlaps. Preserve all group and span counts.
    physical=sorted(original,key=lambda g:g['records'][0]['offset'])
    out=[];cursor=0;changed_records=0
    for g in physical:
        struct.pack_into('>I',modified,g['header']+4,cursor)
        for j,row in enumerate(g['records']):
            raw=main_records.get(id(row),row['raw'])
            if id(row) in main_records:
                stored,flags=_encode(raw,row['flags']);changed_records+=1
            else:
                stored,flags=row['stored'],row['flags']
            struct.pack_into('>II',modified,g['table']+8*j,flags,len(raw))
            out.append(stored);cursor+=len(stored)
    if changed_records!=main['na']:
        raise GarageError('Expected every main CPU span to be rewritten.','rx8_stream_sidecar')
    new_body=bytes(modified);new_stream=b''.join(out)
    MDL3(new_body).validate()
    verified=groups(new_body,new_stream)
    old_g={g['label']:g for g in original};new_g={g['label']:g for g in verified}
    if set(old_g)!=set(new_g):
        raise GarageError('Native stream group structure changed.','rx8_stream_sidecar')
    for label,g in new_g.items():
        old=old_g[label]
        if (g['na'],g['nb'],len(g['records']))!=(old['na'],old['nb'],len(old['records'])):
            raise GarageError('Native stream span count changed.','rx8_stream_sidecar')
        for i,rec in enumerate(g['records']):
            want=new_cpu if label=='//0' and i<g['na'] else None
            if want is not None:continue
            if rec['raw']!=old['records'][i]['raw']:
                raise GarageError('Unrelated native stream span changed.','rx8_stream_sidecar')
    after_cpu=b''.join(x['raw'] for x in new_g['//0']['records'][:new_g['//0']['na']])
    if after_cpu!=new_cpu or len(after_cpu)!=len(cpu):
        raise GarageError('Modified native CPU stream did not roundtrip.','rx8_stream_sidecar')
    if new_body[MDL3(new_body).cut:]!=built[MDL3(built).cut:]:
        raise GarageError('Native GPU shader/resource tail changed.','rx8_stream_sidecar')
    return new_body,new_stream,{
        'passed_offline':True,'experiment':'zero auxiliary references for 24 source-backed inline shapes',
        'slots_cleared':len(touched),'original_aux_slots_nonzero':len(ownership['slots_with_nonzero_auxiliary_metadata']),
        'touched':touched,'main_cpu_allocation_bytes':len(cpu),
        'main_cpu_allocation_preserved':True,'span_count_preserved':True,
        'other_groups_raw_preserved':True,'gpu_tail_preserved':True,
        'old_stream_sha256':sha256(donor_stream),
        'new_stream_sha256':sha256(new_stream),
        'new_body_sha256':sha256(new_body),
        'runtime_verified':False,'installable':False,
        'warning':'The auxiliary pointers may be necessary runtime data. This is a comparison candidate ONLY.'
    }
