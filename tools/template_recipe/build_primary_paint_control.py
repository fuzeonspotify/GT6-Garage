"""Keep geometry exact; route painted panels to Supra's original body shader.
The earlier conversion used native material0 (mirror paint), while native shape7
uses material10 for the main body. This control replaces only material0 records
and the private paint FVF key, not factory colour tables, textures or geometry.
"""
from pathlib import Path
import json,shutil,struct,hashlib,sys
W=Path.cwd()
from native_routes import inspect
native=W/'native/car/0004/0006/hq/body';raw=native.read_bytes();routes=inspect(native)
u=lambda b,p:struct.unpack_from('>I',b,p)[0]
h=lambda b,p:struct.unpack_from('>H',b,p)[0]
sha=lambda b:hashlib.sha256(b).hexdigest()
ms=u(raw,0x44);mt=u(raw,ms+8);kp=u(raw,0x90);count=h(raw,0x8e);assert len(routes['materials'])==525
assert routes['materials'][0]['name'].startswith('CBaseEnv1_CLR_VARI') and routes['materials'][10]['name'].startswith('CBaseEnv_CLR_VARI')
assert routes['materials'][0]['vertex_semantics']==routes['materials'][10]['vertex_semantics']
base=W/'candidates/supra_panel_batches_v014';out=W/'candidates/supra_primary_paint_v015';assert not out.exists();shutil.copytree(base,out)
receipt=json.loads((out/'build_receipt.json').read_text());checks=[]
for lod in ('hq','race'):
 p=out/'car/0004/0006'/lod/'body';before=p.read_bytes();b=bytearray(before);bms=u(b,0x44);bmt=u(b,bms+8);bkp=u(b,0x90);allowed=set()
 assert h(b,bms)==525 and b[bms+4]==7
 for bank in range(7):
  oldp=mt+(bank*75+10)*52;row=bytearray(raw[oldp:oldp+52]);nk=h(row,10);oldkey=u(row,12)
  assert kp<=oldkey<=kp+8*count-8*nk
  struct.pack_into('>I',row,12,bkp+oldkey-kp);dest=bmt+bank*75*52;b[dest:dest+52]=row;allowed.update(range(dest,dest+52))
 # Keep the independently encoded layout, change only its native shader key.
 ft=u(b,0x40);hits=[]
 for i in range(h(raw,0x18),h(b,0x18)):
  pos=ft+120*i;namep=u(b,pos);end=b.index(0,namep);name=bytes(b[namep:end]).decode()
  if name=='Flex' and u(b,pos+4)==0:
   assert b[pos+24]==3 and b[pos+25]==24
   struct.pack_into('>I',b,pos+4,6);allowed.update(range(pos+4,pos+8));hits.append(i)
 assert len(hits)==1
 assert all(x==y or i in allowed for i,(x,y) in enumerate(zip(before,b)))
 p.write_bytes(b);after=inspect(p)
 for bank in range(7):
  item=after['materials'][bank*75];original=routes['materials'][bank*75+10]
  for key in ('name','shader_key','fragment_program_sha256','vertex_semantics','texture_keys'):assert item[key]==original[key],key
 checks.append(dict(lod=lod,paint_fvf=hits[0],old_shader='Flex0 / mirror CBaseEnv1',new_shader='Flex6 / body CBaseEnv',all_seven_native_colour_materials_preserved=True,before_sha256=sha(before),after_sha256=sha(b)))
receipt['primary_paint_control']=checks;receipt['runtime_tested']=False;(out/'build_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({'candidate':str(out),'geometry_and_shadow_programs_unchanged':True,'checks':checks}))
