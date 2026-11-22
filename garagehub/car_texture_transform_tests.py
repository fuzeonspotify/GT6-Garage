"""Base-colour UV compatibility through the real loader and native vertex packer.

The only substitute is a material-editor recorder; no game resource is needed.
KHR_texture_transform defines T * R * S and lets its texCoord override the
textureInfo set. GLB.scene owns that operation; material image conversion must
neither transform the pixels nor apply the coordinate transform a second time.
Reference: KhronosGroup/glTF, extensions/2.0/Khronos/KHR_texture_transform.
"""
from __future__ import annotations
import copy, hashlib, io, json, math, struct, unittest
import numpy as np
from PIL import Image
from .car_geometry import batches, prepare_parts
from .car_materials import Materials
from .errors import GarageError
from .glb import GLB


UV0 = np.array([[0, 0], [1, 0], [1, 1], [0, 1]], np.float32)
UV1 = np.array([[0, 0], [.5, 0], [.5, .25], [0, .25]], np.float32)
PIXELS = np.array([[[x * 60, y * 60, (x + y) * 30, 255]
                    for x in range(4)] for y in range(4)], np.uint8)


def textured_glb(base=None, *, uv_sets=None, normals=True, change=None):
    """A real embedded PNG and two triangles, with no model-specific names."""
    binary = bytearray(); views = []; accessors = []
    def view(data):
        binary.extend(b'\0' * (-len(binary) % 4)); offset = len(binary)
        binary.extend(data)
        views.append({'buffer': 0, 'byteOffset': offset, 'byteLength': len(data)})
        return len(views) - 1
    def attribute(values, kind, ctype=5126):
        values = np.asarray(values, dtype='<f4' if ctype == 5126 else '<u2')
        accessors.append({'bufferView': view(values.tobytes()),
                          'componentType': ctype, 'count': len(values), 'type': kind})
        return len(accessors) - 1
    attrs = {'POSITION': attribute([[-1, 0, -2], [1, 0, -2],
                                    [1, 1, -2], [-1, 1, -2]], 'VEC3')}
    if normals:
        attrs['NORMAL'] = attribute([[0, 0, 1]] * 4, 'VEC3')
    uv_sets = {0: UV0, 1: UV1} if uv_sets is None else uv_sets
    for number, uv in uv_sets.items():
        attrs['TEXCOORD_' + str(number)] = attribute(uv, 'VEC2')
    indices = attribute([0, 1, 2, 0, 2, 3], 'SCALAR', 5123)
    png = io.BytesIO(); Image.fromarray(PIXELS).save(png, format='PNG')
    image_view = view(png.getvalue())
    base = {'index': 0} if base is None else copy.deepcopy(base)
    doc = {'asset': {'version': '2.0'}, 'buffers': [{'byteLength': len(binary)}],
           'bufferViews': views, 'accessors': accessors,
           'images': [{'bufferView': image_view, 'mimeType': 'image/png'}],
           'textures': [{'source': 0}],
           'materials': [{'pbrMetallicRoughness': {'baseColorTexture': base,
                                                   'metallicFactor': 0}}],
           'meshes': [{'primitives': [{'attributes': attrs, 'indices': indices,
                                       'material': 0}]}],
           'nodes': [{'mesh': 0, 'name': 'Textured panel'}],
           'scenes': [{'nodes': [0]}], 'scene': 0}
    if 'KHR_texture_transform' in base.get('extensions', {}):
        doc['extensionsUsed'] = ['KHR_texture_transform']
        doc['extensionsRequired'] = ['KHR_texture_transform']
    if change:
        change(doc)
    encoded = json.dumps(doc, separators=(',', ':')).encode()
    encoded += b' ' * (-len(encoded) % 4)
    binary.extend(b'\0' * (-len(binary) % 4))
    return (b'glTF' + struct.pack('<II', 2, 28 + len(encoded) + len(binary)) +
            struct.pack('<II', len(encoded), 0x4e4f534a) + encoded +
            struct.pack('<II', len(binary), 0x004e4942) + bytes(binary))


def transformed(value, texcoord=0):
    return {'index': 0, 'texCoord': texcoord,
            'extensions': {'KHR_texture_transform': value}}


class RecordingEditor:
    def __init__(self): self.images = []; self.clones = []
    def image(self, image, label):
        self.images.append((label, image.copy())); return len(self.images) - 1
    def texture_keys(self, template): return [('diffuseMapSampler', 0)]
    def fvf(self, template): return template
    def clone_material(self, template, label, bindings, replace):
        self.clones.append((template, label, dict(bindings), replace)); return replace


class CarTextureTransformTests(unittest.TestCase):
    def native_uv(self, scene, material=0):
        """Use real source preparation/batching; only shader allocation is fake."""
        inspection = {'forward': '+Z', 'parts': [
            {'id': p.id, 'role': 'body', 'material': p.material} for p in scene.parts]}
        fit = (np.diag([1.1, 1.2, 1.1]), np.array([0., .25, 0.]),
               {'within_profile_limits': True, 'fit': {'uniform_scale': 1.1}})
        prepared, reports, _ = prepare_parts(scene, inspection, 'hq', fit_solution=fit)
        editor = RecordingEditor(); recipe = Materials(scene, editor).get(material, 'body')
        self.assertIsNone(recipe['constant_uv'])
        self.assertTrue(all(not report['reduced'] for report in reports))
        vertex_bytes, _, _, _ = next(batches(prepared))
        uv = np.array([struct.unpack_from('>2f', vertex_bytes, offset + 16)
                       for offset in range(0, len(vertex_bytes), 24)])
        return uv, editor

    def test_transform_order_override_and_native_packing_apply_once(self):
        raw = textured_glb(transformed({'texCoord': 1, 'scale': [2, 3],
                                       'rotation': math.pi / 2, 'offset': [.25, .5]}))
        scene = GLB(raw).scene(); before = copy.deepcopy(scene)
        # Independent expected coordinates: scale, CCW rotation, then offset.
        expected = np.array([[.25, .5], [.25, 1.5], [-.5, 1.5], [-.5, .5]])
        native, editor = self.native_uv(scene)
        np.testing.assert_allclose(scene.parts[0].uv, expected, atol=1e-6)
        np.testing.assert_allclose(native, expected, atol=1e-6)
        np.testing.assert_array_equal(np.asarray(editor.images[0][1]), PIXELS)
        self.assertEqual(scene.raw, raw)
        self.assertEqual(scene.digest, hashlib.sha256(raw).hexdigest())
        self.assertEqual(scene.document, before.document)
        self.assertEqual(scene.materials, before.materials)
        for field in ('positions', 'normals', 'uv', 'indices'):
            np.testing.assert_array_equal(getattr(scene.parts[0], field),
                                          getattr(before.parts[0], field))
        # Original accessor bytes remain pre-transform, distinct from Part.uv.
        accessor = scene.document['meshes'][0]['primitives'][0]['attributes']['TEXCOORD_1']
        np.testing.assert_array_equal(GLB(raw).accessor(accessor, 'VEC2'), UV1)

    def test_nonzero_base_texcoord_without_extension_is_supported(self):
        scene = GLB(textured_glb({'index': 0, 'texCoord': 1}, uv_sets={1: UV1})).scene()
        native, _ = self.native_uv(scene)
        np.testing.assert_array_equal(native, UV1)

    def test_extension_zero_texcoord_overrides_nonzero_base(self):
        scene = GLB(textured_glb(transformed({'texCoord': 0}, texcoord=1),
                                uv_sets={0: UV0})).scene()
        native, _ = self.native_uv(scene)
        np.testing.assert_array_equal(native, UV0)

    def test_omitted_and_identity_transforms_preserve_legacy_output(self):
        cases = [None, transformed({}), transformed({'scale': [1, 1],
                                                   'offset': [0, 0], 'rotation': 0})]
        for base in cases:
            with self.subTest(base=base):
                native, editor = self.native_uv(GLB(textured_glb(base)).scene())
                np.testing.assert_array_equal(native, UV0)
                np.testing.assert_array_equal(np.asarray(editor.images[0][1]), PIXELS)

    def test_negative_and_zero_scales_are_valid_repeat_coordinates(self):
        for scale, offset, expected in [([-1, 1], [1, 0], 1 - UV0[:, :1]),
                                        ([0, 1], [.25, 0], np.full((4, 1), .25))]:
            with self.subTest(scale=scale):
                scene = GLB(textured_glb(transformed({'scale': scale, 'offset': offset}))).scene()
                native, editor = self.native_uv(scene)
                np.testing.assert_array_equal(native[:, :1], expected)
                np.testing.assert_array_equal(native[:, 1], UV0[:, 1])
                np.testing.assert_array_equal(np.asarray(editor.images[0][1]), PIXELS)

    def test_flat_normal_expansion_retains_transformed_corner_uvs(self):
        scene = GLB(textured_glb(transformed({'scale': [2, .5], 'offset': [-.5, .25]}),
                                normals=False)).scene()
        expected = (UV0 * [2, .5] + [-.5, .25])[[0, 1, 2, 0, 2, 3]]
        native, _ = self.native_uv(scene)
        np.testing.assert_array_equal(native, expected)
        self.assertEqual(scene.triangles, 2)

    def test_missing_selected_uv_does_not_fall_back_to_other_set(self):
        for base, uv in [(transformed({'texCoord': 1}), {0: UV0}),
                         (transformed({'texCoord': 0}, texcoord=1), {1: UV1}),
                         ({'index': 0, 'texCoord': 2}, {0: UV0, 1: UV1})]:
            with self.subTest(base=base), self.assertRaisesRegex(GarageError, 'missing TEXCOORD_'):
                GLB(textured_glb(base, uv_sets=uv)).scene()

    def test_invalid_transform_and_coordinate_metadata_are_rejected(self):
        bad = [None, [], {'offset': [0]}, {'offset': ['0', 0]},
               {'scale': [1, True]}, {'scale': [1, 2, 3]}, {'rotation': True},
               {'rotation': '1'}, {'rotation': float('inf')},
               {'texCoord': True}, {'texCoord': -1}, {'texCoord': 1.5}]
        for transform in bad:
            with self.subTest(transform=transform), self.assertRaises(GarageError):
                GLB(textured_glb(transformed(transform))).scene()

    def test_clamp_and_mirror_wrap_stay_explicitly_unsupported(self):
        for axis in ('wrapS', 'wrapT'):
            for mode in (33071, 33648):
                def change(doc):
                    doc['samplers'] = [{axis: mode}]; doc['textures'][0]['sampler'] = 0
                scene = GLB(textured_glb(transformed({'offset': [.25, .5]}), change=change)).scene()
                with self.subTest(axis=axis, mode=mode), self.assertRaisesRegex(GarageError, 'repeat-wrapped'):
                    Materials(scene, None).source_image(scene.materials[0])

    def test_transform_does_not_enable_compressed_only_texture_recipe(self):
        def change(doc):
            doc['textures'][0] = {'extensions': {'KHR_texture_basisu': {'source': 0}}}
        with self.assertRaisesRegex(GarageError, 'uncompressed PNG/JPEG'):
            GLB(textured_glb(transformed({'offset': [.25, .5]}), change=change)).scene()

    def test_cutout_and_textured_paint_still_require_native_recipes(self):
        scene = GLB(textured_glb(transformed({'scale': [2, 2]}))).scene()
        with self.assertRaisesRegex(GarageError, 'Textured paint/liveries'):
            Materials(scene, RecordingEditor()).get(0, 'paint')
        scene = GLB(textured_glb(transformed({'scale': [2, 2]}),
                                change=lambda d: d['materials'][0].update(alphaMode='MASK'))).scene()
        with self.assertRaisesRegex(GarageError, 'Alpha-cutout'):
            Materials(scene, RecordingEditor()).get(0, 'body')


if __name__ == '__main__': unittest.main()
