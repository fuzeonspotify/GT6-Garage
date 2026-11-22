from __future__ import annotations

import sys

from pathlib import Path

import argparse, collections, hashlib, json, math, struct, zlib

from decode_mdl3_direct import MDL3

from build_f50_body_full_hqboth_probe import streams, decode_span

BOX_ORDER=((1,0,1),(1,0,0),(1,1,0),(1,1,1),(0,0,1),(0,0,0),(0,1,0),(0,1,1))

def need(v,m):
 if not v:raise ValueError(m)

def sha(b):return hashlib.sha256(b).hexdigest()

def u16(b,p):return struct.unpack_from('>H',b,p)[0]

def u32(b,p):return struct.unpack_from('>I',b,p)[0]

def align(n,a=16):return (n+a-1)//a*a

def box(ps):return [[min(p[k]for p in ps)for k in range(3)],[max(p[k]for p in ps)for k in range(3)]]

def corners(b):return b''.join(struct.pack('>3f',*(b[t[k]][k]for k in range(3)))for t in BOX_ORDER)

def cstr(b,p):
 need(0<=p<len(b),'String range');e=b.index(0,p);need(e-p<16384,'String length');return b[p:e].decode('ascii')

class Meta:
 def __init__(self,raw,label):
  self.raw=raw;self.label=label;self.cut=u32(raw,0x6c);self.b=bytearray(raw[:self.cut]);self.tail=raw[self.cut:];self.changes=[];self.allocations=[];self.allowed=set()
  need(raw[:4]==b'MDL3'and u16(raw,0xc)==14 and u32(raw,8)==0 and u32(raw,4)==len(raw),'Unexpected native header '+label)
  self.sh=u32(raw,0x4c);self.tx=u32(raw,0x48);self.mirrors=[0x6c,self.sh+0x28,self.tx+0x20]
  pm=u32(raw,0xd0)
  if pm and raw[pm:pm+4]==b'PMSH'and u32(raw,pm+12)==self.cut:self.mirrors.append(pm+12)
  need(all(u32(raw,p)==self.cut for p in self.mirrors),'Extent mirrors '+label)
 def alloc(self,label,data,alignment=16):
  p=align(len(self.b),alignment);self.b.extend(bytes(p-len(self.b)));self.b.extend(data);self.allocations.append({'label':label,'offset':p,'bytes':len(data),'alignment':alignment});return p
 def string(self,s):return self.alloc('string_'+s,s.encode()+b'\0',1)
 def put(self,p,data,label):
  data=bytes(data);need(0<=p<=len(self.b)-len(data),'Metadata write range '+label);old=bytes(self.b[p:p+len(data)])
  if old!=data:
   self.b[p:p+len(data)]=data;self.changes.append({'label':label,'offset':p,'bytes':len(data),'before_sha256':sha(old),'after_sha256':sha(data)})
   if p<self.cut:self.allowed.update(range(p,min(self.cut,p+len(data))))
 def s16(self,p,v,label):self.put(p,struct.pack('>H',v),label)
 def s32(self,p,v,label):self.put(p,struct.pack('>I',v),label)
 def fvfs(self,materials,template_id=0):
  raw=self.raw;oldn=u16(raw,0x18);oldp=u32(raw,0x40);original=raw[oldp:oldp+oldn*120];template=bytearray(original[template_id*120:(template_id+1)*120])
  need(not any(template[12:24])and not any(template[26:116]),'Uninspected FVF extension fields')
  arr=raw[u32(template,116):u32(template,116)+16];need(arr==bytes.fromhex('00000000000000010000000000100000'),'Uninspected FVF array state')
  fieldspec={'position':(0,3,2,0),'normal':(12,1,6,0),'map12':(16,2,2,0)}
  clones=[];mapping={};receipts=[]
  for key,material in sorted({tuple(x['shader_key']):x for x in materials}.items()):
   sem={x['name']:x['register']for x in material['vertex_semantics']}
   need(sem=={'position':0,'normal':2,'map12':8},'Native material requires unsupported attributes '+str(key))
   need(set(material.get('compiled_attribute_registers',[0,2,8]))=={0,2,8},'Compiled attributes differ '+str(key))
   name=self.string(key[0]);names={n:self.string(n)for n in fieldspec}
   fb=b''.join(struct.pack('>I4B',names[n],*fieldspec[n])for n in sorted(fieldspec))
   fp=self.alloc('float_UV_fields_'+str(key),fb);ap=self.alloc('FVF_array_'+str(key),arr)
   rec=bytearray(template);struct.pack_into('>III',rec,0,name,key[1],fp);rec[24:26]=bytes((3,24));struct.pack_into('>I',rec,116,ap)
   idx=oldn+len(clones);clones.append(bytes(rec));mapping[key]=idx
   receipts.append({'id':idx,'key':list(key),'stride':24,'fields_pointer':fp,'array_pointer':ap,'source_material_id':material['id'],'compiled_attributes':sorted(sem.values())})
  ptr=self.alloc('original_and_private_FVF_table',original+b''.join(clones));self.s16(0x18,oldn+len(clones),'FVF_count');self.s32(0x40,ptr,'FVF_table')
  need(self.b[ptr:ptr+len(original)]==original,'Original FVFs changed')
  return mapping,receipts
 def finish(self):
  newcut=align(len(self.b),128);self.b.extend(bytes(newcut-len(self.b)));delta=newcut-self.cut
  for p in self.mirrors:self.s32(p,newcut,'relocation_extent')
  self.s32(4,newcut+len(self.tail),'physical_length')
  fp_count=u16(self.raw,self.sh+0x34);fp_table=u32(self.raw,self.sh+0x40);reloc=[]
  for i in range(fp_count):
   f=fp_table+32*i+4;p=u32(self.raw,f);n=u32(self.raw,f+4);need(self.cut<=p<=len(self.raw)-n,'Shader tail range')
   self.s32(f,p+delta,'fragment_pointer');reloc.append({'field':f,'old':p,'new':p+delta,'bytes':n,'sha256':sha(self.raw[p:p+n])})
  # Self-contained native wheel images live in the same tail. Body images are0/streamed.
  it=self.tx+u32(self.raw,self.tx+28)-u32(self.raw,self.tx+8)
  for i in range(u16(self.raw,self.tx+22)):
   f=it+32*i;p=u32(self.raw,f);n=u32(self.raw,f+4)
   if p:
    need(self.cut<=p<=len(self.raw)-n,'Unexpected original inline image before tail')
    self.s32(f,p+delta,'native_inline_image_pointer');reloc.append({'image':i,'field':f,'old':p,'new':p+delta,'bytes':n,'sha256':sha(self.raw[p:p+n])})
  out=bytes(self.b)+self.tail
  need(out[newcut:]==self.raw[self.cut:],'Native tail changed')
  need({i for i,(a,b)in enumerate(zip(self.raw[:self.cut],out[:self.cut]))if a!=b}<=self.allowed,'Unclassified original metadata edit')
  for q in reloc:need(sha(out[q['new']:q['new']+q['bytes']])==q['sha256'],'Tail relocation hash')
  return out,{'old_cut':self.cut,'new_cut':newcut,'native_tail_exact':True,'relocations':reloc,'changes':self.changes,'allocations':self.allocations}

def stream_cpu(group):return b''.join(x['raw']for x in group['records'][:group['na']])

def append_data(cpu,data,alignment):
 p=align(len(cpu),alignment);cpu.extend(bytes(p-len(cpu)));cpu.extend(data);return p

def rebuild_streams(meta,groups,replacements):
 planned={};receipts=[]
 for g in groups:
  if g['label']not in replacements:
   planned[g['label']]=[(r['raw'],r['stored'],r['wbits']is not None,True)for r in g['records']];continue
  cpu=bytes(replacements[g['label']]);need(len(cpu)%128==0,'CPU alignment')
  cpu_spans=[]
  for start in range(0,len(cpu),61440):
   raw=cpu[start:start+61440];co=zlib.compressobj(9,zlib.DEFLATED,-15);packed=co.compress(raw)+co.flush()
   need(len(packed)<65536 and decode_span(packed,len(raw),True)[0]==raw,'CPU deflate constraint');cpu_spans.append((raw,packed,True,False))
  gpu=[(r['raw'],r['stored'],r['wbits']is not None,True)for r in g['records'][g['na']:]]
  rows=cpu_spans+gpu;table=meta.alloc('span_table_'+g['label'],bytes(len(rows)*8))
  meta.put(g['header']+8,struct.pack('>HHI',len(cpu_spans),len(gpu),table),'stream_count_and_table')
  planned[g['label']]=rows
  receipts.append({'group':g['label'],'old_cpu_bytes':len(stream_cpu(g)),'new_cpu_bytes':len(cpu),'cpu_spans':len(cpu_spans),'gpu_spans':len(gpu),'native_GPU_stored_bytes_exact':True,'new_table':table})
 cursor=0;stored=[];expected={}
 for g in sorted(groups,key=lambda x:x['records'][0]['offset']):
  meta.s32(g['header']+4,cursor,'stored_group_offset');table=u32(meta.b,g['header']+12)
  for i,(raw,packed,compressed,exact)in enumerate(planned[g['label']]):
   meta.put(table+i*8,struct.pack('>II',len(packed)|(0x80000000 if compressed else 0),len(raw)),'span_descriptor')
   need(decode_span(packed,len(raw),compressed)[0]==raw,'Span roundtrip')
   stored.append(packed);cursor+=len(packed);expected[(g['label'],i)]=sha(raw)
 return b''.join(stored),receipts,expected

def cpu_with_geometry(oldcpu,items):
 cpu=bytearray(oldcpu);nmesh=u16(cpu,20);mt=u32(cpu,24);tt=u32(cpu,28);nt=u16(cpu,22)
 rows={u32(cpu,mt+i*32):mt+i*32 for i in range(nmesh)};need(len(rows)==nmesh,'Duplicate native bindings')
 original_texture_rows=[list(struct.unpack_from('>4I',cpu,tt+i*16))for i in range(nt)]
 bindings=[]
 for sid,geo in items:
  need(sid in rows,'Missing original binding '+str(sid))
  v=append_data(cpu,geo['vertex'],128);i=append_data(cpu,geo['index'],16);b=append_data(cpu,corners(geo['bounds']),16)
  newrow=[sid,v,i,b,0,0,0,0];struct.pack_into('>8I',cpu,rows[sid],*newrow);bindings.append({'shape':sid,'row_offset':rows[sid],'row':newrow})
 cpu.extend(bytes(align(len(cpu),128)-len(cpu)));delta=len(cpu)-len(oldcpu);gpu_bytes=u32(oldcpu,12)
 struct.pack_into('>III',cpu,4,len(cpu),len(cpu)+gpu_bytes,gpu_bytes)
 for j,row in enumerate(original_texture_rows):
  # The race donor mixes images in CPU and GPU storage. Appended geometry only
  # moves the GPU base; original image bytes within the CPU prefix stay in place.
  need(0<row[1]<len(oldcpu)+gpu_bytes,'Native texture address outside its stream')
  if row[1]>=len(oldcpu):row[1]+=delta
  struct.pack_into('>4I',cpu,tt+j*16,*row)
 changed_sids={s for s,g in items}
 for sid,p in rows.items():
  if sid not in changed_sids:need(cpu[p:p+32]==oldcpu[p:p+32],'Protected binding changed')
 return bytes(cpu),bindings

def main_model_union_bounds(meta,models,points):
 b=box(points);out=[]
 for model in models:
  p=model['offset'];n=u16(meta.raw,p+18);bp=u32(meta.raw,p+20);need(n==8,'Model bounds must be eight corners')
  prior=[struct.unpack_from('>3f',meta.raw,bp+i*12)for i in range(8)];old=box(prior)
  new=[[min(old[0][k],b[0][k])for k in range(3)],[max(old[1][k],b[1][k])for k in range(3)]]
  meta.put(bp,corners(new),'conservative_model_bounds');out.append({'model':model['id'],'old':old,'new':new})
 return out
