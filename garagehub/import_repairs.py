"""Reproducible texture preparation on a separate player-import working copy."""
from collections import defaultdict

from PIL import Image

from .blender_preflight import _primitive, _vertex_alpha_is_solid
from .blender_textures import ExportImages
from .car_geometry import check_cancel
from .car_profiles import source_recipe
from .errors import GarageError
from .recognition import CORNER_ROLES, component_name_role, infer


REVISION = 1


def prepare_player_materials(glb, scene, cancel=None):
    if source_recipe(scene):
        return scene.raw, []
    uses = defaultdict(list)
    for part in scene.parts:
        uses[part.material].append(part)
    rows = {row['id']:row for row in infer(scene)['parts']}
    images = ExportImages(glb)
    decisions = []
    changed = False
    for index, parts in uses.items():
        check_cancel(cancel)
        if index >= len(glb.doc.get('materials',[])):
            continue
        mat = glb.doc['materials'][index]
        if all(rows[p.id]['role'] in CORNER_ROLES or rows[p.id]['role']=='ignore' for p in parts):
            continue
        prims = [_primitive(glb.doc,p) for p in parts]
        name = mat.get('name',f'Material {index+1}')
        def record(action, message):
            decisions.append({'action':action,'material':name,'part_ids':[p.id for p in parts],'message':message})
        try:
            pbr = mat.get('pbrMetallicRoughness',{})
            labels = {p.extras.get('gt6_role') or component_name_role(p.name,name) for p in parts}
            if (mat.get('alphaMode') in ('MASK','BLEND') and labels <= {'body','paint','interior'}
                    and 'KHR_materials_transmission' not in mat.get('extensions',{})
                    and pbr.get('baseColorFactor',[1,1,1,1])[3]==1
                    and _vertex_alpha_is_solid(glb,prims) and images.opaque(pbr.get('baseColorTexture'))):
                mat['alphaMode']='OPAQUE'
                mat.pop('alphaCutoff',None)
                changed = True
                record('fixed','Set the export surface to opaque after checking every source of transparency.')
            paint = [p for p in parts if rows[p.id]['role']=='paint']
            if paint and any(p.extras.get('gt6_role')=='paint' for p in paint) and not pbr.get('baseColorTexture'):
                # An authored factory paint finish cannot carry a baked colour map.
                continue
            changes = images.prepare(mat, prims)
            for message in changes:
                record('fixed',message)
            changed |= bool(changes)
            if mat.get('pbrMetallicRoughness',{}).get('baseColorTexture') and paint:
                tagged={id(prim) for p,prim in zip(parts,prims) if p.extras.get('gt6_role')
                        or rows[p.id]['role']!='paint'}
                for part in paint:
                    prim=_primitive(glb.doc,part)
                    if id(prim) in tagged:
                        continue
                    prim.setdefault('extras',{})['gt6_role']='body'
                    changed = True
                    decisions.append({'action':'fixed','material':name,'part_ids':[part.id],
                                      'message':'Kept the colour texture and markings on a Body detail surface.'})
        except (GarageError, OSError, Image.DecompressionBombError) as exc:
            record('review',str(exc))
    return (images.pack() if changed else scene.raw), decisions
