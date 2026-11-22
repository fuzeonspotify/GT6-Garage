"""Four imported rims through the captured v026 native-material path.
Preserve original GT6 tyre/surround, spin blur, shadow and draw contexts.
No texture/sampler replacement and no emulator, physics, save or light changes.
"""
from pathlib import Path
import sys,json,struct,hashlib,shutil
import numpy as np
W=Path.cwd(); R=W/"reports"
import build_supra_optics_v003 as core
from build_supra_race_wheels_v001 import add_fvfs
from supra_native_materials import MaterialMeta
from native_routes import inspect
from build_zonda_materials_v001 import shape_row
g=core.g;BASE=W/'candidates/supra_race_wheel_probe_v018';OUT=W/'candidates/supra_four_rims_native_material_v027';sha=lambda x:hashlib.sha256(x).hexdigest()
def main():
 assert not OUT.exists()
 src=W/'native/car/0004/0006/race/wheel';raw=src.read_bytes();m=g.MDL3(src);rt=inspect(src);meta=MaterialMeta(raw,'Four_imported_rims_native_material_v027')
 doc=json.loads((W/'authoring/neutral/race.json').read_text());blob=(W/'authoring/neutral/race.bin').read_bytes();assert g.sha(blob)==doc['binary_sha256']
 fvfs=add_fvfs(meta,[rt['materials'][i] for i in range(4)]);records=[]
 for ci,corner in enumerate(('FL','FR','RL','RR')):
  selected=[x for x in doc['chunks'] if x['corner']==corner and x['role']in('rim','valve')];assert len(selected)==2
  geo=core.encode(selected,blob,True);assert geo['triangles']==3001
  vp=meta.alloc('rim_'+corner+'_vertices',geo['vertex'],128);ip=meta.alloc('rim_'+corner+'_indices',geo['index'],128);bp=meta.alloc('rim_'+corner+'_bounds',g.corners(geo['bounds']),16)
  sid=ci*3+1;pos=m.shape_pointer+sid*48;row=shape_row(raw[pos:pos+48],geo,fvfs[tuple(rt['materials'][ci]['shader_key'])],ci,0x1809,vp=vp,ip=ip,bp=bp);meta.put(pos,row,'only_'+corner+'_rim')
  mod=rt['models'][ci];mp=mod['offset'];oldbp=m.u32(mp+20);oldn=m.u16(mp+18);oldpoints=[m.unpack('fff',oldbp+i*12) for i in range(oldn)];points=oldpoints+geo['positions'];g.main_model_union_bounds(meta,[mod],points)
  origin=np.array(m.unpack('fff',mp+4));lo=np.min(points,axis=0);hi=np.max(points,axis=0);radius=max(np.linalg.norm(np.array([x,y,z])-origin) for x in(lo[0],hi[0]) for y in(lo[1],hi[1]) for z in(lo[2],hi[2]));meta.put(mp,struct.pack('>f',float(np.nextafter(np.float32(radius),np.float32(np.inf)))),'rim_union_bound')
  records.append(dict(corner=corner,shape=sid,source_components=[x['id'] for x in selected],triangles=geo['triangles'],vertices=geo['vertices'],vertex_sha256=sha(geo['vertex']),index_sha256=sha(geo['index'])))
 meta.finish_material_tables();data,metadata=meta.finish();shutil.copytree(BASE,OUT);dest=OUT/'car/0004/0006/race/wheel';dest.write_bytes(data)
 result=inspect(dest);parsed=g.MDL3(dest);assert result['models']==rt['models'] and result['materials']==rt['materials'];assert parsed.shape_count==16 and parsed.model_count==8
 for sid in range(16):
  if sid not in (1,4,7,10):assert data[m.shape_pointer+48*sid:m.shape_pointer+48*(sid+1)]==raw[m.shape_pointer+48*sid:m.shape_pointer+48*(sid+1)]
 for item in records:
  s=parsed.shape(item['shape']);assert s['vertex_count']==item['vertices'] and s['index_count']==9003
  assert sha(data[s['vertex_pointer']:s['vertex_pointer']+s['vertex_count']*28])==item['vertex_sha256'];assert sha(data[s['index_pointer']:s['index_pointer']+s['index_count']*2])==item['index_sha256']
 changed=[p.relative_to(OUT).as_posix() for p in (OUT/'car').rglob('*') if p.is_file() and p.read_bytes()!=(BASE/p.relative_to(OUT)).read_bytes()];assert changed==['car/0004/0006/race/wheel']
 proof=dict(passed=True,candidate=OUT.name,new_sha256=sha(data),source_triangles=12004,source_components=8,rims=records,native_materials_exact=True,native_draw_programs_exact=True,native_tyre_surround_and_blur_shapes_exact=True,source_rim_geometry_preserved=True,changed_assets=changed,metadata=metadata,runtime_verified=False,physical_PS3_tested=False,limitations=['Native GT6 tyre/surround and fast-spin blur representations retained.','Native wheel material and texture mapping retained; imported flat-colour finish not yet remapped.'])
 receipt=json.loads((BASE/'build_receipt.json').read_text());receipt.update(passed=True,runtime_tested=False,four_rim_repair=proof);(OUT/'build_receipt.json').write_text(json.dumps(receipt,indent=2));(R/'four_rims_validation_v027.json').write_text(json.dumps(proof,indent=2));print(json.dumps({k:v for k,v in proof.items() if k not in ('metadata','rims')}))
if __name__=='__main__':main()
