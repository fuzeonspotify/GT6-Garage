"""Build source-derived native PS3 Supra MDL3 assets; never writes game/save files."""
from pathlib import Path
import sys,json,struct,hashlib,collections,math,io,shutil,argparse
import numpy as np
from PIL import Image
W=Path.cwd()
sys.dont_write_bytecode=True
import build_zonda_geometry_v001 as g
from supra_native_materials import MaterialMeta
from native_routes import inspect
from build_zonda_materials_v001 import span_count,shape_row,update_model_materials
from rsx_vertex_codec import encode_cmp32_be
import supra_optical_profile_v003 as optics
import supra_index_budget_v002 as indexbudget
NATIVE=W/'native/car/0004/0006';NEUTRAL=W/'authoring/neutral'
sha=lambda b:hashlib.sha256(b).hexdigest()

def save(p,obj):p.write_text(json.dumps(obj,indent=2,allow_nan=False),encoding='utf-8')
def arr(blob,ch,key,cols,dtype):
 d=ch['buffers'][key];return np.frombuffer(blob,dtype=dtype,count=d['count']*cols,offset=d['byte_offset']).reshape(-1,cols).copy()
def encode(chunks,blob,wheel=False):
 vb=bytearray();allp=[];indices=[];coverage=[];offset=0
 for ch in chunks:
  ps=arr(blob,ch,'positions',3,'<f4');ns=arr(blob,ch,'normals',3,'<f4');uv=arr(blob,ch,'uv0',2,'<f4');ids=arr(blob,ch,'indices',1,'<u4').reshape(-1)
  ns=optics.native_normals(ch,ps,ns,uv,ids)
  uv=optics.native_uv(ch,uv)
  assert np.isfinite(ps).all() and np.isfinite(ns).all() and np.isfinite(uv).all()
  assert ids.max()<len(ps) and len(ids)==3*ch['triangles']
  faces=ids.reshape(-1,3);cross=np.cross(ps[faces[:,1]]-ps[faces[:,0]],ps[faces[:,2]]-ps[faces[:,0]])
  dots=(cross*ns[faces].mean(axis=1)).sum(axis=1);weight=float(np.abs(dots).sum())
  alignment=float(dots.sum())/weight if weight>1e-12 else 1.0
  flipped=alignment < -0.99
  if flipped:
   assert ch['role']!='static' and ch['id'] in (1,2,3,4,6,176), 'Unexpected inconsistent source surface'
   faces[:,[1,2]]=faces[:,[2,1]];ids=faces.reshape(-1);alignment=-alignment
  assert alignment>0.95, (ch['id'],alignment)
  for p,n,t in zip(ps,ns,uv):
   vb.extend(struct.pack('>3f',*p));vb.extend(encode_cmp32_be(n,normalize=False));vb.extend(struct.pack('>2f',*t))
   if wheel:vb.extend(bytes((64,64,64,255)))
  indices.extend((ids+offset).tolist());coverage.append(dict(mesh=ch['id'],vertices=len(ps),triangles=len(ids)//3,winding_corrected_against_authored_normals=flipped,normal_winding_agreement=alignment));offset+=len(ps);allp.extend(ps.tolist())
 assert offset<65536 and len(indices)//3<65536
 return dict(vertex=bytes(vb),index=np.array(indices,dtype='>u2').tobytes(),positions=allp,vertices=offset,indices=len(indices),triangles=len(indices)//3,bounds=g.box(allp),coverage=coverage)

class Textures:
 def __init__(self):self.images=[];self.payloads={};self.cache={}
 def add(self,im,label):
  im=im.convert('RGBA');alpha=im.getchannel('A').getextrema();fmt='DXT1' if alpha==(255,255) else 'DXT5';output=io.BytesIO();im.save(output,format='DDS',pixel_format=fmt)
  data=output.getvalue();assert data[:4]==b'DDS ' and data[84:88]==fmt.encode();data=data[128:];key=(sha(data),im.size,fmt)
  if key in self.cache:return self.cache[key]
  i=len(self.images);self.cache[key]=i;self.images.append(dict(id=i,label=label,width=im.width,height=im.height,bytes=len(data),sha256=sha(data),native_format_byte=134 if fmt=='DXT1' else 136,mip_count=1));self.payloads[i]=data;return i
 def solid(self,color,label):return self.add(Image.new('RGBA',(4,4),tuple(color)),label)

def srgb(v):return int(round(255*(12.92*v if v<=.0031308 else 1.055*v**(1/2.4)-.055)))
def source_texture(mat):
 rgb=[srgb(max(0,min(1,v))) for v in mat['color'][:3]]
 if mat['base_color_texture'] is None:return Image.new('RGBA',(4,4),(*rgb,255))
 # These source texture objects map one-to-one to the embedded image table.
 j=json.loads((W/'input/gltf_metadata.json').read_text())
 iid=j['textures'][mat['base_color_texture']]['source'];p=next((NEUTRAL/'textures').glob(f'image_{iid:02d}.*'));im=Image.open(p).convert('RGBA');im.thumbnail((512,512),Image.Resampling.LANCZOS)
 w=2**round(math.log2(im.width));h=2**round(math.log2(im.height));im=im.resize((max(4,w),max(4,h)),Image.Resampling.LANCZOS)
 return im

def make_plans(route,materials,tex,asset):
 plans=[];mats={m['id']:m for m in route['materials']};meta=g.MDL3(Path(route['source']))
 for mat in materials:
  family=mat['family'];sid=mat['id'];samplers=[]
  if asset=='wheel':tid=0
  elif family=='paint':tid=0
  elif family=='glass':tid=12 # exact fragment hash matches previously audited transparent-lens family
  elif family=='metal':tid=15
  else:tid=3
  tmpl=mats[tid];keys=collections.defaultdict(list)
  for key in tmpl['texture_keys']:keys[key['parameter']].append(key['name'])
  image=source_texture(mat)
  def bind(pid,image_id,unit):
   samplers.append(dict(unit=unit,aliases=keys[pid],image_id=image_id,wrap_S=1,wrap_T=1,gamma_RGBA_mask=7))
  diffpid=next(x['parameter'] for x in tmpl['texture_keys'] if x['name']=='diffuseMapSampler')
  if family=='glass' and asset=='body':
   assert tmpl['fragment_program_sha256']=='85c2368dade92812d48834d2c141e08f62d39cc6300a48bfa7f05a2db31c3f59'
   tint,opacity=optics.build_maps(mat,tex)
   alpha_pid=next(x['parameter'] for x in tmpl['texture_keys'] if x['name']=='alphaMapSampler');bind(alpha_pid,opacity,0);bind(diffpid,tint,1)
  elif family=='paint' and asset=='body':
   # Preserve native colour-shift BRDFs in every factory colour bank.
   response=tex.solid((189,190,189,70),'native_body_paint_response');selector=tex.solid((0,0,0,255),'native_body_paint_selector')
   selectpid=next(x['parameter'] for x in tmpl['texture_keys'] if x['name']=='materialSelectMapSampler');bind(diffpid,response,0);bind(selectpid,selector,1)
  elif asset=='body' and sid==2:
   bind(diffpid,optics.build_metal_map(tex),0)
  elif asset=='body' and sid==11:
   bind(diffpid,tex.solid((32,36,42,69),'optical_rear_lamp_housing'),0)
  else:
   if asset=='body' and family=='metal':image.putalpha(69)
   elif asset=='body':image.putalpha(255)
   bind(diffpid,tex.add(image,f'source_{asset}_{sid}'),0)
  plans.append(dict(source_material_id=sid,family=family,double_sided_source=False,bindings={'hq':dict(asset='hq_'+asset,template_material_id=tid,shader_key=tmpl['shader_key'],samplers=samplers)}))
 return plans

def group_batches(chunks,blob,wheel=False):
 by=collections.defaultdict(list)
 for ch in chunks:by[ch['material_id']].append(ch)
 result=[]
 for mid,cc in sorted(by.items()):
  batch=[];nv=nt=0
  for ch in cc:
   if batch and (nv+ch['vertices']>=62000 or nt+ch['triangles']>=62000):result.append((mid,encode(batch,blob,wheel)));batch=[];nv=nt=0
   batch.append(ch);nv+=ch['vertices'];nt+=ch['triangles']
  if batch:result.append((mid,encode(batch,blob,wheel)))
 return indexbudget.split_batches(result,wheel)

def build_body(lod,doc,blob,out):
 src=NATIVE/'hq';raw=(src/'body').read_bytes();rt=inspect(src/'body');m=g.MDL3(src/'body');meta=MaterialMeta(raw,lod+'_Supra_source_body');assert rt['banks']==7
 gs,_=g.streams(m,(src/'body_s').read_bytes(),span_count(raw));main=next(x for x in gs if x['label']=='//0');oldcpu=g.stream_cpu(main);mt=g.u32(oldcpu,24)
 rows=[g.u32(oldcpu,mt+32*i) for i in range(g.u16(oldcpu,20))];available=[x for x in rows if x<=32]
 chunks=[x for x in doc['chunks'] if x['role']=='static'];batches=group_batches(chunks,blob)
 assert len(batches)<=len(available), 'Do not expand native shape count in this control'
 oldcpu,available,new_shape_ids=indexbudget.extend_body_slots(oldcpu,m,available,len(batches),g);extra_shape_rows={}
 assert len(batches)<=len(available),(len(batches),available)
 byid={x['id']:x for x in doc['materials']};batches.sort(key=lambda a:optics.order(a[0],byid))
 mats=[byid[i] for i in sorted({x[0] for x in batches})];paint=[x for x in mats if x['family']=='paint'];assert len(paint)==1
 tex=Textures();plans=make_plans(rt,[x for x in mats if x['family']!='paint'],tex,'body');assert len(plans)==25
 protected=set()
 for model in rt['models']:
  for command in model['commands']:
   sid=command.get('shape_id')
   if sid is None:continue
   if model['id'] in (0,5) and sid<=32:continue
   assert sid not in available[:len(batches)], 'A replaced static shape is referenced by another model'
   protected.add(rt['shapes'][sid]['material'])
 free=sorted(set(range(meta.old_bank_size))-protected-{0});assert free==list(range(1,26))
 replace={p['source_material_id']:i for p,i in zip(sorted(plans,key=lambda p:p['source_material_id']),free)}
 meta.add_inline_images(tex.images,tex.payloads);mmap=meta.clone_source_materials(plans,'hq',{i['id']:i for i in tex.images},rt,replace_slots=replace);mmap[paint[0]['id']]=0
 required=[rt['materials'][p['bindings']['hq']['template_material_id']] for p in plans]+[rt['materials'][0]];fm,fvfs=meta.fvfs(required)
 byplan={p['source_material_id']:p for p in plans};byplan[paint[0]['id']]={'bindings':{'hq':{'template_material_id':0}}};items=[];shapes=[];allp=[]
 for sid,(mid,geo) in zip(available,batches):
  template=byplan[mid]['bindings']['hq']['template_material_id'];flags=0x3809 if byid[mid]['family']=='glass' else 0x1809;fvf=fm[tuple(rt['materials'][template]['shader_key'])]
  if byid[mid]['family']=='paint':
   flags |= 0x10 # Retain the native painted shape's material-variation flag.
   mmap[mid]=0
   vertices=bytearray(geo['vertex'])
   for off in range(0,len(vertices),24):struct.pack_into('>2f',vertices,off+16,.5078125,.5078125)
   geo['vertex']=bytes(vertices)
  pos=m.shape_pointer+48*sid;template_row=raw[pos:pos+48] if sid<m.shape_count else raw[m.shape_pointer:m.shape_pointer+48]
  row=bytearray(shape_row(template_row,geo,fvf,mmap[mid],flags & ~0x10))
  if flags & 0x10:
   assert byid[mid]['family']=='paint' and g.u16(raw,m.shape_pointer)==0x1011
   struct.pack_into('>H',row,0,flags)
  if sid<m.shape_count:meta.put(pos,row,'Supra_static_shape')
  else:extra_shape_rows[sid]=bytes(row)
  items.append((sid,geo));allp.extend(geo['positions']);shapes.append(dict(shape=sid,source_material=mid,material=mmap[mid],vertices=geo['vertices'],triangles=geo['triangles'],coverage=geo['coverage'],bounds=geo['bounds']))
 indexbudget.finish_body_shapes(meta,m,extra_shape_rows,g)
 cpu,bindings=g.cpu_with_geometry(oldcpu,items)
 # Replace only the static prefix; all native mechanical transforms and relative branches retain exact bytes.
 models=[]
 for model in rt['models']:
  if model['id'] not in(0,5):continue
  commands=model['commands'];mech=next(x['offset'] for x in commands if x['opcode']==14)
  special=[x for x in commands if x['offset']<mech and x.get('shape_id',0)>32]
  assert all(x['shape_id']==95 and 'AUTOTIRE' in rt['shapes'][95]['name'] for x in special)
  context=next(x['context'] for x in commands if 'shape_id' in x);cp=model['command_offset'];end=cp+model['command_bytes']
  for cmd in commands:
   if cmd['offset']>=mech:
    targets=([cmd['target']] if 'target'in cmd else [])+[x['target'] for x in cmd.get('branches',[])]
    assert all(t>=mech for t in targets)
  prefix=b''.join(bytes((59,s['shape'],context)) for s in shapes)
  tail=raw[mech:end]
  old_static_suffix=b''.join(bytes((59,i,context)) for i in (26,8,1))+b'\0'
  assert tail.endswith(old_static_suffix)
  # These original static meshes are replaced; do not draw source slots twice.
  for command in commands:
   targets=([command['target']] if 'target' in command else [])+[b['target'] for b in command.get('branches',[])]
   assert not any(end-9 <= target < end for target in targets)
  tail=tail[:-len(old_static_suffix)]+b'\0'
  program=b''.join(raw[x['offset']:x['next']] for x in special)+prefix+tail
  newp=meta.alloc('Supra_body_commands',program);meta.s32(model['offset']+24,newp,'source_command_pointer');meta.s32(model['offset']+28,len(program),'source_command_bytes')
  oldmats={rt['shapes'][cmd['shape_id']]['material'] for cmd in commands if 'shape_id'in cmd and (cmd['offset']>=mech or cmd in special)}
  material_list=update_model_materials(meta,model,oldmats|set(mmap.values()))
  models.append(dict(model=model['id'],native_mechanical_bytes_preserved=True,material_list=material_list))
 bounds=g.main_model_union_bounds(meta,[x for x in rt['models'] if x['id']in(0,5)],allp)
 stream,spans,expected=g.rebuild_streams(meta,gs,{'//0':cpu});meta.finish_material_tables();body,metadata=meta.finish()
 folder=out/'car/0004/0006'/lod;folder.mkdir(parents=True,exist_ok=True);(folder/'body').write_bytes(body);(folder/'body_s').write_bytes(stream)
 parsed=g.MDL3(folder/'body');gg,rr=g.streams(parsed,stream,len(expected));assert {(x['group'],x['index']):sha(x['raw']) for x in rr}==expected
 return dict(lod=lod,kind='body',basis='Original Supra HQ PS3 native pipeline, independent race detail geometry',triangles=sum(x['triangles'] for x in shapes),source_chunks=len(chunks),shapes=shapes,models=models,images=tex.images,material_maps=mmap,
  bytes={'body':len(body),'body_s':len(stream)},metadata=metadata,bindings=bindings,stream_changes=spans,source_physics_untouched=True,factory_material_banks_preserved=7,index_safe_new_shape_ids=new_shape_ids,max_draw_indices=max(x['triangles']*3 for x in shapes))

def wheel_fvf(meta,mat):
 # Native wheel shader matches the proven F50 wheel shader interface, including colorSet1.
 expected={'position':0,'normal':2,'map12':8,'colorSet1':3};assert {x['name']:x['register'] for x in mat['vertex_semantics']}==expected
 raw=meta.raw;oldn=g.u16(raw,0x18);oldp=g.u32(raw,0x40);orig=raw[oldp:oldp+120*oldn];template=bytearray(orig[:120]);names={s:meta.string(s) for s in expected}
 fields={'position':(0,3,2,0),'normal':(12,1,6,0),'map12':(16,2,2,0),'colorSet1':(24,4,4,0)}
 fp=meta.alloc('Source_wheel_fields',b''.join(struct.pack('>I4B',names[n],*fields[n]) for n in sorted(fields)));ap=meta.alloc('Source_wheel_array',raw[g.u32(template,116):g.u32(template,116)+16])
 struct.pack_into('>III',template,0,meta.string(mat['shader_key'][0]),mat['shader_key'][1],fp);template[24:26]=bytes((4,28));struct.pack_into('>I',template,116,ap)
 table=meta.alloc('Original_and_source_wheel_FVFs',orig+template);meta.s16(0x18,oldn+1,'wheel_FVF_count');meta.s32(0x40,table,'wheel_FVF_table');return oldn

def build_wheel(lod,doc,blob,out):
 src=NATIVE/'hq/wheel';raw=src.read_bytes();m=g.MDL3(src);rt=inspect(src);meta=MaterialMeta(raw,lod+'_Supra_wheel')
 chunks=[x for x in doc['chunks'] if x['role']!='static'];byid={x['id']:x for x in doc['materials']};mats=[byid[i] for i in sorted({x['material_id'] for x in chunks})];tex=Textures();plans=make_plans(rt,mats,tex,'wheel');meta.add_inline_images(tex.images,tex.payloads);mapping=meta.clone_source_materials(plans,'hq',{i['id']:i for i in tex.images},rt,asset='wheel');fvf=wheel_fvf(meta,rt['materials'][0])
 sp=m.shape_pointer;kp=m.u32(0x3c);count=m.shape_count;rows=[raw[sp+48*i:sp+48*(i+1)] for i in range(count)];keys=[(m.string(m.u32(kp+8*i)),m.u32(kp+8*i),m.u32(kp+8*i+4)) for i in range(m.u16(0x16))];shapes=[]
 for model,corner in zip(rt['models'][:4],('FL','FR','RL','RR')):
  calls=[x for x in model['commands'] if 'shape_id' in x];assert len(calls)==1
  anchor=calls[0]['shape_id'];context=calls[0]['context'];used=[];allp=[]
  for j,(mid,geo) in enumerate(group_batches([x for x in chunks if x['corner']==corner],blob,True)):
   sid=anchor if j==0 else len(rows);vp=meta.alloc('wheel_vertices',geo['vertex'],128);ip=meta.alloc('wheel_indices',geo['index']);bp=meta.alloc('wheel_bounds',g.corners(geo['bounds']));row=shape_row(rows[anchor],geo,fvf,mapping[mid],0x1809,vp=vp,ip=ip,bp=bp)
   if j==0:rows[anchor]=row
   else:
    rows.append(row);name=f'|SUPRA_RZ97|{corner}|material_{mid}';keys.append((name,meta.string(name),sid))
   used.append(sid);allp.extend(geo['positions']);shapes.append(dict(shape=sid,corner=corner,material=mid,vertices=geo['vertices'],triangles=geo['triangles'],bounds=geo['bounds']))
  cmd=b''.join(bytes((59,sid,context)) for sid in used)+b'\0';cp=meta.alloc('source_wheel_commands',cmd);meta.s32(model['offset']+24,cp,'wheel_commands');meta.s32(model['offset']+28,len(cmd),'wheel_command_length');update_model_materials(meta,model,set(mapping.values()));g.main_model_union_bounds(meta,[model],allp)
 table=meta.alloc('source_wheel_shape_table',b''.join(rows));keys.sort(key=lambda x:x[0].encode());kt=meta.alloc('source_wheel_keys',b''.join(struct.pack('>II',p,s) for name,p,s in keys));meta.s16(0x14,len(rows),'wheel_count');meta.s16(0x16,len(keys),'wheel_key_count');meta.s32(0x38,table,'wheel_shapes');meta.s32(0x3c,kt,'wheel_keys');meta.finish_material_tables();data,metadata=meta.finish()
 path=out/'car/0004/0006'/lod/'wheel';path.parent.mkdir(parents=True,exist_ok=True);path.write_bytes(data);parsed=g.MDL3(path)
 for s in shapes:assert parsed.shape(s['shape'])['vertex_count']==s['vertices']
 return dict(lod=lod,kind='wheel',source_chunks=len(chunks),triangles=sum(x['triangles'] for x in shapes),shapes=shapes,bytes=len(data),metadata=metadata,native_spin_contexts_preserved=True)

def main():
 ap=argparse.ArgumentParser();ap.add_argument('--name',default='supra_visual_v001');args=ap.parse_args();assert args.name.replace('_','').isalnum()
 out=W/'candidates'/args.name;out.mkdir(parents=True,exist_ok=False)
 results=[]
 for lod in('hq','race'):
  doc=json.loads((NEUTRAL/f'{lod}.json').read_text());blob=(NEUTRAL/f'{lod}.bin').read_bytes();assert sha(blob)==doc['binary_sha256']
  results.append(build_body(lod,doc,blob,out));print('BODY',lod,'BUILT',flush=True)
  results.append(build_wheel(lod,doc,blob,out));print('WHEEL',lod,'BUILT',flush=True)
  assert sum(r['triangles'] for r in results if r['lod']==lod)==doc['triangles']
 # Keep car-specific driver, camera, rain and lighting parameters intact.
 for name in ('info','interior'):shutil.copyfile(NATIVE/name,out/'car/0004/0006'/name)
 save(out/'build_receipt.json',dict(passed=True,source='SupraRZ97.zip',source_sha256='3f6bd85250e83798c41e2282485d84ddadd7329610cf2326b0228958d517a523',car_id=82,model='car/0004/0006',runtime_tested=False,results=results,optical_profile=optics.manifest(),reflector_normal_audit=optics.NORMAL_AUDIT,index_limit_contract=dict(target_material_ids=[4,9],max_target_triangles_per_draw=20000,max_target_indices_per_draw=60000,lossless_triangle_partition=True),
  limits=['Native shader approximations; source normal maps/transmission not directly transferred.','Uses native Supra HQ shader/command pipeline in both paths, with separate race geometry budget.','Native shadows, driver/cameras and mechanical models retained.','Not a claim of a fully functional premium cockpit or physical PS3 performance.']))
 print('BUILD_COMPLETE',str(out))
if __name__=='__main__':main()
