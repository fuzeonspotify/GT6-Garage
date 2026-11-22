"""Profile-driven complete exterior assembly. No game install or launch.

Source body/glass and lamp surfaces become native MDL3 draw groups in both
showroom/race assets. The owned template supplies the rig, physics, native tyre
behaviour, shaders and shadows. This is a candidate builder, not runtime QA.
"""
from __future__ import annotations
from pathlib import Path
import collections,json,struct,shutil,zipfile,time
import numpy as np
from .errors import GarageError,Cancelled
from .util import hash_file,atomic_json,sha256
from .native import MDL3
from .native_editor import Editor,need
from .car_profiles import PROFILE,ASSETS,validate_template
from .car_geometry import prepare_parts,batches,switch,evaluate,check_cancel
from .car_materials import Materials
from . import wheels
from . import body_streams
from .ordinary_streams import validate_generated_streams
from .import_readiness import source_assessment
from .wheel_fit import tyre_fit_report

# Measured resident body and graphics payloads from the F50 which GT6 loaded
# in Arcade Single Race. These bound this Supra profile, not the GT6 engine.
BODY_BYTES = {'hq': 1018240, 'race': 979072}
GPU_BYTES = 480768


def _next_texture_edge(report, lod, previous):
    remaining = min(GPU_BYTES, BODY_BYTES[lod]-report['new_cut']) - report['gpu_tail_bytes_before']
    images = report['images']
    largest = max((max(im['width'], im['height']) for im in images), default=0)
    limits = [edge for edge in (256, 128, 64, 32, 16, 8, 4) if edge < largest and (previous is None or edge < previous)]
    for edge in limits:
        size = 0
        for im in images:
            scale = max(1, max(im['width'], im['height'])/edge)
            w, h = (max(4, round(im[key]/scale)) for key in ('width', 'height'))
            block = 8 if im['format']==134 else 16
            size += ((w//4 * (h//4) * block + 127)//128)*128
        if size <= remaining:
            return edge
    # Encoding can deduplicate cells. Measure the smallest real result once.
    if limits and remaining >= 0:
        return limits[-1]
    raise GarageError('The '+('garage' if lod=='hq' else 'race')+' body exceeds the tested Supra model loading budget. Simplify materials or body detail before exporting.', 'car_budget')


def plan(scene,project,template_root):
    checked=validate_template(template_root);source=source_assessment(scene,project)
    problems=[requirement['message'] for requirement in source['requirements']]
    return {'profile':PROFILE['id'],'label':PROFILE['label'],'ready':not problems,'actions':problems,'fit':source['fit'],'body_fit_status':source['body_fit_status'],'source_orientation':source['source_orientation'],'source_sha256':scene.digest,'parts':len(scene.parts),'source_triangles':scene.triangles,'inherited':PROFILE['inherited'],'limits':PROFILE['limits'],'template':checked,'output_kind':'Native exterior geometry for the explicitly selected new car entry or compatible replacement','runtime_verified':False}


def build_body(scene,project,template,lod,output,cancel):
    from .body_fit import resolve_body_fit
    return _build_body_with_fit(scene,project,template,lod,output,cancel,
                                fit_solution=resolve_body_fit(scene,project))


def _build_body_with_fit(scene,project,template,lod,output,cancel,*,fit_solution):
    """Internal serializer shared by independently reviewed developer routes.

    Production callers must continue through build_body/resolve_body_fit. This
    helper adds no HTTP route, new source capability or automatic approval.
    """
    data,stream,report,parts=_prepare_body_streams(scene,project,template,lod,cancel,fit_solution)
    output.write_bytes(data);output.with_name('body_s').write_bytes(stream)
    return report,parts


def _prepare_body_streams(scene,project,template,lod,cancel,fit_solution):
    raw=(template/lod/'body').read_bytes()
    parts,preparation,fit=prepare_parts(scene,project['inspection'],lod,cancel,fit_solution=fit_solution)
    edge=None
    initial=None
    while True:
        check_cancel(cancel)
        emitted_parts=[dict(p) for p in parts]
        data,stream,report=_serialize_body(scene,template,lod,cancel,raw,emitted_parts,edge)
        if initial is None:
            initial=len(data)
        if len(data)<=BODY_BYTES[lod] and report['gpu_tail_bytes_after']<=GPU_BYTES:
            break
        edge=_next_texture_edge(report,lod,edge)
    report.update(source_preparation=preparation,fit=fit,texture_loading={
        'maximum_texture_edge':edge, 'original_body_bytes':initial, 'body_bytes':len(data),
        'body_limit':BODY_BYTES[lod], 'gpu_bytes':report['gpu_tail_bytes_after'], 'gpu_limit':GPU_BYTES})
    return data,stream,report,emitted_parts


def _serialize_body(scene,template,lod,cancel,raw,parts,edge):
    ed=Editor(raw,texture_edge=edge);m=ed.m
    need(m.u16(0x14)==105 and ed.bank_size==79 and ed.banks==7,'Body template structure mismatch.')
    materials=Materials(scene,ed);groups=collections.defaultdict(list);emit={'headlight':[],'taillight':[],'brakelight':[]}
    for p in parts:
        role=p['role']
        if role in emit:
            emit[role].append(p)
            # Original unlit lamp material is still visible with the lights off.
            groups[('body',p['material'])].append(p)
        else:groups[(role,p['material'])].append(p)
    # Consolidate only byte-identical native material recipes, not arbitrary
    # different-looking surfaces. Reuse proven static shape IDs 0..32 first.
    merged={}
    for (role,mat),pp in sorted(groups.items()):
        recipe=materials.get(mat,role)
        for p in pp:
            if recipe['constant_uv'] is not None:p['native_uv']=np.tile(recipe['constant_uv'],(len(p['positions']),1))
        key=(recipe['material'],recipe['transparent'],recipe['variation'])
        if key not in merged:merged[key]=(recipe,[])
        merged[key][1].extend(pp)
    opaque=[];glass=[];allpoints=[];materials_used=set();source_coverage=[];slot=0
    for (mid,isglass,variation),(recipe,pp) in sorted(merged.items(),key=lambda item:(item[0][1],item[0][0])):
        check_cancel(cancel)
        for vb,ib,positions,ids in batches(pp,PROFILE['draw_triangles']):
            need(slot<len(PROFILE['static_slots']),"Body exceeds this profile's native draw slots. Reduce material variety or geometry; no components were silently omitted.")
            sid=ed.shape(vb,ib,positions,recipe['fvf'],recipe['material'],f'{lod}_surface_{mid}_{slot}',recipe['transparent'],recipe['variation'],replace=PROFILE['static_slots'][slot],streamed=True);slot+=1
            (glass if recipe['transparent'] else opaque).append(sid);materials_used.add(recipe['material']);allpoints.append(positions);source_coverage.extend(ids)
    # Other native models never reference slots 0..32 in this hash-pinned
    # profile. Only the two main programs do, and both are rebuilt below.
    ed.disabled_static_shapes=set(range(33))-set(opaque+glass)
    for sid in sorted(ed.disabled_static_shapes):
        empty=bytearray(48);struct.pack_into('>Hhh',empty,0,0x1001,-1,-1);ed.shape_rows[sid]=empty
    lights={};light_sources={}
    for key,source,mid in [('headlamp',emit['headlight'],75),('running',emit['taillight'],76),('brake',emit['taillight'],77),('stop',emit['brakelight'],78)]:
        ids=[];clones=[]
        for p in source:
            q=dict(p);q['positions']=p['positions']+p['normals']*.00065;q['native_uv']=np.tile([.5,.5],(len(q['positions']),1));clones.append(q)
        for vb,ib,positions,_ in batches(clones,PROFILE['draw_triangles']):
            need(not ids,'The reviewed emitter slot cannot contain multiple draw batches.')
            sid=ed.shape(vb,ib,positions,ed.fvf(mid),mid,f'{lod}_light_{key}',mid in(76,77),replace={'headlamp':101,'running':102,'brake':103,'stop':104}[key]);ids.append(sid);allpoints.append(positions)
        lights[key]=ids;light_sources[key]=[p['id'] for p in source];materials_used.add(mid)
    need(len(ed.shape_rows)<=PROFILE['static_shape_limit'],'Too many materials/draw groups for this profile. Combine compatible materials.')
    expected=set(p['id'] for p in parts);need(set(source_coverage)==expected,'Some source components were not emitted.')
    checks=[];points=np.vstack(allpoints)
    for mid,context in ((0,0),(5,17)):
        mp=m.u32(0x30)+mid*48;cp=m.u32(mp+24);size=m.u32(mp+28);offset=PROFILE['mechanical_offsets'][lod][str(mid)];tail=raw[cp+offset:cp+size]
        need(tail[0]==14 and tail[-1]==0,'Mechanical command suffix mismatch.')
        call=lambda ids:b''.join(bytes((59,i,context)) for i in ids)
        light=switch(7,b'',call(lights['headlamp']))+switch(0,switch(7,b'',call(lights['running'])),call(lights['brake']+lights['stop']))
        states={}
        for night in(0,1):
            for brake in(0,1):
                want=(lights['headlamp'] if night else [])+(lights['brake']+lights['stop'] if brake else lights['running'] if night else [])
                got=evaluate(light,night,brake);need(got==want,'Lamp state truth table mismatch.');states[f'night{night}_brake{brake}']=got
        prefix=(bytes((59,95,17)) if mid==5 else b'')+call(opaque)+light+call(glass)
        ed.model(mid,prefix+tail,materials_used,points);checks.append({'model':mid,'lamp_states':states,'mechanical_tail_sha256':sha256(tail),'mechanical_tail_retained':True})
    stream,stream_report=body_streams.attach(ed,(template/lod/'body_s').read_bytes())
    data,report=ed.finish()
    ordinary_validation=validate_generated_streams(data,stream,[row for row in report['added_shapes'] if row['streamed']])
    decoded=body_streams.groups(data,stream);main=next(g for g in decoded if g['label']=='//0');cpu=b''.join(x['raw'] for x in main['records'][:main['na']])
    for row in stream_report['bindings']:
        need(sha256(cpu[row['vertex_pointer']:row['vertex_pointer']+row['vertex_bytes']])==row['vertex_sha256'] and sha256(cpu[row['index_pointer']:row['index_pointer']+row['index_bytes']])==row['index_sha256'],'Streamed geometry failed independent decompression.')
    # Independent shape data check (not the builder's mutable buffers).
    parsed=MDL3(data);parsed.validate()
    for row in report['added_shapes']:
        if row['streamed']:continue
        sp=parsed.u32(0x38)+row['id']*48;stride=parsed.fvfs()[parsed.s16(sp+2)]['stride'];vp=parsed.u32(sp+12);ip=parsed.u32(sp+24)
        need(sha256(parsed.span(vp,row['vertices']*stride))==row['vertex_sha256'],'Exported body vertices differ.');need(sha256(parsed.span(ip,row['triangles']*6))==row['index_sha256'],'Exported body indices differ.')
    for mid in range(m.u16(0x10)):
        if mid in(0,5):continue
        mp=m.u32(0x30)+48*mid;cp=m.u32(mp+24);n=m.u32(mp+28);need(data[cp:cp+n]==raw[cp:cp+n],'Unrelated model commands changed.')
    report.update(lod=lod,source_parts=len(parts),source_components_covered=sorted(expected),lamp_sources=light_sources,light_checks=checks,material_notes=materials.notes,static_triangles=sum(len(p['faces']) for p in parts),native_gpu_streams_retained=True,disabled_obsolete_static_slots=sorted(ed.disabled_static_shapes),body_stream=stream_report,ordinary_stream_validation=ordinary_validation,shadow_geometry_inherited=True)
    return data,stream,report


def showroom_wheels(scene,inspection,template,output,cancel):
    raw=template.read_bytes();ed=Editor(raw);m=ed.m
    need(sha256(raw)==ASSETS['hq/wheel'],'Unknown showroom wheel template.')
    records=[]
    # geometry() can select roles separately without changing the race adapter.
    for ci,corner in enumerate(('FL','FR','RL','RR')):
        allp=[]
        for role,sid,mid in [('rim',8+ci*2,6),('valve',9+ci*2,7)]:
            selected={p['id'] for p in inspection['parts'] if p['role']==role and p['corner']==corner}
            if not selected and role=='valve':
                # Hide this absent template valve in the model draw list below.
                records.append({'corner':corner,'role':role,'omitted':True,'shape':sid});continue
            vb,ib,positions,faces,parts=wheels.geometry(scene,inspection,corner,max_triangles=5200 if role=='rim' else 500,cancel=cancel,roles=(role,))
            # Native HQ rim and valve templates have uniform colour textures.
            b=bytearray(vb)
            for off in range(0,len(b),28):struct.pack_into('>2f',b,off+16,.5,.5)
            ed.shape(bytes(b),ib,positions,1,mid,f'hq_{corner}_{role}',replace=sid);allp.append(positions);records.append({'corner':corner,'role':role,'shape':sid,'triangles':len(faces),'source':parts})
        calls=[ci,8+ci*2]+([9+ci*2] if any(p['id'] in {r['id'] for r in inspection['parts'] if r['role']=='valve' and r['corner']==corner} for p in inspection['parts']) else [])
        program=b''.join(bytes((59,sid,ci)) for sid in calls)+b'\0';ed.model(ci,program,{5,6,7},np.vstack(allp))
    data,report=ed.finish();output.write_bytes(data);return {'shapes':records,'checks':report,'native_hq_tyres_preserved':True,'tyre_fit':tyre_fit_report(scene,inspection)}


def fit_projectors(raw,prepared):
    b=bytearray(raw)
    if b[:4]!=b'CAR5':raise GarageError('Unknown car-info attachment format.')
    main=struct.unpack_from('>I',b,16)[0];count,ptr=struct.unpack_from('>II',b,main+24)
    if count!=6 or ptr+count*128>len(b):raise GarageError('Unexpected headlamp-projector layout.')
    source=[p['positions'] for p in prepared if p['role']=='headlight'];need(bool(source),'No headlamp surfaces assigned.');points=np.vstack(source);changes=[]
    for i in range(count):
        off=ptr+i*128;old=np.asarray(struct.unpack_from('>3f',b,off+48));side=np.sign(old[0]);p=points[np.sign(points[:,0])==side]
        need(len(p)>0,'Headlamps must include both left and right emitting surfaces.')
        target=(p.min(0)+p.max(0))/2;delta=target-old
        for rel in(48,84):v=np.asarray(struct.unpack_from('>3f',b,off+rel));struct.pack_into('>3f',b,off+rel,*(v+delta))
        changes.append({'record':i,'origin':target.tolist(),'translation':delta.tolist()})
    allowed={ptr+i*128+off+k for i in range(count) for off in(48,84) for k in range(12)};need(all(x==y or i in allowed for i,(x,y) in enumerate(zip(raw,b))),'Unrelated CAR5 bytes changed.')
    return bytes(b),changes


def build_car(scene,project,template_root,output:Path,cancel=None,progress=None):
    start=time.time();report=plan(scene,project,template_root)
    if not report['ready']:raise GarageError(' '.join(report['actions']),'car_review')
    template=Path(report['template']['root']);output=Path(output)
    if output.exists():raise GarageError('Candidate output already exists.')
    output.mkdir(parents=True);root=output/PROFILE['model_path'];root.mkdir(parents=True)
    notify=lambda text:progress(text) if progress else None
    try:
        details={};hqparts=None
        for lod in('hq','race'):
            check_cancel(cancel);notify('Building '+('showroom' if lod=='hq' else 'race')+' body, glass and lamp surfaces');folder=root/lod;folder.mkdir()
            details[lod],parts=build_body(scene,project,template,lod,folder/'body',cancel)
            if lod=='hq':hqparts=parts
        notify('Building all four race and showroom rims');race,rr=wheels.build(scene,project['inspection'],(template/'race/wheel').read_bytes(),cancel);(root/'race/wheel').write_bytes(race)
        hq=showroom_wheels(scene,project['inspection'],template/'hq/wheel',root/'hq/wheel',cancel)
        # Standalone rim exports use advisory body alignment. A whole-car
        # receipt must instead measure the body transform actually approved
        # for this build; the independent native rim geometry stays unchanged.
        from .body_fit import resolve_body_fit
        matrix,translation,_=resolve_body_fit(scene,project)
        for wheel_report in (rr,hq):
            wheel_report['tyre_fit']=tyre_fit_report(scene,project['inspection'],matrix,translation)
        info,projectors=fit_projectors((template/'info').read_bytes(),hqparts);(root/'info').write_bytes(info);shutil.copyfile(template/'interior',root/'interior')
        hashes={p.relative_to(output).as_posix():hash_file(p) for p in root.rglob('*') if p.is_file()}
        check_cancel(cancel);validate_template(template)
        receipt={'schema':1,'kind':'profile-native-exterior-candidate','passed':True,'profile_id':PROFILE['id'],'model_path':PROFILE['model_path'],'car_id':PROFILE['car_id'],'source_sha256':scene.digest,'name':project['name'],'source_license':project.get('license',{}),'source_parts':len(scene.parts),'source_orientation':report['source_orientation'],'body':details,'race_wheels':rr,'showroom_wheels':hq,'projectors':projectors,'source_virtual_assets':hashes,'inherited':PROFILE['inherited'],'limitations':PROFILE['limits'],'runtime_tested':False,'physical_ps3_tested':False,'game_installation_changed':False,'additional_database_entry':False,'seconds':time.time()-start}
        receipt['geometry_review']=report['fit']
        atomic_json(output/'build_receipt.json',receipt);notify('Native candidate complete; ready for isolated repacking');return receipt
    except Exception:
        atomic_json(output/'FAILED.json',{'release_ready':False,'reason':'Build stopped; incomplete candidate must not be installed.'});raise
