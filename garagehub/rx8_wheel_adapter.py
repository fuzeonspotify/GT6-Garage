"""F50-to-original-RX8 wheel experiment. No game install and NO acceptance claim.

Preserves every authored F50 metal-rim/emblem triangle and dimensions, maps
rim assemblies to an explicitly historical native reference mount, binds the
real emblem texture, and preserves the donor's shadow, spin/blur and other
models. Shadow geometry never supplies physical tyre dimensions. Tyre fit and
complete suspension/body integration remain unverified.
"""
from __future__ import annotations
import struct
from pathlib import Path
import numpy as np
from .errors import GarageError
from .native import MDL3,pack_normal
from .native_editor import Editor,need
from .car_materials import Materials
from .car_geometry import switch
from .recognition import axes,CORNERS
from .f50_wheel_adapter import F50_SOURCE_SHA256
from .donor_profiles import inspect_rx8,RX8_ASSETS
from .util import sha256,hash_file
from .wheel_mounts import rigid_axle_mount

# These are wheel-local->body matrices from the four independently matched
# HQ native wheel draws in the original RX8 control. They describe one garage
# pose; they are not static suspension pivots or a race-state verification.
# The source/donor geometry is NOT scaled to these captured matrices.
REFERENCE_EVIDENCE={
 'report':'reports/vertex_encoding/native_wheel_v002_capture_binding.json',
 'report_sha256':'27b3352509ef8f631a8a64c5dbe4a293d7e4db9535c5b2cfdc39ed065730c7f2',
 'capture_sha256':'5a750f59e2ae91ef279617ee77439ad679021768d2ea1b17a69ae58bda0eaa21',
 'reference_scope':'Historical HQ native-wheel control, one captured pose; not current candidate or race acceptance.',
}
REFERENCE_MOUNTS={
 'FL':((.9999619864148102,.00872653520850783,1.607008809209224e-17,-.8624962078631819),
       (-.008726538583954568,.9999620324308786,3.308722450212111e-24,.19948181321129987),
       (5.8809508883679276e-08,-1.2189180745060631e-09,.9999999999999999,-1.3500002579040085)),
 'FR':((-.9999619510603857,-.008726538007241023,-1.607008809209224e-17,.8624957900413772),
       (-.00872653493998265,.9999620312516772,-3.308722450212111e-24,.1994818105495632),
       (-1.0386660708466187e-08,-2.8146365138349934e-09,-.9999999999999999,-1.3499999174201638)),
 'RL':((.9996027494242381,.026176953273501974,-.010468086485657557,-.8674554028526953),
       (-.026175521038293435,.9996574538750984,.00027411920463289187,.2014447791043676),
       (.010471716849305396,-1.0531726879059511e-09,.9999452506077047,1.3488216022619306)),
 'RR':((-.9996026444098814,-.026176953627686256,-.010468227438526746,.8674557414033188),
       (-.02617552013974354,.9996574503378529,-.00027412341314488324,.20144477711280367),
       (.010471752602355998,4.3859891070474053e-10,-.9999452138758106,1.3488222255085442)),
}
RIM_MAIN_SOURCE='n41p0'
RIM_EMBLEM_SOURCE='n42p0'


def shadow_reference_geometry(editor,lod):
    """Measure the actual retained shadow meshes without calling them tyres."""
    need(lod in ('hq','race'),'Unknown wheel shadow LOD.')
    m=editor.m;fvfs=m.fvfs();first=4 if lod=='hq' else 12
    expected_material=4 if lod=='hq' else 8
    rows=[]
    for sid in range(first,first+4):
        at=m.u32(0x38)+sid*48;material=m.s16(at+4);fid=m.s16(at+2)
        need(material==expected_material and 0<=material<editor.bank_size,
             'Retained wheel shadow material contract changed.')
        name=m.string(m.u32(editor.material_ptr+material*52))
        need(name=='ShadowCBaseEnv1_NoDirt' and not editor.texture_keys(material),
             'The measured reference must be the reviewed shadow material, not a physical tyre.')
        need(editor.shader_key(material)==('Flex',1 if lod=='hq' else 8),
             'Retained wheel shadow shader contract changed.')
        need(0<=fid<len(fvfs),'Retained wheel shadow has no direct vertex layout.')
        layout=fvfs[fid];position=next((f for f in layout['fields'] if f['name']=='position'),None)
        need(position and position['format']==2 and position['components']==3 and position['stream']==0
             and position['offset']+12<=layout['stride'],'Unsupported wheel shadow position format.')
        vp=m.u32(at+12);nv=m.u32(at+8);ni=m.u32(at+20);ip=m.u32(at+24)
        need(vp and nv and ip and ni,'Retained shadow has no direct geometry.')
        vertex=m.span(vp,nv*layout['stride'],m.cut);index=m.span(ip,ni*2,m.cut)
        points=np.ndarray((nv,3),dtype='>f4',buffer=vertex,offset=position['offset'],
                          strides=(layout['stride'],4)).astype(float)
        need(np.isfinite(points).all(),'Wheel shadow positions are not finite.')
        bounds=np.array([points.min(0),points.max(0)]);extent=bounds[1]-bounds[0]
        rows.append({'shape':sid,'material':material,'material_name':name,
                     'shader':list(editor.shader_key(material)),'role':'shadow',
                     'coordinate_space':'native_wheel_local','bounds':bounds.tolist(),
                     'radial_aabb_radius':float(max(extent[1:])/2),'axial_width':float(extent[0]),
                     'vertex_sha256':sha256(vertex),'index_sha256':sha256(index),
                     'physical_tyre_measurement':False,'used_for_source_scale':False})
    return rows


def source_rim_records(scene,inspection,corner):
    """Pure, geometry-preserving coordinate/attribute conversion. No decimation."""
    if corner not in CORNERS:
        raise GarageError('Wheel profile geometry is not reviewed.','wheel_fit')
    if corner not in inspection['wheel_centers'] or corner not in inspection['wheel_radii']:
        raise GarageError('Four source tyre centres and radii must be reviewed.','wheel_fit')
    left,_,_=axes(inspection['forward'])
    center,matrix,offset,mount=rigid_axle_mount(inspection,corner,REFERENCE_MOUNTS[corner])
    source_r=float(inspection['wheel_radii'][corner])
    mapping={p['id']:p for p in inspection['parts']}
    tyres=[p for p in scene.parts if mapping[p.id]['role']=='tyre' and mapping[p.id]['corner']==corner]
    if not tyres:raise GarageError('No tyre geometry assigned to '+corner,'wheel_fit')
    bounds=np.array([p.bounds for p in tyres]);lat=int(np.argmax(np.abs(left)))
    source_w=float(bounds[:,1,lat].max()-bounds[:,0,lat].min())
    if not .1<source_r<1 or not .04<source_w<1:
        raise GarageError('Tyre centre/radius/width does not match a car wheel.','wheel_fit')
    inverse=np.linalg.inv(matrix)
    out=[]
    for source_id in (RIM_MAIN_SOURCE,RIM_EMBLEM_SOURCE):
        pid=source_id+'__'+corner
        part=next((p for p in scene.parts if p.id==pid),None)
        if part is None or mapping[pid]['role']!='rim' or mapping[pid]['corner']!=corner:
            raise GarageError('Missing independent source rim/emblem piece '+pid,'rim_source')
        pos=(part.positions.astype(float)-center)@matrix.T+offset
        normals=part.normals.astype(float)@inverse
        normals/=np.maximum(np.linalg.norm(normals,axis=1)[:,None],1e-20)
        faces=part.indices.astype(np.int64).copy()
        if np.linalg.det(matrix)<0:faces=faces[:,[0,2,1]]
        uv=part.uv.astype(float).copy()
        need(len(faces)>0 and len(pos)<=65535 and len(faces)<=20000,'Rim source exceeds native per-draw budget.')
        need(np.isfinite(pos).all() and np.isfinite(uv).all() and np.isfinite(normals).all(),'Nonfinite wheel source attribute.')
        if source_id==RIM_EMBLEM_SOURCE:
            need(scene.materials[part.material]['base_texture'] is not None and np.ptp(uv,axis=0).min()>.1,
                 'Emblem is textured but has invalid authored UVs.')
        vb=bytearray()
        for p,n,t in zip(pos,normals,uv if source_id==RIM_EMBLEM_SOURCE else np.full_like(uv,.5)):
            vb+=struct.pack('>3f',*p)+pack_normal(n)+struct.pack('>2f',*t)
        out.append({'id':pid,'material':part.material,'vertex':bytes(vb),'index':faces.astype('>u2').tobytes(),
                    'points':pos,'faces':faces,'uv_source':uv,'source_triangles':len(part.indices),
                    'new_triangles':len(faces),'source_vertices':len(part.positions),
                    'source_normal_alignment':winding_alignment(part.positions,part.normals,part.indices),
                    'native_normal_alignment':winding_alignment(pos,normals,faces)})
    return out,{**mount,'source_tyre_radius_m':source_r,'source_tyre_width_m':source_w,
                'donor_tyre_radius_m':None,'donor_tyre_width_m':None,
                'physical_tyre_dimensions_status':'unverified',
                'reference_evidence':dict(REFERENCE_EVIDENCE),
                'original_rim_triangles':sum(x['source_triangles'] for x in out)}


def winding_alignment(p,n,f):
    tri=np.asarray(p)[np.asarray(f,dtype=np.int64)]
    cross=np.cross(tri[:,1]-tri[:,0],tri[:,2]-tri[:,0])
    dots=np.einsum('ij,ij->i',cross,np.asarray(n)[f].mean(axis=1))
    valid=np.linalg.norm(cross,axis=1)>1e-12
    return float((dots[valid]>0).mean()) if valid.any() else 0.


def wheel_program_calls(program,state):
    """Decode only the independently observed RX8 wheel opcodes/states."""
    need(state in (0,1),'Invalid wheel speed/blur state.')
    calls=[];i=0
    while i<len(program):
        op=program[i]
        if op==0:return calls
        if op==59:
            need(i+3<=len(program),'Truncated wheel draw command.')
            calls.append((program[i+1],program[i+2]));i+=3
        elif op==9:
            need(i+2<=len(program),'Truncated wheel branch skip.')
            i+=2+program[i+1]
        elif op==5:
            need(i+8<=len(program) and program[i:i+4]==bytes((5,0,0,2)),
                 'Wheel branch is not the reviewed speed selector.')
            pos=i+4+2*state
            i=pos+2+struct.unpack_from('>H',program,pos)[0]
        else:raise GarageError('Unreviewed wheel bytecode opcode.','wheel_program')
    raise GarageError('Wheel command stream did not terminate.','wheel_program')


def insert_emblem_low_speed(old,main,emblem,context):
    """Add an emblem only to the native low-speed rim branch, not to blur."""
    expected=bytes((5,0,0,2,0,4,0,7,9,11,59,main,context,9,6))
    need(old[:15]==expected and old[-1:]==b'\0' and len(old)==22,
         'Original RX8 wheel state program differs from approved format.')
    high=wheel_program_calls(old,1)
    need(wheel_program_calls(old,0)==[(main,context)] and len(high)==2,
         'Unexpected native wheel speed-state truth table.')
    # Growing the low-speed case moves both the high case and the fallback
    # End target. Rebuild every switch offset while retaining both original
    # case bodies; patching only the case table leaves fallback in blur code.
    low=old[10:13]+bytes((59,emblem,context))
    program=switch(0,low,old[15:-1])+old[-1:]
    need(wheel_program_calls(program,0)==[(main,context),(emblem,context)]
         and wheel_program_calls(program,1)==high,
         'F50 emblem moved into the native speed-blur branch.')
    return program


def build_wheel_lod(scene,inspection,donor_bytes:bytes,lod:str):
    need(lod in ('hq','race'),'Invalid RX8 wheel LOD.')
    need(scene.digest==F50_SOURCE_SHA256,'This source-to-RX8 wheel experiment is pinned to the reviewed F50 GLB.')
    need(sha256(donor_bytes)==RX8_ASSETS[lod+'/wheel'],'RX8 wheel donor fingerprint differs from the reviewed original.')
    ed=Editor(donor_bytes);m=ed.m;materials=Materials(scene,ed)
    count=m.u16(0x14)
    need((count,m.u16(0x10),ed.banks)==((8,8,1) if lod=='hq' else (16,8,1)),'Unreviewed RX8 wheel rig.')
    shadow_geometry=shadow_reference_geometry(ed,lod)
    original_programs=[m.span(m.u32(m.u32(0x30)+48*i+24),m.u32(m.u32(0x30)+48*i+28)) for i in range(8)]
    # Material 39 is untextured metallic rim; material 40 carries a genuine
    # emblem texture. Both are authored parts, neither may be dropped.
    images={}
    for source_id in (39,40):
        img=materials.source_image(scene.materials[source_id])
        images[source_id]=ed.image(img,f'f50_rim_source_material_{source_id}')
    report=[]
    for ci,corner in enumerate(CORNERS):
        parts,fit=source_rim_records(scene,inspection,corner)
        main_sid=ci if lod=='hq' else 3*ci
        at=m.u32(0x38)+main_sid*48
        need(m.s16(at+2)==-1 and m.u32(at+8)==0 and m.u32(at+12)==0,
             'Main native wheel slot is not the reviewed packed-rim slot.')
        mid=ci
        template=ci if lod=='hq' else ci*2
        names=dict(ed.texture_keys(template))
        diff=names.get('diffuseMapSampler');need(diff is not None,'Native wheel lacks diffuse map sampler.')
        fvf=ed.fvf(template)
        shape_ids=[]
        for part in parts:
            bindings={name:images[part['material']] for name,pid in names.items() if pid==diff}
            need('diffuseMapSampler' in bindings and 'ambientMapSampler' in bindings,
                 'Native rim map aliases cannot be rebound coherently.')
            material=ed.clone_material(template,'f50_'+corner+'_'+part['id'],bindings)
            replace=main_sid if part['id'].startswith(RIM_MAIN_SOURCE) else None
            sid=ed.shape(part['vertex'],part['index'],part['points'],fvf,material,
                         'f50_'+corner+'_'+part['id'],replace=replace,streamed=False)
            shape_ids.append(sid)
        main,logo=shape_ids
        old=original_programs[mid]
        if lod=='hq':
            need(old==bytes((59,main,ci,0)),'Unexpected HQ rim draw command.')
            program=bytes((59,main,ci,59,logo,ci,0))
            need(wheel_program_calls(program,0)==[(main,ci),(logo,ci)],
                 'HQ custom wheel call order is invalid.')
        else:
            # Contexts are 1/3/5/7 (not 0/2/4/6). The emblem must
            # disappear when native fast-wheel/blur states become active.
            program=insert_emblem_low_speed(old,main,logo,ci*2+1)
        ed.model(mid,program,{ed.new_shapes[-2]['material'],ed.new_shapes[-1]['material']},
                 np.vstack([p['points'] for p in parts]))
        report.append({'corner':corner,'shape_ids':shape_ids,'source_triangles':fit['original_rim_triangles'],
                       'source_vertex_count':sum(p['source_vertices'] for p in parts),
                       'native_triangles':sum(p['new_triangles'] for p in parts),
                       'source_and_native_winding':[{'part':p['id'],'source':p['source_normal_alignment'],
                         'native':p['native_normal_alignment']} for p in parts],**fit})
    data,detail=ed.finish()
    for mi in range(4,8):
        at=m.u32(0x30)+mi*48;newm=MDL3(data);newat=newm.u32(0x30)+mi*48
        need(newm.span(newm.u32(newat+24),newm.u32(newat+28))==original_programs[mi],
             'Native shadow model commands changed.')
    # These direct meshes are shadow references, not tyres. Preserve their
    # vertex/index data and shader contract, but never use their envelope as
    # a target physical wheel size.
    output_shadows=shadow_reference_geometry(Editor(data),lod)
    need(output_shadows==shadow_geometry,'Retained native wheel shadow geometry changed.')
    # Signed 16-bit no lossy geometry elimination: every original F50 rim
    # triangle must appear in the native candidate and both split parts.
    need(sum(x['source_triangles'] for x in report)==(1743+16)*4,'F50 rim triangle coverage changed.')
    need(sum(x['native_triangles'] for x in report)==sum(x['source_triangles'] for x in report),
         'F50 authored rim triangle was omitted.')
    return data,{'lod':lod,'donor_sha256':sha256(donor_bytes),'native_sha256':sha256(data),
      'source_sha256':scene.digest,'wheels':report,'native_validator':MDL3(data).validate(),
      'source_rim_triangles_retained':True,'source_rim_dimensions_preserved':True,
      'native_shadow_and_blur_models_preserved':True,'native_shadow_geometry':shadow_geometry,
      'physical_tyre_dimensions_status':'unverified','physical_tyre_dimensions':None,
      'historical_mount_reference':dict(REFERENCE_EVIDENCE),
      'current_candidate_mount_verified':False,'race_mount_verified':False,
      'emblem_source_texture_transferred':True,'physical_f50_tyre_fit_verified':False,
      'full_f50_export':False,'game_runtime_verified':False,
      'material_conversion':'Native Flex shader approximation; actual appearance unverified.',
      'warning':'Native .250m reference geometry is a shadow mesh, not a measured tyre. Source rim dimensions are retained; tyre fit, body/axle alignment, suspension and current HQ/race appearance remain unverified.'}


def experiment_from_verified_donor(scene,inspection,donor_path,output):
    """No game install. Writes only reviewed output/new directory."""
    root=Path(donor_path);profile=inspect_rx8(root);root=Path(profile['donor_root'])
    dest=Path(output);need(not dest.exists(),'Select a new separate experiment directory.')
    generated={}
    for lod in ('hq','race'):
        generated[lod]=build_wheel_lod(scene,inspection,(root/lod/'wheel').read_bytes(),lod)
    dest.mkdir(parents=True,exist_ok=False)
    from .util import atomic_json
    for lod,(data,rep) in generated.items():
        (dest/lod).mkdir()
        (dest/lod/'wheel').write_bytes(data)
        atomic_json(dest/(lod+'_wheel_receipt.json'),rep)
    return {'source':scene.digest,'output':str(dest),'two_distinct_lod_wheels':True,
           'native_fit_to_f50_accepted':False,'installable_package':False,
           'donor_unchanged':True}
