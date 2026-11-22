"""Prepare embedded colour images on the Blender export copy."""
import copy
import io
import math

import numpy as np
from PIL import Image

from .errors import GarageError
from .model_inputs import _pack_glb
from .car_materials import MAX_SOURCE_PIXELS

# These use ordinary glTF texture slots and buffer views. Preserve all original
# indices when another extension may define references we do not know about.
COMPACTABLE = {'KHR_texture_transform', 'KHR_materials_unlit', 'KHR_materials_transmission',
               'KHR_materials_ior', 'KHR_materials_clearcoat', 'KHR_materials_emissive_strength',
               'KHR_materials_specular', 'KHR_materials_sheen', 'KHR_materials_volume',
               'KHR_materials_iridescence', 'KHR_materials_anisotropy'}


def _fit(size, limit):
    ratio = min(1., limit / max(size))
    return tuple(max(1, round(side * ratio)) for side in size)


def _uv(info):
    ext = info.get('extensions', {})
    transform = ext.get('KHR_texture_transform', {})
    return (transform.get('texCoord', info.get('texCoord', 0)), transform.get('rotation', 0),
            tuple(transform.get('offset', [0, 0])), tuple(transform.get('scale', [1, 1])))


class ExportImages:
    def __init__(self, glb):
        self.glb = glb
        self.doc = glb.doc
        self.added = bytearray()
        self.alpha = {}

    def image(self, info):
        textures = self.doc.get('textures', [])
        index = info.get('index')
        if type(index) is not int or not 0 <= index < len(textures):
            raise GarageError('Select a readable image for this texture in Blender.')
        source = textures[index].get('source')
        images = self.doc.get('images', [])
        if type(source) is not int or not 0 <= source < len(images):
            raise GarageError('Pack a PNG or JPEG for this texture in Blender.')
        data, _ = self.glb.view(images[source]['bufferView'])
        im = Image.open(io.BytesIO(data))
        if im.width * im.height > 64 * 1024**2:
            im.close()
            raise GarageError('Scale this image to 8192 pixels per side or smaller in a copy before preparing it.')
        return im

    def opaque(self, info):
        if not info:
            return True
        index = info['index']
        if index not in self.alpha:
            with self.image(info) as im:
                self.alpha[index] = (im.convert('RGBA').getchannel('A').getextrema()[0] == 255
                                     if 'A' in im.getbands() or 'transparency' in im.info else True)
        return self.alpha[index]

    def wrap(self, info):
        tex = self.doc['textures'][info['index']]
        sampler = self.doc.get('samplers', [])[tex['sampler']] if 'sampler' in tex else {}
        return sampler.get('wrapS', 10497), sampler.get('wrapT', 10497)

    def store(self, image, reference, name):
        out = io.BytesIO()
        image.save(out, format='PNG')
        data = out.getvalue()
        views = self.doc.setdefault('bufferViews', [])
        pictures = self.doc.setdefault('images', [])
        textures = self.doc.setdefault('textures', [])
        offset = len(self.glb.buffers[0]) + len(self.added)
        padding = -offset % 4
        self.added.extend(bytes(padding))
        views.append({'buffer': 0, 'byteOffset': offset + padding, 'byteLength': len(data)})
        self.added.extend(data)
        pictures.append({'name': name + ' GT6', 'bufferView': len(views)-1, 'mimeType': 'image/png'})
        texture = {'source': len(pictures)-1}
        if 'sampler' in textures[reference['index']]:
            texture['sampler'] = textures[reference['index']]['sampler']
        textures.append(texture)
        return {'index': len(textures)-1, **{k: copy.deepcopy(reference[k]) for k in ('texCoord', 'extensions') if k in reference}}

    def prepare(self, mat, uses):
        pbr = mat.get('pbrMetallicRoughness', {})
        base = pbr.get('baseColorTexture')
        normal = mat.get('normalTexture')
        occlusion = mat.get('occlusionTexture')
        maps = [info for info in (base, normal, occlusion) if info is not None]
        if not maps:
            return []
        reference = maps[0]
        if any(_uv(info) != _uv(reference) or self.wrap(info) != self.wrap(reference) for info in maps):
            raise GarageError('The detail and colour maps use different UVs or wrapping. Bake them onto one UV map in Blender.')
        uv, _, _, _ = _uv(reference)
        if any('TEXCOORD_'+str(uv) not in prim['attributes'] for prim in uses):
            raise GarageError(f'This image needs UV map {uv}. UV unwrap the affected mesh in Blender.')
        if self.wrap(reference) != (10497, 10497):
            raise GarageError('This texture uses clamp or mirror wrapping. Bake its edges into a repeatable texture in Blender.')
        with self.image(reference) as im:
            old_size = im.size
        oversized = math.prod(old_size) > MAX_SOURCE_PIXELS
        if not normal and not occlusion and not oversized:
            return []
        size = _fit(old_size, 2048 if normal or occlusion else 4096)
        if base:
            with self.image(base) as im:
                # Straight channels retain the original colour under transparent pixels.
                channels = [c.resize(size, Image.Resampling.LANCZOS) for c in im.convert('RGBA').split()]
                colour = Image.merge('RGBA', channels)
        else:
            colour = Image.new('RGBA', size, 'white')
        shade = np.ones((size[1], size[0]), dtype=np.float32)
        changes = []
        if normal:
            with self.image(normal) as im:
                v = np.asarray(im.convert('RGB').resize(size, Image.Resampling.BILINEAR), dtype=np.float32)/127.5-1
            v[:, :, :2] *= float(normal.get('scale', 1.))
            length = np.maximum(np.linalg.norm(v, axis=2), 1.e-6)
            # Relative Lambert response under a side light on a flat tangent-space reference.
            light = (-.52*v[:,:,0] + .4*v[:,:,1] + .76*v[:,:,2]) / (.76*length)
            shade *= .45 + .55*np.clip(light, .25, 1.35)
            changes.append('Baked the normal map into base colour with fixed side lighting.')
        if occlusion:
            with self.image(occlusion) as im:
                ao = np.asarray(im.convert('RGB').getchannel('R').resize(size, Image.Resampling.BILINEAR), dtype=np.float32)/255
            shade *= 1 + float(occlusion.get('strength', 1.))*(ao-1)
            changes.append('Baked ambient occlusion into base colour.')
        if normal or occlusion:
            rgb = np.asarray(colour.convert('RGB'), dtype=np.float32)/255
            linear = np.where(rgb <= .04045, rgb/12.92, ((rgb+.055)/1.055)**2.4) * shade[:,:,None]
            srgb = np.where(linear <= .0031308, 12.92*linear, 1.055*np.maximum(linear, 0)**(1/2.4)-.055)
            baked = Image.fromarray(np.rint(255*np.clip(srgb, 0, 1)).astype(np.uint8))
            baked.putalpha(colour.getchannel('A'))
            colour = baked
        if size != old_size:
            changes.append(f'Resized the colour image from {old_size[0]} × {old_size[1]} to {size[0]} × {size[1]}.')
        mat.setdefault('pbrMetallicRoughness', {})['baseColorTexture'] = self.store(colour, reference, mat.get('name', 'Surface'))
        mat.pop('normalTexture', None)
        mat.pop('occlusionTexture', None)
        return changes

    def pack(self):
        blob = self.glb.buffers[0] + self.added
        # Optional/third-party extensions can reference resources in their own
        # format. Keep those indices intact. A standard Blender GLB can drop
        # the large original images replaced by the preparations.
        known = set(self.doc.get('extensionsUsed', [])) <= COMPACTABLE
        if not self.added or not known:
            return _pack_glb(self.doc, blob)
        def references(value, predicate):
            if isinstance(value, dict):
                for key, item in value.items():
                    if key=='extras':
                        continue
                    if predicate(key, item):
                        yield value, key
                    else:
                        yield from references(item, predicate)
            elif isinstance(value, list):
                for item in value:
                    yield from references(item, predicate)
        def compact(name, pointers):
            pairs = list(pointers)
            indices = sorted({obj[key] for obj,key in pairs})
            mapping = {old:new for new,old in enumerate(indices)}
            previous = self.doc.get(name, [])
            self.doc[name] = [previous[i] for i in indices]
            for obj,key in pairs:
                obj[key] = mapping[obj[key]]
        slots = references(self.doc.get('materials', []), lambda key,val:key.endswith('Texture') and isinstance(val,dict) and 'index' in val)
        compact('textures', ((obj[key],'index') for obj,key in slots))
        compact('images', ((tex,'source') for tex in self.doc['textures']))
        compact('bufferViews', references(self.doc, lambda key,val:key=='bufferView' and type(val) is int))
        packed = bytearray()
        for view in self.doc['bufferViews']:
            start, size = view.get('byteOffset',0), view['byteLength']
            if start<0 or size<0 or start+size>len(blob):
                raise GarageError('A mesh or image refers to incomplete data.')
            packed.extend(bytes(-len(packed)%4))
            view['byteOffset'] = len(packed)
            packed.extend(blob[start:start+size])
        return _pack_glb(self.doc, packed)
