"""Restore native headlamp/running/brake switching, preserving the saved Supra.
Adds only source-fitted lit surfaces; retains all previous optical/physics work.
"""
from pathlib import Path
import sys,json,struct,hashlib,shutil,collections,argparse
import numpy as np
W=Path.cwd()
sys.dont_write_bytecode=True
import build_zonda_geometry_v001 as g
from build_zonda_materials_v001 import shape_row,update_model_materials
from supra_native_materials import MaterialMeta
from native_routes import inspect
from build_supra_optics_v003 import Textures,encode
NATIVE=W/'native/car/0004/0006';BASE=W/'candidates/supra_primary_paint_v015'
sha=lambda b:hashlib.sha256(b).hexdigest()

def switch(selector,off,on):
    # The same opcode/relative-field semantics as the original Supra branches.
    assert len(off)+len(on)+2<256
    b=bytearray(b'\x05'+struct.pack('>H',selector)+b'\x02'+bytes(4))
    b+=bytes((9,len(off)+len(on)+2));p0=len(b);b+=off;b+=bytes((9,len(on)));p1=len(b);b+=on
    struct.pack_into('>H',b,4,p0-6);struct.pack_into('>H',b,6,p1-8)
    return bytes(b)

def evaluate(program,daynight,brake):
    p=0;calls=[];steps=0
    while p<len(program):
        steps+=1;assert steps<200
        op=program[p]
        if op==0:break
        if op==59:calls.append(program[p+1]);p+=3
        elif op==9:p+=2+program[p+1]
        elif op==5:
            selector=struct.unpack_from('>H',program,p+1)[0];assert selector in (0,7) and program[p+3]==2
            field=p+4+2*(brake if selector==0 else daynight);p=field+2+struct.unpack_from('>H',program,field)[0]
        else:raise ValueError(('unexpected state opcode',op,p))
    return calls

def append_emit_materials(meta,native,route,recipes,tex):
    meta.add_inline_images(tex.images,tex.payloads);newrows=[[] for _ in range(meta.banks)];assigned={};nms=g.u32(native,0x44);nmt=g.u32(native,nms+8)
    for name,tid,imageid in recipes:
        mid=meta.old_bank_size+len(assigned);assigned[name]=mid;template=route['materials'][tid]
        aliases=[x['name'] for x in template['texture_keys']];pids={x['parameter'] for x in template['texture_keys']};assert len(pids)==1
        template_pid=pids.pop();pid=meta.source_parameter(template_pid,imageid,dict(wrap_S=1,wrap_T=1,gamma_RGBA_mask=7),tex.images[imageid])
        for bank in range(meta.banks):
            row=bytearray(native[nmt+(bank*75+tid)*52:nmt+(bank*75+tid+1)*52]);start=len(meta.key_rows)
            for alias in aliases:meta.key_rows.extend(struct.pack('>II',meta.string(alias),pid))
            struct.pack_into('>I',row,0,meta.string('Supra/'+name));struct.pack_into('>HI',row,10,len(aliases),start)
            newrows[bank].append(row)
    kp=meta.alloc('lighting_combined_texture_keys',meta.key_rows);active=[];raw=meta.raw
    for bank in range(meta.banks):
        for i in range(meta.old_bank_size):
            p=meta.original_material_pointer+(bank*meta.old_bank_size+i)*52;row=bytearray(raw[p:p+52]);n=g.u16(row,10);old=g.u32(row,12)
            if n:
                assert meta.original_key_pointer<=old<=meta.original_key_pointer+8*meta.original_key_count-8*n
                struct.pack_into('>I',row,12,kp+old-meta.original_key_pointer)
            active.append(row)
        for row in newrows[bank]:struct.pack_into('>I',row,12,kp+g.u32(row,12));active.append(row)
    md=g.u32(raw,meta.ms+12);refs=g.u32(raw,0x88)
    for i in range(g.u16(raw,meta.ms+2)):
        p=md+40*i;n=struct.unpack_from('>h',raw,p+10)[0];old=g.u32(raw,p+16);rp=g.u32(raw,p+20)
        assert rp==refs+40*i
        if n:
            assert g.u32(raw,rp+28)==old and meta.original_key_pointer<=old<=meta.original_key_pointer+8*meta.original_key_count-8*n
            for field in (p+16,rp+28):meta.s32(field,kp+old-meta.original_key_pointer,'lighting_key_reference')
    mp=meta.alloc('lighting_extended_material_banks',b''.join(active));meta.s16(meta.ms,len(active),'lighting_material_count');meta.s32(meta.ms+8,mp,'lighting_material_pointer');meta.s16(0x8e,len(meta.key_rows)//8,'lighting_texture_key_count');meta.s32(0x90,kp,'lighting_texture_keys')
    return assigned

def source_geometry(doc,blob,ids):
    chunks=[x for x in doc['chunks'] if x['id']in ids];assert len(chunks)==len(ids)
    geo=encode(chunks,blob);vertices=bytearray(geo['vertex'])
    from rsx_vertex_codec import decode_cmp32_be
    for i in range(geo['vertices']):
        o=i*24;p=np.array(struct.unpack_from('>3f',vertices,o));n=np.array(decode_cmp32_be(vertices[o+12:o+16]));p+=n*.00065
        struct.pack_into('>3f',vertices,o,*p);struct.pack_into('>2f',vertices,o+16,.5,.5)
    geo['vertex']=bytes(vertices);ps=np.ndarray((geo['vertices'],3),dtype='>f4',buffer=vertices,strides=(24,4)).copy();geo['positions']=ps.tolist();geo['bounds']=[ps.min(0).tolist(),ps.max(0).tolist()]
    return geo

def build(root,lod,native,route):
    src=BASE/'car/0004/0006'/lod;raw=(src/'body').read_bytes();m=g.MDL3(src/'body');meta=MaterialMeta(raw,'Supra_functional_lights_'+lod);current=inspect(src/'body')
    doc=json.loads((W/f'authoring/neutral/{lod}.json').read_text());blob=(W/f'authoring/neutral/{lod}.bin').read_bytes()
    tex=Textures();recipes=[('headlamps',9,tex.solid((255,226,182,255),'headlamp_warm_white')),('running_tail',17,tex.solid((100,2,1,255),'tail_running_red')),('brake_tail',17,tex.solid((245,10,4,255),'tail_brake_red')),('high_stop',9,tex.solid((245,10,4,255),'high_stop_red'))]
    materials=append_emit_materials(meta,native,route,recipes,tex);fvfs,_=meta.fvfs([route['materials'][9],route['materials'][17]])
    geometries={'headlamps':source_geometry(doc,blob,[123]),'running_tail':source_geometry(doc,blob,[71]),'brake_tail':source_geometry(doc,blob,[71]),'high_stop':source_geometry(doc,blob,[162])}
    shapes=[];shape_rows=[];shapemap={}
    for name,tid,_ in recipes:
        geo=geometries[name];sid=m.shape_count+len(shapes);shapemap[name]=sid;vp=meta.alloc('light_surface_vertices_'+name,geo['vertex'],128);ip=meta.alloc('light_surface_indices_'+name,geo['index']);bp=meta.alloc('light_surface_bounds_'+name,g.corners(geo['bounds']))
        template=bytes(48);fvf=fvfs[tuple(route['materials'][tid]['shader_key'])];flags=0x1809 if tid==9 else 0x3809
        shape_rows.append(shape_row(template,geo,fvf,materials[name],flags,vp=vp,ip=ip,bp=bp));shapes.append(dict(name=name,id=sid,vertices=geo['vertices'],triangles=geo['triangles'],material=materials[name],bounds=geo['bounds'],source_chunks=[x['mesh'] for x in geo['coverage']]))
    assert max(shapemap.values())<256
    table=meta.alloc('lighting_shapes',raw[m.shape_pointer:m.shape_pointer+48*m.shape_count]+b''.join(shape_rows));meta.s16(0x14,m.shape_count+len(shapes),'lighting_shape_count');meta.s32(0x38,table,'lighting_shape_table')
    keys=[];oldkp=g.u32(raw,0x3c)
    for i in range(g.u16(raw,0x16)):
        p,sid=struct.unpack_from('>II',raw,oldkp+8*i);keys.append((g.cstr(raw,p),p,sid))
    for name,sid in shapemap.items():name='|SUPRA_LIGHT_STATE|'+name;keys.append((name,meta.string(name),sid))
    keys.sort(key=lambda x:x[0].encode());kp=meta.alloc('lighting_shape_names',b''.join(struct.pack('>II',p,sid) for _,p,sid in keys));meta.s16(0x16,len(keys),'lighting_shape_key_count');meta.s32(0x3c,kp,'lighting_shape_keys')
    base_receipt=json.loads((BASE/'build_receipt.json').read_text());br=next(x for x in base_receipt['results'] if x['lod']==lod and x['kind']=='body');glass_mids={6,12,14,16,25,27};glass_shapes={x['shape'] for x in br['shapes'] if x['source_material']in glass_mids}
    checks=[]
    for model in current['models']:
        if model['id'] not in (0,5):continue
        insert=next(x['offset'] for x in model['commands'] if x.get('shape_id')in glass_shapes);context=next(x['context'] for x in model['commands'] if x.get('shape_id')in glass_shapes)
        call=lambda key:bytes((59,shapemap[key],context))
        program=switch(7,b'',call('headlamps'))+switch(0,switch(7,b'',call('running_tail')),call('brake_tail')+call('high_stop'))
        states={}
        for night in (0,1):
            for brake in (0,1):
                expected=([shapemap['headlamps']] if night else [])+([shapemap['brake_tail'],shapemap['high_stop']] if brake else [shapemap['running_tail']] if night else [])
                actual=evaluate(program,night,brake);assert actual==expected;states[f'night{night}_brake{brake}']=actual
        start=model['command_offset'];end=start+model['command_bytes'];assert all(x['opcode']==59 for x in model['commands'] if x['offset']<insert)
        combined=raw[start:insert]+program+raw[insert:end];cp=meta.alloc('native_light_control_model_'+str(model['id']),combined);meta.s32(model['offset']+24,cp,'light_model_commands');meta.s32(model['offset']+28,len(combined),'light_model_command_length')
        matp=g.u32(raw,0xb0)+64*model['id'];oldlist=g.u32(raw,matp);oldcount=g.u16(raw,matp+48);oldids=struct.unpack_from('>'+str(oldcount)+'H',raw,oldlist);update_model_materials(meta,model,set(oldids)|set(materials.values()));checks.append(dict(model=model['id'],truth_table=states,all_previous_commands_preserved=True))
    meta.finish_material_tables();out,details=meta.finish();folder=root/'car/0004/0006'/lod;folder.mkdir(parents=True);(folder/'body').write_bytes(out)
    for name in ('body_s','wheel'):shutil.copyfile(src/name,folder/name)
    after=inspect(folder/'body');assert len(after['shapes'])==m.shape_count+4
    for model in range(m.model_count):
        if model in(0,5):continue
        a=current['models'][model];b=after['models'][model];assert raw[a['command_offset']:a['command_offset']+a['command_bytes']]==out[b['command_offset']:b['command_offset']+b['command_bytes']]
    return dict(lod=lod,shapes=shapes,material_ids=materials,state_checks=checks,metadata=details,original_visible_geometry_preserved=True,original_wheel_and_stream_bytes_preserved=True,all_shadow_programs_preserved=True)

def fit_projector_origins(root):
    raw=(BASE/'car/0004/0006/info').read_bytes();b=bytearray(raw);main=struct.unpack_from('>I',b,16)[0];count,p=struct.unpack_from('>II',b,main+24);assert count==6
    # Fit native emitting origins to the same model's front optics. Preserve all
    # rotation, colour, beam gain, cutoff, attenuation and unknown fields.
    doc=json.loads((W/'authoring/neutral/hq.json').read_text());blob=(W/'authoring/neutral/hq.bin').read_bytes();changes=[]
    for i in range(count):
        off=p+i*128;origin=np.array(struct.unpack_from('>3f',b,off+48));sid=106 if i<2 else 173 if i<4 else 168;ch=next(x for x in doc['chunks'] if x['id']==sid);v=ch['buffers']['positions'];ps=np.frombuffer(blob,dtype='<f4',count=v['count']*3,offset=v['byte_offset']).reshape(-1,3).copy()
        if sid==173:ps[:,0]=np.abs(ps[:,0])*np.sign(origin[0])
        ps=ps[np.sign(ps[:,0])==np.sign(origin[0])];target=(ps.min(0)+ps.max(0))*.5;delta=target-origin
        assert np.linalg.norm(delta)<.08,(i,delta)
        for relative in (48,84):
            old=np.array(struct.unpack_from('>3f',b,off+relative));struct.pack_into('>3f',b,off+relative,*(old+delta))
        changes.append(dict(record=i,source_mesh=sid,old_origin_m=origin.tolist(),new_origin_m=target.tolist(),translation_m=delta.tolist()))
    allowed={p+i*128+off+j for i in range(count) for off in (48,84) for j in range(12)};assert all(a==c or i in allowed for i,(a,c) in enumerate(zip(raw,b)))
    (root/'car/0004/0006/info').write_bytes(b);shutil.copyfile(BASE/'car/0004/0006/interior',root/'car/0004/0006/interior');return changes

def main():
    ap=argparse.ArgumentParser();ap.add_argument('--name',default='supra_functional_lights_v017');a=ap.parse_args();assert a.name.replace('_','').isalnum();root=W/'candidates'/a.name;root.mkdir(parents=True,exist_ok=False)
    native=(NATIVE/'hq/body').read_bytes();route=inspect(NATIVE/'hq/body');results=[]
    for lod in ('hq','race'):results.append(build(root,lod,native,route));print('LIGHTS_BUILT',lod,flush=True)
    fit=fit_projector_origins(root)
    receipt=json.loads((BASE/'build_receipt.json').read_text());receipt.update(runtime_tested=False,lighting_revision=dict(base=str(BASE),surfaces=results,projector_fit=fit,night_selector=7,brake_selector=0,native_emission_families=[route['materials'][i] for i in(9,17)],physical_ps3_tested=False))
    (root/'build_receipt.json').write_text(json.dumps(receipt,indent=2,allow_nan=False));print('LIGHTING_BUILD_COMPLETE',str(root))
if __name__=='__main__':main()
