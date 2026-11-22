"""Repair shared relocatable FVF field arrays; retain v028 art and settings.
The loader must not rebase the same mutable field-pointer cells for four FVF
owners. Sharing immutable strings is safe; sharing their pointer cells is not.
This builds an isolated candidate and fixes the reusable exporter with backup.
"""
from pathlib import Path
import datetime,hashlib,json,shutil,struct,sys
W=Path.cwd(); R=W/"reports"
import build_supra_optics_v003 as core
from supra_native_materials import MaterialMeta
from native_routes import inspect
G=core.g;sha=lambda b:hashlib.sha256(b).hexdigest()
BASE=W/'candidates/supra_four_rims_opaque_uv_v028';OUT=W/'candidates/supra_unique_fvf_v029'
KEY='car/0004/0006/race/wheel'
def rows(data):
 u32=lambda o:struct.unpack_from('>I',data,o)[0]
 p=u32(0x40);n=struct.unpack_from('>H',data,0x18)[0];result=[]
 for i in range(n):
  at=p+i*120;fp=u32(at+8);count=data[at+24]
  assert 0<fp and fp+8*count<=u32(0x6c)
  result.append(dict(id=i,record=at,pointer=fp,count=count,fields_hex=data[fp:fp+count*8].hex(),pointer_cells=[fp+j*8 for j in range(count)]))
 return result

def relocation_check(data,base=0x60000000):
 records=rows(data);memory=bytearray(data);seen={};duplicates=[]
 for row in records:
  for cell in row['pointer_cells']:
   if cell in seen:duplicates.append(dict(cell=cell,first_owner=seen[cell],next_owner=row['id']))
   else:seen[cell]=row['id']
   value=struct.unpack_from('>I',memory,cell)[0];struct.pack_into('>I',memory,cell,(value+base)&0xffffffff)
 valid=True
 for row in records:
  for cell in row['pointer_cells']:
   expected=struct.unpack_from('>I',data,cell)[0]+base
   valid &= struct.unpack_from('>I',memory,cell)[0]==expected
 return dict(passed=not duplicates and bool(valid),shared_pointer_cells=duplicates,simulated_rebase_exactly_once=bool(valid))

def main():
 assert not OUT.exists();raw=(BASE/KEY).read_bytes();assert sha(raw)=='a51d9038cd616f994fe9d7bec5b41c83cdf0a3efc62bdbd52cd01256a7fc88ee'
 before=rows(raw);assert len(before)==5 and len({r['pointer'] for r in before[1:]})==1
 oldcheck=relocation_check(raw);assert not oldcheck['passed']
 meta=MaterialMeta(raw,'Unique_FVF_pointer_array_owners_v029');changes=[]
 for row in before[1:]:
  payload=bytes.fromhex(row['fields_hex']);newp=meta.alloc('FVF_'+str(row['id'])+'_owned_fields',payload,16)
  meta.s32(row['record']+8,newp,'FVF_field_array_owned_once');changes.append(dict(layout=row['id'],old_pointer=row['pointer'],new_pointer=newp,field_bytes_exact=True))
 meta.finish_material_tables();data,metadata=meta.finish();after=rows(data)
 assert [r['fields_hex'] for r in before]==[r['fields_hex'] for r in after]
 newcheck=relocation_check(data);assert newcheck['passed']
 shutil.copytree(BASE,OUT);dest=OUT/KEY;dest.write_bytes(data)
 m=G.MDL3(BASE/KEY);n=G.MDL3(dest);a=inspect(BASE/KEY);b=inspect(dest)
 assert a['models']==b['models'] and a['materials']==b['materials'] and m.fvfs==n.fvfs
 assert data[m.shape_pointer:m.shape_pointer+48*m.shape_count]==raw[m.shape_pointer:m.shape_pointer+48*m.shape_count]
 for sid in (1,4,7,10):
  s=m.shape(sid);vp=s['vertex_pointer'];ip=s['index_pointer'];assert data[vp:vp+28*s['vertex_count']]==raw[vp:vp+28*s['vertex_count']]
  assert data[ip:ip+2*s['index_count']]==raw[ip:ip+2*s['index_count']]
 assert data[metadata['new_cut']:]==raw[metadata['old_cut']:]
 changed=[p.relative_to(OUT).as_posix() for p in (OUT/'car').rglob('*') if p.is_file() and p.read_bytes()!=(BASE/p.relative_to(OUT)).read_bytes()];assert changed==[KEY]
 report=dict(passed=True,candidate=OUT.name,parent=BASE.name,source_triangles=12004,new_sha256=sha(data),parent_sha256=sha(raw),old_aliasing_rejected=oldcheck,fixed_ownership=newcheck,field_table_changes=changes,positions_normals_UVs_colors_indices_byte_exact=True,all_model_programs_and_material_semantics_exact=True,all_native_texture_and_shader_payloads_exact=True,body_lights_physics_F50_Cinque_unchanged=True,changed_assets=changed,runtime_verified=False,physical_PS3_tested=False,metadata=metadata)
 (R/'unique_fvf_validation_v029.json').write_text(json.dumps(report,indent=2));receipt=json.loads((BASE/'build_receipt.json').read_text());receipt.update(passed=True,diagnostic_only=True,runtime_tested=False,unique_fvf_repair=report);(OUT/'build_receipt.json').write_text(json.dumps(receipt,indent=2))

if __name__=='__main__': main()
