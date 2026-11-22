"""Bounded GLB 2 reader. Asset input is data, never executable code.
Reference: Khronos glTF 2.0 (GLB, accessors, transforms, primitive topology).
External resources, skins and compressed meshes are refused with useful guidance.
"""
from __future__ import annotations
from dataclasses import dataclass, field
import base64, json, math, struct
from pathlib import Path
import numpy as np
from .errors import GarageError, Cancelled
from .util import MAX_MODEL_BYTES, clean_name, sha256

DTYPES={5120:'i1',5121:'u1',5122:'<i2',5123:'<u2',5125:'<u4',5126:'<f4'}
SIZES={'SCALAR':1,'VEC2':2,'VEC3':3,'VEC4':4,'MAT2':4,'MAT3':9,'MAT4':16}
MAX_VERTICES=4_000_000; MAX_TRIANGLES=5_000_000; MAX_PARTS=4096


def _record(items,idx,label):
    """Validate references before Python can interpret -1 or True as an index."""
    if type(idx) is not int or not isinstance(items,list) or not 0<=idx<len(items) or not isinstance(items[idx],dict):
        raise GarageError('Model references an invalid '+label+'.')
    return items[idx]


def _number(value,label,minimum=0.,maximum=1.):
    if isinstance(value,bool) or not isinstance(value,(int,float)) or not math.isfinite(value) or value<minimum or (maximum is not None and value>maximum):
        raise GarageError('Invalid '+label+'.')
    return float(value)

@dataclass
class Part:
    id: str
    name: str
    node: int
    primitive: int
    material: int
    positions: np.ndarray
    normals: np.ndarray
    uv: np.ndarray
    indices: np.ndarray
    extras: dict = field(default_factory=dict)
    warnings: list[str] = field(default_factory=list)
    @property
    def bounds(self):return np.array([self.positions.min(0),self.positions.max(0)])
    @property
    def center(self):return self.bounds.mean(0)
    @property
    def extent(self):return np.ptp(self.bounds,axis=0)

@dataclass
class Scene:
    parts: list[Part]
    materials: list[dict]
    document: dict
    raw: bytes
    digest: str
    warnings: list[str]
    @property
    def bounds(self):
        return np.array([np.min([p.bounds[0] for p in self.parts],axis=0),np.max([p.bounds[1] for p in self.parts],axis=0)])
    @property
    def triangles(self):return sum(len(p.indices) for p in self.parts)


def unit_normals(p, faces):
    n=np.zeros_like(p,dtype=np.float64)
    cross=np.cross(p[faces[:,1]]-p[faces[:,0]], p[faces[:,2]]-p[faces[:,0]])
    for i in range(3):np.add.at(n,faces[:,i],cross)
    # No absolute area threshold: a tiny authored triangle is still a surface.
    size=np.linalg.norm(n,axis=1);valid=size>0
    n[valid]/=size[valid,None];n[~valid]=[0,1,0]
    return n.astype(np.float32)


def trs(node):
    if 'matrix' in node:
        if any(k in node for k in ('translation','rotation','scale')):raise GarageError('A model node contains both matrix and TRS transforms.')
        a=np.asarray(node['matrix'],dtype=np.float64)
        if a.shape!=(16,):raise GarageError('Invalid node matrix.')
        m=a.reshape(4,4).T.copy()
        if not np.allclose(m[3],[0,0,0,1],atol=1e-7):raise GarageError('Perspective node transforms are unsupported.')
    else:
        t=np.asarray(node.get('translation',[0,0,0]),float);q=np.asarray(node.get('rotation',[0,0,0,1]),float);s=np.asarray(node.get('scale',[1,1,1]),float)
        if t.shape!=(3,) or q.shape!=(4,) or s.shape!=(3,) or not np.isfinite(np.r_[t,q,s]).all():raise GarageError('Invalid node transform.')
        ql=np.linalg.norm(q)
        if ql<1e-12:raise GarageError('Zero-length rotation quaternion.')
        x,y,z,w=q/ql
        r=np.array([[1-2*(y*y+z*z),2*(x*y-z*w),2*(x*z+y*w)],[2*(x*y+z*w),1-2*(x*x+z*z),2*(y*z-x*w)],[2*(x*z-y*w),2*(y*z+x*w),1-2*(x*x+y*y)]])
        m=np.eye(4);m[:3,:3]=r@np.diag(s);m[:3,3]=t
    if not np.isfinite(m).all():raise GarageError('Non-finite node transform.')
    return m


class GLB:
    @staticmethod
    def chunks(raw:bytes):
        if not isinstance(raw,bytes) or len(raw)<20:raise GarageError('This is not a complete GLB model.', 'invalid_glb')
        if len(raw)>MAX_MODEL_BYTES:raise GarageError('Model is over 256 MiB. Export a smaller GLB.', 'model_limit')
        magic,version,size=struct.unpack_from('<4sII',raw)
        if magic!=b'glTF' or version!=2 or size!=len(raw):raise GarageError('Expected a complete GLB 2.0 file.', 'invalid_glb')
        chunks=[];off=12
        while off<len(raw):
            if off+8>len(raw):raise GarageError('Truncated GLB chunk.')
            n,kind=struct.unpack_from('<II',raw,off);off+=8
            if n%4 or off+n>len(raw):raise GarageError('Invalid GLB chunk length.')
            chunks.append((kind,raw[off:off+n]));off+=n
        if not chunks or chunks[0][0]!=0x4e4f534a or sum(k==0x4e4f534a for k,b in chunks)!=1:raise GarageError('Missing or duplicate GLB JSON chunk.')
        if len(chunks[0][1])>32*1024**2:raise GarageError('Model metadata exceeds the safe limit.')
        return chunks

    def __init__(self,raw:bytes):
        chunks=self.chunks(raw)
        try:
            self.doc=json.loads(chunks[0][1].decode('utf-8'),parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
        except (ValueError,UnicodeError,RecursionError) as e:raise GarageError('GLB metadata is invalid JSON.') from e
        if not isinstance(self.doc,dict) or not isinstance(self.doc.get('asset'),dict) or self.doc['asset'].get('version')!='2.0':raise GarageError('Expected glTF 2.0 metadata.')
        for name in ('buffers','bufferViews','accessors','images','textures','materials','nodes','meshes','scenes','samplers'):
            rows=self.doc.get(name,[])
            if not isinstance(rows,list) or any(not isinstance(row,dict) for row in rows):raise GarageError('Invalid '+name+' array in model metadata.')
        self.raw=raw;bins=[b for k,b in chunks if k==0x004e4942]
        if len(bins)>1:raise GarageError('GLB has multiple binary chunks.')
        self.buffers=[]
        for i,b in enumerate(self.doc.get('buffers',[])):
            if 'uri' in b:
                raise GarageError('Export one GLB with embedded geometry; external or data-URI buffers are not accepted.', 'external_resource')
            if i or not bins:raise GarageError('GLB binary buffer is missing.')
            n=b.get('byteLength',-1)
            if type(n) is not int or not 0<=n<=len(bins[0]) or len(bins[0])-n>3:raise GarageError('GLB buffer length is inconsistent.')
            self.buffers.append(bins[0][:n])
        required=self.doc.get('extensionsRequired',[])
        if not isinstance(required,list) or any(not isinstance(x,str) for x in required):raise GarageError('Invalid required extension list.')
        supported={'KHR_materials_unlit','KHR_materials_transmission','KHR_materials_ior','KHR_materials_clearcoat','KHR_materials_emissive_strength','KHR_texture_transform','KHR_materials_specular','KHR_materials_sheen','KHR_materials_volume','KHR_materials_iridescence','KHR_materials_anisotropy'}
        unknown=set(required)-supported
        if unknown:raise GarageError('Re-export without mesh/texture compression or unsupported extensions: '+', '.join(sorted(unknown)), 'unsupported_extension')
        for im in self.doc.get('images',[]):
            if 'uri' in im:raise GarageError('This GLB references a separate texture. Use Open file to include nearby textures, or zip the GLB together with its texture folder.', 'external_resource')
            if 'bufferView' not in im:raise GarageError('Embedded texture has no image data.')
            if im.get('mimeType') not in ('image/png','image/jpeg','image/webp'):raise GarageError('Use PNG or JPEG textures in the GLB.', 'unsupported_texture')
            self.view(im['bufferView'])
        if len(self.doc.get('nodes',[]))>10000 or len(self.doc.get('meshes',[]))>MAX_PARTS:raise GarageError('Too many objects in this model.', 'model_limit')

    def view(self, idx):
        v=_record(self.doc.get('bufferViews',[]),idx,'buffer view');bi=v.get('buffer',0)
        if type(bi) is not int or not 0<=bi<len(self.buffers):raise GarageError('Model references a missing buffer.')
        b=self.buffers[bi]
        off=v.get('byteOffset',0);n=v.get('byteLength',-1)
        if type(off) is not int or type(n) is not int or off<0 or n<0 or off+n>len(b):raise GarageError('Buffer view leaves the model data.')
        return memoryview(b)[off:off+n],v

    def accessor(self,idx,kind=None):
        a=_record(self.doc.get('accessors',[]),idx,'attribute')
        if kind and a.get('type')!=kind:raise GarageError('Unexpected attribute component count.')
        if a.get('type') not in SIZES or a.get('componentType') not in DTYPES:raise GarageError('Unsupported attribute format.')
        cols=SIZES[a['type']];dt=np.dtype(DTYPES[a['componentType']]);count=a.get('count',0)
        if isinstance(count,bool) or not isinstance(count,int) or count<1 or count>MAX_VERTICES*3 or count*cols*dt.itemsize>256*1024**2:raise GarageError('Attribute exceeds the geometry budget.', 'model_limit')
        if a['type'].startswith('MAT'):raise GarageError('Matrix attributes are not used by the rigid car importer.')
        if 'bufferView' in a:
            b,v=self.view(a['bufferView']);off=a.get('byteOffset',0);stride=v.get('byteStride',dt.itemsize*cols)
            if type(off) is not int or off<0 or off%dt.itemsize or type(stride) is not int or stride<dt.itemsize*cols or stride%dt.itemsize or stride>252 or off+(count-1)*stride+cols*dt.itemsize>len(b):raise GarageError('Attribute reads outside its buffer view.')
            out=np.ndarray((count,cols),dtype=dt,buffer=b,offset=off,strides=(stride,dt.itemsize)).copy()
        else:out=np.zeros((count,cols),dtype=dt)
        if 'sparse' in a:
            sp=a['sparse']
            if not isinstance(sp,dict) or not isinstance(sp.get('indices'),dict) or not isinstance(sp.get('values'),dict):raise GarageError('Invalid sparse attribute metadata.')
            n=sp.get('count',0)
            if type(n) is not int or not 0<n<=count:raise GarageError('Invalid sparse attribute count.')
            si=sp['indices'];ctype=si.get('componentType')
            if ctype not in (5121,5123,5125):raise GarageError('Invalid sparse index type.')
            ib,iv=self.view(si.get('bufferView'));io=si.get('byteOffset',0);it=np.dtype(DTYPES[ctype]);vb,vv=self.view(sp['values'].get('bufferView'));vo=sp['values'].get('byteOffset',0)
            if type(io) is not int or type(vo) is not int or io<0 or vo<0 or io%it.itemsize or vo%dt.itemsize or io+n*it.itemsize>len(ib) or vo+n*cols*dt.itemsize>len(vb) or 'byteStride' in iv or 'byteStride' in vv:raise GarageError('Sparse data is misaligned, strided or truncated.')
            ids=np.frombuffer(ib,it,n,io).astype(np.int64)
            if ids[-1]>=count or (len(ids)>1 and np.any(np.diff(ids)<=0)):raise GarageError('Sparse indices must be ordered, unique and in range.')
            out[ids]=np.frombuffer(vb,dt,n*cols,vo).reshape(n,cols)
        if a.get('normalized'):
            if a['componentType'] in (5120,5122):out=np.maximum(out.astype(np.float64)/(127 if dt.itemsize==1 else 32767),-1)
            elif a['componentType'] in (5121,5123):out=out.astype(np.float64)/(255 if dt.itemsize==1 else 65535)
            else:raise GarageError('Invalid normalized attribute type.')
        if not np.isfinite(out).all():raise GarageError('Model contains non-finite geometry.')
        return out

    def scene(self,cancel=None):
        parts=[];warnings=[];doc=self.doc;nodes=doc.get('nodes',[]);meshes=doc.get('meshes',[])
        if not nodes or not meshes:raise GarageError('The GLB contains no car geometry.', 'empty_model')
        mats=[]
        for i,m in enumerate(doc.get('materials',[])):
            p=m.get('pbrMetallicRoughness',{});ext=m.get('extensions',{})
            if not isinstance(p,dict) or not isinstance(ext,dict) or not isinstance(p.get('baseColorTexture',{}),dict):raise GarageError('Invalid material metadata.')
            color=p.get('baseColorFactor',[1,1,1,1])
            if not isinstance(color,list) or len(color)!=4:raise GarageError('Invalid material colour.')
            color=[_number(x,'material colour') for x in color]
            tex=p.get('baseColorTexture',{}).get('index');em=m.get('emissiveFactor',[0,0,0])
            if tex is not None:
                texture=_record(doc.get('textures',[]),tex,'base colour texture')
                if 'source' not in texture:raise GarageError('Use an uncompressed PNG/JPEG base colour texture.','unsupported_texture')
                _record(doc.get('images',[]),texture['source'],'base colour image')
            if not isinstance(em,list) or len(em)!=3:raise GarageError('Invalid material emission.')
            em=[_number(x,'material emission',maximum=None) for x in em]
            alpha=m.get('alphaMode','OPAQUE')
            if alpha not in ('OPAQUE','MASK','BLEND'):raise GarageError('Invalid material alpha mode.')
            # glTF OPAQUE explicitly ignores both factor and texture alpha. Keep
            # the authored value in document, but expose effective opacity to
            # component recognition and native material selection.
            if alpha=='OPAQUE':color[3]=1.
            cutoff=_number(m.get('alphaCutoff',.5),'alpha cutoff',maximum=None)
            mats.append(dict(id=i,name=clean_name(m.get('name',f'Material {i+1}')),color=color,metallic=_number(p.get('metallicFactor',1),'metallic factor'),roughness=_number(p.get('roughnessFactor',1),'roughness factor'),alpha_mode=alpha,alpha_cutoff=cutoff,double_sided=bool(m.get('doubleSided',False)),transmission=_number(ext.get('KHR_materials_transmission',{}).get('transmissionFactor',0),'transmission factor'),emissive=em,base_texture=tex,normal_map='normalTexture' in m))
        mats.append(dict(id=len(mats),name='Default',color=[.6,.6,.6,1],metallic=0,roughness=.6,alpha_mode='OPAQUE',alpha_cutoff=.5,double_sided=False,transmission=0,emissive=[0,0,0],base_texture=None,normal_map=False));default=len(mats)-1
        parents={}
        for i,n in enumerate(nodes):
            children=n.get('children',[])
            if not isinstance(children,list):raise GarageError('Invalid child-node list.')
            for c in children:
                if type(c) is not int or c<0 or c>=len(nodes) or c in parents:raise GarageError('Invalid or multiply-parented scene node.')
                parents[c]=i
        scenes=doc.get('scenes',[])
        if scenes:
            si=doc.get('scene',0)
            if type(si) is not int or not 0<=si<len(scenes):raise GarageError('Invalid default scene.')
            roots=scenes[si].get('nodes',[])
        else:roots=[i for i in range(len(nodes)) if i not in parents]
        if not isinstance(roots,list) or any(type(i) is not int or not 0<=i<len(nodes) or i in parents for i in roots):raise GarageError('Scene roots must reference parentless nodes.')
        if not roots:raise GarageError('The model has no scene roots (possibly a node cycle).')
        visited=set();total_v=0;total_t=0
        stack=[(i,np.eye(4),0) for i in reversed(roots)]
        while stack:
            if cancel and cancel.is_set():raise Cancelled()
            ni,parent,depth=stack.pop()
            if type(ni) is not int or not 0<=ni<len(nodes) or ni in visited or depth>128:raise GarageError('Invalid, cyclic or excessively deep scene hierarchy.')
            visited.add(ni);node=nodes[ni];world=parent@trs(node)
            for child in reversed(node.get('children',[])):stack.append((child,world,depth+1))
            if 'mesh' not in node:continue
            if 'skin' in node:raise GarageError('Apply/bake the skin before importing. This workflow expects rigid car parts.', 'skinned_mesh')
            mi=node['mesh']
            if type(mi) is not int or not 0<=mi<len(meshes):raise GarageError('Missing mesh reference.')
            for pi,prim in enumerate(meshes[mi].get('primitives',[])):
                if len(parts)>=MAX_PARTS:raise GarageError('Too many parts.', 'model_limit')
                if prim.get('mode',4) not in (4,5,6):raise GarageError('Use triangle meshes, not lines or point clouds.', 'topology')
                if prim.get('targets'):warnings.append('Morph targets are not animated; base geometry is used.')
                if any(x in prim.get('extensions',{}) for x in ('KHR_draco_mesh_compression','EXT_meshopt_compression')):raise GarageError('Re-export without Draco/meshopt compression.', 'compressed_mesh')
                at=prim.get('attributes',{})
                if 'POSITION' not in at:raise GarageError('A part has no position attribute.')
                if _record(doc.get('accessors',[]),at['POSITION'],'position attribute').get('componentType')!=5126:raise GarageError('Export unquantized float positions.', 'unsupported_attribute')
                pos=self.accessor(at['POSITION'],'VEC3').astype(np.float64);nv=len(pos)
                if 'indices' in prim:
                    ac=_record(doc.get('accessors',[]),prim['indices'],'index attribute')
                    if ac.get('componentType') not in (5121,5123,5125) or ac.get('normalized'):raise GarageError('Invalid mesh index type.')
                    ids=self.accessor(prim['indices'],'SCALAR').reshape(-1).astype(np.int64)
                else:ids=np.arange(nv,dtype=np.int64)
                if np.any(ids<0) or np.any(ids>=nv):raise GarageError('A triangle references a missing vertex.')
                mode=prim.get('mode',4)
                if mode==4:
                    if len(ids)%3:raise GarageError('Triangle index count is not a multiple of three.')
                    faces=ids.reshape(-1,3).copy()
                elif mode==5:
                    faces=np.array([ids[i:i+3] if i%2==0 else ids[[i+1,i,i+2]] for i in range(len(ids)-2)],np.int64)
                else:faces=np.array([[ids[0],ids[i],ids[i+1]] for i in range(1,len(ids)-1)],np.int64)
                if faces.ndim!=2 or len(faces)==0:raise GarageError('Part has no triangles.')
                linear=world[:3,:3];sign,logdet=np.linalg.slogdet(linear)
                # A determinant is cubic in scale. A perfectly usable small
                # model must not be rejected by a fixed absolute threshold.
                if sign==0 or not math.isfinite(logdet):raise GarageError('A mesh has a zero scale. Correct its transform before importing.')
                pos=pos@linear.T+world[:3,3]
                if not np.isfinite(pos).all() or np.abs(pos).max()>1e7:raise GarageError('Car geometry has invalid or excessively large coordinates.')
                if sign<0:faces[:,[1,2]]=faces[:,[2,1]]
                pw=[]
                mat=prim.get('material',default)
                if type(mat) is not int or not 0<=mat<len(mats) or ('material' in prim and mat==default):raise GarageError('Missing material reference.')
                base=doc['materials'][mat].get('pbrMetallicRoughness',{}).get('baseColorTexture',{}) if mat!=default else {}
                extension=base.get('extensions',{})
                if not isinstance(extension,dict):raise GarageError('Invalid texture transform metadata.')
                transform=extension.get('KHR_texture_transform',{})
                if not isinstance(transform,dict):raise GarageError('Invalid texture transform metadata.')
                texcoord=transform.get('texCoord',base.get('texCoord',0))
                if type(texcoord) is not int or texcoord<0:raise GarageError('Invalid texture-coordinate set.')
                uvkey='TEXCOORD_'+str(texcoord)
                if uvkey in at:
                    uv=self.accessor(at[uvkey],'VEC2').astype(np.float32)
                    if len(uv)!=nv:raise GarageError('UV count does not match the vertex count.')
                else:
                    if mats[mat]['base_texture'] is not None:raise GarageError('A textured part is missing '+uvkey+'. Export its referenced UV coordinates with the model.','missing_uv')
                    uv=np.zeros((nv,2),np.float32);pw.append('No UV map. Flat-colour materials only.')
                if transform:
                    offset=transform.get('offset',[0.,0.]);scale=transform.get('scale',[1.,1.]);angle=transform.get('rotation',0.)
                    if not isinstance(offset,list) or not isinstance(scale,list) or len(offset)!=2 or len(scale)!=2 or any(isinstance(x,bool) or not isinstance(x,(int,float)) or not math.isfinite(x) for x in [*offset,*scale,angle]):raise GarageError('Invalid texture transform values.')
                    cs=math.cos(angle);sn=math.sin(angle)
                    uv=((uv.astype(np.float64)*scale)@np.array([[cs,sn],[-sn,cs]])+offset).astype(np.float32)
                    if not np.isfinite(uv).all():raise GarageError('Texture transform creates non-finite UV coordinates.')
                    pw.append('Base-colour texture transform baked into prepared UVs; native shader compatibility still requires its profile.')
                if 'NORMAL' in at:
                    norm=self.accessor(at['NORMAL'],'VEC3').astype(np.float64)
                    if len(norm)!=nv:raise GarageError('Normal count does not match the vertex count.')
                    norm=norm@np.linalg.inv(linear);nl=np.linalg.norm(norm,axis=1);bad=nl<1e-15
                    norm[~bad]/=nl[~bad,None]
                    if np.any(bad):norm[bad]=unit_normals(pos,faces)[bad];pw.append('Repaired zero-length normals.')
                else:
                    # glTF requires flat face normals when NORMAL is absent.
                    # Split corners rather than welding/smoothing across an
                    # authored edge; all source triangles and UVs survive.
                    corners=faces.reshape(-1)
                    if total_v+len(corners)>MAX_VERTICES:raise GarageError('Flat-normal expansion exceeds the safe vertex budget. Export authored normals or a smaller model.','model_limit')
                    pos=pos[corners];uv=uv[corners];nv=len(pos)
                    faces=np.arange(nv,dtype=np.int64).reshape(-1,3)
                    norm=unit_normals(pos,faces);pw.append('Generated missing flat vertex normals; source faces and UV seams retained.')
                e={}
                if isinstance(node.get('extras'),dict):e.update(node['extras'])
                if isinstance(prim.get('extras'),dict):e.update(prim['extras'])
                total_v+=nv;total_t+=len(faces)
                if total_v>MAX_VERTICES or total_t>MAX_TRIANGLES:raise GarageError('Model exceeds the safe import geometry budget.', 'model_limit')
                parts.append(Part(f'n{ni}p{pi}',clean_name(node.get('name',meshes[mi].get('name',f'Part {mi+1}'))),ni,pi,mat,pos.astype(np.float32),norm.astype(np.float32),uv,faces.astype(np.uint32),e,pw))
        if not parts:raise GarageError('Selected scene contains no meshes.')
        if doc.get('animations'):warnings.append('Animation is not imported; rigid base pose is used.')
        if any(m['normal_map'] for m in mats):warnings.append('Normal maps can be previewed from source, but transfer requires a native material recipe.')
        scene=Scene(parts,mats,doc,self.raw,sha256(self.raw),sorted(set(warnings)))
        from .f50_wheel_adapter import adapt_source
        return adapt_source(scene)
