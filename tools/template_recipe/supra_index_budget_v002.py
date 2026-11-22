import numpy as np

MAX_TRIANGLES=20000

def split_batches(batches,wheel=False):
 if wheel:return batches
 result=[]
 for mid,geo in batches:
  if mid not in (4,9) or geo['triangles']<=MAX_TRIANGLES:result.append((mid,geo));continue
  stride=len(geo['vertex'])//geo['vertices'];assert stride==24
  v=np.frombuffer(geo['vertex'],dtype='u1').reshape(-1,stride);indices=np.frombuffer(geo['index'],dtype='>u2').reshape(-1,3)
  ranges=[];vt=ft=0
  for item in geo['coverage']:
   ranges.append((vt,vt+item['vertices'],ft,ft+item['triangles'],item));vt+=item['vertices'];ft+=item['triangles']
  assert vt==geo['vertices'] and ft==geo['triangles']
  source_p=np.asarray(geo['positions'])
  for start in range(0,len(indices),MAX_TRIANGLES):
   end=min(len(indices),start+MAX_TRIANGLES);old=indices[start:end].reshape(-1);unique,remapped=np.unique(old,return_inverse=True)
   assert len(unique)<65536 and len(old)<65536
   pos=source_p[unique];coverage=[]
   for v0,v1,f0,f1,item in ranges:
    lo=max(start,f0);hi=min(end,f1)
    if lo>=hi:continue
    row=dict(item);row.update(vertices=int(((unique>=v0)&(unique<v1)).sum()),triangles=hi-lo,source_triangle_first=lo-f0,source_triangle_count=item['triangles']);coverage.append(row)
   assert sum(x['vertices'] for x in coverage)==len(unique)
   assert sum(x['triangles'] for x in coverage)==end-start
   result.append((mid,dict(vertex=v[unique].tobytes(),index=remapped.astype('>u2').tobytes(),positions=pos.tolist(),vertices=len(unique),indices=len(old),triangles=end-start,bounds=[pos.min(0).tolist(),pos.max(0).tolist()],coverage=coverage)))
 return result

def extend_body_slots(oldcpu,model,available,needed,g):
 """Add only the missing main-stream bindings; retain every existing shape ID."""
 import struct
 count=max(0,needed-len(available))
 if not count:return oldcpu,available,[]
 newids=list(range(model.shape_count,model.shape_count+count));assert max(newids)<256
 cpu=bytearray(oldcpu);n=g.u16(cpu,20);oldtable=g.u32(cpu,24);tt=g.u32(cpu,28);nt=g.u16(cpu,22)
 rows=cpu[oldtable:oldtable+32*n];assert len(rows)==32*n
 while len(cpu)%16:cpu.append(0)
 table=len(cpu);cpu.extend(rows)
 for sid in newids:cpu.extend(struct.pack('>8I',sid,0,0,0,0,0,0,0))
 cpu.extend(bytes((-len(cpu))%128));delta=len(cpu)-len(oldcpu);gpu=g.u32(oldcpu,12)
 struct.pack_into('>H',cpu,20,n+count);struct.pack_into('>I',cpu,24,table);struct.pack_into('>II',cpu,4,len(cpu),len(cpu)+gpu)
 for i in range(nt):
  p=tt+i*16;address=g.u32(cpu,p+4)
  if address>=len(oldcpu):struct.pack_into('>I',cpu,p+4,address+delta)
 return bytes(cpu),available+newids,newids

def finish_body_shapes(meta,model,extra,g):
 import struct
 if not extra:return
 assert sorted(extra)==list(range(model.shape_count,model.shape_count+len(extra)))
 oldp=model.shape_pointer;rows=bytes(meta.b[oldp:oldp+48*model.shape_count])+b''.join(extra[k] for k in sorted(extra))
 sp=meta.alloc('source_extended_body_shapes',rows)
 raw=meta.raw;kp=g.u32(raw,0x3c);keys=[]
 for i in range(g.u16(raw,0x16)):
  p,sid=struct.unpack_from('>II',raw,kp+i*8);keys.append((g.cstr(raw,p),p,sid))
 for sid in sorted(extra):
  name='|SUPRA_RZ97|INDEX_SAFE|'+str(sid);keys.append((name,meta.string(name),sid))
 keys.sort(key=lambda x:x[0].encode());newkeys=meta.alloc('source_extended_body_shape_keys',b''.join(struct.pack('>II',p,sid) for name,p,sid in keys))
 meta.s16(0x14,model.shape_count+len(extra),'index_safe_shape_count');meta.s16(0x16,len(keys),'index_safe_shape_key_count');meta.s32(0x38,sp,'index_safe_shape_table');meta.s32(0x3c,newkeys,'index_safe_shape_keys')
