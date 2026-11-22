"""Deterministic, source-hash-pinned F50 wheel-group preparation.

Separates four authored wheel corners without modifying the GLB, inventing
geometry, or requiring Blender.  This is a PREPARATION adapter; it does not
certify a native donor profile or a finished GT6 car.
"""
from __future__ import annotations
import numpy as np
from .errors import GarageError
from .glb import Part,Scene

F50_SOURCE_SHA256='9106a2181f66c3bcc8a7e24a54de29e23d4a4761382df3078ab1688a4c6b4249'
# Reviewed geometry/material provenance from the 50-component source.
F50_WHEEL_GROUPS={
 'n41p0':('material_39','rim',6972),
 'n42p0':('rim_emblem','rim',64),
 'n47p0':('tire','tyre',4800),
 'n48p0':('tire_side','tyre',4320),
}
F50_CORNERS=('FL','FR','RL','RR')
F50_MID_AXLE_Z=-.086
# The approved source-preparation audit normalizes the original 4.93421m
# visual envelope to Ferrari's documented 4.480m.  The source GLB bytes and
# original triangle topology are retained, with a reversible uniform scale.
F50_SOURCE_SCALE=.907946532743
# A source-specific, measured physical separation plane, not a universal
# heuristic applied to arbitrary four-wheeled files.
def split_four_corners(part:Part,role:str,mid_axle_z:float):
    faces=np.asarray(part.indices,np.int64)
    if len(faces)==0:raise GarageError('Wheel component is empty.','wheel_partition')
    triangles=part.positions[faces]
    x=triangles[:,:,0];z=triangles[:,:,2]
    if np.any(np.min(np.abs(x),axis=1)<.05) or np.any(np.min(np.abs(z-mid_axle_z),axis=1)<.10):
        raise GarageError('Wheel mesh crosses a quadrant boundary. Review axles; no geometry was dropped.','wheel_partition')
    side=np.all(x>0,axis=1)
    front=np.all(z>mid_axle_z,axis=1)
    if np.any(~(np.all(x>0,axis=1)|np.all(x<0,axis=1))) or np.any(~(np.all(z>mid_axle_z,axis=1)|np.all(z<mid_axle_z,axis=1))):
        raise GarageError('Mixed-corner wheel triangle; no geometry was emitted.','wheel_partition')
    pieces=[]
    for corner in F50_CORNERS:
        # +X is vehicle left for +Z front in this GLB's RH frame.
        take=(side==(corner[1]=='L'))&(front==(corner[0]=='F'))
        source_faces=faces[take]
        if len(source_faces)==0:raise GarageError('One of four wheel quadrants is empty.','wheel_partition')
        used,local=np.unique(source_faces,return_inverse=True)
        extras=dict(part.extras);extras.update(gt6_role=role,gt6_corner=corner,gt6_source_part=part.id,
                                             gt6_source_face_count=len(source_faces))
        pieces.append(Part(part.id+'__'+corner,part.name+' '+corner,part.node,
                           part.primitive,part.material,part.positions[used].copy(),
                           part.normals[used].copy(),part.uv[used].copy(),
                           local.reshape(-1,3).astype(np.uint32),extras,list(part.warnings)))
        # Independently prove every emitted triangle and its vertex attributes
        # still match source, not only an equal total triangle count.
        q=pieces[-1]
        if not np.array_equal(q.positions[q.indices],part.positions[source_faces]) or not np.array_equal(q.uv[q.indices],part.uv[source_faces]) or not np.array_equal(q.normals[q.indices],part.normals[source_faces]):
            raise GarageError('Wheel partition changed authored attributes.','wheel_partition')
    if sum(len(p.indices) for p in pieces)!=len(faces):
        raise GarageError('Wheel partition lost geometry.','wheel_partition')
    return pieces

def adapt_source(scene:Scene)->Scene:
    if scene.digest!=F50_SOURCE_SHA256:return scene
    if len(scene.parts)!=50:raise GarageError('Known F50 source has unexpected part count.','source_profile')
    parts=[];handled=set()
    for part in scene.parts:
        contract=F50_WHEEL_GROUPS.get(part.id)
        if contract is None:parts.append(part);continue
        material_name,role,count=contract
        if scene.materials[part.material]['name']!=material_name or len(part.indices)!=count:
            raise GarageError('Known F50 wheel source changed. Review the source and profile.','source_profile')
        parts.extend(split_four_corners(part,role,F50_MID_AXLE_Z));handled.add(part.id)
    if handled!=set(F50_WHEEL_GROUPS) or len(parts)!=62 or sum(len(x.indices) for x in parts)!=scene.triangles:
        raise GarageError('Known F50 wheel inventory failed independent coverage validation.','source_profile')
    for role in ('tyre','rim'):
        for corner in F50_CORNERS:
            if not any(p.extras.get('gt6_role')==role and p.extras.get('gt6_corner')==corner for p in parts):
                raise GarageError('Missing wheel component in prepared F50.','source_profile')
    min_y=float(scene.bounds[0,1])
    # Transform ALL components identically after the source-space partition,
    # preserving authored UVs/normals and all triangle connectivity.
    scaled=[]
    for part in parts:
        p=((part.positions.astype(np.float64)-np.array([0.,min_y,0.]))*F50_SOURCE_SCALE).astype(np.float32)
        scaled.append(Part(part.id,part.name,part.node,part.primitive,part.material,
                           p,part.normals.copy(),part.uv.copy(),part.indices.copy(),
                           dict(part.extras),list(part.warnings)))
    result=Scene(scaled,scene.materials,scene.document,scene.raw,scene.digest,
                 sorted(set(scene.warnings+['F50 source positions uniformly normalized from its verified 4.934m model envelope to 4.480m, with geometry bottom at Y=0.',
                                            'The reviewed F50 wheel groups were divided by physical axle quadrant; 100% of source triangles retained. Separate RX-8/F50 native template, taillamp and textured rim recipe still require approval.'])))
    if abs(float(result.bounds[1,2]-result.bounds[0,2])-4.480)>.002:
        raise GarageError('Reviewed F50 source normalization does not match the known 4.480m model length.','source_profile')
    return result
