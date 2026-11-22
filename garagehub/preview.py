"""Complete-surface source preview; never presented as actual GT6 footage."""
from __future__ import annotations
import struct, json
import numpy as np
from .glb import Scene,unit_normals
from .errors import GarageError


def build_preview(scene:Scene,limit=800000):
    pos=[];norm=[];indices=[];meshes=[];offset=0;ip=0
    ratio=min(1,limit/max(1,scene.triangles))
    for p in scene.parts:
        # Do not skip triangles: that produces holes, not a lower-detail surface.
        pts=p.positions;ns=p.normals;faces=p.indices;simplified=False
        count=max(4,min(len(faces),int(len(faces)*ratio)))
        if len(faces)>count:
            try:import fast_simplification
            except ImportError as e:
                raise GarageError('Install fast-simplification for bounded high-detail/software previews, or use the Windows build.','simplifier_missing') from e
            pts,faces=fast_simplification.simplify(pts.astype(np.float64),faces.astype(np.int32),target_count=count,agg=5)
            pts=np.asarray(pts,np.float32);faces=np.asarray(faces,np.uint32)
            if not len(faces):
                # Tiny components may collapse completely. Keep their full surface.
                pts=p.positions;ns=p.normals;faces=p.indices
            else:ns=unit_normals(pts,faces);simplified=True
        used,back=np.unique(faces,return_inverse=True);pts=pts[used];ns=ns[used]
        pos.append(pts.astype('<f4'));norm.append(ns.astype('<f4'));ix=(back.reshape(-1,3)+offset).astype('<u4');indices.append(ix)
        meshes.append(dict(id=p.id,material=p.material,index_start=ip,index_count=ix.size,preview_triangles=len(faces),surface_simplified=simplified));offset+=len(pts);ip+=ix.size
    inter=np.column_stack([np.concatenate(pos),np.concatenate(norm)]).astype('<f4');ix=np.concatenate(indices).reshape(-1).astype('<u4')
    header=dict(vertices=len(inter),indices=len(ix),meshes=meshes,bounds=scene.bounds.tolist(),materials=scene.materials,source_triangles=scene.triangles,preview_triangles=len(ix)//3,preview_only=True,geometry_policy='complete surfaces; optional QEM simplification; no triangle skipping')
    j=json.dumps(header,separators=(',',':'),allow_nan=False).encode();j+=b' '*(-len(j)%4)
    return b'GGV1'+struct.pack('<III',len(j),len(inter),len(ix))+j+inter.tobytes()+ix.tobytes()
