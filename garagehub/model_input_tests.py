"""Real adapter regressions using portable in-memory source model fixtures."""
from __future__ import annotations

import base64
import io
import json
import stat
import struct
import unittest
import zipfile

import numpy as np
from PIL import Image

from .errors import GarageError
from .glb import GLB
from .model_inputs import prepare_model
from .util import sha256


TRIANGLE_OBJ = b'o body\nv 0 0 0\nv 1 0 0\nv 0 1 0\nf 1 2 3\n'


def model_zip(files):
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, 'w', zipfile.ZIP_DEFLATED) as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return stream.getvalue()


def png():
    output = io.BytesIO()
    Image.new('RGBA', (2, 2), (255, 30, 0, 140)).save(output, 'PNG')
    return output.getvalue()


def triangle_gltf(two_buffers=True):
    positions = struct.pack('<9f', 0, 0, 0, 1, 0, 0, 0, 1, 0)
    indices = struct.pack('<3H', 0, 1, 2)
    doc = {'asset': {'version': '2.0'},
           'buffers': [{'uri': 'mesh.bin', 'byteLength': len(positions)}, {'uri': 'indices.bin', 'byteLength': len(indices)}],
           'bufferViews': [{'buffer': 0, 'byteLength': len(positions)}, {'buffer': 1, 'byteLength': len(indices)}],
           'accessors': [{'bufferView': 0, 'componentType': 5126, 'count': 3, 'type': 'VEC3'}, {'bufferView': 1, 'componentType': 5123, 'count': 3, 'type': 'SCALAR'}],
           'meshes': [{'primitives': [{'attributes': {'POSITION': 0}, 'indices': 1}]}],
           'nodes': [{'mesh': 0, 'name': 'body'}], 'scenes': [{'nodes': [0]}], 'scene': 0}
    resources = {'mesh.bin': positions, 'indices.bin': indices}
    if not two_buffers:
        resources = {'mesh.bin': positions + indices}
        doc['buffers'] = [{'uri': 'mesh.bin', 'byteLength': len(positions + indices)}]
        doc['bufferViews'][1].update(buffer=0, byteOffset=len(positions))
    return doc, resources


def embedded_glb():
    doc, resources = triangle_gltf(False)
    data = resources['mesh.bin']
    del doc['buffers'][0]['uri']
    meta = json.dumps(doc).encode()
    meta += b' ' * (-len(meta) % 4)
    data += b'\0' * (-len(data) % 4)
    return struct.pack('<4sII', b'glTF', 2, 28 + len(meta) + len(data)) + struct.pack('<II', len(meta), 0x4e4f534a) + meta + struct.pack('<II', len(data), 0x004e4942) + data


class ModelInputTests(unittest.TestCase):
    def test_glb_with_external_texture_keeps_binary_and_embeds_the_image(self):
        import tempfile
        from pathlib import Path
        from .model_inputs import local_model_upload
        doc,files=triangle_gltf(two_buffers=False)
        del doc['buffers'][0]['uri']
        doc['images']=[{'uri':'Textures/paint.png'}]
        meta=json.dumps(doc).encode()
        meta+=b' '*(-len(meta)%4)
        data=files['mesh.bin']
        data+=b'\0'*(-len(data)%4)
        body=struct.pack('<II',len(meta),0x4e4f534a)+meta+struct.pack('<II',len(data),0x004e4942)+data
        model=struct.pack('<4sII',b'glTF',2,len(body)+12)+body
        with tempfile.TemporaryDirectory() as tmp:
            path=Path(tmp)/'body.glb';path.write_bytes(model)
            with self.assertRaises(GarageError):local_model_upload(path)
            (path.parent/'Textures').mkdir()
            (path.parent/'Textures/paint.png').write_bytes(png())
            (path.parent/'unrelated.png').write_bytes(png())
            source,name=local_model_upload(path)
        with zipfile.ZipFile(io.BytesIO(source)) as archive:
            self.assertEqual(set(archive.namelist()),{'body.glb','Textures/paint.png'})
        normalized,meta=prepare_model(source,name)
        glb=GLB(normalized)
        self.assertEqual(glb.scene().triangles,1)
        self.assertEqual(bytes(glb.view(glb.doc['images'][0]['bufferView'])[0]),png())

    def test_neutral_obj_colours_and_degenerate_faces(self):
        raw=b'v 0 0 0 1 1 1\nv 1 0 0 1 1 1\nv 0 1 0 1 1 1\nf 1 2 1\nf 1 2 3\n'
        scene,meta=self.scene(raw)
        self.assertEqual(scene.triangles,1)
        self.assertTrue(meta['warnings'])
        with self.assertRaises(GarageError):self.scene(raw.replace(b'1 1 1',b'0 1 1'))

    def test_fbx_colour_atlas_blending_changes_only_for_fully_opaque_pixels(self):
        from .blender_bridge import opaque_fbx_materials
        from .model_inputs import _pack_glb
        for alpha in (255,140):
            doc,files=triangle_gltf(two_buffers=False)
            del doc['buffers'][0]['uri']
            rgba=io.BytesIO();Image.new('RGBA',(2,2),(20,50,80,alpha)).save(rgba,'PNG')
            binary=files['mesh.bin'];binary+=b'\0'*(-len(binary)%4)
            doc['bufferViews'].append({'buffer':0,'byteOffset':len(binary),'byteLength':len(rgba.getvalue())})
            binary+=rgba.getvalue();doc['buffers'][0]['byteLength']=len(binary)
            doc.update(images=[{'bufferView':2,'mimeType':'image/png'}],textures=[{'source':0}],
                       materials=[{'name':'Atlas','alphaMode':'BLEND','pbrMetallicRoughness':{'baseColorTexture':{'index':0}}}])
            doc['meshes'][0]['primitives'][0]['material']=0
            raw=_pack_glb(doc,binary)
            corrected=opaque_fbx_materials(raw)
            self.assertEqual(GLB(corrected).doc['materials'][0]['alphaMode'],'OPAQUE' if alpha==255 else 'BLEND')
            if alpha!=255:self.assertEqual(raw,corrected)

    def test_steering_wheel_is_cabin_geometry(self):
        from .recognition import _name_role,component_name_role
        self.assertEqual(_name_role('InteriorSteeringWheel04 Material 3'),'interior')
        self.assertEqual(_name_role('wheel-front-left'),'rim')
        self.assertEqual(component_name_role('wipers static','Tires'),'body')
        self.assertEqual(component_name_role('grills','Tires'),'body')
        self.assertEqual(component_name_role('grill wheel','Tires'),'tyre')

    def scene(self, raw, name='car.obj'):
        normalized, metadata = prepare_model(raw, name)
        return GLB(normalized).scene(), metadata

    def test_glb_is_byte_identical_and_provenance_is_distinct(self):
        raw = embedded_glb()
        result, metadata = prepare_model(raw, 'original.glb')
        self.assertEqual(result, raw)
        self.assertEqual(metadata['source_sha256'], sha256(raw))
        self.assertEqual(metadata['normalized_sha256'], metadata['source_sha256'])
        obj_result, obj_meta = prepare_model(TRIANGLE_OBJ, 'car.obj')
        self.assertEqual(obj_meta['source_sha256'], sha256(TRIANGLE_OBJ))
        self.assertEqual(obj_meta['normalized_sha256'], sha256(obj_result))
        self.assertNotEqual(obj_meta['source_sha256'], obj_meta['normalized_sha256'])

    def test_obj_triangle_deterministic_and_coordinates_preserved(self):
        a, metadata = prepare_model(TRIANGLE_OBJ, 'car.obj')
        self.assertEqual(a, prepare_model(TRIANGLE_OBJ, 'car.obj')[0])
        scene = GLB(a).scene()
        self.assertEqual(scene.triangles, 1)
        np.testing.assert_allclose(scene.parts[0].positions, [[0, 0, 0], [1, 0, 0], [0, 1, 0]])
        np.testing.assert_allclose(scene.parts[0].normals, [[0, 0, 1]] * 3)
        self.assertTrue(metadata['requires_axis_review'])
        self.assertEqual(metadata['units'], 'as_authored')

    def test_concave_polygon_preserves_area_and_orientation(self):
        raw = b'v 0 0 0\nv 2 0 0\nv 2 2 0\nv 1 1 0\nv 0 2 0\nf 1 2 3 4 5\n'
        for source, direction in ((raw, 1), (raw.replace(b'f 1 2 3 4 5', b'f 5 4 3 2 1'), -1)):
            with self.subTest(direction=direction):
                scene, _ = self.scene(source)
                self.assertEqual(scene.triangles, 3)
                p = scene.parts[0]
                cross = np.cross(p.positions[p.indices[:, 1]] - p.positions[p.indices[:, 0]], p.positions[p.indices[:, 2]] - p.positions[p.indices[:, 0]])
                self.assertTrue(np.all(cross[:, 2] * direction > 0))
                self.assertAlmostEqual(float(np.abs(cross[:, 2]).sum() / 2), 3.)

    def test_tiny_valid_triangle_is_not_removed(self):
        scene, _ = self.scene(b'v 0 0 0\nv 0.00000001 0 0\nv 0 0.00000001 0\nf 1 2 3\n')
        self.assertEqual(scene.triangles, 1)
        self.assertGreater(float(scene.parts[0].positions[1, 0]), 0.)

    def test_negative_indices_resolve_at_face_definition(self):
        scene, _ = self.scene(TRIANGLE_OBJ.replace(b'f 1 2 3', b'f -3 -2 -1') + b'v 20 30 40\n')
        self.assertEqual(scene.triangles, 1)
        np.testing.assert_allclose(scene.bounds, [[0, 0, 0], [1, 1, 0]])

    def test_uv_and_normal_discontinuities_are_not_welded(self):
        raw = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nvt .5 .5\nvn 0 0 1\nvn 0 0 -1\nf 1/1/1 2/2/1 3/3/1\nf 1/4/2 3/3/2 2/2/2\n'
        scene, _ = self.scene(raw)
        part = scene.parts[0]
        self.assertEqual(scene.triangles, 2)
        self.assertEqual(len(part.positions), 6)
        np.testing.assert_allclose(part.uv[0], [0, 1])
        np.testing.assert_allclose(part.uv[3], [.5, .5])
        np.testing.assert_allclose(part.normals[[0, 3]], [[0, 0, 1], [0, 0, -1]])

    def test_missing_normals_respect_flat_and_smoothing_groups(self):
        base = b'v 0 0 0\nv 1 0 0\nv 0 1 0\nv 0 0 1\n'
        faces = b'f 1 2 3\nf 1 4 2\n'
        flat, _ = self.scene(base + faces)
        smooth, _ = self.scene(base + b's 1\n' + faces)
        self.assertEqual(len(flat.parts[0].positions), 6)
        self.assertEqual(len(smooth.parts[0].positions), 4)
        np.testing.assert_allclose(smooth.parts[0].normals[0], [0, 2**-.5, 2**-.5], atol=1e-6)

    def test_textured_obj_zip_retains_material_parts_and_opacity(self):
        obj = b'mtllib paint.mtl\no Door\ng shell\nusemtl Paint\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n'
        mtl = b'newmtl Paint\nKd .2 .4 .6\nd .5\nPr .3\nPm .8\nmap_Kd ../textures/body color.png\n'
        scene, metadata = self.scene(model_zip({'car/model.obj': obj, 'car/paint.mtl': mtl, 'textures/body color.png': png()}), 'car.zip')
        mat = scene.materials[0]
        self.assertEqual(mat['alpha_mode'], 'BLEND')
        np.testing.assert_allclose(mat['color'], [.2, .4, .6, .5])
        self.assertAlmostEqual(mat['metallic'], .8)
        self.assertAlmostEqual(mat['roughness'], .3)
        self.assertEqual(mat['base_texture'], 0)
        self.assertIn('Door', scene.parts[0].name)
        self.assertEqual(metadata['archive_model_path'], 'car/model.obj')
        self.assertEqual(scene.document['images'][0]['mimeType'], 'image/png')

    def test_obj_alpha_channel_does_not_invent_material_transparency(self):
        obj = b'mtllib a.mtl\nusemtl Paint\nv 0 0 0\nv 1 0 0\nv 0 1 0\nvt 0 0\nvt 1 0\nvt 0 1\nf 1/1 2/2 3/3\n'
        scene, _ = self.scene(model_zip({'a.obj': obj, 'a.mtl': b'newmtl Paint\nmap_Kd color.png\n', 'color.png': png()}), 'model.zip')
        self.assertEqual(scene.materials[0]['alpha_mode'], 'OPAQUE')
        self.assertEqual(scene.materials[0]['color'][3], 1)

    def test_missing_material_or_texture_is_actionable(self):
        with self.assertRaisesRegex(GarageError, 'Put the model.*ZIP'):
            prepare_model(b'mtllib absent.mtl\n' + TRIANGLE_OBJ, 'car.obj')
        files = {'a.obj': b'mtllib a.mtl\n' + TRIANGLE_OBJ, 'a.mtl': b'newmtl Paint\nmap_Kd absent.png\n'}
        with self.assertRaisesRegex(GarageError, 'absent.png'):
            prepare_model(model_zip(files), 'car.zip')

    def test_textured_face_requires_complete_uvs(self):
        files = {'a.obj': b'mtllib a.mtl\nusemtl Paint\n' + TRIANGLE_OBJ, 'a.mtl': b'newmtl Paint\nmap_Kd a.png\n', 'a.png': png()}
        with self.assertRaisesRegex(GarageError, 'without UVs'):
            prepare_model(model_zip(files), 'car.zip')

    def test_corrupt_texture_is_reported_before_native_conversion(self):
        files = {'a.obj': b'mtllib a.mtl\n' + TRIANGLE_OBJ, 'a.mtl': b'newmtl Paint\nmap_Kd a.png\n', 'a.png': b'\x89PNG\r\n\x1a\ntruncated'}
        with self.assertRaisesRegex(GarageError, 'readable image'):
            prepare_model(model_zip(files), 'car.zip')

    def test_large_authored_normals_normalize_without_overflow(self):
        raw = TRIANGLE_OBJ.replace(b'f 1 2 3', b'vn 1e300 0 1e300\nf 1//1 2//1 3//1')
        scene, _ = self.scene(raw)
        np.testing.assert_allclose(scene.parts[0].normals, [[2**-.5, 0, 2**-.5]] * 3, atol=1e-6)

    def test_unsupported_texture_transform_is_not_silently_dropped(self):
        files = {'a.obj': b'mtllib a.mtl\n' + TRIANGLE_OBJ, 'a.mtl': b'newmtl Paint\nmap_Kd -s 2 2 1 a.png\n', 'a.png': png()}
        with self.assertRaisesRegex(GarageError, 'Bake that UV transform'):
            prepare_model(model_zip(files), 'car.zip')

    def test_invalid_polygons_are_rejected_instead_of_fan_filled(self):
        cases = [b'v 0 0 0\nv 1 1 0\nv 0 1 0\nv 1 0 0\nf 1 2 3 4\n',
                 b'v 0 0 0\nv 1 0 0\nv 1 1 1\nv 0 1 0\nf 1 2 3 4\n',
                 TRIANGLE_OBJ.replace(b'f 1 2 3', b'f 0 2 3')]
        for raw in cases:
            with self.subTest(raw=raw):
                with self.assertRaises(GarageError):
                    prepare_model(raw, 'car.obj')

    def test_multiple_gltf_buffers_embed_and_mirrored_winding_is_correct(self):
        doc, resources = triangle_gltf()
        doc['nodes'][0]['scale'] = [-1, 1, 1]
        resources['car.gltf'] = json.dumps(doc).encode()
        scene, _ = self.scene(model_zip(resources), 'model.zip')
        self.assertEqual(len(scene.document['buffers']), 1)
        self.assertNotIn('uri', scene.document['buffers'][0])
        self.assertEqual(scene.triangles, 1)
        part = scene.parts[0]
        i = part.indices[0]
        normal = np.cross(part.positions[i[1]] - part.positions[i[0]], part.positions[i[2]] - part.positions[i[0]])
        self.assertGreater(float(normal @ part.normals[i].mean(axis=0)), 0)

    def test_gltf_embedded_base64_resources_work_without_zip(self):
        doc, resources = triangle_gltf()
        for b in doc['buffers']:
            b['uri'] = 'data:application/octet-stream;base64,' + base64.b64encode(resources[b['uri']]).decode()
        scene, metadata = self.scene(json.dumps(doc).encode(), 'car.gltf')
        self.assertEqual(scene.triangles, 1)
        self.assertEqual(metadata['input_format'], 'gltf')

    def test_gltf_external_image_is_embedded_and_opaque_semantics_preserved(self):
        doc, resources = triangle_gltf()
        doc['images'] = [{'uri': 'paint.png'}]
        resources['paint.png'] = png()
        doc['materials'] = [{'alphaMode': 'OPAQUE', 'pbrMetallicRoughness': {'baseColorFactor': [.5, .5, .5, .2]}}]
        doc['meshes'][0]['primitives'][0]['material'] = 0
        resources['car.gltf'] = json.dumps(doc).encode()
        scene, _ = self.scene(model_zip(resources), 'car.zip')
        self.assertNotIn('uri', scene.document['images'][0])
        self.assertEqual(scene.materials[0]['alpha_mode'], 'OPAQUE')
        self.assertEqual(scene.materials[0]['color'][3], 1.)

    def test_gltf_missing_buffer_and_remote_uri_rejected(self):
        for uri in ('missing.bin', 'https://example.invalid/model.bin', '../../outside.bin', '%2e%2e/outside.bin', 'file:///secret.bin'):
            with self.subTest(uri=uri):
                doc, _ = triangle_gltf()
                doc['buffers'][0]['uri'] = uri
                with self.assertRaises(GarageError):
                    prepare_model(json.dumps(doc).encode(), 'car.gltf')

    def test_buffer_range_is_validated_before_rebasing(self):
        doc, resources = triangle_gltf()
        doc['bufferViews'][0]['byteOffset'] = 10
        resources['car.gltf'] = json.dumps(doc).encode()
        with self.assertRaisesRegex(GarageError, 'outside'):
            prepare_model(model_zip(resources), 'model.zip')

    def test_zip_model_ambiguity_and_case_conflicts_rejected(self):
        for files in ({'a.obj': TRIANGLE_OBJ, 'b.obj': TRIANGLE_OBJ}, {'a.obj': TRIANGLE_OBJ, 'A.OBJ': TRIANGLE_OBJ}):
            with self.subTest(files=list(files)):
                with self.assertRaises(GarageError):
                    prepare_model(model_zip(files), 'car.zip')

    def test_zip_path_traversal_and_symlink_rejected_without_extraction(self):
        with self.assertRaises(GarageError):
            prepare_model(model_zip({'a.obj': TRIANGLE_OBJ, '../outside': b'x'}), 'car.zip')
        stream = io.BytesIO()
        with zipfile.ZipFile(stream, 'w') as archive:
            archive.writestr('a.obj', TRIANGLE_OBJ)
            item = zipfile.ZipInfo('linked.mtl')
            item.create_system = 3
            item.external_attr = (stat.S_IFLNK | 0o777) << 16
            archive.writestr(item, '/etc/passwd')
        with self.assertRaisesRegex(GarageError, 'symbolic link'):
            prepare_model(stream.getvalue(), 'car.zip')

    def test_executable_obj_directives_and_unsupported_sources_rejected(self):
        with self.assertRaisesRegex(GarageError, 'executable OBJ'):
            prepare_model(b'csh echo injected\n' + TRIANGLE_OBJ, 'car.obj')
        with self.assertRaisesRegex(GarageError, 'Export FBX/Blend/CAD'):
            prepare_model(b'FBX', 'car.fbx')


if __name__ == '__main__':
    unittest.main()
