"""Two-bank RX-8 donor / actual F50 material transfer for an isolated BODY lab.

No standalone F50 body exporter exists yet. Only appends native material recipes,
preserving original native meshes, bytecode, GPU resources and car identity.
Do not install the resulting loose MDL3 files. This module is deliberately
separate from the proven seven-bank Supra converter.
"""
from __future__ import annotations
from pathlib import Path
import json
import numpy as np
from PIL import Image
from .errors import GarageError
from .native_editor import Editor,need
from .native import MDL3
from .car_materials import Materials,srgb
from .car_profiles import source_recipe
from .recognition import infer
from .glb import GLB
from .f50_wheel_adapter import F50_SOURCE_SHA256
from .donor_profiles import inspect_rx8
from .util import sha256,hash_file,atomic_json

# Reviewed RX-8 native material families at GT6 1.22, two banks, not
# transferable to the seven-bank Supra profile.
# HQ / race IDs differ in emission family, confirmed from original donor.
TEMPLATES={'hq':{'paint':0,'body':2,'metal':3,'glass':15,'lamp':10,'interior':2},
           'race':{'paint':0,'body':2,'metal':3,'glass':15,'lamp':7,'interior':2}}
ROLE_SET={'body','paint','interior','glass','headlight','taillight','brakelight'}

def same_sampler_bindings(editor,template,groups):
    """Alias-complete bindings for BOTH native material banks. No alias splitting."""
    if editor.banks!=2:raise GarageError('Only reviewed two-bank RX8 material donor supported.','rx8_material')
    bindings={}
    for bank in range(2):
        keys=dict(editor.texture_keys(template,bank))
        for sampler,img in groups.items():
            need(sampler in keys,f'Donor family {template} has no {sampler} sampler in bank {bank}.')
            alias={k for k,pid in keys.items() if pid==keys[sampler]}
            for name in alias:
                if name in bindings and bindings[name]!=img:
                    raise GarageError('Two-bank native sampler aliases conflict. Refusing incorrect glass/emission binding.','rx8_material')
                bindings[name]=img
    for bank in range(2):
        keys=dict(editor.texture_keys(template,bank))
        for sampler,img in groups.items():
            alias={k for k,pid in keys.items() if pid==keys[sampler]}
            need(all(bindings.get(k)==img for k in alias),'Partial source texture alias binding.')
    return bindings

class RX8BodyMaterials:
    def __init__(self,scene,editor,lod,passive_source_lenses=False):
        if lod not in TEMPLATES or editor.banks!=2 or editor.bank_size!= (65 if lod=='hq' else 68):
            raise GarageError('Original F50 material experiment needs the reviewed two-bank RX8 body template.','rx8_material')
        self.scene=scene;self.editor=editor;self.lod=lod
        self.passive_source_lenses=bool(passive_source_lenses)
        if self.passive_source_lenses:
            # Static source lenses stay visible with the lights off, but
            # only switched emitter clones may sample emission.
            for bank in (0,1):
                need('emissionMapSampler' not in dict(editor.texture_keys(TEMPLATES[lod]['body'],bank)),
                     'RX8 passive lens body shader unexpectedly samples emission.')
        self.textures=Materials(scene,editor);self.cache={};self.material_cache={};self.report=[]
    def recipe(self,source_id,role):
        if role not in ROLE_SET or not 0<=source_id<len(self.scene.materials):
            raise GarageError('Unsupported F50 source material role.','rx8_material')
        key=(source_id,role)
        if key in self.cache:return self.cache[key]
        mat=self.scene.materials[source_id];tpl=TEMPLATES[self.lod]
        passive_lens=self.passive_source_lenses and role in ('headlight','taillight','brakelight')
        if passive_lens and (mat['alpha_mode']!='OPAQUE' or mat['transmission']>.1):
            raise GarageError('Transparent/emissive F50 lens requires a reviewed nonemissive transparent native family.','rx8_passive_lens')
        family=('paint' if role=='paint' else 'glass' if role=='glass' or mat['alpha_mode']!='OPAQUE' or mat['transmission']>.1
                else 'body' if passive_lens else 'lamp' if role in ('headlight','taillight','brakelight')
                else 'metal' if mat['metallic']>.3 else 'interior' if role=='interior' else 'body')
        template=tpl[family]
        im=self.textures.source_image(mat)
        constant_uv=None
        if family=='glass':
            color=np.asarray(srgb(mat['color'][:3]),dtype=np.uint8)
            # Distinct native response and opacity channels; preserve source
            # alpha where authored. Actual in-game optics still need inspection.
            if mat['base_texture'] is None:
                opacity=int(np.clip(mat['color'][3]*255,28,210))
                tint=tuple(int(v) for v in (color*.6))
                im=Image.new('RGBA',(4,4),(*tint,160))
                mask=Image.new('RGBA',(4,4),(0,255,0,opacity))
                constant_uv=(.5,.5)
            else:
                mask=Image.new('RGBA',im.size,(0,255,0,0))
                mask.putalpha(im.getchannel('A'))
                im.putalpha(160)
            response=self.editor.image(im,f'f50_{self.lod}_glass_response_{source_id}')
            alpha=self.editor.image(mask,f'f50_{self.lod}_glass_opacity_{source_id}')
            bindings=same_sampler_bindings(self.editor,template,{'diffuseMapSampler':response,
                                                                  'alphaMapSampler':alpha})
            transparent=True
        else:
            # Opaque geometry source PBR base factor and original UV carried.
            # Emission is only a material recipe, NOT a working lamp switch.
            im.putalpha(255)
            image=self.editor.image(im,f'f50_{self.lod}_{family}_{source_id}')
            group={'diffuseMapSampler':image}
            if family=='lamp':group['emissionMapSampler']=image
            bindings=same_sampler_bindings(self.editor,template,group)
            transparent=False
            if mat['base_texture'] is None:constant_uv=(.5,.5)
        # Append (not replace) for both banks. Overwriting original RX8
        # material slots can alter mechanical rig / driver / shadows.
        # All native sampler aliases and both banks are normalized before
        # sharing. Distinct source parts may only share the native material if
        # the shader family AND every bound texture ID agree byte-for-byte.
        # This does not merge geometry or discard UVs or source materials.
        signature=(template,tuple(sorted(bindings.items())))
        reused=signature in self.material_cache
        if reused:material=self.material_cache[signature]
        else:
            material=self.editor.clone_material(template,
                f'f50_{self.lod}_{family}_{source_id}',bindings)
            self.material_cache[signature]=material
        result={'source_id':source_id,'source_role':role,'native_template':template,
                'native_material':material,'native_material_reused':reused,'fvf':self.editor.fvf(template),
                'transparent':transparent,'constant_uv':constant_uv,
                'source_has_texture':mat['base_texture'] is not None,
                'source_normal_map_untransferred':bool(mat['normal_map']),
                'alpha_optics_unverified':family=='glass',
                'paint_palette_unverified':family=='paint',
                 'lamp_states_unverified':family=='lamp',
                'passive_source_lens':bool(passive_lens),
                'base_lens_emission_sampler_disabled':bool(passive_lens),
                'source_material_name':mat['name'],'family':family}
        self.cache[key]=result;self.report.append(result)
        return result

def experiment(source,donor,output):
    """Transfer all actual distinct source BODY materials; no model changes."""
    source,donor,output=map(Path,(source,donor,output))
    if output.exists() or not output.parent.is_dir() or output.parent.is_symlink():
        raise GarageError('Choose a new material-lab folder with a regular parent.','rx8_material_path')
    if any(x.lower() in {'usrdir','pdipfs','ps3_game','dev_hdd0'} for x in output.resolve().parts):
        raise GarageError('Cannot write a material lab into any game installation.','rx8_material_path')
    if not source.is_file() or source.suffix.lower()!='.glb' or hash_file(source)!=F50_SOURCE_SHA256:
        raise GarageError('Use the original reviewed Ferrari F50 source GLB.','rx8_material_source')
    profile=inspect_rx8(donor);root=Path(profile['donor_root'])
    if output.resolve()==root.resolve() or root.resolve() in output.resolve().parents:
        raise GarageError('Output cannot overwrite the donor.','rx8_material_path')
    scene=GLB(source.read_bytes()).scene()
    inspection=infer(scene,overrides=source_recipe(scene))
    roles={p['id']:p for p in inspection['parts']}
    if len(scene.parts)!=62 or len(roles)!=62:
        raise GarageError('Reviewed F50 source inventory changed.','rx8_material_source')
    per_lod={}
    wanted=sorted(set((p.material,roles[p.id]['role']) for p in scene.parts
                       if roles[p.id]['role'] in ROLE_SET))
    for lod in ('hq','race'):
        raw=(root/lod/'body').read_bytes();ed=Editor(raw)
        recipes=RX8BodyMaterials(scene,ed,lod)
        built=[recipes.recipe(sid,role) for sid,role in wanted]
        data,report=ed.finish()
        m=MDL3(data);assert m.validate()['passed']
        old=MDL3(raw)
        if not data[report['new_cut']:report['new_cut']+len(raw)-old.cut]==raw[old.cut:]:
            raise GarageError('Donor GPU source tail changed.','rx8_material_integrity')
        for mid in range(old.u16(0x10)):
            a=old.u32(0x30)+mid*48;b=m.u32(0x30)+mid*48
            if old.span(old.u32(a+24),old.u32(a+28))!=m.span(m.u32(b+24),m.u32(b+28)):
                raise GarageError('Material-only experiment mutated donor model commands.','rx8_material_integrity')
        for sid in range(old.u16(0x14)):
            if old.span(old.u32(0x38)+48*sid,48)!=m.span(m.u32(0x38)+48*sid,48):
                raise GarageError('Material-only experiment mutated donor mesh descriptors.','rx8_material_integrity')
        if len(built)!=len(wanted) or len(report['added_materials'])!=len(wanted):
            raise GarageError('F50 material coverage incomplete.','rx8_material_integrity')
        per_lod[lod]={'native_body_material_metadata':data,'report':{
            'lod':lod,'source_sha256':scene.digest,'donor_sha256':sha256(raw),
            'new_native_sha256':sha256(data),'source_role_material_pairs':len(wanted),
            'native_materials_added':len(report['added_materials']),
            'native_images_added':len(report['images']),
            'original_native_shapes_and_model_programs_unchanged':True,
            'original_native_gpu_tail_retained':True,
            'native_material_banks':2,'source_triangles_retained_in_GLb':scene.triangles,
            'material_recipes':recipes.report,'native_exporter_complete':False,
            'runtime_tested':False,'installable_package':False,
            'warnings':['No actual F50 body geometry is present in this metadata-only experiment.',
                        'Native inherited donor chassis/tyres are not the physical size of Ferrari F50.',
                        'Paint variation, optical/emission and reflection behavior have not been validated in GT6.']}}
    output.mkdir()
    for lod,rec in per_lod.items():
        d=output/lod;d.mkdir()
        (d/'body_material_metadata_ONLY.mdl3').write_bytes(rec['native_body_material_metadata'])
        atomic_json(d/'receipt.json',rec['report'])
    (output/'README_NON_INSTALLABLE.txt').write_text(
        'F50 RX-8 two-bank material experiment. NOT A COMPLETE CAR OR INSTALLABLE PACKAGE.\n'
        'The source F50 material textures and factors were converted into new native tables\n'
        'without changing donor geometry, model bytecode or its two GPU resource banks.\n'
        'No body mesh, car identity, wheels or lamps are installed. No PS3 certification.\n',
        encoding='utf-8')
    receipt={'passed':True,'source_sha256':scene.digest,'output':str(output),
             'hq':{k:v for k,v in per_lod['hq']['report'].items() if k!='material_recipes'},
             'race':{k:v for k,v in per_lod['race']['report'].items() if k!='material_recipes'},
             'native_exporter_complete':False,'installable_package':False,'game_changed':False}
    atomic_json(output/'LAB_RECEIPT.json',receipt)
    return receipt
