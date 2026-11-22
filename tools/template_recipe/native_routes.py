from pathlib import Path

import sys, json, struct, hashlib

from decode_mdl3_direct import MDL3

from inspect_native_draw_commands import instruction as base_instruction

def instruction(b,p,end):
 if b[p]!=8:return base_instruction(b,p,end)
 flag=b[p+17];n=18
 if flag&128:n+=(4 if flag&64 else 0)+(2 if flag&32 else 0)+(1 if flag&16 else 0)+4*(flag&15)+1
 n+=2*(flag&15)
 assert p+n<=end
 return dict(offset=p,opcode=8,flag=flag,bytes=n,next=p+n,raw_hex=b[p:p+n].hex())

def inspect(path):
 m=MDL3(path);b=m.data;u=m.u32;h=m.u16;sh=u(0x4c);ms=u(0x44);mp=u(ms+8);md=u(ms+12)
 definitions=[];sp=u(sh+0x1c)
 for i in range(h(sh+0x16)):
  p=sp+12*i;definitions.append(dict(id=i,name=m.string(u(p)),key=u(p+4),vp=h(p+8),fp=h(p+10)))
 dmap={(x['name'],x['key']):x for x in definitions};vps=[]
 for i in range(h(sh+0x18)):
  p=u(sh+0x20)+40*i;at=u(p+20);n=h(p+10)
  sem=[dict(name=m.string(u(at+16*j)),register=h(at+16*j+6),field8=u(at+16*j+8),field12=u(at+16*j+12)) for j in range(n)]
  vps.append(dict(id=i,semantics=sem,program_sha256=hashlib.sha256(b[u(p):u(p)+u(p+4)]).hexdigest()))
 materials=[]
 for i in range(h(ms)):
  p=mp+i*52;di=h(p+4);rp=u(md+40*di+20);key=(m.string(u(rp)),u(rp+4));definition=dmap[key]
  kp=u(p+12);keys=[dict(name=m.string(u(kp+8*j)),parameter=u(kp+8*j+4)) for j in range(h(p+10))]
  fp=u(sh+0x2c)+72*definition['fp'];cg=u(fp);ft=u(sh+0x40)
  gpu=[ft+32*j for j in range(h(sh+0x34)) if u(ft+32*j)==cg]
  assert len(gpu)==1;off=u(gpu[0]+4);length=u(gpu[0]+8)
  materials.append(dict(id=i,name=m.string(u(p)),shader_key=list(key),vertex_program=definition['vp'],fragment_program=definition['fp'],vertex_semantics=vps[definition['vp']]['semantics'],texture_keys=keys,fragment_program_sha256=hashlib.sha256(b[off:off+length]).hexdigest()))
 models=[]
 for i in range(m.model_count):
  p=u(0x30)+48*i;cp,n=m.unpack('II',p+24);pos=cp;commands=[]
  while pos<cp+n:
   r=instruction(b,pos,cp+n);commands.append(r);pos=r['next']
   if r['opcode']==0:break
  assert pos==cp+n
  models.append(dict(id=i,offset=p,command_offset=cp,command_bytes=n,commands=commands))
 return dict(source=str(path),source_sha256=hashlib.sha256(b).hexdigest(),banks=b[ms+4],bank_size=h(ms)//b[ms+4],materials=materials,defs=definitions,vertex_programs=vps,models=models,
  shapes=[{k:v for k,v in m.shape(i).items() if k not in ('vertices','triangles')} for i in range(m.shape_count)])
