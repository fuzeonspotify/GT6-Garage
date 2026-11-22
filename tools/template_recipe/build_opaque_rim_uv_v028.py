"""Keep native shader/resources; map imported flat-colour rims to opaque native texels.
Reject source UVs sampling the unrelated stock wheel's cutout mask. The rim and
valve source materials have no albedo texture, so constant UVs preserve their
intended flat-colour roles. Native tyre, blur and shadow UVs are not touched.
"""
from pathlib import Path
import sys,io,struct,json,hashlib,shutil
import numpy as np
from PIL import Image
W=Path.cwd(); R=W/"reports"
import build_supra_optics_v003 as core
from supra_native_materials import MaterialMeta
from native_routes import inspect
g=core.g;BASE=W/'candidates/supra_four_rims_native_material_v027';OUT=W/'candidates/supra_four_rims_opaque_uv_v028';sha=lambda b:hashlib.sha256(b).hexdigest()
def image(w,h,data):
 b=io.BytesIO();Image.new('RGBA',(w,h)).save(b,format='DDS',pixel_format='DXT5');return np.array(Image.open(io.BytesIO(b.getvalue()[:128]+data)).convert('RGBA'))
def sample(arr,u,v):
 h,w=arr.shape[:2];x=u*w-.5;y=v*h-.5;x0=int(np.floor(x));y0=int(np.floor(y));fx=x-x0;fy=y-y0
 return (arr[y0%h,x0%w]*(1-fx)*(1-fy)+arr[y0%h,(x0+1)%w]*fx*(1-fy)+arr[(y0+1)%h,x0%w]*(1-fx)*fy+arr[(y0+1)%h,(x0+1)%w]*fx*fy).tolist()
def main():
 assert not OUT.exists();native=(W/'native/car/0004/0006/race/wheel').read_bytes();meta=MaterialMeta(native,'sample_native');ir=meta.image_rows[0];assert ir[9]==136
 width,height=g.u16(ir,12),g.u16(ir,14);offset=g.u32(ir,0);mips=[]
 for level in range(ir[10]):
  n=((width+3)//4)*((height+3)//4)*16;mips.append(image(width,height,native[offset:offset+n]).astype(float));offset+=n;width=max(1,width//2);height=max(1,height//2)
 choices=[]
 for y in range(88,169,2):
  for x in range(88,169,2):
   uv=((x+.5)/256,(y+.5)/256);colors=[sample(a,*uv) for a in mips]
   if min(c[3] for c in colors[:5])>250 and min(c[3] for c in colors[:6])>195:choices.append((uv,colors))
 assert choices,'No alpha-safe native material sample'
 rim=max(choices,key=lambda v:sum(v[1][0][:3]));valve=min(choices,key=lambda v:sum(v[1][0][:3]));assert sum(rim[1][0][:3])>sum(valve[1][0][:3])
 doc=json.loads((W/'authoring/neutral/race.json').read_text());materials={x['id']:x for x in doc['materials']};src=BASE/'car/0004/0006/race/wheel';raw=src.read_bytes();m=g.MDL3(src);b=bytearray(raw);allowed=set();rows=[]
 for ci,corner in enumerate(('FL','FR','RL','RR')):
  chunks=[x for x in doc['chunks'] if x['corner']==corner and x['role'] in ('rim','valve')];s=m.shape(ci*3+1);vo=0
  for ch in chunks:
   assert materials[ch['material_id']]['base_color_texture'] is None;uv,colors=rim if ch['role']=='rim' else valve
   for i in range(ch['vertices']):
    p=s['vertex_pointer']+(vo+i)*28+16;struct.pack_into('>2f',b,p,*uv);allowed.update(range(p,p+8))
   rows.append(dict(corner=corner,role=ch['role'],vertices=ch['vertices'],uv=uv,rgba_by_mip=colors));vo+=ch['vertices']
  assert vo==s['vertex_count']
 changed=[i for i,(x,y) in enumerate(zip(raw,b)) if x!=y];assert set(changed)<=allowed
 shutil.copytree(BASE,OUT);dest=OUT/'car/0004/0006/race/wheel';dest.write_bytes(b);before=inspect(src);after=inspect(dest);assert before['models']==after['models'] and before['materials']==after['materials']
 proof=dict(passed=True,candidate=OUT.name,new_sha256=sha(b),parent_sha256=sha(raw),source_triangles=12004,only_rim_valve_uv_bytes_changed=True,changed_byte_count=len(changed),rims=rows,native_materials_and_texture_bytes_exact=True,native_tyre_blur_shadow_bytes_exact=True,positions_normals_indices_exact=True,runtime_verified=False,physical_PS3_tested=False,limit='Native tyre/surround and native fast-spin blur retained. Native silver response used for imported flat-colour alloy; no exact source PBR match claimed.')
 (R/'opaque_rim_validation_v028.json').write_text(json.dumps(proof,indent=2));receipt=json.loads((BASE/'build_receipt.json').read_text());receipt.update(passed=True,runtime_tested=False,opaque_rim_mapping=proof);(OUT/'build_receipt.json').write_text(json.dumps(receipt,indent=2));print(json.dumps({k:v for k,v in proof.items() if k!='rims'}));print('RIM_UV',rim[0],rim[1][0],'VALVE_UV',valve[0],valve[1][0])
if __name__=='__main__':main()
