"""Structural guards for independently simplified adjoining exterior panels.
A source open edge is a real authored contour, not permission to erode a gap.
Checks do not replace in-game visual inspection.
"""
from __future__ import annotations
import numpy as np
from .errors import GarageError


def boundary_edges(faces):
    f=np.asarray(faces,dtype=np.int64)
    if f.ndim!=2 or f.shape[1]!=3:raise GarageError('Invalid triangle topology in boundary check.')
    edges=np.sort(np.concatenate((f[:,[0,1]],f[:,[1,2]],f[:,[2,0]])),axis=1)
    unique,counts=np.unique(edges,axis=0,return_counts=True)
    return unique[counts==1]


def verify_boundaries(source_points,source_faces,points,faces,index_mapping,*,tolerance=1e-6):
    """Require original open-boundary vertices and edge connectivity to survive."""
    a=np.asarray(source_points,float);b=np.asarray(points,float);mapping=np.asarray(index_mapping,dtype=np.int64)
    if mapping.shape!=(len(a),) or not np.isfinite(b).all():raise GarageError('Panel boundary mapping is invalid.')
    old=boundary_edges(source_faces);new=boundary_edges(faces);ids=np.unique(old);mapped=mapping[ids]
    if len(old)!=len(new) or np.any(mapped<0) or np.any(mapped>=len(b)) or len(np.unique(mapped))!=len(ids):raise GarageError('Panel reduction lost or changed an authored open boundary. No car package was emitted.','panel_boundary')
    displacement=np.linalg.norm(a[ids]-b[mapped],axis=1) if len(ids) else np.empty(0)
    if len(displacement) and displacement.max()>tolerance:raise GarageError('Panel reduction moved a protected edge; this would open a seam or filler-panel gap.','panel_boundary')
    expected=np.sort(mapping[old],axis=1)
    if len(expected):
        expected=expected[np.lexsort((expected[:,1],expected[:,0]))]
        new=new[np.lexsort((new[:,1],new[:,0]))]
        if not np.array_equal(expected,new):raise GarageError('Panel boundary connectivity changed during reduction.','panel_boundary')
    return {'passed':True,'source_boundary_edges':len(old),'retained_boundary_edges':len(new),'source_boundary_vertices':len(ids),'maximum_boundary_displacement':float(displacement.max()) if len(displacement) else 0.,'tolerance':tolerance}
