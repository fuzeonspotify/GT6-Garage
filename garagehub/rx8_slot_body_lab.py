"""Expert-only F50 ORIGINAL STATIC SLOT integration experiment.

Retargets the hash-pinned RX-8's existing 40 body draw slots, not unsupported
appended shapes. Preserves all donor mechanical command suffixes, other model
bytecode, native GPU resources and the unmodified body_s stream. Despite
linked body draws, this is NOT a new car/FTP package or an approved donor fit.
"""
from __future__ import annotations
from pathlib import Path
import struct
import numpy as np
from .errors import GarageError
from .glb import GLB
from .car_profiles import source_recipe
from .recognition import infer
from .rx8_body_materials import RX8BodyMaterials,ROLE_SET
from .donor_profiles import inspect_rx8
from .f50_wheel_adapter import F50_SOURCE_SHA256
from .native import MDL3
from .native_editor import Editor,need
from .car_geometry import batches
from .rx8_lamp_lab import emit_lamp_surfaces,state_program
from .rx8_stream_guard import check_native_stream_ownership
from .body_streams import groups as body_stream_groups
from .packed_streams import audit_packed_streams
from .util import sha256,hash_file,atomic_json

# Offsets independently inspected in the original, hash-verified RX8 GT6 1.22
# model 0/5 programs. Everything from this offset to the terminator is the
# donor's original mechanical suffix. Never search an arbitrary byte pattern
# in an unverified car file and assume it marks the same instruction.
SUFFIX={('hq',0):204,('hq',5):207,('race',0):215,('race',5):207}
SUFFIX_HEAD=bytes.fromhex('0e 05 00 0b 02 00 04 00 20 09 1e')
STATIC_SLOTS=tuple(range(40))


def audit_candidate_stream(model:bytes,stream:bytes,slots,*,variant):
    """Audit the serialized candidate pair without changing or approving it."""
    selected=tuple(slots)
    main=[group for group in body_stream_groups(model,stream) if group['label']=='//0']
    need(len(main)==1,'Expected exactly one candidate main body stream.')
    cpu=b''.join(row['raw'] for row in main[0]['records'][:main[0]['na']])
    audit=audit_packed_streams(model,cpu,selected_slots=selected)
    selected_issues=[issue for issue in audit['issues'] if issue['shape'] in selected]
    audit.update(candidate_variant=variant,candidate_body_sha256=sha256(model),
                 candidate_stream_sha256=sha256(stream),candidate_cpu_sha256=sha256(cpu),
                 selected_slots=list(selected),selected_issue_count=len(selected_issues),
                 selected_slots_with_issues=sorted({issue['shape'] for issue in selected_issues}),
                 payload_bytes_reclaimed=False,runtime_verified=False,
                 interpretation='Packed-binding consistency only. This does not establish stream-loader behavior, rendering correctness, or permission to reclaim any payload.')
    return audit


def linked_program(donor:MDL3,lod,mid,opaque,glass,lamp_program=b''):
    need(mid in (0,5) and lod in ('hq','race'),'Unsupported mechanical body program.')
    mp=donor.u32(0x30)+mid*48
    old=donor.span(donor.u32(mp+24),donor.u32(mp+28));cut=SUFFIX[lod,mid]
    need(old[cut:cut+len(SUFFIX_HEAD)]==SUFFIX_HEAD and old[-1:]==b'\0',
         'RX8 mechanical suffix changed; refusing to modify native draw commands.')
    # Model 5 presents the original driver rig, using its known original
    # context. Original model 0 has no such leading driver call.
    prefix=bytes((59,96,17)) if mid==5 else b''
    ctx=17 if mid==5 else 0
    draws=b''.join(bytes((59,sid,ctx)) for sid in (*opaque,*glass))
    need(len(set((*opaque,*glass)))==len(opaque)+len(glass),'Duplicate F50 draw call.')
    need(all(sid in STATIC_SLOTS for sid in (*opaque,*glass)),'F50 draw leaves reviewed original static slots.')
    opaque_draws=b''.join(bytes((59,sid,ctx)) for sid in opaque)
    glass_draws=b''.join(bytes((59,sid,ctx)) for sid in glass)
    need(isinstance(lamp_program,bytes),'Native lamp switching must be prevalidated bytes.')
    program=prefix+opaque_draws+lamp_program+glass_draws+old[cut:]
    need(program.endswith(old[cut:]) and len(draws)//3<=40,'Mechanical suffix not retained.')
    return program,sha256(old[cut:])


def build_slot_lod(scene,inspection,donor,lod,lamps=False,donor_stream=None):
    need(scene.digest==F50_SOURCE_SHA256 and len(scene.parts)==62,'F50 source fingerprint/inventory changed.')
    need(isinstance(donor_stream,bytes) and donor_stream,'Original RX-8 body_s stream is required for inline shape ownership review.')
    m=MDL3(donor);ed=Editor(donor);need(m.u16(0x14)==102 and ed.banks==2,'Wrong RX8 static template.')
    # Unlike the detached v038 lab this shares only byte-equivalent material
    # recipes, allowing all 46 distinct source groups in <=40 original slots.
    materials=RX8BodyMaterials(scene,ed,lod,passive_source_lenses=lamps);roles={x['id']:x for x in inspection['parts']}
    centers=np.asarray(list(inspection['wheel_centers'].values()),float)
    need(centers.shape==(4,3),'Four original F50 wheel centres required.')
    center=centers.mean(0);M=np.diag([-1.,1.,-1.]);origin=np.array((center[0],0.,center[2]))
    groups={};expected=[];source_triangle_count=0
    for part in scene.parts:
        role=roles[part.id]['role']
        if role not in ROLE_SET:continue
        recipe=materials.recipe(part.material,role)
        positions=(part.positions.astype(float)-origin)@M.T
        normals=part.normals.astype(float)@M.T
        faces=part.indices.astype(np.int64).copy()
        need(np.isfinite(positions).all() and np.isfinite(normals).all(),
             'F50 body group has nonfinite source geometry.')
        record={'id':part.id,'role':role,'material':part.material,
                'positions':positions,'normals':normals,
                'uv':part.uv.astype(float).copy(),'faces':faces}
        if recipe['constant_uv'] is not None:
            record['native_uv']=np.tile(recipe['constant_uv'],(len(positions),1))
        key=(recipe['native_material'],recipe['fvf'],recipe['transparent'])
        groups.setdefault(key,[]).append(record)
        expected.append(part.id);source_triangle_count+=len(faces)
    opaque=[];glass=[];reports=[];output_triangles=0;allpts=[];covered=[]
    for (mid,fvf,transparent),parts in sorted(groups.items(),key=lambda x:(x[0][2],x[0][0])):
        for vb,ib,points,source_ids in batches(parts,18000):
            sid=len(reports)
            need(sid<len(STATIC_SLOTS),'F50 body exceeds original RX8 static slots. No output written.')
            actual=ed.shape(vb,ib,points,fvf,mid,'F50_SOURCE_'+lod+'_'+str(sid),
                            transparent=transparent,replace=STATIC_SLOTS[sid])
            need(actual==STATIC_SLOTS[sid],'Original-slot mapping changed.')
            (glass if transparent else opaque).append(sid);covered.extend(source_ids)
            output_triangles+=len(ib)//6;allpts.append(points)
            reports.append({'shape':sid,'native_material':mid,'source_part_ids':source_ids,
                            'triangles':len(ib)//6,'vertex_sha256':sha256(vb),
                            'index_sha256':sha256(ib),'transparent':transparent})
    need(output_triangles==source_triangle_count and set(covered)==set(expected),
         'Some F50 source body triangles or parts were omitted.')
    need(opaque and glass and allpts,'F50 source requires opaque body and source glass.')
    lamp_ids=None;lamp_records=[];lamp_points=[];lamp_tables={}
    if lamps:
        lamp_ids,lamp_records,lamp_points=emit_lamp_surfaces(
            scene,inspection,materials,ed,lod,origin,M,len(reports))
    ownership=check_native_stream_ownership(donor,donor_stream,[r['shape'] for r in reports+lamp_records])
    allpoints=np.vstack(allpts+lamp_points);used_materials=[x['native_material'] for x in reports+lamp_records]
    tail_hashes={}
    for mid in (0,5):
        command,table=state_program(lamp_ids,17 if mid==5 else 0) if lamps else (b'',{})
        program,digest=linked_program(m,lod,mid,opaque,glass,command)
        if lamps:lamp_tables[str(mid)]=table
        ed.model(mid,program,used_materials,allpoints)
        tail_hashes[str(mid)]=digest
    data,done=ed.finish();out=MDL3(data);need(out.validate()['passed'],'F50 slot-body is not valid MDL3.')
    # Verify source-backed bytes after serialization, not just in Python buffers.
    fvfs=out.fvfs()
    for r in reports:
        at=out.u32(0x38)+r['shape']*48
        stride=fvfs[out.s16(at+2)]['stride'];nv=out.u32(at+8);ni=out.u32(at+20)
        need(stride==24 and ni//3==r['triangles'],'Native F50 original-slot layout differs.')
        need(sha256(out.span(out.u32(at+12),nv*stride))==r['vertex_sha256'] and
             sha256(out.span(out.u32(at+24),ni*2))==r['index_sha256'],
             'F50 material/mesh bytes changed after serialization.')
    for r in lamp_records:
        at=out.u32(0x38)+r['shape']*48
        stride=fvfs[out.s16(at+2)]['stride'];nv=out.u32(at+8);ni=out.u32(at+20)
        need(stride==24 and ni//3==r['triangles'] and nv==r['vertices'],
             'Native F50 lamp draw geometry differs after writing.')
        need(sha256(out.span(out.u32(at+12),nv*stride))==r['vertex_sha256'] and
             sha256(out.span(out.u32(at+24),ni*2))==r['index_sha256'],
             'Serialized F50 lamp emitter bytes changed.')
    for mid in range(m.u16(0x10)):
        oldmp=m.u32(0x30)+mid*48;newmp=out.u32(0x30)+mid*48
        old=m.span(m.u32(oldmp+24),m.u32(oldmp+28));new=out.span(out.u32(newmp+24),out.u32(newmp+28))
        if mid in (0,5):need(new.endswith(old[SUFFIX[lod,mid]:]),'Mechanical suffix changed.')
        else:need(old==new,'Unrelated RX8 model bytecode changed.')
    need(data[done['new_cut']:done['new_cut']+len(donor)-m.cut]==donor[m.cut:],
         'Original RX8 shader/GPU resource tail changed.')
    candidate_audit=audit_candidate_stream(data,donor_stream,
        [r['shape'] for r in reports+lamp_records],variant='inline_with_original_stream')
    return data,{'source_sha256':scene.digest,'lod':lod,
        'source_body_parts':len(expected),'source_triangles':source_triangle_count,
        'native_triangles':output_triangles,'original_static_slots_used':len(reports),
        'original_static_slots_available':len(STATIC_SLOTS),
        'new_native_materials':len(done['added_materials']),
         'native_textures_added':len(done['images']),
        'passive_base_source_lenses':sorted(set(p.id for p in scene.parts
            if roles[p.id]['role'] in ('headlight','taillight','brakelight'))) if lamps else [],
        'source_lens_shader_emission_disabled':bool(lamps),
        'source_lens_material_recipe_audit':[r for r in materials.report
            if r['source_role'] in ('headlight','taillight','brakelight')],
        'body_models_relinked':[0,5],'mechanical_suffix_sha256':tail_hashes,
        'other_model_bytecode_preserved':True,'native_gpu_tail_preserved':True,
        'body_s_original_intact_required':True,'stream_ownership_audit':ownership,
        'post_conversion_stream_audit':candidate_audit,
        'native_sha256':sha256(data),
        'lamp_state_lab_enabled':bool(lamps),'lamp_native_draws':lamp_ids,
        'lamp_truth_tables':lamp_tables,'lamp_shape_records':lamp_records,
        'verified_original_body_slots':[r['shape'] for r in reports],
        'source_part_ids':sorted(expected),'shape_records':reports,
        'runtime_verified':False,'installable':False,
        'limitations':['Native donor tyres/blur/physics do not match Ferrari physical radius/wheelbase.',
            'Native lamp state branches are emitted only in the opt-in lamp lab; light output remains untested in GT6.',
            'Retained RX8 packed descriptors/payloads and their runtime interaction with converted inline shapes remain unverified.',
            'This is NOT a distinct F50 car identity or tested FTP package.']}


def experiment(source,donor,output,lamps=False,sidecar_variant=False):
    source,donor,output=map(Path,(source,donor,output))
    if not source.is_file() or hash_file(source)!=F50_SOURCE_SHA256:
        raise GarageError('Original reviewed Ferrari F50 GLB required.','f50_slot_source')
    if output.exists() or not output.parent.is_dir() or output.parent.is_symlink():
        raise GarageError('Choose a fresh F50 original-slot experiment folder.','f50_slot_output')
    if {'pdipfs','usrdir','dev_hdd0','ps3_game'} & {p.lower() for p in output.resolve().parts}:
        raise GarageError('F50 original-slot experiment cannot write inside any game installation.','f50_slot_output')
    profile=inspect_rx8(donor);root=Path(profile['donor_root'])
    if output.resolve()==root.resolve() or root.resolve() in output.resolve().parents:
        raise GarageError('Output cannot modify the original RX8 donor.','f50_slot_output')
    scene=GLB(source.read_bytes()).scene();inspection=infer(scene,overrides=source_recipe(scene))
    builds={lod:build_slot_lod(scene,inspection,(root/lod/'body').read_bytes(),lod,lamps=lamps,donor_stream=(root/lod/'body_s').read_bytes()) for lod in ('hq','race')}
    variants={}
    if sidecar_variant:
        need(lamps,'Sidecar stream A/B requires source-backed native lamp variant.')
        from .rx8_stream_sidecar_lab import make_sidecar_variant
        for lod,(body,report) in builds.items():
            source_stream=(root/lod/'body_s').read_bytes()
            reviewed_slots=[x['shape'] for x in report['shape_records']+report['lamp_shape_records']]
            alternative,alt_stream,alt_report=make_sidecar_variant(body,(root/lod/'body').read_bytes(),source_stream,reviewed_slots)
            alt_report['post_conversion_stream_audit']=audit_candidate_stream(
                alternative,alt_stream,reviewed_slots,variant='inline_with_cleared_sidecar')
            variants[lod]=(alternative,alt_stream,alt_report)
    output.mkdir()
    for lod,(data,report) in builds.items():
        p=output/lod;p.mkdir();(p/'body_ORIGINAL_SLOT_LAB_NOT_INSTALLABLE.mdl3').write_bytes(data)
        atomic_json(p/'slot_receipt.json',report)
        if sidecar_variant:
            alternative,alt_stream,alt_report=variants[lod]
            (p/'body_ALT_SIDECAR_NOT_INSTALLABLE.mdl3').write_bytes(alternative)
            (p/'body_s_ALT_SIDECAR_NOT_INSTALLABLE').write_bytes(alt_stream)
            atomic_json(p/'sidecar_report.json',alt_report)
    (output/'DO_NOT_INSTALL.txt').write_text('Ferrari F50 source-derived original-slot native body lab. NOT INSTALLABLE.\n'
        'Unfitted RX8 tyres/physics, no verified lamp states, no separate database entry, no in-game QA.\n'
        'No PS3/FTP package. Use only for further isolated exporter research.\n',encoding='utf-8')
    receipt={'passed':True,'source_sha256':scene.digest,
             'builds':{lod:{k:v for k,v in report.items() if k not in ('shape_records','source_part_ids')}
                        for lod,(_,report) in builds.items()},
             'installable':False,'runtime_verified':False,'original_donor_changed':False,'lamp_state_lab_enabled':bool(lamps)}
    if sidecar_variant:
        receipt['alternate_sidecar_stream']={lod:report for lod,(_,_,report) in variants.items()}
    atomic_json(output/'LAB_RECEIPT.json',receipt)
    return receipt
