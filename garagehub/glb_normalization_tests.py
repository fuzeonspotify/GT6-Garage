"""Source-format regression cases, independent of car names and GT6 templates.

Khronos glTF 2.0 specifies flat missing normals, OPAQUE alpha, strict indices,
CCW winding under reflected instances and per-corner vertex attributes.
"""
from __future__ import annotations
import base64,copy,json,struct,unittest
import numpy as np
from .glb import GLB
from .recognition import infer
from .errors import GarageError


PNG=base64.b64decode('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAQAAAC1HAwCAAAAC0lEQVR42mP8/x8AAwMCAO+a3ioAAAAASUVORK5CYII=')


def model(positions=None,faces=None,normals=None,uv=None,uv1=None,change=None):
    positions=np.asarray(positions if positions is not None else [[0,0,0],[1,0,0],[0,1,0]],dtype='<f4')
    faces=np.asarray(faces if faces is not None else [0,1,2],dtype='<u2').reshape(-1)
    binary=bytearray();views=[];accessors=[]
    def attribute(array,kind,ctype=5126):
        binary.extend(b'\0'*(-len(binary)%4));off=len(binary);binary.extend(array.tobytes())
        views.append({'buffer':0,'byteOffset':off,'byteLength':array.nbytes})
        accessors.append({'bufferView':len(views)-1,'componentType':ctype,'count':len(array),'type':kind})
        return len(accessors)-1
    attrs={'POSITION':attribute(positions,'VEC3')};index=attribute(faces,'SCALAR',5123)
    if normals is not None:attrs['NORMAL']=attribute(np.asarray(normals,dtype='<f4'),'VEC3')
    if uv is not None:attrs['TEXCOORD_0']=attribute(np.asarray(uv,dtype='<f4'),'VEC2')
    if uv1 is not None:attrs['TEXCOORD_1']=attribute(np.asarray(uv1,dtype='<f4'),'VEC2')
    binary.extend(b'\0'*(-len(binary)%4));io=len(binary);binary.extend(PNG)
    views.append({'buffer':0,'byteOffset':io,'byteLength':len(PNG)})
    doc={'asset':{'version':'2.0'},'buffers':[{'byteLength':len(binary)}],
         'bufferViews':views,'accessors':accessors,
         'images':[{'bufferView':len(views)-1,'mimeType':'image/png'}],
         'textures':[{'source':0}],
         'meshes':[{'primitives':[{'attributes':attrs,'indices':index}]}],
         'nodes':[{'mesh':0,'name':'Generic body'}],'scenes':[{'nodes':[0]}],'scene':0}
    if change:change(doc)
    text=json.dumps(doc,separators=(',',':')).encode();text+=b' '*(-len(text)%4)
    binary.extend(b'\0'*(-len(binary)%4))
    return (b'glTF'+struct.pack('<II',2,28+len(text)+len(binary))+
            struct.pack('<II',len(text),0x4e4f534a)+text+
            struct.pack('<II',len(binary),0x004e4942)+bytes(binary))


def material(doc,alpha='OPAQUE',factor=.2,cutoff=.5):
    doc['materials']=[{'pbrMetallicRoughness':{'baseColorFactor':[.4,.5,.6,factor]},
                       'alphaMode':alpha,'alphaCutoff':cutoff}]
    doc['meshes'][0]['primitives'][0]['material']=0


class GLBNormalizationTests(unittest.TestCase):
    def test_opaque_factor_cannot_turn_solid_body_into_glass(self):
        raw=model(change=lambda d:material(d,factor=0.))
        scene=GLB(raw).scene()
        self.assertEqual(scene.materials[0]['color'][3],1.)
        self.assertEqual(scene.document['materials'][0]['pbrMetallicRoughness']['baseColorFactor'][3],0.)
        self.assertNotEqual(infer(scene)['parts'][0]['role'],'glass')
        self.assertEqual(scene.raw,raw)

    def test_mask_cutoff_and_blend_opacity_remain_distinct(self):
        for alpha in ('MASK','BLEND'):
            with self.subTest(alpha=alpha):
                scene=GLB(model(change=lambda d:material(d,alpha,.2,.7))).scene()
                self.assertEqual(scene.materials[0]['alpha_mode'],alpha)
                self.assertEqual(scene.materials[0]['color'][3],.2)
                self.assertEqual(scene.materials[0]['alpha_cutoff'],.7)

    def test_missing_normals_preserve_hard_edges_and_all_uv_corners(self):
        positions=[[0,0,0],[1,0,0],[0,1,0],[0,0,1]]
        faces=np.array([[0,1,2],[0,3,1]])
        uv=np.array([[0,0],[1,0],[0,1],[.5,.5]])
        part=GLB(model(positions,faces,uv=uv)).scene().parts[0]
        self.assertEqual(len(part.indices),2)
        self.assertEqual(len(part.positions),6)
        np.testing.assert_array_equal(part.positions[part.indices],np.array(positions)[faces])
        np.testing.assert_array_equal(part.uv[part.indices],uv[faces])
        np.testing.assert_allclose(part.normals[part.indices],[[[0,0,1]]*3,[[0,1,0]]*3])

    def test_tiny_valid_triangle_keeps_geometry_and_correct_normal(self):
        positions=np.array([[0,0,0],[1e-12,0,0],[0,1e-12,0]],dtype='<f4')
        part=GLB(model(positions)).scene().parts[0]
        self.assertEqual(len(part.indices),1)
        np.testing.assert_array_equal(part.positions,positions)
        np.testing.assert_allclose(part.normals,[[0,0,1]]*3)

    def test_tiny_valid_node_scale_is_not_mistaken_for_zero(self):
        part=GLB(model(change=lambda d:d['nodes'][0].update(scale=[1e-6]*3))).scene().parts[0]
        np.testing.assert_allclose(part.positions,[[0,0,0],[1e-6,0,0],[0,1e-6,0]],rtol=1e-6)
        np.testing.assert_allclose(part.normals,[[0,0,1]]*3)

    def test_actual_zero_scale_refused(self):
        with self.assertRaisesRegex(GarageError,'zero scale'):
            GLB(model(change=lambda d:d['nodes'][0].update(scale=[1,0,1]))).scene()

    def test_reflected_nonuniform_transform_keeps_front_and_authored_normal(self):
        positions=[[0,0,0],[1,0,1],[0,1,0]]
        n=np.array([-1.,0,1]);n/=np.linalg.norm(n)
        part=GLB(model(positions,normals=[n]*3,change=lambda d:d['nodes'][0].update(scale=[-2,3,.5]))).scene().parts[0]
        tri=part.positions[part.indices[0]];face=np.cross(tri[1]-tri[0],tri[2]-tri[0]);face/=np.linalg.norm(face)
        np.testing.assert_allclose(part.normals,np.repeat(face[None,:],3,axis=0),atol=1e-7)
        np.testing.assert_array_equal(part.indices,[[0,2,1]])

    def test_instances_keep_separate_transforms_and_stable_ids(self):
        def change(doc):
            doc['nodes'].append({'mesh':0,'translation':[10,0,0]})
            doc['scenes'][0]['nodes']=[0,1]
        parts=GLB(model(normals=[[0,0,1]]*3,change=change)).scene().parts
        self.assertEqual([p.id for p in parts],['n0p0','n1p0'])
        np.testing.assert_allclose(parts[1].positions,parts[0].positions+[10,0,0])

    def test_authored_uv_and_normal_seams_are_not_welded(self):
        positions=[[0,0,0],[1,0,0],[0,1,0],[0,0,0],[0,0,1],[1,0,0]]
        normals=[[0,0,1]]*3+[[0,1,0]]*3
        uv=[[0,0],[1,0],[0,1],[.3,.2],[.5,.8],[.6,.2]]
        part=GLB(model(positions,[0,1,2,3,4,5],normals=normals,uv=uv)).scene().parts[0]
        self.assertEqual(len(part.positions),6)
        np.testing.assert_array_equal(part.indices,[[0,1,2],[3,4,5]])
        np.testing.assert_array_equal(part.positions,np.asarray(positions,np.float32))
        np.testing.assert_array_equal(part.normals,np.asarray(normals,np.float32))
        np.testing.assert_array_equal(part.uv,np.asarray(uv,np.float32))

    def test_invalid_position_references_produce_actionable_errors(self):
        for index in (-1,True,999,'0',None):
            with self.subTest(index=index),self.assertRaises(GarageError):
                GLB(model(change=lambda d:d['meshes'][0]['primitives'][0]['attributes'].update(POSITION=index))).scene()

    def test_invalid_index_references_produce_actionable_errors(self):
        for index in (-1,True,999,'0',None):
            with self.subTest(index=index),self.assertRaises(GarageError):
                GLB(model(change=lambda d:d['meshes'][0]['primitives'][0].update(indices=index))).scene()

    def test_negative_buffer_reference_is_not_python_last_item(self):
        with self.assertRaises(GarageError):GLB(model(change=lambda d:d['bufferViews'][0].update(buffer=-1))).scene()

    def test_scene_cannot_list_child_as_a_root_and_drop_its_parent_transform(self):
        def change(doc):
            doc['nodes'].append({'translation':[5,0,0],'children':[0]})
        with self.assertRaisesRegex(GarageError,'parentless'):
            GLB(model(change=change)).scene()

    def test_duplicate_roots_refused(self):
        with self.assertRaises(GarageError):
            GLB(model(change=lambda d:d['scenes'][0].update(nodes=[0,0]))).scene()

    def test_texture_transform_uses_selected_uv_set_before_rotation_and_offset(self):
        def change(doc):
            material(doc)
            doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']={
                'index':0,'texCoord':0,'extensions':{'KHR_texture_transform':{
                    'texCoord':1,'scale':[2,3],'rotation':np.pi/2,'offset':[.25,.5]}}}
        uv1=np.array([[0,0],[1,0],[0,1]],dtype=np.float32)
        part=GLB(model(normals=[[0,0,1]]*3,uv=[[.5,.5]]*3,uv1=uv1,change=change)).scene().parts[0]
        np.testing.assert_allclose(part.uv,[[.25,.5],[.25,2.5],[-2.75,.5]],atol=1e-6)

    def test_missing_referenced_uv_set_refused_instead_of_constant_texture(self):
        def change(doc):
            material(doc)
            doc['materials'][0]['pbrMetallicRoughness']['baseColorTexture']={'index':0,'texCoord':1}
        with self.assertRaisesRegex(GarageError,'TEXCOORD_1'):
            GLB(model(uv=[[0,0],[1,0],[0,1]],change=change)).scene()

    def test_material_factor_validation_rejects_non_numeric_and_out_of_range(self):
        for value in ('one',True,-.1,1.1):
            def change(doc):
                material(doc);doc['materials'][0]['pbrMetallicRoughness']['metallicFactor']=value
            with self.subTest(value=value),self.assertRaises(GarageError):GLB(model(change=change)).scene()

    def test_sparse_misaligned_offset_refused_as_model_error(self):
        def change(doc):
            doc['accessors'][0]['sparse']={'count':1,'indices':{'bufferView':1,'componentType':5123,'byteOffset':True},'values':{'bufferView':0}}
        with self.assertRaises(GarageError):GLB(model(change=change)).scene()

    def test_explicit_material_index_cannot_select_implicit_default(self):
        with self.assertRaises(GarageError):
            GLB(model(change=lambda d:d['meshes'][0]['primitives'][0].update(material=0))).scene()


if __name__=='__main__':unittest.main()
