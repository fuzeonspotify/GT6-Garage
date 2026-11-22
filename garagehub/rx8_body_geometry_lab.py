"""Full original F50 BODY geometry -> detached native MDL3 prototype.

This makes material-connected native shape resources but deliberately keeps all
original donor model programs, so NO F50 mesh is drawn in GT6. It is NOT an
installable car. Finishing requires donor-safe model/stream/identity integration.
"""
from __future__ import annotations
from pathlib import Path
import numpy as np
from .errors import GarageError
from .car_profiles import source_recipe
from .recognition import infer
from .rx8_body_materials import RX8BodyMaterials,ROLE_SET
from .glb import GLB
from .donor_profiles import inspect_rx8
from .car_geometry import batches
from .native import MDL3
from .native_editor import Editor,need
from .util import sha256,hash_file,atomic_json
from .f50_wheel_adapter import F50_SOURCE_SHA256

def build_geometry_lod(scene,inspection,donor,lod):
    """No simplified/omitted source faces, parts or UVs. Geometry is UNLINKED."""
    ed=Editor(donor);m=ed.m;mat=RX8BodyMaterials(scene,ed,lod)
    mapping={p['id']:p for p in inspection['parts']}
    need(scene.digest==F50_SOURCE_SHA256 and len(scene.parts)==62,'F50 prepared inventory changed.')
    wheel_centers=np.array(list(inspection['wheel_centers'].values()),float)
    need(wheel_centers.shape==(4,3),'F50 physical four-wheel centres are missing.')
    center=wheel_centers.mean(0)
    # Source is physically scaled to F50's 4.480m length by the verified
    # source adapter. Reflect X and Z to native orientation, do NOT stretch
    # its genuine body to match the shorter RX-8's donor tyre/rig layout.
    matrix=np.diag([-1.,1.,-1.]);offset=np.array([center[0],0.,center[2]])
    source_groups=[];source_triangles=0;transformed={}
    for part in scene.parts:
        role=mapping[part.id]['role']
        if role not in ROLE_SET:continue
        p=(part.positions.astype(float)-offset)@matrix.T
        n=part.normals.astype(float)@matrix.T
        f=part.indices.astype(np.int64).copy()
        if np.linalg.det(matrix)<0:f=f[:,[0,2,1]]
        need(np.isfinite(p).all() and np.isfinite(n).all() and np.isfinite(part.uv).all(),
             'Nonfinite F50 authored body component.')
        need((f>=0).all() and f.max()<len(p),'F50 source index leaves vertex range.')
        record={'id':part.id,'role':role,'material':part.material,'positions':p,
                'normals':n,'uv':part.uv.astype(float).copy(),'faces':f}
        source_groups.append(record);source_triangles+=len(f);transformed[part.id]=record
    ids=[];reports=[];max_triangles=18000;total=0
    for group in source_groups:
        recipe=mat.recipe(group['material'],group['role'])
        if recipe['constant_uv'] is not None:
            group['native_uv']=np.tile(recipe['constant_uv'],(len(group['positions']),1))
        for vb,ib,points,source_ids in batches([group],max_triangles):
            sid=ed.shape(vb,ib,points,recipe['fvf'],recipe['native_material'],
                         'F50_DETACHED_'+lod+'_'+group['id'],transparent=recipe['transparent'],
                         streamed=False)
            ids.append(sid);triangles=len(ib)//6;total+=triangles
            reports.append({'id':sid,'part_ids':source_ids,'role':group['role'],'source_triangles':len(group['faces']),
                            'native_triangles':triangles,'vertex_sha256':sha256(vb),'index_sha256':sha256(ib),
                            'native_material':recipe['native_material'],
                            'authored_uv_preserved':recipe['constant_uv'] is None})
    need(total==source_triangles and len(ids)==len(set(ids)) and max(ids)<=255,
         'F50 native detached shape triangle coverage changed.')
    data,done=ed.finish();out=MDL3(data);need(out.validate()['passed'],'Native shape validation failed.')
    for rec in reports:
        at=out.u32(0x38)+48*rec['id'];stride=out.fvfs()[out.s16(at+2)]['stride'];n=out.u32(at+8);ni=out.u32(at+20)
        need(stride==24 and ni//3==rec['native_triangles'],'Native body shape layout differs.')
        vp=out.u32(at+12);ip=out.u32(at+24)
        need(sha256(out.span(vp,n*stride))==rec['vertex_sha256'] and
             sha256(out.span(ip,ni*2))==rec['index_sha256'],
             'Native serialized F50 body geometry lost authored attributes.')
    orig=MDL3(donor)
    need(data[done['new_cut']:done['new_cut']+len(donor)-orig.cut]==donor[orig.cut:],
         'Donor body native GPU tail changed.')
    for mid in range(orig.u16(0x10)):
        a=orig.u32(0x30)+48*mid;b=out.u32(0x30)+48*mid
        need(orig.span(orig.u32(a+24),orig.u32(a+28))==
             out.span(out.u32(b+24),out.u32(b+28)),
             'Detached geometry lab modified native model execution.')
    for i in range(orig.u16(0x14)):
        need(orig.span(orig.u32(0x38)+i*48,48)==out.span(out.u32(0x38)+i*48,48),
             'Detached geometry lab modified existing donor shape.')
    return data,{'source_sha256':scene.digest,'lod':lod,
        'source_triangle_count':scene.triangles,'body_parts':len(source_groups),
        'authored_body_triangles':source_triangles,'native_triangle_count':total,
        'native_shapes_added':len(ids),'native_materials_added':len(done['added_materials']),
        'native_images_added':len(done['images']),'native_file_sha256':sha256(data),
        'original_donor_shapes_and_models_byte_identical':True,
        'unreferenced_extra_shape_ids':ids,'source_wheel_centres_m':inspection['wheel_centers'],
        'source_wheelbase_m':inspection['wheelbase'],'native_chassis_fit_verified':False,
        'runtime_tested':False,'do_not_install':True,
        'failure_to_finish':['New shapes are NOT referenced by native model command bytecode.',
                             'Original RX-8 body/stream will remain visible instead of F50 body.',
                             'Original RX-8 tyre/rig geometry is not fitted to the original F50 wheel radius.',
                             'Lamp state branches, shadow and new SpecDB identity are unimplemented.'],
        'shapes':reports}

def experiment(source,donor,output):
    source,donor,output=map(Path,(source,donor,output))
    if not source.is_file() or hash_file(source)!=F50_SOURCE_SHA256:
        raise GarageError('Use the exact reviewed original Ferrari F50 GLB.','f50_geometry_source')
    if output.exists() or not output.parent.is_dir() or output.parent.is_symlink():
        raise GarageError('Choose a new regular experiment output path.','f50_geometry_output')
    prohibited={'pdipfs','usrdir','dev_hdd0','ps3_game'}
    if prohibited & {x.lower() for x in output.resolve().parts}:
        raise GarageError('Refusing F50 geometry experiment in a game installation.','f50_geometry_output')
    root=Path(inspect_rx8(donor)['donor_root'])
    if output.resolve()==root.resolve() or root.resolve() in output.resolve().parents:
        raise GarageError('Output may not overwrite the RX-8 donor.','f50_geometry_output')
    scene=GLB(source.read_bytes()).scene()
    inspection=infer(scene,overrides=source_recipe(scene))
    build={lod:build_geometry_lod(scene,inspection,(root/lod/'body').read_bytes(),lod)
           for lod in ('hq','race')}
    output.mkdir()
    for lod,(data,report) in build.items():
        (output/lod).mkdir()
        (output/lod/'body_DETACHED_SHAPES_NOT_INSTALLABLE.mdl3').write_bytes(data)
        atomic_json(output/lod/'geometry_receipt.json',report)
    (output/'DO_NOT_INSTALL.txt').write_text(
        'F50 original body-to-native geometry laboratory only. No native model commands\n'
        'reference the new detached shapes. It is NOT A COMPLETE CAR and must not be\n'
        'installed, repacked or uploaded to a PS3. Donor chassis/tyres are not fitted.\n',
        encoding='utf-8')
    r={'source':scene.digest,'builds':{lod:{k:v for k,v in report.items()if k!='shapes'}
       for lod,(_,report)in build.items()},'full_car_import_accepted':False,
       'game_files_changed':False,'installable':False}
    atomic_json(output/'LAB_RECEIPT.json',r)
    return r
