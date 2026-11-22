"""Native MDL3 v14 structural checks distilled from the v029 loader repair.
The mutable field-pointer cells must have one owner, even when their strings
are shared. This is an export guard, not a claim of runtime visual correctness.
"""
from __future__ import annotations
import struct, math
import numpy as np
from .errors import GarageError
from .util import sha256

class MDL3:
    def __init__(self,data:bytes):
        self.data=data
        if len(data)<0xe4 or data[:4]!=b'MDL3' or self.u16(12)!=14:raise GarageError('Expected a GT6 big-endian MDL3 v14 asset.', 'native_format')
        if self.u32(4)!=len(data) or self.u32(8)!=0:raise GarageError('Native asset length/runtime relocation state is invalid.', 'native_format')
        self.cut=self.u32(0x6c)
        if not 0xe4<=self.cut<=len(data):raise GarageError('Invalid native metadata extent.', 'native_format')
    def span(self,p,n,limit=None):
        if p<0 or n<0 or p+n>(len(self.data) if limit is None else limit):raise GarageError('Native pointer leaves the asset.', 'native_pointer')
        return self.data[p:p+n]
    def u16(self,p):return struct.unpack('>H',self.span(p,2))[0]
    def s16(self,p):return struct.unpack('>h',self.span(p,2))[0]
    def u32(self,p):return struct.unpack('>I',self.span(p,4))[0]
    def string(self,p):
        if not 0<p<self.cut:raise GarageError('Native name pointer leaves metadata.', 'native_pointer')
        end=self.data.find(b'\0',p,min(p+1024,self.cut))
        if end<0:raise GarageError('Native name is unterminated.')
        try:return self.data[p:end].decode('utf-8')
        except UnicodeError as e:raise GarageError('Native name has invalid encoding.') from e
    def fvfs(self):
        n=self.u16(0x18);p=self.u32(0x40);self.span(p,n*120,self.cut)
        seen={};rows=[]
        for i in range(n):
            at=p+i*120;fp=self.u32(at+8);count=self.data[at+24];stride=self.data[at+25]
            if not 0<count<=16 or stride==0:raise GarageError('Invalid vertex layout.')
            self.span(fp,count*8,self.cut);fields=[]
            for j in range(count):
                cell=fp+j*8
                if cell in seen:raise GarageError(f'Unsafe shared vertex-field pointer: layouts {seen[cell]} and {i}. This is the v028 map-load crash pattern.', 'shared_fvf_pointer')
                seen[cell]=i;name=self.string(self.u32(cell));off,nc,fmt,stream=self.data[cell+4:cell+8]
                fields.append(dict(name=name,offset=off,components=nc,format=fmt,stream=stream))
            ap=self.u32(at+116)
            if ap:self.span(ap,16,self.cut)
            rows.append(dict(id=i,fields=fields,stride=stride,field_pointer=fp,name=self.string(self.u32(at)),key=self.u32(at+4)))
        return rows
    def validate(self):
        fvfs=self.fvfs();n=self.u16(0x14);p=self.u32(0x38);self.span(p,n*48,self.cut);direct=[]
        if n>4096:raise GarageError('Native shape count is excessive.')
        for i in range(n):
            at=p+i*48;f=self.s16(at+2)
            if f<0:continue
            if f>=len(fvfs):raise GarageError('Native shape references a missing vertex layout.')
            nv,vp=self.u32(at+8),self.u32(at+12);ni,ip=self.u32(at+20),self.u32(at+24);nt=self.u16(at+38)
            if not vp or not ip:continue # streamed shape, validated by the enclosing body-stream adapter
            if nv>65536 or ni%3 or nt!=ni//3:raise GarageError('Invalid native direct-mesh counts.')
            layout=fvfs[f];self.span(vp,nv*layout['stride'],self.cut);self.span(ip,ni*2,self.cut)
            ids=np.frombuffer(self.data,dtype='>u2',count=ni,offset=ip)
            if ni and ids.max()>=nv:raise GarageError('Native triangle references a missing vertex.')
            pos=next((x for x in layout['fields'] if x['name']=='position'),None)
            if pos and pos['format']==2 and pos['components']==3:
                if pos['offset']+12>layout['stride']:raise GarageError('Position exceeds native vertex stride.')
                a=np.ndarray((nv,3),dtype='>f4',buffer=self.data,offset=vp+pos['offset'],strides=(layout['stride'],4))
                if not np.isfinite(a).all():raise GarageError('Native mesh contains invalid positions.')
            direct.append(dict(shape=i,vertices=nv,triangles=ni//3))
        return dict(passed=True,sha256=sha256(self.data),version=14,layout_count=len(fvfs),unique_pointer_owners=True,direct_shapes=direct,visual_validation=False)


def corners(bounds):
    lo,hi=bounds
    # GT6 reads this as an ordered hexahedron, not an unordered point set.
    # Preserve the winding used by the working v029 exporter.
    order=((1,0,1),(1,0,0),(1,1,0),(1,1,1),(0,0,1),(0,0,0),(0,1,0),(0,1,1))
    return b''.join(struct.pack('>3f',*(bounds[t[k]][k] for k in range(3))) for t in order)


def pack_normal(n):
    # CELL_GCM_VERTEX_CMP: signed 11/11/10-bit packed normal.
    q=np.asarray(n,float)
    if q.shape!=(3,) or not np.isfinite(q).all() or np.any(np.abs(q)>1.0001):raise GarageError('Expected three finite normalized normal components.')
    x,y,z=[max(lo,min(hi,round(float(v)*32767.0/shift))) for v,lo,hi,shift in zip(q,(-1024,-1024,-512),(1023,1023,511),(32,32,64))]
    return struct.pack('>I',(x&2047)|((y&2047)<<11)|((z&1023)<<22))
