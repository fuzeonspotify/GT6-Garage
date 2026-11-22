"""Versioned owned-resource contracts. Profiles describe interfaces, not game data."""
from __future__ import annotations
from pathlib import Path
from .errors import GarageError
from .util import hash_file
from .native import MDL3

PROFILE_ID='supra-v029-exterior-1'
ASSETS={
 'info':'03f5c8428a055597781ea532759eb3d4716723d56156f45aadeaf8e11774bdd6',
 'interior':'351e5991857562295f55f8d22f2b6925057c5f92c23716478296124bd0eec5da',
 'hq/body':'36fc62925952bdd60afce3816e10f9acd7f40f9f0c189f22e58022680807c831',
 'hq/body_s':'e885f576d1b60eda59d5602b30be970e6db4d32ce48915f0239295c3382b7bdc',
 'hq/wheel':'4aa8cbae8423b819772a394da0112274d6e7936853392d8982991d1f47274fd7',
 'race/body':'cecdb582a14871cae164d95d30bf9df48dce4247d275d967d250b0e6f4f1941c',
 'race/body_s':'911a474a37213b141efccf12c9f77cbcf273087c60e69036830c9192079fb566',
 'race/wheel':'c8dcbfadfcd55bb83538b6f74374335cc57b5cc651d6e844355fc60c8daf38bb',
}
PROFILE={
 'id':PROFILE_ID,'schema':1,'label':'Supra-derived exterior / GT6 1.22',
 'model_path':'car/0004/0006','car_id':82,'title_id':'BCES01893','game_update':'01.22',
 'wheelbase':2.55,'front_track':1.536523,'rear_track':1.536523,'wheel_y':.19565,
 # Existing reviewed native wheel frames, shared by body alignment and both
 # rim exporters. These are donor dimensions, not source-car specifications.
 # Source/body tyre-envelope compatibility still requires dimensional review.
 'wheel_geometry':{
  'FL':{'center_metres':[-.7682615,.19565,-1.275],'tyre_radius_metres':.32165,'tyre_width_metres':.235},
  'FR':{'center_metres':[.7682615,.19565,-1.275],'tyre_radius_metres':.32165,'tyre_width_metres':.235},
  'RL':{'center_metres':[-.7682615,.19565,1.275],'tyre_radius_metres':.3179,'tyre_width_metres':.255},
  'RR':{'center_metres':[.7682615,.19565,1.275],'tyre_radius_metres':.3179,'tyre_width_metres':.255},
 },
 'material_roles':{'paint':0,'body':1,'metal':2,'glass':5,'headlight':75,'taillight':76,'brakelight':77,'high_stop':78},
 'mechanical_offsets':{'hq':{'0':138,'5':141},'race':{'0':132,'5':135}},
 'main_models':{'0':0,'5':17},'static_shape_limit':240,'draw_triangles':20000,'static_slots':list(range(27))+[30,31,32],
 'body_triangles':{'hq':190000,'race':105000},'max_static_vertices':420000,
 'inherited':['Supra chassis / handling / sound','driver and native mechanical rig','tyres, tyre shadows and race spin blur','native body shadow representation','factory paint palette'],
 'limits':['Exterior template, not an arbitrary cockpit/rig converter.','Paint uses the template factory palette; custom paint liveries require a dedicated recipe.','Material conversion approximates native shaders, not a physically identical glTF renderer.','New output requires independent in-game inspection.','Native geometry uses this reviewed vehicle template and is routed to the explicitly selected new entry or compatible replacement.'],
}
# A saved correction recipe is permitted only for this exact previously reviewed
# source fingerprint. Generic recognition/export contains no model mesh-number table.
KNOWN_SOURCE='b05092a00374905e96d7c954dba702330d361a1a73eb0813044ef796ff2669b6'
KNOWN_LAMPS={'n250p0':{'role':'headlight','corner':None},'n146p0':{'role':'taillight','corner':None},'n328p0':{'role':'brakelight','corner':None}}

def source_recipe(scene):
    from .f50_wheel_adapter import F50_SOURCE_SHA256,F50_WHEEL_GROUPS,F50_CORNERS
    if scene.digest==F50_SOURCE_SHA256:
        wheels=[p for p in scene.parts if p.extras.get('gt6_source_part') in F50_WHEEL_GROUPS]
        required={part+'__'+corner for part in F50_WHEEL_GROUPS for corner in F50_CORNERS}
        if len(wheels)!=16 or {p.id for p in wheels}!=required:raise GarageError('F50 source must be prepared with all four wheel assemblies.','source_profile')
        recipe={p.id:{'role':p.extras['gt6_role'],'corner':p.extras['gt6_corner']} for p in wheels}
        ids={p.id for p in scene.parts}
        if not {'n3p0','n7p0','n27p0'}<=ids:raise GarageError('F50 paint, trim or lamp inventory changed.','source_profile')
        # Correct two source-material naming anomalies. The blackplastic
        # surface is NOT paint; the red exterior is material 1.
        # Original F50 GLB material 8 is named 'brakelight' and contains
        # the four red rear lens assemblies. There is no independently
        # authored running-lamp mesh. The reviewed donor can illuminate
        # these SAME lens faces at running and brake intensities through
        # separate native lamp command branches. Do not invent geometry or
        # claim a dedicated high-stop lamp. The two brightness states still
        # need real in-game verification before F50 acceptance.
        rear=next((p for p in scene.parts if p.id=='n10p0'),None)
        if rear is None or getattr(rear,'material',None)!=8 or len(rear.indices)!=2016:
            raise GarageError('Known F50 rear red-lens source no longer matches the reviewed brake/tail lamp surface.','source_profile')
        recipe.update({'n3p0':{'role':'paint','corner':None},
                       'n7p0':{'role':'body','corner':None},
                       'n27p0':{'role':'glass','corner':None},
                       'n10p0':{'role':'taillight','corner':None}})
        return recipe
    if scene.digest!=KNOWN_SOURCE:return {}
    ids={p.id for p in scene.parts}
    if not set(KNOWN_LAMPS)<=ids:raise GarageError('Known-source parts do not match its recipe.')
    # Reopened reviewed neutral/hq.json establishes source material 13 as
    # paint and 12 as matte trim. Apply only to the exact source fingerprint.
    paint=[p for p in scene.parts if p.material==13];trim=[p for p in scene.parts if p.material==12]
    if len(paint)!=7 or len(trim)!=34:raise GarageError('Known-source paint/trim inventory does not match the reviewed recipe.')
    recipe={p.id:{'role':'body','corner':None} for p in trim}
    recipe.update({p.id:{'role':'paint','corner':None} for p in paint})
    recipe.update({k:dict(v) for k,v in KNOWN_LAMPS.items()})
    return recipe

def locate(root:Path)->Path:
    root=Path(root)
    candidates=[root,root/'car/0004/0006',root/'Authoring/car/0004/0006',root/'Input/car/0004/0006',
                root/'Support/Car template',root/'_internal/resources/stock-authoring/template',
                root/'Support/Starting package/Input/car/0004/0006',root/'stock-BCES01893-122/Authoring/car/0004/0006']
    for p in candidates:
        if (p/'hq/body').is_file() and (p/'race/wheel').is_file():return p
    raise GarageError('Choose a prepared car template, your earlier Garage installation, or your previous stock workspace.','car_template')

def validate_template(root:Path):
    root=locate(root);files={}
    if root.is_symlink() or (hasattr(root,'is_junction') and root.is_junction()):raise GarageError('Choose a regular template folder, not a filesystem link.')
    for rel,digest in ASSETS.items():
        path=root/rel
        if not path.is_file() or path.is_symlink() or path.stat().st_size>16*1024**2 or hash_file(path)!=digest:raise GarageError(f'The {rel} file does not match the reviewed v029 profile. No files changed.','template_identity')
        if path.name in ('body','wheel','interior'):MDL3(path.read_bytes()).validate()
        files[rel]={'sha256':digest,'bytes':path.stat().st_size}
    return {'id':PROFILE_ID,'label':PROFILE['label'],'root':str(root),'files':files,'game_update':'01.22','inherited':PROFILE['inherited'],'limits':PROFILE['limits']}
