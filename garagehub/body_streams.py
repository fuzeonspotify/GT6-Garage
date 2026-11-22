"""Native car body-stream adapter; preserve GPU spans and unrelated groups.
New static body draws use the same separate CPU geometry path as the approved
v029 body exporter. Direct metadata geometry remains available for lamp surfaces.
"""
from __future__ import annotations
import struct,zlib
from .native import MDL3,corners
from .native_editor import need
from .util import sha256


def decode(data,expanded,compressed):
    if not compressed:
        need(len(data)==expanded,'Native raw span length differs.');return data
    valid=[]
    for bits in(-15,15,31):
        try:
            dec=zlib.decompressobj(bits);raw=dec.decompress(data,expanded+1)
            if dec.eof and not dec.unused_data and not dec.unconsumed_tail and len(raw)==expanded:valid.append(raw)
        except zlib.error:pass
    need(len(valid)==1,'Native span compression is not recognized.');return valid[0]


def groups(model,stream):
    m=MDL3(model);sep=m.u32(0xdc);need(sep>0,'Body template has no separate stream info.');n=m.u32(sep);keys=m.u32(sep+8);out=[]
    def add(label,h):
        offset=m.u32(h+4);na,nb=m.u16(h+8),m.u16(h+10);table=m.u32(h+12);need(na+nb<65536,'Excessive stream span count.');rows=[]
        for i in range(na+nb):
            flags=m.u32(table+8*i);size=flags&0x7fffffff;expanded=m.u32(table+8*i+4);need(expanded<=4*1024**2 and offset+size<=len(stream),'Native span leaves file/budget.');stored=stream[offset:offset+size];raw=decode(stored,expanded,bool(flags>>31));rows.append({'offset':offset,'raw':raw,'stored':stored,'flags':flags});offset+=size
        out.append({'label':label,'header':h,'na':na,'nb':nb,'records':rows,'table':table})
    for i in range(n):
        k=keys+12*i;name=m.string(m.u32(k));count=m.u32(k+4);p=m.u32(k+8);need(count<256,'Excessive body stream group count.')
        for j in range(count):
            h=p+j*24;add(name+'/'+str(j),h);extra=m.u32(h+16);ep=m.u32(h+20);need(extra<256,'Excessive extra stream group count.')
            for z in range(extra):add(name+'/'+str(j)+'/extra'+str(z),ep+z*16)
    cursor=0
    for row in sorted((r for g in out for r in g['records']),key=lambda r:r['offset']):need(row['offset']==cursor,'Overlapping or missing body spans.');cursor+=len(row['stored'])
    need(cursor==len(stream),'Unparsed bytes in body stream.');return out


def attach(editor,source_stream):
    gs=groups(editor.raw,source_stream);main=next((g for g in gs if g['label']=='//0'),None);need(main is not None,'Reviewed main body stream is missing.')
    oldcpu=b''.join(r['raw'] for r in main['records'][:main['na']]);cpu=bytearray(oldcpu);u16=lambda p:struct.unpack_from('>H',oldcpu,p)[0];u32=lambda p:struct.unpack_from('>I',oldcpu,p)[0]
    count=u16(20);mt=u32(24);nt=u16(22);tt=u32(28);need(mt+32*count<=len(cpu) and tt+16*nt<=len(cpu),'Body stream tables leave the CPU prefix.')
    rows={u32(mt+i*32):bytes(oldcpu[mt+i*32:mt+(i+1)*32]) for i in range(count)};need(len(rows)==count,'Duplicate native streamed shape.')
    def put(data,alignment=16):
        cpu.extend(bytes(-len(cpu)%alignment));p=len(cpu);cpu.extend(data);return p
    # This prefix is verified for both hash-pinned v029 body templates. It
    # contains the native shape table, shadow/mechanical data, texture table,
    # and all opaque native references. Static imported geometry starts here.
    prefix=108160
    need(mt==32 and tt+nt*16<=prefix<=len(oldcpu),'Unreviewed CPU prefix/table layout.')
    wanted={x['id']:x for x in editor.stream_shapes}
    need(len(wanted)==len(editor.stream_shapes) and set(wanted)<=set(rows),'Replacement requires unsupported stream bindings.')
    cpu=bytearray(oldcpu[:prefix]);checks=[];retained=[];original_order=list(rows)
    layouts=editor.m.fvfs();shape_table=editor.m.u32(0x38)
    for sid in original_order:
        original=list(struct.unpack('>8I',rows[sid]));shape=wanted.get(sid)
        if sid in getattr(editor,'disabled_static_shapes',set()):
            need(sid<=32 and sid not in wanted,'Cannot discard a live or protected source shape.')
            rows[sid]=struct.pack('>8I',sid,0,0,0,0,0,0,0);continue
        if shape is None and (original[1]>=prefix or original[2]>=prefix):
            need(sid<=32 and original[1] and original[2] and not any(original[4:]),'Unclassified non-static stream payload would be truncated.')
            sp=shape_table+sid*48;fid=editor.m.s16(sp+2);nv=editor.m.u32(sp+8);ni=editor.m.u32(sp+20);stride=layouts[fid]['stride']
            need(original[1]+nv*stride<=len(oldcpu) and original[2]+ni*2<=len(oldcpu) and original[3]+96<=len(oldcpu),'Protected old shape range is invalid.')
            shape={'id':sid,'vertex':oldcpu[original[1]:original[1]+nv*stride],'index':oldcpu[original[2]:original[2]+ni*2],'bounds':oldcpu[original[3]:original[3]+96]};retained.append(sid)
        if shape is not None:
            v=put(shape['vertex'],128);i=put(shape['index']);bp=put(shape['bounds']);rows[sid]=struct.pack('>8I',sid,v,i,bp,0,0,0,0)
            checks.append({'shape':sid,'replacement':sid in wanted,'vertex_pointer':v,'index_pointer':i,'vertex_bytes':len(shape['vertex']),'index_bytes':len(shape['index']),'vertex_sha256':sha256(shape['vertex']),'index_sha256':sha256(shape['index'])})
        else:
            need(all(0<=v<prefix for v in original[1:7]),'Protected native CPU pointer leaves the retained prefix.')
    # Keep the native binding table's address/order/count stable. No stale
    # alias points at the old table and no dead previous car stays resident.
    for i,sid in enumerate(original_order):cpu[mt+32*i:mt+32*(i+1)]=rows[sid]
    cpu.extend(bytes(-len(cpu)%128));used_bytes=len(cpu)
    need(used_bytes<=len(oldcpu),'Prepared geometry exceeds this native stream allocation. Reduce the geometry budget or select a reviewed larger template; no game package was emitted.')
    # The passing runtime profile retains the original CPU/GPU allocation,
    # GPU offsets and span count. Do not grow a native car allocation silently.
    cpu.extend(bytes(len(oldcpu)-len(cpu)));delta=0;gpu=u32(12);struct.pack_into('>III',cpu,4,len(cpu),len(cpu)+gpu,gpu)
    for j in range(nt):
        off=tt+j*16;p=u32(off+4)
        if p>=len(oldcpu):struct.pack_into('>I',cpu,off+4,p+delta)
    need(len(cpu)<24*1024**2,'Expanded body CPU stream exceeds the reviewed authoring budget.')
    compressed=[]
    for start in range(0,len(cpu),61440):
        data=bytes(cpu[start:start+61440]);co=zlib.compressobj(9,zlib.DEFLATED,-15);stored=co.compress(data)+co.flush();need(len(stored)<65536 and decode(stored,len(data),True)==data,'New stream does not roundtrip.');compressed.append({'raw':data,'stored':stored,'flags':len(stored)|0x80000000})
    main['records']=compressed+main['records'][main['na']:];main['na']=len(compressed);main['table']=editor.alloc(bytes(8*len(main['records'])));editor.put(main['header']+8,struct.pack('>HHI',main['na'],main['nb'],main['table']))
    output=[];cursor=0
    for g in sorted(gs,key=lambda x:editor.m.u32(x['header']+4)):
        editor.u32(g['header']+4,cursor)
        for j,row in enumerate(g['records']):editor.put(g['table']+j*8,struct.pack('>II',row['flags'],len(row['raw'])));output.append(row['stored']);cursor+=len(row['stored'])
    for x in checks:need(sha256(cpu[x['vertex_pointer']:x['vertex_pointer']+x['vertex_bytes']])==x['vertex_sha256'] and sha256(cpu[x['index_pointer']:x['index_pointer']+x['index_bytes']])==x['index_sha256'],'Streamed source geometry changed.')
    return b''.join(output),{'main_cpu_bytes_before':len(oldcpu),'main_cpu_bytes_after':len(cpu),'used_cpu_bytes':used_bytes,'native_allocation_preserved':True,'native_gpu_spans_preserved':True,'bindings':checks,'original_groups':len(gs),'native_binding_table_preserved':True,'retained_native_prefix_bytes':prefix,'retained_inactive_static_shapes':retained,'dead_prior_static_geometry_removed':True,'runtime_validation':False}
