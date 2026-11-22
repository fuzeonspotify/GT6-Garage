"""Prepare source-derived HQ/race geometry with explicit PS3 budgets and frames."""
from pathlib import Path
import bpy, json, struct, hashlib, math, collections
import numpy as np
from mathutils import Matrix,Vector
W=Path.cwd()
source=W/'input/SupraRZ97/1997_toyota_supra_rz.glb';data=source.read_bytes();jlen=struct.unpack_from('<I',data,12)[0];gltf=json.loads(data[20:20+jlen])
source_mesh_nodes={n['mesh']:n['name'] for n in gltf['nodes'] if 'mesh' in n}
wheel_roles={0:'FR',1:'FR',2:'FR',3:'RR',4:'RR',5:'RR',6:'RL',7:'RL',8:'RL',174:'FL',175:'FL',176:'FL'}
materials=[];group_map={};keys={}
for mi,m in enumerate(gltf['materials']):
 p=m.get('pbrMetallicRoughness',{});color=p.get('baseColorFactor',[1,1,1,1]);texture=p.get('baseColorTexture',{}).get('index');alpha=m.get('alphaMode','OPAQUE')
 family='glass' if alpha=='BLEND' else ('paint' if mi==13 else 'metal' if p.get('metallicFactor',1)>.3 else 'matte')
 key=(family,tuple(round(x,4) for x in color),texture)
 if key not in keys:
  gid=len(keys);keys[key]=gid;materials.append(dict(id=gid,family=family,color=color,base_color_texture=texture,source_material_ids=[],normal_maps_not_transferred=True))
 gid=keys[key];group_map[mi]=gid;materials[gid]['source_material_ids'].append(mi)
bpy.ops.wm.read_factory_settings(use_empty=True)
bpy.ops.import_scene.gltf(filepath=str(source))
bpy.context.view_layer.update()
# Reload once to stabilize Blender's mesh ordering before the decimation pass.
blend_path = W / 'authoring/source.blend'
bpy.ops.wm.save_as_mainfile(filepath=str(blend_path))
bpy.ops.wm.open_mainfile(filepath=str(blend_path))
scene=bpy.context.scene
src_objects={mid:bpy.data.objects[name] for mid,name in source_mesh_nodes.items()}
# glTF source +Z points forward; native GT6 uses -Z. Blender is Z-up.
scale=2.55/(1.2340936660766602+1.3238823413848877)
cx=-.01332981;cz=(1.2340936660766602-1.3238823413848877)*.5;floor=-.15389527
A=np.array([[-scale,0,0],[0,0,scale],[0,scale,0]],dtype=np.float64)
T=np.array([cx*scale,-floor*scale-.130,cz*scale])
centers={}
for mid,corner in wheel_roles.items():
 if mid not in (0,5,6,176):continue
 o=src_objects[mid];pts=np.array([tuple(o.matrix_world@v.co) for v in o.data.vertices]);p=pts@A.T+T
 centers[corner]=(p.min(0)+p.max(0))*.5
source_count=sum(len(p.vertices)-2 for o in src_objects.values() for p in o.data.polygons)
out=W/'authoring/neutral';out.mkdir(exist_ok=False)
(W/'input/SOURCE_LICENSE.txt').write_text('This work is based on "1997 Toyota Supra RZ" by OUTPISTON.\nSource: https://sketchfab.com/3d-models/1997-toyota-supra-rz-d6ed1376d91e4fd3b8e6fc061065c44f\nLicense: CC BY-NC-SA 4.0 https://creativecommons.org/licenses/by-nc-sa/4.0/\nCredit required. Noncommercial. Adapted model must retain the same license.\n',encoding='utf-8')
# Extract embedded images only; never execute source data.
binstart=28+jlen;blen=struct.unpack_from('<I',data,20+jlen)[0];blobsrc=data[binstart:binstart+blen];(out/'textures').mkdir()
for i,im in enumerate(gltf['images']):
 v=gltf['bufferViews'][im['bufferView']];ext='.png' if im['mimeType']=='image/png' else '.jpg'
 (out/'textures'/f'image_{i:02d}{ext}').write_bytes(blobsrc[v.get('byteOffset',0):v.get('byteOffset',0)+v['byteLength']])
results={};optimized={}
for lod in ('hq','race'):
 collection=bpy.data.collections.new('Supra_'+lod.upper());scene.collection.children.link(collection)
 binary=bytearray();chunks=[];total_tri=0
 def put(arr):
  binary.extend(b'\0'*(-len(binary)%16));p=len(binary);raw=arr.tobytes();binary.extend(raw);return dict(byte_offset=p,byte_length=len(raw),count=len(arr))
 for mid,o in src_objects.items():
  smid=gltf['meshes'][mid]['primitives'][0].get('material',0);tri=sum(len(p.vertices)-2 for p in o.data.polygons)
  if tri<1200:ratio=1.
  elif mid in wheel_roles:ratio=.30 if lod=='hq' else .15
  elif smid==13:ratio=.60 if lod=='hq' else .32
  elif tri>4000:ratio=.24 if lod=='hq' else .10
  else:ratio=.70 if lod=='hq' else .35
  obj=o.copy();obj.data=o.data.copy();collection.objects.link(obj);obj.name=f'Supra_{lod}_{mid:03d}'
  # Preserve parent transforms while preparing the evaluated local mesh.
  world=o.matrix_world.copy();obj.parent=None;obj.matrix_world=world
  if ratio<1:
   mod=obj.modifiers.new('PS3 detail budget','DECIMATE');mod.decimate_type='COLLAPSE';mod.ratio=ratio;mod.use_collapse_triangulate=True
  dg=bpy.context.evaluated_depsgraph_get();ev=obj.evaluated_get(dg);mesh=bpy.data.meshes.new_from_object(ev,preserve_all_data_layers=True,depsgraph=dg)
  obj.modifiers.clear();obj.data=mesh;mesh.calc_loop_triangles()
  MW=np.array(world,dtype=np.float64);B=A@MW[:3,:3];t=A@MW[:3,3]+T;NM=np.linalg.inv(B).T;flipped=np.linalg.det(B)<0
  uv_layer=mesh.uv_layers.active;normal_data=mesh.corner_normals
  pos=[];normal=[];uv=[];indices=[];cache={}
  for face in mesh.loop_triangles:
   loops=list(face.loops)
   if flipped:loops[1],loops[2]=loops[2],loops[1]
   for li in loops:
    vid=mesh.loops[li].vertex_index;p=B@np.array(mesh.vertices[vid].co)+t;n=NM@np.array(normal_data[li].vector);length=np.linalg.norm(n)
    n=n/length if length>1e-9 else np.array([0.,1.,0.]);v=uv_layer.data[li].uv if uv_layer else (0,0);tex=(float(v[0]),1-float(v[1]))
    key=struct.pack('<8f',*p,*n,*tex)
    if key not in cache:cache[key]=len(pos);pos.append(p);normal.append(n);uv.append(tex)
    indices.append(cache[key])
  ps=np.array(pos,dtype='<f4');ns=np.array(normal,dtype='<f4');uvs=np.array(uv,dtype='<f4');ids=np.array(indices,dtype='<u4')
  corner=wheel_roles.get(mid);role='static'
  if corner:
   role='tire' if smid==1 else ('rim' if smid==2 else 'valve');cen=centers[corner]
   target_width=.235 if corner[0]=='F' else .255;target_r=.32165 if corner[0]=='F' else .3179
   # Shared full-source tyre radii/widths keep rim and valve coherent.
   source_radius=.326678*scale;source_width=.243640*scale
   sx=1 if corner.endswith('L') else -1
   localA=np.diag([sx*target_width/source_width,target_r/source_radius,sx*target_r/source_radius])
   ps=((ps-cen)@localA.T+np.array([target_width*.5,0,0])).astype('<f4')
   ns=ns@np.linalg.inv(localA);ns/=np.linalg.norm(ns,axis=1)[:,None];ns=ns.astype('<f4')
  bounds=[ps.min(0).tolist(),ps.max(0).tolist()]
  chunks.append(dict(id=mid,source_mesh_id=mid,source_material_id=smid,material_id=group_map[smid],role=role,corner=corner,
    vertices=len(ps),triangles=len(ids)//3,source_triangles=tri,decimation_ratio_requested=ratio,negative_transform_winding_corrected=bool(flipped),
    buffers={'positions':put(ps),'normals':put(ns),'uv0':put(uvs),'indices':put(ids)},bounds=bounds))
  total_tri+=len(ids)//3
  obj.hide_render=(lod!='hq');obj.hide_set(lod!='hq')
 for o in src_objects.values():o.hide_render=True;o.hide_set(True)
 (out/f'{lod}.bin').write_bytes(binary)
 doc=dict(lod=lod,binary=f'{lod}.bin',binary_sha256=hashlib.sha256(binary).hexdigest(),source_triangles=source_count,triangles=total_tri,chunks=chunks,materials=materials,material_map=group_map,
  source_asset=gltf['asset'],source_sha256=hashlib.sha256(data).hexdigest(),wheel_centers_body={k:v.tolist() for k,v in centers.items()},uniform_body_scale=scale,
  uv_policy='Original glTF UV restored after Blender import; imported image rows not flipped.',source_components_retained=len(chunks),normal_policy='Inverse-transpose, normalized; negative determinant triangles reversed',shader_limitations='Source PBR normal maps and transmission are approximated by native shaders, not copied as raw incompatible maps.')
 (out/f'{lod}.json').write_text(json.dumps(doc,indent=2));results[lod]=dict(triangles=total_tri,vertices=sum(c['vertices'] for c in chunks),groups=len(materials))
 print('PREPARED',lod,json.dumps(results[lod]),flush=True)
