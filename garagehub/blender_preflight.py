"""Offline Blender handoff. Operates exclusively on a disposable export snapshot."""
import copy
import hashlib
import json
import logging
from collections import defaultdict
from dataclasses import replace
from pathlib import Path

import numpy as np
from PIL import Image

from . import __version__
from .blender_textures import ExportImages
from .car_profiles import source_recipe
from .errors import GarageError
from .glb import GLB
from .recognition import CORNER_ROLES, component_name_role, infer
from .surface_realization import native_finish_substitutions, opaque_two_sided_plan
from .template_shell import _materials
from .util import MAX_MODEL_BYTES, atomic_bytes, atomic_json, read_json, sha256


OPTICS = {'glass', 'headlight', 'taillight', 'brakelight'}
ROLES = OPTICS | {'body', 'interior'}
FINISH_EXTENSIONS = {'KHR_materials_specular', 'KHR_materials_clearcoat', 'KHR_materials_ior',
                     'KHR_materials_sheen', 'KHR_materials_iridescence', 'KHR_materials_anisotropy',
                     'KHR_materials_volume', 'KHR_materials_unlit', 'KHR_materials_emissive_strength'}
LABELS = {'body':'Body detail', 'interior':'Interior', 'glass':'Glass', 'headlight':'Headlight',
          'taillight':'Taillight', 'brakelight':'Brake light'}


def _primitive(doc, part):
    return doc['meshes'][doc['nodes'][part.node]['mesh']]['primitives'][part.primitive]


def _material_key(glb, index, parts, image_hashes):
    mat = glb.doc['materials'][index]
    images = []
    def visit(value):
        if isinstance(value, dict):
            for name, item in value.items():
                if name.endswith('Texture') and isinstance(item, dict) and type(item.get('index')) is int:
                    tex = glb.doc['textures'][item['index']]
                    image = tex.get('source')
                    if image is not None:
                        if image not in image_hashes:
                            view = glb.doc['images'][image]['bufferView']
                            image_hashes[image] = sha256(glb.view(view)[0])
                        images.append((name, image_hashes[image]))
                else:
                    visit(item)
        elif isinstance(value, list):
            for item in value:
                visit(item)
    visit(mat)
    identity = [index, mat, sorted(images), sorted((p.name, p.extras.get('gt6_role', '')) for p in parts)]
    return hashlib.sha256(json.dumps(identity, sort_keys=True).encode()).hexdigest()


def _vertex_alpha_is_solid(glb, primitives):
    for prim in primitives:
        attr = prim['attributes'].get('COLOR_0')
        if attr is not None and glb.doc['accessors'][attr]['type'] == 'VEC4':
            if not np.all(glb.accessor(attr)[:, 3] == 1):
                return False
    return True


def _material_fixes(glb, images, index, parts, rows, choice, report):
    mat = glb.doc['materials'][index]
    pbr = mat.get('pbrMetallicRoughness', {})
    prims = [_primitive(glb.doc, p) for p in parts]
    tags = {p.extras.get('gt6_role') for p in parts}
    hints = [p.extras.get('gt6_role') or component_name_role(p.name, mat.get('name','')) for p in parts]
    for i,row in enumerate(rows):
        if row.get('paired_lens'):
            hints[i]=row['role']
    optical = {role for role in hints if role in OPTICS}
    extensions = mat.get('extensions', {})
    transmission = extensions.get('KHR_materials_transmission', {})
    alpha = mat.get('alphaMode', 'OPAQUE')
    base = pbr.get('baseColorTexture')
    factor = pbr.get('baseColorFactor', [1.,1.,1.,1.])
    alpha_solid = (alpha != 'OPAQUE' and factor[3] == 1 and images.opaque(base) and
                   _vertex_alpha_is_solid(glb, prims))
    has_transmission = transmission.get('transmissionFactor', 0) > 0 or 'transmissionTexture' in transmission
    exports_colour_image = bool(base or mat.get('normalTexture') or mat.get('occlusionTexture'))
    ambiguous = ((alpha != 'OPAQUE' and not alpha_solid) or has_transmission) and not all(h in OPTICS for h in hints)
    if alpha == 'MASK' and not alpha_solid:
        ambiguous = True
    # A body role alone does not tell us whether the artist intended grille holes.
    if choice == 'auto' and not ambiguous and len(tags) == 1 and tags <= OPTICS:
        choice = next(iter(tags))
    if choice == 'auto' and ambiguous:
        report('choose', 'Choose how these pixels should appear. Use Solid body for solid panels, select a lens for glass or lights, or model real openings in Blender.')
    solid = choice in ('body','interior') or (choice == 'auto' and alpha_solid and not optical and not has_transmission)
    if solid:
        if alpha != 'OPAQUE' or has_transmission:
            mat['alphaMode'] = 'OPAQUE'
            mat.pop('alphaCutoff', None)
            if 'baseColorFactor' in pbr:
                pbr['baseColorFactor'][3] = 1.
            extensions.pop('KHR_materials_transmission', None)
            report('fixed', 'Set the exported panel to opaque. Its colour texture keeps its markings.')
    elif choice in OPTICS and (alpha == 'MASK' or 'transmissionTexture' in transmission):
        mat['alphaMode'] = 'BLEND'
        mat.pop('alphaCutoff', None)
        transmission.pop('transmissionTexture', None)
        report('fixed', 'Applied the selected GT6 lens finish.')
    if not has_transmission and 'KHR_materials_transmission' in extensions:
        extensions.pop('KHR_materials_transmission')

    for p, row in zip(parts, rows):
        target = choice if choice in ROLES else None
        if not target and row.get('paired_lens'):
            target=row['role']
        if not target and exports_colour_image and row['role']=='paint':
            target = 'body'
        if not target and solid and row['role']=='glass':
            target = 'body'
        if target:
            primitive = _primitive(glb.doc, p)
            if p.extras.get('gt6_role') != target:
                primitive.setdefault('extras', {})['gt6_role'] = target
                report('fixed', 'Assigned '+LABELS[target]+' to the export copy.')

    for change in images.prepare(mat, prims):
        report('fixed', change)
    if 'metallicRoughnessTexture' in pbr:
        pbr.pop('metallicRoughnessTexture')
        report('fixed', 'Used GT6 surface reflections in place of the metallic/roughness map.')
    native = set(native_finish_substitutions(mat))
    for ext in list(extensions):
        if ext in FINISH_EXTENSIONS and (ext in native or ext=='KHR_materials_ior' or choice in ROLES):
            extensions.pop(ext)
            report('fixed', 'Used the GT6 finish for '+ext.removeprefix('KHR_materials_')+'.')
        elif ext in FINISH_EXTENSIONS:
            report('choose', 'Choose a surface to use its native GT6 finish for '+ext.removeprefix('KHR_materials_')+'.')
    if 'emissiveTexture' in mat or any(mat.get('emissiveFactor', [])):
        # The workshop reads source emission to identify marker lights. Preserve
        # that evidence for its approved native finish instead of deleting it.
        report('info', 'Garage will review the source glow when choosing GT6 lights.')
    if not extensions:
        mat.pop('extensions', None)


def _audit_materials(raw, choices):
    glb = GLB(raw)
    scene = glb.scene()
    if source_recipe(scene):
        return raw, [dict(level='info', message='Garage recognized an exact car with a reviewed preparation recipe. The original file is going through that recipe.')]
    original_document = copy.deepcopy(glb.doc)
    inferred = {r['id']:r for r in infer(scene)['parts']}
    by_material = defaultdict(list)
    for part in scene.parts:
        by_material[part.material].append(part)
    images, hashes = ExportImages(glb), {}
    items, keys = [], {}
    excluded = set(CORNER_ROLES) | {'ignore'}
    for index, uses in by_material.items():
        if index >= len(glb.doc.get('materials', [])):
            continue  # The GLB reader supplies a flat default material.
        parts = [p for p in uses if inferred[p.id]['role'] not in excluded]
        if not parts:
            # A later role or wheel selection in Garage can use these images.
            material = glb.doc['materials'][index]
            context = dict(material=material.get('name', f'Material {index+1}'), object=uses[0].name)
            try:
                for message in images.fit_base_colour(material):
                    items.append(dict(level='fixed', message=message, **context))
            except (GarageError, OSError, Image.DecompressionBombError) as exc:
                items.append(dict(level='block', message=str(exc), **context))
            continue
        name = glb.doc['materials'][index].get('name', f'Material {index+1}')
        key = _material_key(glb, index, parts, hashes)
        keys[index] = key
        seen = set()
        def report(level, message):
            if (level, message) not in seen:
                items.append(dict(level=level, material=name, object=parts[0].name,
                                  material_key=key, message=message))
                seen.add((level, message))
        selected = choices.get(key, 'auto')
        if selected not in ROLES | {'auto'}:
            raise GarageError('Choose a valid surface in the Blender panel.')
        # An instanced mesh can have different roles on different nodes. A
        # primitive-wide tag cannot express a change for just one instance.
        shared = {id(_primitive(glb.doc,p)) for p in uses if inferred[p.id]['role'] in excluded}
        if any(id(_primitive(glb.doc,p)) in shared for p in parts):
            report('block','Make this instanced mesh single-user in Blender. It shares a primitive between bodywork and moving parts.')
            continue
        try:
            _material_fixes(glb, images, index, parts, [inferred[p.id] for p in parts], selected, report)
            if not seen:
                report('info', 'Existing material is usable. Choose a different surface here if needed.')
        except (GarageError, OSError, Image.DecompressionBombError) as exc:
            report('block', str(exc))
    # Only remove extension declarations when the extension no longer occurs.
    def declarations(value, found):
        if isinstance(value, list):
            for child in value:
                declarations(child, found)
        elif isinstance(value, dict):
            if isinstance(value.get('extensions'), dict):
                found.update(value['extensions'])
            for child in value.values():
                declarations(child, found)
    present = set()
    declarations(glb.doc, present)
    for field in ('extensionsUsed','extensionsRequired'):
        if field in glb.doc:
            glb.doc[field] = [e for e in glb.doc[field] if e in present or e not in FINISH_EXTENSIONS | {'KHR_materials_transmission'}]
            if not glb.doc[field]:
                del glb.doc[field]
    prepared = raw if not images.added and glb.doc == original_document else images.pack()
    result = GLB(prepared).scene()
    current = {r['id']:r for r in infer(result)['parts']}
    checked = set()
    for part in result.parts:
        row = current[part.id].copy()
        role = row['role']
        if role in excluded or (part.material,role) in checked:
            continue
        checked.add((part.material,role))
        key = keys.get(part.material)
        if any(i.get('material_key')==key and i['level'] in ('choose','block') for i in items):
            continue
        material = result.materials[part.material]
        row['use_native_optics'] = role in OPTICS and (material['double_sided'] or any(material['emissive']))
        originals = result.document.get('materials',[])
        row['use_native_finish'] = part.material<len(originals) and bool(native_finish_substitutions(originals[part.material]))
        row['disposition'] = 'emit'
        try:
            opaque_two_sided_plan(replace(result,parts=[part]), {'parts':[row]})
            _materials(result, [row])
        except GarageError as exc:
            help_steps = (exc.detail or {}).get('material_help', {}).get('steps', [])
            message = ' '.join([str(exc), *help_steps]) if help_steps else str(exc)
            items.append(dict(level='block', material=material['name'], material_key=key or '',
                              object=part.name, message=message))
    return prepared, items


def run_check(job, profile, web):
    """One fresh Blender job directory, with no writes to the player's Garage profile."""
    from .server import App
    job = Path(job)
    request = read_json(job/'request.json')
    report = dict(schema=1, version=__version__, can_export=False, findings=[], runtime_tested=False)
    if (job/'report.json').exists() or (job/'prepared.glb').exists():
        raise GarageError('Start a fresh Blender check.')
    try:
        source = job/'source.glb'
        if source.stat().st_size > MAX_MODEL_BYTES:
            raise GarageError('The Blender export exceeds 256 MiB. Reduce the model or its images.')
        if request.get('schema') != 1 or not isinstance(request.get('choices'), dict):
            raise GarageError('Update the Blender add-on to match GT6 Garage.')
        prepared, findings = _audit_materials(source.read_bytes(), request['choices'])
        report['findings'] = findings
        app = App(job/'workshop', web, allow_dialogs=False)
        try:
            settings_path = Path(profile)/'settings.json'
            if settings_path.is_file():
                config = read_json(settings_path)
                app.config.update({k:config[k] for k in ('car_baseline','wheel_defaults') if config.get(k)})
            row = app.model_tests.run('Blender car.glb', lambda:(prepared, 'Blender car.glb'), None)
        finally:
            app.executor.shutdown(wait=True, cancel_futures=True)
        report['garage'] = {k:row[k] for k in ('status','stage','code','messages','wheels','parts',
                                               'lights','lamp_mount_checks') if k in row}
        for end, lamps in row.get('lights',{}).items():
            findings.append(dict(level='workshop' if lamps['origin']=='review' else 'info',
                                 message=end.capitalize()+' lights — '+lamps['message']))
            for part in lamps['parts']:
                findings.append(dict(level='info',material=part['material'],object=part['name'],
                                     message=end.capitalize()+' light — '+part['reason']+'.'))
            for part in lamps.get('candidates',[]):
                findings.append(dict(level='info',material=part['material'],object=part['name'],
                                     message='Possible '+end+' lens — '+part['reason']))
        if row['status']=='needs_attention':
            messages = row.get('messages') or ['Inspect this model in Car workshop.']
            codes = row.get('blocker_codes') or [row.get('code')] * len(messages)
            for code, msg in zip(codes, messages, strict=True):
                assembly = code in ('shell_light_mount','shell_lamps','shell_placement','wheel_fit','shell_mechanical')
                if not code and row['stage'] in ('wheel placement','garage and race geometry'):
                    assembly = True
                level = 'workshop' if assembly else 'block'
                if not any(item['message']==msg for item in findings):
                    findings.append(dict(level=level, message=msg))
            if any(c in ('shell_light_mount','shell_lamps') for c in codes):
                findings.append(dict(level='workshop',message='Select separate front and rear lamp surfaces and set their types in Blender, or adjust supplied lights in the Garage assembled preview. Run the check again after changing them.'))
        if all(i['level'] not in ('choose','block') for i in findings):
            atomic_bytes(job/'prepared.glb', prepared)
            report.update(can_export=True, sha256=sha256(prepared), bytes=len(prepared))
    except (GarageError, OSError, ValueError, IndexError, KeyError, TypeError, Image.DecompressionBombError) as exc:
        report['findings'].append(dict(level='block', message=str(exc)))
    except Exception:
        logging.exception('Blender preflight failed')
        report['findings'].append(dict(level='block', message='Garage could not finish this check. Review the model and retry.'))
    atomic_json(job/'report.json', report)
    return report
