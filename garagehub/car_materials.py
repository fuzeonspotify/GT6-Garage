"""Source-material recipes for the hash-pinned Supra-derived native shader set."""
from __future__ import annotations
import io,math
import numpy as np
from PIL import Image,ImageOps
from .glb import GLB
from .errors import GarageError

MAX_SOURCE_PIXELS = 16_777_216


def srgb(v):
    a=np.clip(np.asarray(v,dtype=float),0,1)
    return np.round(np.where(a<=.0031308,a*12.92,1.055*a**(1/2.4)-.055)*255).astype(np.uint8)


class Materials:
    def __init__(self,scene,editor):
        self.scene=scene;self.editor=editor;self.cache={};self.gltf=None;self.notes=[];self.semantic_cache={};self.flat_palette=None

    def opaque_palette(self,source_id):
        # Uniform factors have no authored UV-dependent detail. Colour cells
        # are 4 px wide so constant cell-centre samples cannot bleed.
        if self.flat_palette is None:
            ids=[m['id'] for m in self.scene.materials if m['base_texture'] is None and m['metallic']<.4]
            if not ids or len(ids)>256:raise GarageError('Too many uniform source colours for this profile palette.')
            side=1
            while side*side<len(ids):side*=2
            image=Image.new('RGBA',(side*4,side*4),(0,0,0,255));uv={}
            for i,sid in enumerate(ids):
                rgb=tuple(srgb(self.scene.materials[sid]['color'][:3]).tolist());x=i%side;y=i//side
                image.paste((*rgb,255),(x*4,y*4,(x+1)*4,(y+1)*4));uv[sid]=((x+.5)/side,(y+.5)/side)
            iid=self.editor.image(image,'uniform_opaque_source_palette');bindings={name:iid for name,_ in self.editor.texture_keys(1)}
            signature=(1,tuple(sorted(bindings.items())))
            if signature not in self.semantic_cache:
                slot=1+len(self.semantic_cache)
                if slot>25:raise GarageError('Native material slot budget exceeded.')
                self.semantic_cache[signature]=self.editor.clone_material(1,'uniform_opaque_palette',bindings,replace=slot)
            self.flat_palette=(self.semantic_cache[signature],uv)
        mid,uv=self.flat_palette
        return {'material':mid,'fvf':self.editor.fvf(1),'transparent':False,'variation':False,'constant_uv':uv[source_id]}

    def embedded_image(self, texture):
        if self.gltf is None:self.gltf=GLB(self.scene.raw)
        doc=self.scene.document; tex=doc['textures'][texture]
        if 'source' not in tex:raise GarageError('Compressed texture-only sources are unsupported by this profile.')
        imdef=doc['images'][tex['source']]
        if 'bufferView' not in imdef:raise GarageError('Embed PNG/JPEG image data in the GLB before native conversion.')
        data,_=self.gltf.view(imdef['bufferView'])
        with Image.open(io.BytesIO(data)) as im:
            if im.width*im.height>MAX_SOURCE_PIXELS:raise GarageError('Source texture exceeds the 16 MP decode budget.')
            return im.convert('RGBA')

    def source_image(self,mat):
        color=mat['color'];texture=mat['base_texture']
        if texture is None:return Image.new('RGBA',(4,4),tuple(srgb(color[:3]).tolist())+(int(round(color[3]*255)),))
        image = self.embedded_image(texture)
        image.thumbnail((512,512),Image.Resampling.LANCZOS)
        # glTF uses factor multiplication in linear colour, not in sRGB bytes.
        pixels=np.asarray(image,dtype=float)/255;rgb=np.where(pixels[:,:,:3]<=.04045,pixels[:,:,:3]/12.92,((pixels[:,:,:3]+.055)/1.055)**2.4)
        pixels[:,:,:3]=srgb(rgb*np.asarray(color[:3]))/255;pixels[:,:,3]*=color[3];image=Image.fromarray(np.round(pixels*255).astype(np.uint8))
        w=max(4,2**round(math.log2(image.width)));h=max(4,2**round(math.log2(image.height)));image=image.resize((w,h),Image.Resampling.LANCZOS)
        # GLB.scene selects baseColorTexture's coordinate set (including the
        # KHR_texture_transform texCoord override) and bakes scale, rotation,
        # then offset into Part.uv. Native preparation/batching retains those
        # coordinates. Transforming image pixels or UVs again here would apply
        # the authored transform twice; non-UV0 sets need no separate shader.
        # This recipe still provides only native repeat wrapping.
        doc = self.scene.document
        tex = doc['textures'][texture]
        sampler=doc.get('samplers',[])[tex['sampler']] if 'sampler' in tex else {}
        if sampler.get('wrapS',10497)!=10497 or sampler.get('wrapT',10497)!=10497:raise GarageError('This profile currently requires repeat-wrapped textures. Bake clamp/mirror edges before conversion.')
        return image

    def get(self,source_id,role):
        key=(source_id,role)
        if key in self.cache:return self.cache[key]
        mat=self.scene.materials[source_id];ed=self.editor
        if mat['alpha_mode']=='MASK':raise GarageError('Alpha-cutout materials need a reviewed native cutout shader; conversion to glass is not equivalent.')
        if role in ('body','interior') and mat['base_texture'] is None and mat['metallic']<.4:
            result=self.opaque_palette(source_id);self.cache[key]=result;return result
        source=self.source_image(mat)
        if role=='paint':
            if mat['base_texture'] is not None:raise GarageError('Textured paint/liveries require a paint-texture recipe. Assign unpainted textured surfaces as Body detail or choose a plain paint material.')
            result={'material':0,'fvf':ed.fvf(0),'template':0,'transparent':False,'constant_uv':(.5078125,.5078125),'variation':True}
        else:
            glass=role=='glass' or mat['alpha_mode']=='BLEND' or mat['transmission']>.1
            metal=not glass and mat['metallic']>.3
            template=5 if glass else 2 if metal else 1
            names=dict(ed.texture_keys(template));bindings={};uv=None
            if glass:
                # Separate reflectivity/response from transparency. This is an
                # authored approximation of the native optical family.
                rgb=srgb(mat['color'][:3]).astype(int);colored=int(rgb.max())-int(rgb.min())>55
                if mat['base_texture'] is None:
                    tint=tuple(int(v) for v in (rgb*.65 if colored else np.array([18,26,24])))
                    alpha=int(np.clip(mat['color'][3]*255,12,210)) if colored else 34
                    source=Image.new('RGBA',(4,4),(*tint,100 if colored else 128));opacity=Image.new('RGBA',(4,4),(0,255,0,alpha));uv=(.5,.5)
                else:
                    opacity=Image.new('RGBA',source.size,(0,255,0,0));opacity.putalpha(source.getchannel('A'));source.putalpha(100)
                diffuse=ed.image(source,f'glass_{source_id}_response');mask=ed.image(opacity,f'glass_{source_id}_opacity')
                diffpid=names['diffuseMapSampler'];alphapid=names['alphaMapSampler']
                if diffpid==alphapid:raise GarageError('Optical template does not separate opacity and response.')
                for n,pid in names.items():
                    if pid==diffpid:bindings[n]=diffuse
                    elif pid==alphapid:bindings[n]=mask
            else:
                source.putalpha(69 if metal else 255);iid=ed.image(source,f'material_{source_id}');diffpid=names['diffuseMapSampler'];bindings={n:iid for n,pid in names.items() if pid==diffpid}
                if mat['base_texture'] is None:uv=(.5,.5)
            signature=(template,tuple(sorted(bindings.items())))
            if signature not in self.semantic_cache:
                slot=1+len(self.semantic_cache)
                if slot>25:raise GarageError('This native profile supports 25 distinct body-detail/glass recipes plus paint. Combine identical materials or choose a larger reviewed profile.')
                self.semantic_cache[signature]=ed.clone_material(template,f'{role}_{source_id}',bindings,replace=slot)
            mid=self.semantic_cache[signature]
            result={'material':mid,'fvf':ed.fvf(template),'template':template,'transparent':glass,'constant_uv':uv,'variation':False}
            if mat['normal_map']:self.notes.append(f'Material {source_id}: normal-map texture is not transferred; authored geometric normals retained.')
            if mat['double_sided']:self.notes.append(f'Material {source_id}: native template culling retained; double-sided equivalence is not claimed.')
        self.cache[key]=result;return result
