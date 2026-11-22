"""Experimental, deterministic four-rim converter for the reviewed Supra template.
Not a full-car importer. Retains native tyres, materials, spin blur and shadows.
Donor files come from the user's owned install; none are bundled with the app.
"""
from __future__ import annotations
import json, struct, math, io, zipfile
from pathlib import Path
import numpy as np
from .native import MDL3, corners, pack_normal
from .recognition import CORNERS
from .glb import unit_normals
from .util import sha256
from .errors import GarageError,Cancelled
from .wheel_fit import rim_frame,tyre_fit_report,normal_winding_report

TEMPLATES={
 '8afd50cf6c2b068b321878c4aeee5448b785e67a24bd1fa2799ff67969e6fd46':'stock-supra-race',
 'c8dcbfadfcd55bb83538b6f74374335cc57b5cc651d6e844355fc60c8daf38bb':'supra-v029',
}
RIM_UV=(.470703125,.517578125);VALVE_UV=(.626953125,.564453125)


def geometry(scene,inspection,corner,max_triangles=3200,cancel=None,roles=('rim','valve')):
    if not isinstance(roles,(tuple,list)) or not roles or any(r not in ('rim','valve') for r in roles):
        raise GarageError('The rim converter accepts rims and valves only. Brake components require their own reviewed native motion route.','mechanical_route')
    mapping={p['id']:p for p in inspection['parts']};chosen=[p for p in scene.parts if mapping[p.id]['corner']==corner and mapping[p.id]['role'] in roles]
    if not chosen:raise GarageError(f'Assign the {corner} {roles[0]} first.')
    center,matrix,translation,_=rim_frame(scene,inspection,corner)
    determinant=float(np.linalg.det(matrix))
    ns=[];ps=[];fs=[];uvs=[];offset=0;reports=[];total=sum(len(p.indices) for p in chosen)
    for p in chosen:
        if cancel and cancel.is_set():raise Cancelled()
        role=mapping[p.id]['role'];mat=scene.materials[p.material]
        if mat['base_texture'] is not None:raise GarageError('This rim template supports flat-colour rims/valves. Textured rim transfer needs a separate material recipe.', 'rim_material')
        pts=p.positions.astype(np.float64);faces=p.indices.astype(np.int64);normals=p.normals.astype(float);original=len(faces);simplified=False
        source_normal_winding=normal_winding_report(pts,normals,faces)
        target=max(64,int(max_triangles*original/total))
        if original>target:
            try:import fast_simplification
            except ImportError as e:raise GarageError('Automatic rim reduction is unavailable in this source environment. Use the Windows build or install fast-simplification.', 'simplifier_missing') from e
            pts,faces=fast_simplification.simplify(pts,faces,target_count=target,agg=5)
            pts=np.asarray(pts,float);faces=np.asarray(faces,np.int64)
            if not len(faces):raise GarageError('Automatic reduction removed a part. Reduce it manually or use a larger budget.')
            normals=unit_normals(pts,faces);simplified=True
        local=(pts-center)@matrix.T+translation;normal=normals@np.linalg.inv(matrix);ln=np.linalg.norm(normal,axis=1);normal/=np.maximum(ln[:,None],1e-20)
        flipped=determinant<0
        if flipped:faces=faces[:,[0,2,1]]
        if not np.isfinite(local).all() or not np.isfinite(normal).all():raise GarageError('Invalid converted rim geometry.')
        uv=np.tile(RIM_UV if role=='rim' else VALVE_UV,(len(local),1))
        ps.append(local);ns.append(normal);fs.append(faces+offset);uvs.append(uv);offset+=len(local)
        reports.append(dict(part=p.id,role=role,source_triangles=original,output_triangles=len(faces),simplified=simplified,generated_normals_after_reduction=simplified,winding_corrected=flipped,winding_policy='transform_determinant',transform_determinant=determinant,source_normal_disagreement=source_normal_winding['opposed_triangles']>0,source_normal_winding=source_normal_winding,output_normal_winding=normal_winding_report(local,normal,faces)))
    pts=np.concatenate(ps);normals=np.concatenate(ns);faces=np.concatenate(fs);uv=np.concatenate(uvs)
    if len(pts)>65535 or len(faces)>6000:raise GarageError('This rim exceeds the reviewed direct-draw budget.')
    vertex=bytearray()
    for p,n,t in zip(pts,normals,uv):vertex.extend(struct.pack('>3f',*p)+pack_normal(n)+struct.pack('>2f',*t)+bytes((64,64,64,255)))
    return bytes(vertex),faces.astype('>u2').tobytes(),pts,faces,reports


def build(scene,inspection,template:bytes,cancel=None):
    identity=sha256(template)
    if identity not in TEMPLATES:raise GarageError('Select the original Supra RZ race/wheel asset or the reviewed v029 wheel template. Unknown templates are not patched.', 'template_identity')
    m=MDL3(template);m.validate()
    if m.u16(0x10)!=8 or m.u16(0x14)!=16:raise GarageError('Unexpected Supra template layout.')
    b=bytearray(template[:m.cut]);tail=template[m.cut:]
    def alloc(data,alignment=16):
        b.extend(b'\0'*(-len(b)%alignment));p=len(b);b.extend(data);return p
    def put32(p,v):struct.pack_into('>I',b,p,v)
    def string(s):return alloc(s.encode()+b'\0',1)
    layouts=m.fvfs();fvfp=m.u32(0x40);base_row=template[fvfp:fvfp+120];rows=[template[fvfp+i*120:fvfp+(i+1)*120] for i in range(len(layouts))];mapping={}
    # Reuse existing v029 layouts where possible; stock donor gets owned tables.
    for ci in range(4):
        existing=next((r for r in layouts if r['name']=='Flex' and r['key']==ci and r['stride']==28),None)
        if existing:mapping[ci]=existing['id'];continue
        fields={'position':(0,3,2,0),'normal':(12,1,6,0),'map12':(16,2,2,0),'colorSet1':(24,4,4,0)}
        names={n:string(n) for n in fields};fb=b''.join(struct.pack('>I4B',names[n],*fields[n]) for n in sorted(fields));fp=alloc(fb)
        ap=alloc(m.span(m.u32(fvfp+116),16));row=bytearray(base_row)
        struct.pack_into('>III',row,0,string('Flex'),ci,fp);row[24:26]=bytes((4,28));struct.pack_into('>I',row,116,ap)
        mapping[ci]=len(rows);rows.append(bytes(row))
    put32(0x40,alloc(b''.join(rows)));struct.pack_into('>H',b,0x18,len(rows))
    report=[];source_triangles=0;output_triangles=0
    for ci,corner in enumerate(CORNERS):
        vb,ib,points,faces,parts=geometry(scene,inspection,corner,cancel=cancel);source_triangles+=sum(p['source_triangles'] for p in parts);output_triangles+=len(faces)
        vp=alloc(vb,128);ip=alloc(ib,128);bounds=np.array([points.min(0),points.max(0)]);bp=alloc(corners(bounds));sid=ci*3+1;sp=m.u32(0x38)+sid*48;row=bytearray(template[sp:sp+48])
        if any(row[6:8]+row[16:20]+row[28:36]):raise GarageError('Unsupported native shape extension.')
        struct.pack_into('>Hhh',row,0,0x1809,mapping[ci],ci);struct.pack_into('>II',row,8,len(points),vp);struct.pack_into('>II',row,20,len(faces)*3,ip);struct.pack_into('>HHII',row,36,0,len(faces),bp,0);b[sp:sp+48]=row
        mp=m.u32(0x30)+ci*48;count=m.u16(mp+18);oldbp=m.u32(mp+20)
        if count!=8:raise GarageError('Unexpected native model bound format.')
        prior=np.frombuffer(m.span(oldbp,96),dtype='>f4').reshape(8,3);union=np.vstack([points,prior]);bounds=np.array([union.min(0),union.max(0)]);newbp=alloc(corners(bounds));put32(mp+20,newbp)
        origin=np.array(struct.unpack_from('>3f',template,mp+4));radius=max(np.linalg.norm(np.array([x,y,z])-origin) for x in bounds[:,0] for y in bounds[:,1] for z in bounds[:,2]);struct.pack_into('>f',b,mp,float(np.nextafter(np.float32(radius),np.float32(np.inf))))
        report.append(dict(corner=corner,shape=sid,vertices=len(points),triangles=len(faces),parts=parts,bounds=bounds.tolist()))
    newcut=(len(b)+127)&~127;b.extend(b'\0'*(newcut-len(b)));delta=newcut-m.cut
    shader=m.u32(0x4c);tx=m.u32(0x48);pm=m.u32(0xd0)
    mirrors=[0x6c,shader+0x28,tx+0x20]
    if pm and m.u32(pm+12)==m.cut:mirrors.append(pm+12)
    for p in mirrors:
        if m.u32(p)!=m.cut:raise GarageError('Native extent mirror does not match template.')
        put32(p,newcut)
    relocations=[]
    ft=m.u32(shader+0x40)
    for i in range(m.u16(shader+0x34)):
        p=ft+32*i+4;old=m.u32(p);size=m.u32(p+4)
        if not m.cut<=old<=len(template)-size:raise GarageError('Fragment program pointer is not in the native tail.')
        put32(p,old+delta);relocations.append((old,old+delta,size))
    image_table=tx+m.u32(tx+28)-m.u32(tx+8)
    for i in range(m.u16(tx+22)):
        p=image_table+32*i;old=m.u32(p);size=m.u32(p+4)
        if old:
            if not m.cut<=old<=len(template)-size:raise GarageError('Image payload is outside native tail.')
            put32(p,old+delta);relocations.append((old,old+delta,size))
    put32(4,newcut+len(tail));data=bytes(b)+tail
    for old,new,n in relocations:
        if data[new:new+n]!=template[old:old+n]:raise GarageError('Native resource changed during relocation.')
    validation=MDL3(data).validate()
    # Verify that no native draw/branch bytecode was rewritten.
    for i in range(8):
        p=m.u32(0x30)+48*i;cp=m.u32(p+24);n=m.u32(p+28)
        if data[cp:cp+n]!=template[cp:cp+n]:raise GarageError('Native wheel-state commands changed.')
    return data,dict(schema=1,kind='experimental-native-wheel',template=TEMPLATES[identity],template_sha256=identity,source_sha256=scene.digest,wheel_sha256=sha256(data),source_triangles=source_triangles,output_triangles=output_triangles,corners=report,tyre_fit=tyre_fit_report(scene,inspection),checks=validation,native_tyres_blur_shadow_materials_retained=True,body_changed=False,game_installation_changed=False,runtime_verified=False,physical_ps3_tested=False,scope='Rim/valve conversion only. Not a complete car importer or an installable PDIPFS archive.')


def export_rims(scene,inspection,template_path:Path,output:Path,cancel=None):
    if output.exists():raise GarageError('Output already exists.')
    if template_path.stat().st_size>8*1024**2:raise GarageError('Template exceeds the safe budget.')
    data,report=build(scene,inspection,template_path.read_bytes(),cancel)
    with zipfile.ZipFile(output,'x',zipfile.ZIP_DEFLATED,compresslevel=5) as z:
        z.writestr('car/0004/0006/race/wheel',data);z.writestr('validation.json',json.dumps(report,indent=2));z.writestr('README.txt','EXPERIMENTAL SUPRA RACE-RIM ASSET\nNative tyres/blur/shadows retained; rims fitted to the Supra template.\nThis is NOT an installable PDIPFS package. Repack it with the rest of your mod using a matching index, then test an isolated copy. Do not upload this loose file as PDIPFS/K/4D.\nNo game is installed or launched by this exporter.\n')
    return {'path':str(output),'bytes':output.stat().st_size,'report':report}
