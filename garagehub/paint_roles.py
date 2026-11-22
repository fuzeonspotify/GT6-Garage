"""Conservative paint proposal, not material-semantic ground truth.
Authored/user roles win. Geometry alone cannot distinguish trim from paint.
An ambiguous proposal leaves source materials untouched and requires review.
"""
from __future__ import annotations
import numpy as np


def propose_paint(scene, records, overrides, lateral_axis, length_axis):
    explicit_paint=any(r['role']=='paint' for r in records.values()) or any(r.get('role')=='paint' for r in overrides.values())
    if explicit_paint:return {'status':'authored_or_saved','candidates':[]}
    grouped={};bounds=scene.bounds;size=np.maximum(bounds[1]-bounds[0],1e-8)
    for part in scene.parts:
        row=records[part.id]
        if row['role']!='body' or part.id in overrides or row['reason']!='Rigid body component':continue
        grouped.setdefault(part.material,[]).append(part)
    candidates=[]
    for mid,parts in grouped.items():
        mat=scene.materials[mid]
        if mat['alpha_mode']!='OPAQUE' or mat['color'][3]<.99 or mat['transmission']>.01 or mat['base_texture'] is not None:continue
        # Smooth opaque shell is only a proposal; chrome and matte trim are
        # not silently promoted. Black glossy paint is deliberately allowed.
        rough=float(mat['roughness']);metal=float(mat['metallic'])
        if rough>.32 or metal>=.95:continue
        lo=np.min([p.bounds[0] for p in parts],axis=0);hi=np.max([p.bounds[1] for p in parts],axis=0);coverage=(hi-lo)/size
        if coverage[length_axis]<.55 or coverage[lateral_axis]<.60 or coverage[1]<.18:continue
        score=.65*(1-rough)+.35*float(np.mean(np.minimum(coverage,1)))
        candidates.append({'material':mid,'score':round(score,6),'roughness':rough,'metallic':metal,'coverage':coverage.tolist(),'parts':[p.id for p in parts]})
    candidates.sort(key=lambda x:(-x['score'],x['material']))
    if not candidates:return {'status':'no_automatic_proposal','candidates':[]}
    if len(candidates)>1 and candidates[0]['score']-candidates[1]['score']<.15:
        return {'status':'ambiguous','candidates':candidates,'message':'Multiple smooth exterior materials could be paint. Assign Paint or explicit Body roles before building; no guess was applied.'}
    chosen=candidates[0]
    for pid in chosen['parts']:records[pid].update(role='paint',confidence=.80,reason='Review proposal: broad opaque smooth shell; not largest bounding box alone')
    return {'status':'proposal','selected_material':chosen['material'],'candidates':candidates,'requires_review':True}
