"""Reviewed, lossless corner partitions of source material-group primitives.

The caller supplies the role explicitly; names and proximity cannot distinguish
a brake rotor from a caliper in the same primitive. Every triangle is retained
whole. A saved decision replays original face indices after the project's
proper source-axis/uniform-unit preparation, without repeating spatial guesses.
This module emits prepared source parts, not native draws or game acceptance.
"""
from __future__ import annotations

import copy
import hashlib
import json
from collections.abc import Mapping

import numpy as np

from .errors import GarageError
from .glb import Part, MAX_PARTS, MAX_TRIANGLES, MAX_VERTICES
from .recognition import CORNERS, CORNER_ROLES, MECHANICAL_ROLES, MOTION_INTENTS
from .scene_coordinates import UP_AXES, coordinate_description


_SCHEMA=1
_CHUNK=65536
_ARITHMETIC_EPS=64*np.finfo(np.float64).eps
_PREPARATION_EPS=4*np.finfo(np.float32).eps


def _fail(message):
    raise GarageError(message,'wheel_partition')


def _role(role):
    if not isinstance(role,str) or role not in CORNER_ROLES:
        _fail('Choose an explicit rim, tyre, valve, brake_rotor or brake_caliper role for the selected surfaces. Mixed rotor/caliper surfaces require separate reviewed parts.')
    return role


def _part(part):
    if not isinstance(part,Part) or not isinstance(part.id,str) or not part.id:
        _fail('Choose a source component with a stable part identity.')
    for name in ('node','primitive','material'):
        value=getattr(part,name)
        if isinstance(value,(bool,np.bool_)) or not isinstance(value,(int,np.integer)) or value<0:
            _fail('The source component has an invalid '+name+' identity.')
    for name,columns in (('positions',3),('normals',3),('uv',2)):
        value=getattr(part,name)
        if not isinstance(value,np.ndarray):
            _fail('Source '+name+' must be a numeric vertex array.')
        if value.ndim!=2 or value.shape[1]!=columns or value.dtype.kind not in 'fiu' or not np.isfinite(value).all():
            _fail('Source '+name+' must be finite numeric vertex attributes.')
        if not 0<len(value)<=MAX_VERTICES:
            _fail('The selected component is empty or exceeds the model vertex limit.')
    if len(part.normals)!=len(part.positions) or len(part.uv)!=len(part.positions):
        _fail('Source positions, normals and UVs must have the same vertex count.')
    faces=part.indices
    if not isinstance(faces,np.ndarray):
        _fail('Source faces must be an integer triangle array.')
    if faces.ndim!=2 or faces.shape[1]!=3 or faces.dtype.kind not in 'iu' or not 0<len(faces)<=MAX_TRIANGLES:
        _fail('The selected component needs a bounded, nonempty integer triangle list.')
    if np.min(faces)<0 or np.max(faces)>=len(part.positions):
        _fail('A source triangle references a missing vertex.')
    if not isinstance(part.name,str) or not isinstance(part.extras,dict) or not isinstance(part.warnings,list):
        _fail('The source component metadata is invalid.')
    return part


def _identity(part):
    return {'id':part.id,'name':part.name,'node':int(part.node),
            'primitive':int(part.primitive),'material':int(part.material),
            'vertices':len(part.positions),'triangles':len(part.indices)}


def _geometry_hash(part):
    """Hash numeric attributes and identity in a portable, canonical encoding."""
    digest=hashlib.sha256(json.dumps(_identity(part),sort_keys=True,separators=(',',':')).encode('utf-8'))
    for name,dtype in (('positions','<f8'),('normals','<f8'),('uv','<f8'),('indices','<u8')):
        array=np.asarray(getattr(part,name))
        digest.update(name.encode('ascii'))
        for start in range(0,len(array),_CHUNK):
            digest.update(np.asarray(array[start:start+_CHUNK],dtype=dtype,order='C').tobytes())
    return digest.hexdigest()


def _numeric_same(actual,expected):
    # Only float32 preparation rounding is allowed, not a physical-fit margin.
    difference=np.abs(actual-expected)
    magnitude=np.maximum(np.abs(actual),np.abs(expected))
    return bool(np.all(difference<=_PREPARATION_EPS*magnitude))


def _prepared_from_source(part,source):
    """Require the existing proper-axis, positive-uniform-unit preparation.

    Supplying a valid canonical source hash cannot authorize different topology,
    UVs, local offsets, nonuniform scales, reflections or edited current vertices.
    The source adapter is already included in the canonical source Part.
    """
    if _identity(part)!=_identity(source):
        _fail('The prepared component does not match the saved source part identity or counts.')
    if not np.array_equal(part.indices,source.indices) or not np.array_equal(part.uv,source.uv):
        _fail('Prepared topology or UVs differ from the canonical source; review and partition the changed source again.')
    if np.array_equal(part.positions,source.positions) and np.array_equal(part.normals,source.normals):
        return
    source_peak=np.unravel_index(int(np.argmax(np.abs(np.asarray(source.positions,float)))),source.positions.shape)
    if float(source.positions[source_peak])==0:
        _fail('The component has no measurable source coordinates for preparation replay.')
    for axis in UP_AXES:
        rotation=np.asarray(coordinate_description(axis)['source_to_y_up_rotation'],dtype=float)
        vertex=source_peak[0]
        reference=np.asarray(source.positions[vertex],float)@rotation.T
        component=int(np.argmax(np.abs(reference)))
        scale=float(part.positions[vertex,component])/float(reference[component])
        if not np.isfinite(scale) or not 1e-6*(1-_PREPARATION_EPS)<=scale<=1e6*(1+_PREPARATION_EPS):
            continue
        # A float32 vertex can place an estimated boundary scale just beyond
        # the permitted setting even though prepare_scene used exactly 1e6.
        scale=min(1e6,max(1e-6,scale))
        valid=True
        for start in range(0,len(part.positions),_CHUNK):
            end=start+_CHUNK
            expected=np.asarray(source.positions[start:end],float)@rotation.T*scale
            normals=np.asarray(source.normals[start:end],float)@rotation.T
            if not _numeric_same(np.asarray(part.positions[start:end],float),expected) or not _numeric_same(np.asarray(part.normals[start:end],float),normals):
                valid=False;break
        if valid:return
    _fail('Prepared positions or normals do not match a proper source-axis rotation and uniform unit scale. Local edits require a new reviewed partition.')


def _source(part,source_part):
    part=_part(part);source=_part(part if source_part is None else source_part)
    _prepared_from_source(part,source)
    return source


def _centres(inspection):
    if not isinstance(inspection,Mapping):
        _fail('Review all four tyre centres and the front direction before partitioning a component.')
    from .assembly_fit import assembly_fit_report
    try:
        report=assembly_fit_report(inspection,{
            'kind':'explicit_centres','forward':inspection.get('forward'),
            'wheel_centers_metres':inspection.get('wheel_centers')})
    except GarageError as exc:
        raise GarageError('Cannot partition with these tyre assignments: '+str(exc),'wheel_partition') from exc
    centers=report['source']['wheel_centers_metres']
    return np.array([centers[k] for k in CORNERS],float)


def _child_ids(part,known_ids):
    if isinstance(known_ids,(str,bytes)):
        _fail('Known component identities must be a collection of part IDs.')
    try:ids=list(known_ids)
    except TypeError as exc:
        raise GarageError('Known component identities must be a collection of part IDs.','wheel_partition') from exc
    if any(not isinstance(x,str) or not x for x in ids) or len(set(ids))!=len(ids):
        _fail('Known component identities are invalid or duplicated.')
    result=[part.id+'__wheel_'+corner for corner in CORNERS]
    if any(pid in ids for pid in result):
        _fail('Partition child IDs already exist in this model. Choose the original unsplit component or remove the conflicting decision.')
    # The caller's existing scene may include or omit the replaced parent.
    if len(ids)-(1 if part.id in ids else 0)+len(result)>MAX_PARTS:
        _fail('This partition would exceed the model component limit.')
    return result


def _owners(part,centers):
    # Only used vertices matter; unreferenced source vertices do not invent a
    # triangle or prevent otherwise complete, lossless face coverage.
    used=np.unique(part.indices)
    owners=np.full(len(part.positions),-1,dtype=np.int8)
    for start in range(0,len(used),_CHUNK):
        selected=used[start:start+_CHUNK]
        with np.errstate(over='ignore',invalid='ignore'):
            delta=np.asarray(part.positions[selected],float)[:,None,:]-centers[None,:,:]
            distances=np.sum(delta*delta,axis=2)
        if not np.isfinite(distances).all():
            _fail('Source-to-tyre distances exceed the numeric range; review the source coordinates.')
        nearest=np.argmin(distances,axis=1)
        ordered=np.sort(distances,axis=1)
        tolerance=_ARITHMETIC_EPS*np.maximum(ordered[:,1],np.finfo(float).tiny)
        if np.any(ordered[:,1]-ordered[:,0]<=tolerance):
            _fail('A used vertex is equally close to multiple tyre centres. Review the component boundaries; no triangle was split or dropped.')
        owners[selected]=nearest
    triangle_owners=owners[part.indices]
    if np.any(triangle_owners!=triangle_owners[:,:1]):
        _fail('A triangle crosses wheel-corner ownership boundaries. Separate the source surfaces; no triangle was split or dropped.')
    return triangle_owners[:,0]


def _emit(part,role,groups):
    result=[]
    for group in groups:
        face_indices=np.asarray(group['source_face_indices'],dtype=np.int64)
        selected=part.indices[face_indices]
        used,local=np.unique(selected,return_inverse=True)
        extras=copy.deepcopy(part.extras)
        extras.update(gt6_role=role,gt6_corner=group['corner'],gt6_source_part=part.id,
                      gt6_source_face_count=len(selected),gt6_motion_intent=MOTION_INTENTS[role],
                      gt6_partition_method='reviewed_original_face_indices')
        child=Part(group['id'],part.name+' '+group['corner'],part.node,part.primitive,part.material,
                   part.positions[used].copy(),part.normals[used].copy(),part.uv[used].copy(),
                   local.reshape(-1,3).astype(np.uint32),extras,list(part.warnings))
        for name in ('positions','normals','uv'):
            if not np.array_equal(getattr(child,name)[child.indices],getattr(part,name)[selected]):
                _fail('Partition verification found changed source vertex attributes; no parts were returned.')
        if len(child.indices)!=group['triangles'] or len(child.positions)!=group['vertices']:
            _fail('Saved partition counts do not match the source triangle selection.')
        result.append(child)
    return result


def partition_wheel_corners(part,inspection,role,*,known_ids=(),source_part=None):
    """Return four lossless Parts and JSON provenance for an explicit role.

    ``source_part`` is the canonical GLB scene Part before coordinate settings;
    supply it when ``part`` is coordinate-prepared. ``known_ids`` should contain
    the scene's current IDs so collisions and the total part budget are checked.
    The caller must review that all selected faces share the requested role;
    this function cannot authenticate a mixed rotor/caliper material group.
    """
    role=_role(role);source=_source(part,source_part)
    ids=_child_ids(part,known_ids);centers=_centres(inspection)
    owners=_owners(part,centers);groups=[]
    for index,corner in enumerate(CORNERS):
        face_indices=np.flatnonzero(owners==index)
        if not len(face_indices):
            _fail('The selected component does not contain surfaces at all four tyre corners. Assign separate components individually instead.')
        groups.append({'id':ids[index],'corner':corner,'source_face_indices':face_indices.tolist(),
                       'triangles':len(face_indices),'vertices':len(np.unique(part.indices[face_indices]))})
    identity={**_identity(source),'geometry_sha256':_geometry_hash(source)}
    provenance={'schema':_SCHEMA,'kind':'wheel_corner_partition','source_part':identity,
                'role':role,'motion_intent':MOTION_INTENTS[role],'role_confirmation':'explicit_caller',
                'forward':inspection['forward'],
                'wheel_centers_at_partition':{k:centers[i].tolist() for i,k in enumerate(CORNERS)},
                'groups':groups,'geometry_unchanged':True,'runtime_verified':False}
    return _emit(part,role,groups),provenance


def replay_wheel_partition(part,provenance,*,known_ids=(),source_part=None):
    """Replay saved source face indices; never infer new corner ownership.

    Source identity/hash and the allowed preparation transform are rechecked.
    All source faces must occur exactly once, and all four groups must remain
    nonempty. Changing role or assignments is a new explicit review operation.
    """
    source=_source(part,source_part);ids=_child_ids(part,known_ids)
    if not isinstance(provenance,dict) or type(provenance.get('schema')) is not int or provenance['schema']!=_SCHEMA or provenance.get('kind')!='wheel_corner_partition':
        _fail('Unsupported saved wheel partition. Review and partition the source component again.')
    role=_role(provenance.get('role'))
    expected={**_identity(source),'geometry_sha256':_geometry_hash(source)}
    if provenance.get('source_part')!=expected:
        _fail('The saved partition does not match this source component or its geometry hash. Review the changed source again.')
    if provenance.get('role_confirmation')!='explicit_caller' or provenance.get('motion_intent')!=MOTION_INTENTS[role]:
        _fail('The saved mechanical role or motion intent is not an explicit, consistent decision.')
    if provenance.get('geometry_unchanged') is not True or provenance.get('runtime_verified') is not False:
        _fail('The saved partition has an unsupported geometry or runtime claim.')
    _centres({'forward':provenance.get('forward'),'wheel_centers':provenance.get('wheel_centers_at_partition')})
    groups=provenance.get('groups')
    if not isinstance(groups,list) or len(groups)!=len(CORNERS):
        _fail('A saved wheel partition must contain exactly four corner groups.')
    seen=np.zeros(len(part.indices),dtype=np.bool_)
    for index,group in enumerate(groups):
        if not isinstance(group,dict) or group.get('corner')!=CORNERS[index] or group.get('id')!=ids[index]:
            _fail('Saved corner labels or child identities changed; review the partition again.')
        selected=group.get('source_face_indices')
        if not isinstance(selected,list) or not 0<len(selected)<=len(part.indices) or any(type(x) is not int or not 0<=x<len(part.indices) for x in selected):
            _fail('Saved source face indices must be nonempty in-range integers.')
        indices=np.asarray(selected,dtype=np.int64)
        if np.any(indices[1:]<=indices[:-1]) or np.any(seen[indices]):
            _fail('A saved source face is duplicated or the original face ordering changed.')
        seen[indices]=True
        if type(group.get('triangles')) is not int or group['triangles']!=len(indices) or type(group.get('vertices')) is not int or group['vertices']!=len(np.unique(part.indices[indices])):
            _fail('Saved corner counts do not match the original face selections.')
    if not seen.all():
        _fail('The saved partition omits source triangles; review complete geometry coverage.')
    return _emit(part,role,groups)
