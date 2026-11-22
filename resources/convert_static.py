"""Blender worker. Import scene data at its stored pose and write one embedded GLB."""
import sys
from pathlib import Path
import bpy

source, output = map(Path, sys.argv[sys.argv.index('--')+1:])
if source.suffix.lower()=='.blend':
    bpy.ops.wm.open_mainfile(filepath=str(source),load_ui=False,use_scripts=False)
else:
    bpy.ops.wm.read_factory_settings(use_empty=True)
    if source.suffix.lower()=='.fbx':
        status=bpy.ops.import_scene.fbx(filepath=str(source),use_anim=False,use_image_search=False)
    else:
        status=bpy.ops.import_scene.gltf(filepath=str(source))
    if 'FINISHED' not in status:raise RuntimeError('Scene import failed')
scene=bpy.context.scene
meshes=[obj for obj in scene.objects if obj.type=='MESH' and not obj.hide_render]
if not meshes:raise RuntimeError('No renderable mesh in this scene')
# Evaluate the visible pose once. Parenting, rig motion and modifiers are baked
# into the static mesh copies. The original .blend or FBX stays read-only.
deps=bpy.context.evaluated_depsgraph_get()
bpy.ops.object.select_all(action='DESELECT')
copies=[]
for obj in meshes:
    evaluated=obj.evaluated_get(deps)
    geometry=bpy.data.meshes.new_from_object(evaluated,preserve_all_data_layers=True,depsgraph=deps)
    copy=bpy.data.objects.new(obj.name+' static',geometry)
    scene.collection.objects.link(copy)
    copy.matrix_world=evaluated.matrix_world.copy()
    copy.select_set(True)
    copies.append(copy)
bpy.context.view_layer.objects.active=copies[0]
result=bpy.ops.export_scene.gltf(filepath=str(output),export_format='GLB',use_selection=True,
    export_animations=False,export_skins=False,export_morph=False,export_yup=True,
    export_draco_mesh_compression_enable=False)
if 'FINISHED' not in result:raise RuntimeError('GLB export failed')
