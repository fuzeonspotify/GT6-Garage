"""Attribute-aware reduction for opaque textured bodywork in crowded native streams."""
import ctypes
import numpy as np
import meshoptimizer
from meshoptimizer._loader import lib

from .errors import GarageError

# The pinned Python wrapper leaves this entry point untyped. Match its published
# meshoptimizer.h declaration so the error tolerance and size_t counts cross the ABI correctly.
_fp=ctypes.POINTER(ctypes.c_float)
_ip=ctypes.POINTER(ctypes.c_uint)
_size=ctypes.c_size_t
lib.meshopt_simplifyWithAttributes.argtypes=[_ip,_ip,_size,_fp,_size,_size,_fp,_size,
    _fp,_size,ctypes.POINTER(ctypes.c_ubyte),_size,ctypes.c_float,ctypes.c_uint,_fp]
lib.meshopt_simplifyWithAttributes.restype=_size


def reduce_textured_part(part, target, contacts):
    indices=np.ascontiguousarray(part.indices,dtype=np.uint32).ravel()
    vertices=np.ascontiguousarray(part.positions,dtype=np.float32)
    attributes=np.ascontiguousarray(np.column_stack((part.normals,part.uv)),dtype=np.float32)
    weights=np.array([.5,.5,.5,10.,10.],dtype=np.float32)
    locked=np.zeros(len(vertices),dtype=np.uint8)
    if contacts:
        adjacent=np.isin(part.indices,np.unique(part.indices[sorted(contacts)])).any(axis=1)
        locked[part.indices[adjacent].ravel()]=1
    dst=np.empty_like(indices)
    error=np.zeros(1,dtype=np.float32)
    error_limit=.008
    count=meshoptimizer.simplify_with_attributes(dst,indices,vertices,attributes,weights,
        vertex_lock=locked,target_index_count=3*target,target_error=error_limit,
        options=meshoptimizer.SIMPLIFY_LOCK_BORDER,result_error=error)
    if (count<=0 or count>len(indices) or count%3 or dst[:count].max()>=len(vertices)
            or not np.isfinite(error[0]) or error[0]>error_limit+1e-6):
        raise GarageError('The textured body could not be reduced within its detail limit.', 'car_budget')
    used, local=np.unique(dst[:count],return_inverse=True)
    # Every output vertex takes its complete position, normal and UV from the
    # same source corner. The simplifier retains topology and locks open borders.
    return (part.positions[used].astype(float),part.normals[used].astype(float),
            part.uv[used].astype(float),local.reshape(-1,3),
            {'source_triangles':len(indices)//3,'output_triangles':count//3,
             'relative_attribute_error':float(error[0]),'relative_error_limit':error_limit,
             'locked_mount_vertices':int(locked.sum())})
