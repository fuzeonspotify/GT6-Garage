"""Owned-template MDL3 metadata editor. Never accesses an installed game.

Keeps original GPU resource bytes and moves their typed descriptors. New mutable
FVF field arrays have one owner; strings and read-only resources may be shared.
Based on the project's v029 repair and Nenkai's documented ModelSet3 layouts.
"""
from __future__ import annotations
import struct, io
import numpy as np
from PIL import Image
from .native import MDL3, corners
from .errors import GarageError
from .util import sha256


def need(value, message):
    if not value: raise GarageError(message, 'native_contract')


class Editor:
    def __init__(self, raw: bytes, *, texture_edge=None):
        self.texture_edge = texture_edge
        self.m=MDL3(raw); self.m.validate(); self.raw=raw; self.b=bytearray(raw[:self.m.cut]); self.tail=bytearray(raw[self.m.cut:])
        m=self.m; self.sh=m.u32(0x4c); self.tx=m.u32(0x48); self.ms=m.u32(0x44)
        self.mirrors=[0x6c,self.sh+0x28,self.tx+0x20]; pm=m.u32(0xd0)
        if pm and m.u32(pm+12)==m.cut: self.mirrors.append(pm+12)
        need(all(m.u32(x)==m.cut for x in self.mirrors),'Template metadata extents disagree.')
        self.image_ptr=self.tx+m.u32(self.tx+28)-m.u32(self.tx+8); self.image_count=m.u16(self.tx+22)
        self.image_rows=[bytearray(m.span(self.image_ptr+i*32,32,m.cut)) for i in range(self.image_count)]
        self.param_ptr=m.u32(self.ms+20); self.param_count=m.u16(self.ms+6)
        self.param_rows=[bytearray(m.span(self.param_ptr+i*68,68,m.cut)) for i in range(self.param_count)]
        self.material_ptr=m.u32(self.ms+8); self.material_count=m.u16(self.ms); self.banks=raw[self.ms+4]
        need(self.banks in (1,2,7) and self.material_count%self.banks==0,'Unsupported material bank structure.')
        self.bank_size=self.material_count//self.banks; self.materials=[[] for _ in range(self.banks)]; self.new_materials=[]
        self.key_ptr=m.u32(0x90); self.key_count=m.u16(0x8e); self.keys=bytearray(m.span(self.key_ptr,self.key_count*8,m.cut))
        self.images=[]; self.image_cache={}; self.sampler_cache={}; self.string_cache={}; self.reused_images=[]
        # The template already contains source-ready inline BC images. Hash
        # their actual BC blocks, not padded allocation bytes. Identical source
        # images must not consume the fixed native GPU arena a second time.
        for iid,row in enumerate(self.image_rows):
            ptr=m.u32(self.image_ptr+32*iid);width=m.u16(self.image_ptr+32*iid+12);height=m.u16(self.image_ptr+32*iid+14)
            if ptr>=m.cut and row[8]==0 and row[9] in(134,136) and row[10]==1 and row[11]==1 and width and height:
                count=((width+3)//4)*((height+3)//4)*(8 if row[9]==134 else 16)
                need(count<=m.u32(self.image_ptr+32*iid+4) and ptr+count<=len(raw),'Inline texture block size leaves native allocation.')
                self.image_cache.setdefault((sha256(raw[ptr:ptr+count]),width,height,'DXT1' if row[9]==134 else 'DXT5'),iid)
        for pid,row in enumerate(self.param_rows):self.sampler_cache.setdefault(bytes(row[:64]),pid)
        fvfp=m.u32(0x40); self.fvf_rows=[bytearray(m.span(fvfp+120*i,120,m.cut)) for i in range(m.u16(0x18))]
        self.fvf_keys={(r['name'],r['key'],r['stride']):r['id'] for r in m.fvfs()}
        self.shape_rows=[bytearray(m.span(m.u32(0x38)+48*i,48,m.cut)) for i in range(m.u16(0x14))]
        self.new_shapes=[]; self.stream_shapes=[]; self.resources=[]; self.model_changes=[]; self.finished=False

    def alloc(self, data, alignment=16):
        need(not self.finished,'Editor already finalized.')
        self.b.extend(bytes(-len(self.b)%alignment)); p=len(self.b); self.b.extend(data)
        need(len(self.b)<32*1024**2,'Native metadata exceeds the 32 MiB authoring gate.')
        return p

    def put(self,p,data):
        need(0<=p<=len(self.b)-len(data),'Metadata write leaves the allocated region.'); self.b[p:p+len(data)]=data
    def u32(self,p,value):self.put(p,struct.pack('>I',value))
    def u16(self,p,value):self.put(p,struct.pack('>H',value))
    def string(self,text):
        if text not in self.string_cache:self.string_cache[text]=self.alloc(text.encode('utf-8')+b'\0',1)
        return self.string_cache[text]

    def shader_key(self,material):
        m=self.m; need(0<=material<self.bank_size,'Unknown template material.')
        at=self.material_ptr+material*52; definition=m.u16(at+4); md=m.u32(self.ms+12)+definition*40; reference=m.u32(md+20)
        return m.string(m.u32(reference)),m.u32(reference+4)

    def texture_keys(self,material,bank=0):
        m=self.m
        need(0<=material<self.bank_size and 0<=bank<self.banks,'Invalid native material/bank index.')
        at=self.material_ptr+(bank*self.bank_size+material)*52; p=m.u32(at+12); n=m.u16(at+10)
        # Genuine donor wheel materials can have NO texture keys and a null
        # pointer. This is valid; it is not the same as an out-of-bounds key.
        if n==0:
            need(p==0 or self.key_ptr<=p<=self.key_ptr+self.key_count*8,'Empty material keys use an invalid pointer.')
            return []
        need(self.key_ptr<=p<=self.key_ptr+self.key_count*8-n*8,'Material keys leave aggregate table.')
        return [(m.string(m.u32(p+8*i)),m.u32(p+8*i+4)) for i in range(n)]

    def image(self,image:Image.Image,label:str):
        im=image.convert('RGBA'); w,h=im.size
        if self.texture_edge and max(w,h)>self.texture_edge:
            scale=max(w,h)/self.texture_edge
            w,h=max(4,round(w/scale)),max(4,round(h/scale))
            im=im.resize((w,h),Image.Resampling.LANCZOS)
        need(w>=4 and h>=4 and w<=1024 and h<=1024 and w&(w-1)==h&(h-1)==0,'Texture dimensions must be powers of two, 4..1024.')
        fmt='DXT1' if im.getchannel('A').getextrema()==(255,255) else 'DXT5'; out=io.BytesIO(); im.save(out,format='DDS',pixel_format=fmt)
        payload=out.getvalue(); need(payload[:4]==b'DDS ' and payload[84:88]==fmt.encode(),'Unexpected texture encoder output.'); payload=payload[128:]
        key=(sha256(payload),w,h,fmt)
        if key in self.image_cache:
            iid=self.image_cache[key]
            if iid<self.image_count:self.reused_images.append({'id':iid,'source_label':label,'bytes':len(payload),'sha256':key[0]})
            return iid
        row=next((r.copy() for r in self.image_rows if r[8]==0 and r[9] in (134,136) and r[11]==1),None)
        need(row is not None,'Template lacks a reviewed local texture descriptor.')
        self.tail.extend(bytes(-len(self.tail)%128)); off=len(self.tail); allocation=(len(payload)+127)&~127
        self.tail.extend(payload+bytes(allocation-len(payload))); iid=len(self.image_rows)
        struct.pack_into('>II',row,0,0,allocation); row[9:12]=bytes((134 if fmt=='DXT1' else 136,1,1)); struct.pack_into('>HH',row,12,w,h)
        self.image_rows.append(row); item=dict(id=iid,tail_offset=off,bytes=len(payload),sha256=sha256(payload),width=w,height=h,format=row[9],mips=1,label=label)
        self.images.append(item); self.image_cache[key]=iid; return iid

    def sampler(self,template_id,image_id,gamma=7):
        need(0<=template_id<self.param_count,'Invalid native sampler template.')
        if image_id<self.image_count:
            row=self.image_rows[image_id];image={'mips':row[10],'format':row[9],'width':struct.unpack_from('>H',row,12)[0],'height':struct.unpack_from('>H',row,14)[0]}
        else:image=next(x for x in self.images if x['id']==image_id)
        r=self.param_rows[template_id].copy(); need(struct.unpack_from('>I',r,4)[0]==0,'Template sampler has an unexpected runtime offset.')
        r[9:12]=bytes((image['mips'],image['format'],(r[11]&~3)|1))
        address=struct.unpack_from('>I',r,12)[0]; address=(address&~(0xf0f|(15<<20)))|1|(1<<8)|(gamma<<20)
        struct.pack_into('>IHH',r,12,address,struct.unpack_from('>H',r,16)[0],struct.unpack_from('>H',r,18)[0])
        struct.pack_into('>HH',r,28,image['width'],image['height']); struct.pack_into('>I',r,56,image_id)
        signature=bytes(r[:64])
        if signature in self.sampler_cache:return self.sampler_cache[signature]
        pid=len(self.param_rows); struct.pack_into('>I',r,64,self.string(f'Garage/{image_id}/{pid}.dds')); self.param_rows.append(r); self.sampler_cache[signature]=pid; return pid

    def clone_material(self,template_id,label,bindings,replace=None):
        """bindings maps native sampler names to new image IDs; aliased roles stay coherent."""
        new_id=(self.bank_size+sum(not x.get('replacement') for x in self.new_materials)) if replace is None else replace
        need(0<=new_id<32767 and not any(x['id']==new_id for x in self.new_materials),'Material index overflow/collision.')
        if replace is not None:need(1<=replace<=25 and self.banks==7,'Only reviewed non-mechanical body material slots may be replaced.')
        for bank in range(self.banks):
            oldkeys=self.texture_keys(template_id,bank); names=dict(oldkeys); need(set(bindings)<=names.keys(),'Unknown sampler binding.')
            # Any aliases of a shared native sampler must be rebound together.
            for pid in {names[n] for n in bindings}:
                aliases={n for n,p in oldkeys if p==pid}; touched=aliases&bindings.keys()
                need(touched==aliases and len({bindings[n] for n in aliases})==1,'Incomplete or conflicting aliased sampler mapping.')
            start=len(self.keys)
            for name,pid in oldkeys:
                target=self.sampler(pid,bindings[name]) if name in bindings else pid
                self.keys.extend(struct.pack('>II',self.string(name),target))
            at=self.material_ptr+(bank*self.bank_size+template_id)*52; row=bytearray(self.m.span(at,52)); struct.pack_into('>I',row,0,self.string('Garage/'+label)); struct.pack_into('>I',row,12,start)
            self.materials[bank].append((new_id,row))
        self.new_materials.append(dict(id=new_id,template=template_id,label=label,shader_key=self.shader_key(template_id),replacement=replace is not None)); return new_id

    def fvf(self,template_material,stride=24):
        key=(*self.shader_key(template_material),stride)
        if key in self.fvf_keys:return self.fvf_keys[key]
        fields={'position':(0,3,2,0),'normal':(12,1,6,0),'map12':(16,2,2,0)}
        if stride==28:fields['colorSet1']=(24,4,4,0)
        need(stride in(24,28),'Unsupported vertex stride.')
        row=self.fvf_rows[0].copy(); need(not any(row[12:24]+row[26:116]),'Unreviewed native FVF extension.')
        fp=self.alloc(b''.join(struct.pack('>I4B',self.string(n),*fields[n]) for n in sorted(fields)))
        ap=self.alloc(self.m.span(self.m.u32(self.m.u32(0x40)+116),16))
        struct.pack_into('>III',row,0,self.string(key[0]),key[1],fp); row[24:26]=bytes((len(fields),stride)); struct.pack_into('>I',row,116,ap)
        i=len(self.fvf_rows); self.fvf_rows.append(row); self.fvf_keys[key]=i; return i

    def shape(self,vb,ib,points,fvf,material,label,transparent=False,variation=False,replace=None,streamed=False):
        stride=self.fvf_rows[fvf][25]; nv=len(vb)//stride; ni=len(ib)//2; nt=ni//3
        need(len(vb)==nv*stride and ni%3==0 and 0<nv<=65535 and 0<nt<=20000,'Mesh exceeds the per-draw budget.')
        need(np.isfinite(points).all() and len(points)==nv,'Invalid geometry position payload.')
        ids=np.frombuffer(ib,dtype='>u2');need(ids.max()<nv,'Mesh index outside its vertex buffer.')
        if streamed:
            need(replace is None or 0<=replace<=32,'Streamed replacement is limited to the reviewed static body slots.')
            vp=ip=bp=0
        else:vp=self.alloc(vb,128);ip=self.alloc(ib,128);bp=self.alloc(corners([points.min(0),points.max(0)]))
        row=bytearray(48)
        flags=(0x3809 if transparent else 0x1809)|(0x10 if variation else 0)
        struct.pack_into('>Hhh',row,0,flags,fvf,material);struct.pack_into('>II',row,8,nv,vp);struct.pack_into('>II',row,20,ni,ip);struct.pack_into('>HHII',row,36,0,nt,bp,0)
        sid=len(self.shape_rows) if replace is None else replace;need(0<=sid<256,'Profile exceeds the byte-sized draw-call shape index.')
        if streamed:self.stream_shapes.append({'id':sid,'vertex':bytes(vb),'index':bytes(ib),'bounds':corners([points.min(0),points.max(0)])})
        if replace is None:self.shape_rows.append(row)
        else:self.shape_rows[sid]=row
        self.new_shapes.append(dict(id=sid,name=label,vertices=nv,triangles=nt,material=material,fvf=fvf,vertex_sha256=sha256(vb),index_sha256=sha256(ib),replaced=replace is not None,streamed=streamed));return sid

    def model(self,mid,program,materials,points):
        m=self.m;need(0<=mid<m.u16(0x10),'Unknown model.');mp=m.u32(0x30)+mid*48
        cp=self.alloc(program);self.u32(mp+24,cp);self.u32(mp+28,len(program))
        q=m.u32(0xb0)+mid*64;oldn=m.u16(q+48);oldp=m.u32(q);ids=sorted(set(struct.unpack('>'+'H'*oldn,m.span(oldp,2*oldn)))|set(materials))
        self.u32(q,self.alloc(struct.pack('>'+'H'*len(ids),*ids)));self.u16(q+48,len(ids));self.bound(mid,points)
        self.model_changes.append(dict(model=mid,program_sha256=sha256(program),program_bytes=len(program),materials=ids))

    def bound(self,mid,points):
        m=self.m;mp=m.u32(0x30)+48*mid;need(m.u16(mp+18)==8,'Expected eight native model bounding corners.')
        old=np.frombuffer(m.span(m.u32(mp+20),96),dtype='>f4').reshape(8,3);p=np.vstack([old,points]);lo,hi=p.min(0),p.max(0)
        self.u32(mp+20,self.alloc(corners([lo,hi])));origin=np.asarray(struct.unpack_from('>3f',self.raw,mp+4))
        radius=max(np.linalg.norm(np.array([x,y,z])-origin) for x in(lo[0],hi[0]) for y in(lo[1],hi[1]) for z in(lo[2],hi[2]));self.put(mp,struct.pack('>f',float(np.nextafter(np.float32(radius),np.float32(np.inf)))))

    def finish(self):
        m=self.m;old=self.raw
        if self.new_materials:
            kp=self.alloc(self.keys);md=m.u32(self.ms+12);refs=m.u32(0x88);count=m.u16(self.ms+2)
            need(count==m.u16(0x2c),'Native shader-reference count differs.')
            for i in range(count):
                p=md+40*i;n=m.s16(p+10);op=m.u32(p+16);rp=m.u32(p+20)
                need(rp==refs+40*i and m.u32(rp+28)==op and m.u32(rp+24)==n>=0,'Native material-key pair differs.')
                if n:
                    need(self.key_ptr<=op<=self.key_ptr+8*self.key_count-8*n,'Invalid paired key range.')
                    self.u32(p+16,kp+op-self.key_ptr);self.u32(rp+28,kp+op-self.key_ptr)
            rows=[]
            for bank in range(self.banks):
                replacements={i:r for i,r in self.materials[bank] if i<self.bank_size}
                for i in range(self.bank_size):
                    at=self.material_ptr+(bank*self.bank_size+i)*52;r=bytearray(m.span(at,52));n=m.u16(at+10);op=m.u32(at+12)
                    if n:
                        need(self.key_ptr<=op<=self.key_ptr+self.key_count*8-n*8,'Original material key range invalid.')
                        struct.pack_into('>I',r,12,kp+op-self.key_ptr)
                    if i in replacements:
                        r=replacements[i].copy();struct.pack_into('>I',r,12,kp+struct.unpack_from('>I',r,12)[0])
                    rows.append(r)
                for mid,r in self.materials[bank]:
                    if mid<self.bank_size:continue
                    r=r.copy();struct.pack_into('>I',r,12,kp+struct.unpack_from('>I',r,12)[0]);rows.append(r)
            self.u32(self.ms+8,self.alloc(b''.join(rows)));self.u16(self.ms,len(rows));self.u32(0x90,kp);self.u16(0x8e,len(self.keys)//8)
        self.u32(0x40,self.alloc(b''.join(self.fvf_rows)));self.u16(0x18,len(self.fvf_rows))
        self.u32(0x38,self.alloc(b''.join(self.shape_rows)));self.u16(0x14,len(self.shape_rows))
        if any(not x['replaced'] for x in self.new_shapes):
            kt=m.u32(0x3c);entries=[(m.string(m.u32(kt+8*i)),m.u32(kt+8*i),m.u32(kt+8*i+4)) for i in range(m.u16(0x16))]
            entries += [('Garage/'+x['name']+'_'+str(x['id']),self.string('Garage/'+x['name']+'_'+str(x['id'])),x['id']) for x in self.new_shapes if not x['replaced']]
            entries.sort(key=lambda x:x[0].encode());self.u32(0x3c,self.alloc(b''.join(struct.pack('>II',p,i) for _,p,i in entries)));self.u16(0x16,len(entries))
        active_images=self.alloc(b''.join(self.image_rows));self.u32(self.tx+28,active_images-self.tx+m.u32(self.tx+8));self.u16(self.tx+22,len(self.image_rows))
        self.u32(self.ms+20,self.alloc(b''.join(self.param_rows)));self.u16(self.ms+6,len(self.param_rows))
        newcut=(len(self.b)+127)&~127;self.b.extend(bytes(newcut-len(self.b)));delta=newcut-m.cut
        for p in self.mirrors:self.u32(p,newcut)
        ft=m.u32(self.sh+0x40)
        for i in range(m.u16(self.sh+0x34)):
            at=ft+32*i+4;p=m.u32(at);n=m.u32(at+4);need(m.cut<=p<=len(old)-n,'Native fragment program is outside the tail.');self.u32(at,p+delta);self.resources.append((p,p+delta,n))
        for i in range(self.image_count):
            at=self.image_ptr+32*i;p=m.u32(at);n=m.u32(at+4)
            if p:
                need(m.cut<=p<=len(old)-n,'Native image payload is outside the tail.');self.u32(at,p+delta);self.u32(active_images+32*i,p+delta);self.resources.append((p,p+delta,n))
        for im in self.images:self.u32(active_images+32*im['id'],newcut+im['tail_offset'])
        self.u32(4,newcut+len(self.tail));data=bytes(self.b)+bytes(self.tail);self.finished=True
        need(data[newcut:newcut+len(old)-m.cut]==old[m.cut:],'Native tail changed.')
        for a,b,n in self.resources:need(data[b:b+n]==old[a:a+n],'Resource relocation mismatch.')
        for im in self.images:need(sha256(data[newcut+im['tail_offset']:newcut+im['tail_offset']+im['bytes']])==im['sha256'],'New image payload mismatch.')
        validation=MDL3(data).validate()
        return data,dict(checks=validation,added_shapes=self.new_shapes,added_materials=self.new_materials,images=self.images,reused_native_images=self.reused_images,gpu_tail_bytes_before=len(old)-m.cut,gpu_tail_bytes_after=len(self.tail),model_commands=self.model_changes,native_resource_payloads_preserved=True,old_cut=m.cut,new_cut=newcut)
