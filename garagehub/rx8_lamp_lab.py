"""F50 source-backed head/running/brake draw-state lab. Not installable.

Reuses genuine source head and rear lens surfaces, with an explicit native
conditional switch. Does not invent a separate rear lamp or high-stop mesh.
"""
from __future__ import annotations
from PIL import Image
import numpy as np
from .errors import GarageError
from .native_editor import need
from .car_geometry import batches,switch,evaluate
from .rx8_body_materials import TEMPLATES,same_sampler_bindings

LAMP_ROLE_KEYS=('headlight','taillight','brakelight')
SOURCE_EXPECTED={'n25p0','n26p0','n10p0'}
INTENSITY={'headlamp':1.0,'running':0.30,'brake':1.0}

def state_program(lights,ctx):
    need(set(lights)=={'headlamp','running','brake'},'Incomplete F50 lamp emitter classification.')
    need(lights['headlamp'] and lights['running'] and lights['brake'],
         'Known F50 source must provide front and shared rear lens surfaces.')
    draw=lambda ids:b''.join(bytes((59,sid,ctx)) for sid in ids)
    command=switch(7,b'',draw(lights['headlamp']))+switch(
        0,switch(7,b'',draw(lights['running'])),draw(lights['brake']))
    table={}
    for night in (False,True):
        for brake in (False,True):
            expected=(lights['headlamp'] if night else [])+(
                lights['brake'] if brake else lights['running'] if night else [])
            got=evaluate(command+b'\0',night,brake)
            need(got==expected,'Native F50 lamp switch did not match requested state.')
            table[f'night{int(night)}_brake{int(brake)}']=got
    return command,table

def emitter_recipe(materials,source_id,kind):
    """Clone a reviewed RX8 emission shader using authentic source texels."""
    need(kind in INTENSITY,'Unsupported emission level.')
    mat=materials.scene.materials[source_id]
    im=materials.textures.source_image(mat).convert('RGBA')
    pixels=np.asarray(im,dtype=np.uint8).copy()
    pixels[:,:,:3]=np.clip(np.rint(pixels[:,:,:3].astype(float)*INTENSITY[kind]),0,255).astype(np.uint8)
    pixels[:,:,3]=255
    image_id=materials.editor.image(Image.fromarray(pixels,'RGBA'),f'f50_{materials.lod}_{kind}_{source_id}')
    template=TEMPLATES[materials.lod]['lamp']
    names={'diffuseMapSampler':image_id,'emissionMapSampler':image_id}
    bindings=same_sampler_bindings(materials.editor,template,names)
    mid=materials.editor.clone_material(template,f'f50_{materials.lod}_{kind}_{source_id}',bindings)
    return {'native_material':mid,'fvf':materials.editor.fvf(template),
            'constant_uv':(.5,.5) if mat['base_texture'] is None else None,
            'image_id':image_id,'source_material':source_id,'kind':kind}

def emit_lamp_surfaces(scene,inspection,materials,editor,lod,origin,matrix,first_slot):
    role={r['id']:r['role'] for r in inspection['parts']}
    selected=[p for p in scene.parts if role.get(p.id) in LAMP_ROLE_KEYS]
    need({p.id for p in selected}==SOURCE_EXPECTED,'Reviewed F50 lamp source inventory changed.')
    source_by_role={k:[p for p in selected if role[p.id]==k] for k in LAMP_ROLE_KEYS}
    need(len(source_by_role['headlight'])==2 and len(source_by_role['taillight'])==1 and
         not source_by_role['brakelight'],'F50 shared rear lens inventory changed.')
    source_rear=source_by_role['taillight']
    lamp_ids={'headlamp':[],'running':[],'brake':[]};records=[];points=[]
    for kind,parts in (('headlamp',source_by_role['headlight']),
                       ('running',source_rear),('brake',source_rear)):
        for part in parts:
            recipe=emitter_recipe(materials,part.material,kind)
            p=(part.positions.astype(float)-origin)@matrix.T
            n=part.normals.astype(float)@matrix.T
            n/=np.maximum(np.linalg.norm(n,axis=1)[:,None],1e-20)
            p=p+n*0.00075
            record={'id':part.id,'positions':p,'normals':n,'uv':part.uv.astype(float).copy(),
                    'faces':part.indices.astype(np.int64).copy()}
            if recipe['constant_uv'] is not None:
                record['native_uv']=np.tile(recipe['constant_uv'],(len(p),1))
            batches_out=list(batches([record],18000))
            need(len(batches_out)==1,'F50 source lamp exceeds a single reviewed draw slot.')
            vb,ib,vertices,source_ids=batches_out[0]
            sid=first_slot+len(records)
            need(sid<40,'Insufficient original RX8 static slots for source emissive geometry.')
            at=editor.m.u32(0x38)+sid*48
            need(editor.m.u32(at+8)==0 and editor.m.u32(at+20)==0,
                 'Only reviewed empty RX8 slots can hold conditional F50 emitters.')
            actual=editor.shape(vb,ib,vertices,recipe['fvf'],recipe['native_material'],
                                f'F50_{lod}_{kind}_{part.id}',replace=sid)
            need(actual==sid and source_ids==[part.id],'Lamp surface was not emitted verbatim.')
            lamp_ids[kind].append(sid);points.append(vertices)
            records.append({'kind':kind,'source_part':part.id,'source_material':part.material,
                            'shape':sid,'native_material':recipe['native_material'],
                            'triangles':len(ib)//6,'vertices':len(vertices),
                            'vertex_sha256':__import__('hashlib').sha256(vb).hexdigest(),
                            'index_sha256':__import__('hashlib').sha256(ib).hexdigest()})
    need(len(lamp_ids['headlamp'])==2 and len(lamp_ids['running'])==len(lamp_ids['brake'])==1,
         'F50 lens coverage does not match original authored surface inventory.')
    return lamp_ids,records,points
