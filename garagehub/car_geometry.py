"""Reviewed project roles -> bounded native draw batches, without Blender or AI."""
from __future__ import annotations
import copy,struct
import numpy as np
from .recognition import axes,CORNERS
from .native import pack_normal
from .surface_guards import verify_boundaries
from .errors import GarageError,Cancelled
from .car_profiles import PROFILE
from .wheel_fit import native_wheel_geometry,tyre_fit_report


def check_cancel(cancel):
    if cancel and cancel.is_set():raise Cancelled()


def alignment(inspection,profile=None):
    """Fit the existing two body scales to all four reviewed native centres.

    The declared axes remain fixed: lateral scaling is independent, while up
    and forward share one scale. This is a constrained least-squares body fit,
    not a rigid mechanical-assembly transform or physical compatibility test.
    No per-axle warp, target height inference or physics edit is performed.
    """
    from collections.abc import Mapping
    from .assembly_fit import assembly_fit_report

    profile=PROFILE if profile is None else profile
    if not isinstance(inspection,Mapping) or not isinstance(profile,Mapping):
        raise GarageError('Body alignment needs an inspection and a reviewed native profile.','car_fit')
    try:
        # Keep the complete supplied XYZ coordinates authoritative. Nominal
        # database tracks/wheelbase cannot supply an unknown native height.
        target_centers={k:profile['wheel_geometry'][k]['center_metres'] for k in CORNERS}
    except (KeyError,TypeError) as exc:
        raise GarageError('The native profile needs explicit reviewed XYZ centres for all four wheels.','car_fit') from exc
    try:
        review=assembly_fit_report(inspection,{'kind':'explicit_centres','forward':'-Z',
                                   'wheel_centers_metres':target_centers})
    except GarageError as exc:
        raise GarageError(str(exc),'car_fit') from exc
    points=np.array([review['source']['wheel_centers_metres'][k] for k in CORNERS])
    target=np.array([review['target']['wheel_centers_metres'][k] for k in CORNERS])
    dimensions=review['source']['dimensions'];target_dimensions=review['target']['dimensions']
    measured_wheelbase=dimensions['wheelbase_metres']
    measured_track=(dimensions['front_track_metres']+dimensions['rear_track_metres'])/2
    # A stale saved inspection must not silently drive a different body fit.
    # This allowance covers floating arithmetic in the cached measurements;
    # it is unrelated to a source/reference or physical-fit tolerance.
    numeric=256*np.finfo(float).eps
    cached=[]
    for name,measured in (('wheelbase',measured_wheelbase),('track',measured_track)):
        value=inspection.get(name)
        if isinstance(value,(bool,np.bool_)) or not isinstance(value,(int,float,np.integer,np.floating)):
            raise GarageError('Review the tyre groups: the saved '+name+' measurement is missing or invalid.','car_fit')
        try:value=float(value)
        except (ValueError,OverflowError) as exc:
            raise GarageError('Review the tyre groups: the saved '+name+' exceeds the numeric range.','car_fit') from exc
        if not np.isfinite(value) or value<=0:
            raise GarageError('Review the tyre groups: the saved '+name+' measurement must be positive and finite.','car_fit')
        if abs(value-measured)>numeric*max(abs(value),abs(measured),float(np.max(np.abs(points)))):
            raise GarageError('Review the tyre groups: the saved '+name+' no longer matches the four measured centres.','car_fit')
        cached.append(value)
    wheelbase,track=cached
    left,up,front=axes(inspection['forward'])
    orientation=np.array([-left,up,-front])
    with np.errstate(over='ignore',invalid='ignore'):
        center=points.mean(0);target_center=target.mean(0)
        source=(points-center)@orientation.T;destination=target-target_center
    if not all(np.isfinite(a).all() for a in (center,target_center,source,destination)):
        raise GarageError('Body alignment exceeds the numeric range; review source units and native centres.','car_fit')
    # Normalization avoids squared large coordinates and preserves the same
    # least-squares objective. Translation is the corresponding centroid fit.
    source_span=float(np.max(np.abs(source)));target_span=float(np.max(np.abs(destination)))
    normalized_source=source/source_span;normalized_target=destination/target_span
    lateral_denominator=float(np.sum(normalized_source[:,0]**2))
    shared_denominator=float(np.sum(normalized_source[:,1:]**2))
    with np.errstate(over='ignore',invalid='ignore',divide='ignore'):
        relative_span=target_span/source_span
        lateral=float(np.sum(normalized_source[:,0]*normalized_target[:,0])/lateral_denominator*relative_span)
        scale=float(np.sum(normalized_source[:,1:]*normalized_target[:,1:])/shared_denominator*relative_span)
        ratio=lateral/scale if scale else float('nan')
    if not np.isfinite([lateral,scale,ratio]).all() or min(lateral,scale)<=0:
        raise GarageError('The reviewed centres do not admit a finite positive body fit in the declared axes.','car_fit')
    # Preserve the established equal-track Supra arithmetic when it gives the
    # same solution to floating precision. Unequal-track targets never use a
    # front-track-only substitute for the least-squares result.
    if target_dimensions['front_track_metres']==target_dimensions['rear_track_metres']:
        legacy_scale=target_dimensions['wheelbase_metres']/wheelbase
        legacy_ratio=target_dimensions['front_track_metres']/(track*legacy_scale)
        legacy_lateral=legacy_scale*legacy_ratio
        if (abs(legacy_scale-scale)<=numeric*max(abs(legacy_scale),abs(scale)) and
                abs(legacy_lateral-lateral)<=numeric*max(abs(legacy_lateral),abs(lateral))):
            scale,ratio=legacy_scale,legacy_ratio
    if not .85<=ratio<=1.15:
        raise GarageError('This model needs a different chassis-fit profile: its track/wheelbase ratio differs by more than 15%.','car_fit',
                          {'reason':'profile_fit_ratio','profile_id':profile.get('id'),
                           'lateral_fit_ratio':ratio,'allowed_ratio':[.85,1.15]})
    with np.errstate(over='ignore',invalid='ignore'):
        matrix=np.array([-left*scale*ratio,up*scale,-front*scale])
        translation=target_center-matrix@center
        mapped=points@matrix.T+translation;residuals=mapped-target
    if not all(np.isfinite(a).all() for a in (matrix,translation,mapped,residuals)):
        raise GarageError('Body alignment exceeds the numeric range; review source units and native centres.','car_fit')
    error=np.hypot.reduce(residuals,axis=1)
    if error.max()>.06:
        raise GarageError('Axle centres exceed this profile\'s existing 0.06 m candidate residual limit. Review tyre groups or use a matching chassis.','car_fit')
    return matrix,translation,{'uniform_scale':scale,'lateral_fit_ratio':ratio,
        'maximum_wheel_centre_error_metres':float(error.max()),
        'target_wheelbase_metres':target_dimensions['wheelbase_metres'],
        'source_wheelbase':wheelbase,'source_track':track,
        'source_front_track_metres':dimensions['front_track_metres'],
        'source_rear_track_metres':dimensions['rear_track_metres'],
        'target_front_track_metres':target_dimensions['front_track_metres'],
        'target_rear_track_metres':target_dimensions['rear_track_metres'],
        'fit_method':'axis_constrained_four_centre_least_squares',
        'fit_scope':'wheel_centres_only','dimensional_review_required':True,
        'policy':'One lateral scale and one shared up/forward scale fit all four explicit native centres. The residual describes this body fit only; tyre envelopes, rigid wheel/brake shape, wheel-arch clearance and suspension compatibility require separate review.'}


def _reduce_surface(points, faces, normals, target, preserve_border):
    import fast_simplification
    _, _, collapses = fast_simplification.simplify(points, faces, target_count=target, agg=4,
                                                  preserve_border=preserve_border, return_collapses=True)
    reduced, triangles, index = fast_simplification.replay_simplification(points, faces, collapses)
    reduced, triangles, index = np.asarray(reduced,float), np.asarray(triangles,np.int64), np.asarray(index,np.int64)
    if len(index)!=len(points) or index.max()>=len(reduced) or not len(triangles):
        raise GarageError('Simplifier remapping failed; no asset written.')
    boundary = verify_boundaries(points, faces, reduced, triangles, index) if preserve_border else None
    valid = index >= 0
    averaged = np.zeros_like(reduced)
    np.add.at(averaged, index[valid], normals[valid])
    missing = np.linalg.norm(averaged,axis=1) < 1e-10
    if np.any(missing):
        from .glb import unit_normals
        averaged[missing] = unit_normals(reduced,triangles)[missing]
    averaged /= np.maximum(np.linalg.norm(averaged,axis=1)[:,None],1e-20)
    return reduced, triangles, averaged, index, boundary


def _reduce_around_mounts(points, faces, normals, target, contacts, paint):
    """Hold each contacted triangle and its immediate neighbours at authored positions."""
    if not contacts:
        reduced, triangles, averaged, _, boundary = _reduce_surface(points,faces,normals,target,paint)
        return reduced, triangles, averaged, boundary, 0
    patch = np.isin(faces, np.unique(faces[sorted(contacts)])).any(axis=1)
    locked, free = faces[patch], faces[~patch]
    if len(free)<4 or target<=len(locked):
        return points, faces, normals, None, len(locked)
    free_ids, local_faces = np.unique(free.ravel(),return_inverse=True)
    held_ids = np.unique(locked)
    reduced, triangles, averaged, index, boundary = _reduce_surface(
        points[free_ids], local_faces.reshape(-1,3), normals[free_ids], target-len(locked), True)
    # Reuse the reduced boundary indices so the protected patch remains welded.
    mapping = np.full(len(points),-1,dtype=np.int64)
    mapping[free_ids] = index
    extra = np.setdiff1d(held_ids, free_ids)
    mapping[extra] = np.arange(len(extra))+len(reduced)
    reduced = np.concatenate((reduced,points[extra]))
    averaged = np.concatenate((averaged,normals[extra]))
    if np.any(mapping[held_ids]<0) or len(np.unique(mapping[held_ids]))!=len(held_ids):
        raise GarageError('The reduced body could not retain a lamp mounting edge.', 'shell_light_mount')
    reduced[mapping[held_ids]] = points[held_ids]
    averaged[mapping[held_ids]] = normals[held_ids]
    triangles = np.concatenate((triangles,mapping[locked]))
    if paint:
        boundary = verify_boundaries(points,faces,reduced,triangles,mapping)
    return reduced, triangles, averaged, boundary, len(locked)


def prepare_parts(scene,inspection,lod,cancel=None,*,fit_solution=None,_triangle_budget=None,_attempt=0):
    from .mechanical_policy import require_static_compatible
    require_static_compatible(inspection)
    if fit_solution is None:
        matrix,translation,fit=alignment(inspection)
    else:
        matrix,translation,review=fit_solution
        matrix=np.asarray(matrix,dtype=float);translation=np.asarray(translation,dtype=float)
        if (matrix.shape!=(3,3) or translation.shape!=(3,) or not np.isfinite(matrix).all()
                or not np.isfinite(translation).all() or np.linalg.det(matrix)<=0
                or not review.get('within_profile_limits')):
            raise GarageError('The reviewed body-fit transform is invalid. Review the current model again.','body_fit')
        fit=copy.deepcopy(review['fit']);fit['body_fit']=copy.deepcopy(review)
    mapping={p['id']:p for p in inspection['parts']};selected=[p for p in scene.parts if mapping[p.id]['role'] not in ('rim','tyre','valve','ignore')]
    # Only the source-bound shell review may request this new realization.
    # Existing authored-car fits retain their original arrays and byte output.
    two_sided=frozenset()
    contacts={}
    if fit_solution is not None and review.get('scope')=='template_shell_developer_review':
        for mount in review.get('generated_lighting',{}).get('mounts',[]):
            for hit in [*mount['mount_samples'], mount['center_mount_sample']]:
                contacts.setdefault(hit['source_part_id'],set()).add(hit['source_triangle'])
    if fit_solution is not None and review.get('scope')=='template_shell_developer_review' and 'opaque_two_sided_realization' in review:
        from .surface_realization import validate_opaque_two_sided_plan
        two_sided=validate_opaque_two_sided_plan(scene,inspection,review['opaque_two_sided_realization'])
    fit['tyre_fit']=tyre_fit_report(scene,inspection,matrix,translation)
    total=sum(len(p.indices)*(2 if p.id in two_sided else 1) for p in selected)
    profile_limit=PROFILE['body_triangles'][lod]
    budget=profile_limit if _triangle_budget is None else _triangle_budget
    # Textured/lamp/glass parts retain authored UVs and split normals. Reduce only
    # opaque uniform-colour surfaces, never sample/skip disconnected triangles.
    fixed=sum(len(p.indices)*(2 if p.id in two_sided else 1) for p in selected if scene.materials[p.material]['base_texture'] is not None or mapping[p.id]['role'] in ('glass','headlight','taillight','brakelight'))
    textured=set()
    if _triangle_budget is not None and fit_solution is not None and review.get('scope')=='template_shell_developer_review':
        textured={p.id for p in selected if len(p.indices)>200 and mapping[p.id]['role'] in ('body','interior')
                  and scene.materials[p.material]['base_texture'] is not None
                  and scene.materials[p.material]['alpha_mode']=='OPAQUE' and not scene.materials[p.material]['transmission']}
        fixed-=sum(len(p.indices)*(2 if p.id in two_sided else 1) for p in selected if p.id in textured)
    flex=total-fixed;ratio=min(1,max(.05,(budget-fixed)/max(1,flex)))
    out=[];reports=[]
    for part in selected:
        check_cancel(cancel);role=mapping[part.id]['role'];mat=scene.materials[part.material];p=part.positions.astype(float);n=part.normals.astype(float);uv=part.uv.astype(float);f=part.indices.astype(np.int64);original=len(f);reduced=False;boundary_check=None
        eligible=mat['base_texture'] is None and role not in ('glass','headlight','taillight','brakelight')
        held=0;texture_lod=None
        if part.id in textured and ratio<.98:
            from .textured_lod import reduce_textured_part
            p,n,uv,f,texture_lod=reduce_textured_part(part,max(120,round(original*ratio)),contacts.get(part.id))
            reduced=len(f)<original
        elif eligible and original>200 and ratio<.98:
            try:
                p,f,n,boundary_check,held=_reduce_around_mounts(p,f,n,max(120,round(original*ratio)),
                                                               contacts.get(part.id),role=='paint')
            except ImportError as e:raise GarageError('Automatic geometry budgeting requires the packaged simplifier.') from e
            if len(f)!=original or p.shape!=part.positions.shape or not np.array_equal(f,part.indices):
                uv=np.full((len(p),2),.5);reduced=True
        p=p@matrix.T+translation;n=n@np.linalg.inv(matrix);n/=np.maximum(np.linalg.norm(n,axis=1)[:,None],1e-20)
        if np.linalg.det(matrix)<0:f=f[:,[0,2,1]]
        if not np.isfinite(p).all() or not np.isfinite(n).all() or np.max(np.abs(p))>12:raise GarageError('Transformed geometry is not finite or exceeds this car profile.')
        realization=None
        if part.id in two_sided:
            from .surface_realization import duplicate_backfaces,METHOD
            front_triangles,front_vertices=len(f),len(p)
            if sum(len(q['faces']) for q in out)+2*front_triangles>max(profile_limit*1.2,20000):raise GarageError('UV-preserving geometry exceeds this profile budget. Reduce textured components or use a reviewed larger profile.','car_budget')
            if sum(len(q['positions']) for q in out)+2*front_vertices>PROFILE['max_static_vertices']:raise GarageError('Vertex budget exceeded.','car_budget')
            p,n,uv,f=duplicate_backfaces(p,n,uv,f)
            realization={'method':METHOD,'prepared_front_triangles':front_triangles,
                         'generated_backface_triangles':front_triangles,
                         'generated_backface_vertices':front_vertices,
                         'source_unchanged':True,'generated_after_lod_preparation':True}
        record={'id':part.id,'role':role,'material':part.material,'positions':p,'normals':n,'uv':uv,'faces':f}
        out.append(record);report={'id':part.id,'role':role,'material':part.material,'source_triangles':original,'output_triangles':len(f),'reduced':reduced,'authored_uv_preserved':not reduced,'authored_normals_averaged_after_reduction':reduced,'protected_panel_boundary':boundary_check}
        if realization is not None:report['surface_realization']=realization
        if held:report['protected_lamp_mount_triangles']=held
        if texture_lod is not None:
            report.update(textured_lod=texture_lod,authored_uv_preserved=True,authored_normals_averaged_after_reduction=False)
        reports.append(report)
    actual=sum(len(p['faces']) for p in out)
    if actual>max(profile_limit*1.2,20000):raise GarageError('UV-preserving geometry exceeds this profile budget. Reduce textured components or use a reviewed larger profile.','car_budget')
    if sum(len(p['positions']) for p in out)>PROFILE['max_static_vertices']:raise GarageError('Vertex budget exceeded.','car_budget')
    if fit_solution is not None and review.get('scope')=='template_shell_developer_review':
        # Both authenticated Supra streams retain 108160 bytes of native tables.
        # Each static vertex uses 24 bytes and each indexed triangle uses six.
        capacity={'hq':4680832,'race':2872064}[lod]-108160
        copies={'headlight':2,'taillight':3,'brakelight':2}
        estimate=sum((24*len(part['positions'])+6*len(part['faces']))*copies.get(part['role'],1) for part in out)
        if _attempt<3 and estimate>.94*capacity and budget>1:
            smaller=max(1,int(min(budget,actual)*.82*capacity/estimate))
            return prepare_parts(scene,inspection,lod,cancel,fit_solution=fit_solution,
                                 _triangle_budget=smaller,_attempt=_attempt+1)
        if _triangle_budget is not None:
            fit['native_geometry_budget']={'target_triangles':budget,'prepared_triangles':actual,'estimated_geometry_bytes':estimate,'available_geometry_bytes':capacity}
    return out,reports,fit


def batches(parts,max_triangles=18000):
    """Partition by complete triangles, preserving all vertex attributes exactly."""
    vb=bytearray();ids=[];points=[];source=[];lookup={}
    def emit():
        nonlocal vb,ids,points,source,lookup
        if not ids:return None
        result=(bytes(vb),np.asarray(ids,dtype='>u2').tobytes(),np.asarray(points),list(source));vb=bytearray();ids=[];points=[];source=[];lookup={};return result
    for part in parts:
        uv=part.get('native_uv',part['uv']);p=part['positions'];n=part['normals']
        raw=[struct.pack('>3f',*a)+pack_normal(b)+struct.pack('>2f',*t) for a,b,t in zip(p,n,uv)]
        for tri in part['faces']:
            keys=[(part['id'],int(i)) for i in tri];new=sum(k not in lookup for k in keys)
            if len(ids)//3>=max_triangles or len(points)+new>60000:
                result=emit()
                if result:yield result
            for key,index in zip(keys,tri):
                if key not in lookup:lookup[key]=len(points);vb.extend(raw[int(index)]);points.append(p[int(index)])
                ids.append(lookup[key])
            if part['id'] not in source:source.append(part['id'])
    result=emit()
    if result:yield result


def switch(selector,off,on):
    if len(off)+len(on)+2>=256:raise GarageError('Lamp branch exceeds the native short-jump budget.')
    b=bytearray(b'\x05'+struct.pack('>H',selector)+b'\x02'+bytes(4));b+=bytes((9,len(off)+len(on)+2));p0=len(b);b+=off;b+=bytes((9,len(on)));p1=len(b);b+=on
    struct.pack_into('>H',b,4,p0-6);struct.pack_into('>H',b,6,p1-8);return bytes(b)


def evaluate(program,night,brake):
    p=0;calls=[];steps=0
    while p<len(program):
        steps+=1
        if steps>1000:raise GarageError('Lamp bytecode does not terminate.')
        op=program[p]
        if op==0:break
        if op==59:calls.append(program[p+1]);p+=3
        elif op==9:p+=2+program[p+1]
        elif op==5:
            sel=struct.unpack_from('>H',program,p+1)[0]
            if sel not in(0,7) or program[p+3]!=2:raise GarageError('Unsupported lamp switch.')
            pos=p+4+2*(brake if sel==0 else night);p=pos+2+struct.unpack_from('>H',program,pos)[0]
        else:raise GarageError('Unsupported lamp bytecode.')
    return calls
