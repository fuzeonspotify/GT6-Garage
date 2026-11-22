import io
import json
from pathlib import Path
import struct
import tempfile
import unittest
import zlib

import numpy as np
from PIL import Image

from .blender_preflight import _audit_materials, run_check
from .car_materials import Materials
from .errors import GarageError
from .glb import GLB
from .model_inputs import _image_bytes, _pack_glb
from .texture_images import native_thumbnail, open_texture
from .template_shell_independent_tests import wheel_free_glb


def textured_fixture(*, opaque_pixels=False, role=None, different_uv=False, wide=False, base_image=None):
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
    base = base_image if base_image is not None else Image.new('RGBA',(4100,4100) if wide else (16,16),(168,54,35,255))
    if base_image is None and not opaque_pixels:
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
    def test_direct_player_import_keeps_original_and_replays_material_repairs(self):
        from .projects import Store
        from .import_repairs import prepare_player_materials
        for solid in (True,False):
            with self.subTest(solid=solid),tempfile.TemporaryDirectory() as d:
                folder=Path(d)
                store=Store(folder/'projects')
                raw=textured_fixture(opaque_pixels=solid,role='body')
                p=store.create(raw,'Car.glb',stock_replacement=True)
                work=(store.folder(p['id'])/'source.glb').read_bytes()
                self.assertEqual((store.folder(p['id'])/'original_input.bin').read_bytes(),raw)
                model=GLB(work)
                mat=model.doc['materials'][0]
                self.assertNotIn('normalTexture',mat)
                self.assertEqual(mat['alphaMode'],'OPAQUE' if solid else 'BLEND')
                self.assertEqual(prepare_player_materials(model,model.scene()),(work,[]))
                self.assertTrue(any('normal map' in r['message'] for r in p['source_metadata']['material_repairs']))
                saved=folder/'saved.gt6car'
                store.export(p['id'],saved)
                reopened=store.import_archive(saved.read_bytes())
                self.assertEqual(reopened['source_sha256'],p['source_sha256'])
                self.assertEqual(reopened['source_metadata'],p['source_metadata'])

    def test_different_uvs_keep_original_material_and_name_the_affected_part(self):
        from .import_repairs import prepare_player_materials
        raw=textured_fixture(different_uv=True)
        glb=GLB(raw)
        prepared,report=prepare_player_materials(glb,glb.scene())
        self.assertEqual(prepared,raw)
        self.assertTrue(any(item['action']=='review' and item['part_ids']==['n0p0']
                            and 'different UVs' in item['message'] for item in report))

    def test_native_reader_samples_shelby_size_and_preserves_palette_alpha(self):
        base = Image.new('P', (4268,4268))
        base.putpalette([240,24,32,24,96,220]+[0]*762)
        base.info['transparency'] = bytes([0,178])
        base.paste(1,(2100,0,4268,4268))
        raw = textured_fixture(base_image=base)
        scene = GLB(raw).scene()
        reader = Materials(scene,None)
        with reader.source_image(scene.materials[0]) as native:
            self.assertEqual(native.size,(512,512))
            self.assertEqual(native.getpixel((64,200))[3],0)
            self.assertEqual(native.getpixel((360,200))[3],178)
            self.assertEqual(native.getpixel((400,120)),native.getpixel((400,127)))
        self.assertEqual(scene.raw,raw)
        with self.assertRaisesRegex(GarageError, 'Full-resolution'):
            reader.embedded_image(scene.materials[0]['base_texture'])

    def test_large_jpeg_native_sampling_preserves_aspect_and_colour(self):
        buf = io.BytesIO()
        with Image.new('RGB',(5031,3459),(128,77,31)) as original:
            original.save(buf,'JPEG')
        with open_texture(buf.getvalue(),'Livery') as image, native_thumbnail(image) as result:
            self.assertEqual(result.width,512)
            self.assertAlmostEqual(result.height/result.width,3459/5031,places=2)
            np.testing.assert_allclose(result.getpixel((140,100)),(128,77,31,255),atol=3)

    def test_embedded_and_loose_headers_share_predecode_limit_and_name(self):
        png=io.BytesIO()
        Image.new('RGB',(1,1)).save(png,'PNG')
        def header(w,h):
            data=bytearray(png.getvalue())
            data[16:24]=struct.pack('>II',w,h)
            data[29:33]=struct.pack('>I',zlib.crc32(data[12:29]))
            return bytes(data)
        with open_texture(header(8192,8192),'Body.png') as allowed:
            self.assertEqual(allowed.size,(8192,8192))
        for read in (open_texture, lambda data,name:_image_bytes(data,name,[])):
            with self.assertRaisesRegex(GarageError,'Body.png.*9000 × 8000.*8192') as rejected:
                read(header(9000,8000),'Body.png')
            self.assertEqual(rejected.exception.code,'texture_size')

    def test_excluded_rim_colour_is_prepared_without_baking_its_other_maps(self):
        raw=textured_fixture(role='rim',wide=True)
        prepared, notes=_audit_materials(raw,{})
        self.assertTrue(any(r['level']=='fixed' and '4096 × 4096' in r['message'] for r in notes),notes)
        scene=GLB(prepared).scene()
        self.assertEqual(scene.parts[0].extras['gt6_role'],'rim')
        self.assertIn('normalTexture',scene.document['materials'][0])
        with Materials(scene,None).embedded_image(scene.materials[0]['base_texture']) as image:
            self.assertEqual(image.size,(4096,4096))

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
