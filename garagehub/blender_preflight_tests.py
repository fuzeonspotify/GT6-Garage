import io
import json
from pathlib import Path
import struct
import tempfile
import unittest

import numpy as np
from PIL import Image

from .blender_preflight import _audit_materials, run_check
from .car_materials import Materials
from .glb import GLB
from .model_inputs import _pack_glb
from .template_shell_independent_tests import wheel_free_glb


def textured_fixture(*, opaque_pixels=False, role=None, different_uv=False, wide=False):
    original = GLB(wheel_free_glb(shell_role=role, shell_name='Panel'))
    doc, binary = original.doc, bytearray(original.buffers[0])
    def texture(image):
        stream = io.BytesIO()
        image.save(stream,format='PNG')
        binary.extend(bytes(-len(binary)%4))
        doc['bufferViews'].append(dict(buffer=0,byteOffset=len(binary),byteLength=stream.tell()))
        binary.extend(stream.getvalue())
        images = doc.setdefault('images',[])
        images.append(dict(bufferView=len(doc['bufferViews'])-1,mimeType='image/png'))
        textures=doc.setdefault('textures',[])
        textures.append(dict(source=len(images)-1))
        return {'index':len(textures)-1}
    base = Image.new('RGBA',(4100,4100) if wide else (16,16),(168,54,35,255))
    if not opaque_pixels:
        base.putpixel((0,0),(168,54,35,17))
    normal = Image.new('RGB',(16,16),(128,128,255))
    normal.paste((230,128,220),(0,0,8,16))
    mat = doc['materials'][0]
    mat.update(name='Chassis',alphaMode='BLEND',doubleSided=True,normalTexture=texture(normal))
    mat['pbrMetallicRoughness']['baseColorTexture']=texture(base)
    if different_uv:
        mat['normalTexture']['texCoord']=1
    return _pack_glb(doc,binary)


class BlenderPreflightTests(unittest.TestCase):
    def test_unmodified_export_preserves_original_bytes_and_mesh_tags(self):
        raw = wheel_free_glb()
        # Use valid, noncanonical JSON whitespace as written by other glTF exporters.
        js_length = int.from_bytes(raw[12:16],'little')
        pretty = json.dumps(json.loads(raw[20:20+js_length]),indent=2).encode()
        pretty += b' ' * (-len(pretty) % 4)
        binary_chunk = raw[20+js_length:]
        source = struct.pack('<4sIIII',b'glTF',2,20+len(pretty)+len(binary_chunk),len(pretty),0x4e4f534a)+pretty+binary_chunk
        result, notes = _audit_materials(source,{})
        self.assertEqual(result,source)
        self.assertFalse(any(r['level'] != 'info' for r in notes))

    def test_body_tag_does_not_erase_real_alpha_without_a_choice(self):
        raw = textured_fixture(role='body')
        preview, notes = _audit_materials(raw,{})
        self.assertTrue(any(r['level']=='choose' and r['material']=='Chassis' for r in notes))
        self.assertEqual(GLB(preview).doc['materials'][0]['alphaMode'],'BLEND')

    def test_normal_only_paint_keeps_bake_and_occlusion_strength(self):
        source = GLB(textured_fixture(opaque_pixels=True,role='paint'))
        mat = source.doc['materials'][0]
        del mat['pbrMetallicRoughness']['baseColorTexture']
        mat['alphaMode'] = 'OPAQUE'
        mat['occlusionTexture'] = {**mat['normalTexture'], 'strength':.5}
        raw = _pack_glb(source.doc,source.buffers[0])
        prepared, notes = _audit_materials(raw,{})
        self.assertFalse(any(r['level'] in ('choose','block') for r in notes),notes)
        self.assertTrue(any('ambient occlusion' in r['message'] for r in notes))
        self.assertEqual(GLB(prepared).scene().parts[0].extras['gt6_role'],'body')

    def test_real_alpha_requires_choice_baked_rgb_and_uvs_survive(self):
        raw = textured_fixture()
        _, initial = _audit_materials(raw,{})
        choices = [r for r in initial if r['level']=='choose']
        self.assertEqual([r['material'] for r in choices],['Chassis'])
        ready, notes = _audit_materials(raw,{choices[0]['material_key']:'body'})
        self.assertFalse(any(r['level'] in ('block','choose') for r in notes))
        scene = GLB(ready).scene()
        original = GLB(raw).scene()
        self.assertEqual(scene.document['materials'][0]['alphaMode'],'OPAQUE')
        self.assertNotIn('normalTexture',scene.document['materials'][0])
        self.assertEqual(len(scene.document['images']),1)
        self.assertEqual(scene.parts[0].extras['gt6_role'],'body')
        self.assertTrue(np.array_equal(scene.parts[0].indices,original.parts[0].indices))
        self.assertTrue(np.array_equal(scene.parts[0].uv,original.parts[0].uv))
        with Materials(scene,None).embedded_image(scene.materials[0]['base_texture']) as image:
            self.assertEqual(image.getpixel((0,0))[3],17)
            self.assertLess(image.getpixel((3,3))[0],image.getpixel((13,3))[0])
        again, _ = _audit_materials(ready,{})
        self.assertEqual(again,ready)

    def test_opaque_and_optical_and_mismatched_uv(self):
        ready, notes = _audit_materials(textured_fixture(opaque_pixels=True),{})
        self.assertFalse(any(r['level']=='choose' for r in notes))
        self.assertEqual(GLB(ready).doc['materials'][0]['alphaMode'],'OPAQUE')
        optical,_ = _audit_materials(textured_fixture(role='glass'),{})
        self.assertEqual(GLB(optical).doc['materials'][0]['alphaMode'],'BLEND')
        _, notes = _audit_materials(textured_fixture(different_uv=True),{})
        self.assertTrue(any(r['level']=='block' and 'different UVs' in r['message'] for r in notes))

    def test_oversized_texture_and_stale_material_choice(self):
        first = textured_fixture()
        _, notes = _audit_materials(first,{})
        key=next(r['material_key'] for r in notes if r['level']=='choose')
        raw = textured_fixture(wide=True)
        result, notes = _audit_materials(raw,{key:'body'})
        self.assertTrue(any(r['level']=='choose' and r['material_key']!=key for r in notes))
        scene=GLB(result).scene()
        with Materials(scene,None).embedded_image(scene.materials[0]['base_texture']) as im:
            self.assertEqual(im.size,(2048,2048))

    def test_private_job_gates_publication_and_preserves_input(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            for name,raw in [('blocked',textured_fixture()),('ready',wheel_free_glb())]:
                job=root/name
                job.mkdir()
                (job/'source.glb').write_bytes(raw)
                (job/'request.json').write_text(json.dumps({'schema':1,'choices':{}}))
                r=run_check(job,root/'empty-profile',root)
                self.assertEqual((job/'source.glb').read_bytes(),raw)
                self.assertEqual((job/'prepared.glb').is_file(),name=='ready')
                self.assertEqual(r['can_export'],name=='ready')
                self.assertFalse(r['runtime_tested'])
            self.assertFalse((root/'empty-profile').exists())
