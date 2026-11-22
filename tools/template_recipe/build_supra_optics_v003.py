from pathlib import Path

import sys, json, struct, hashlib, collections, math, io, shutil, argparse

import numpy as np

from PIL import Image

import build_zonda_geometry_v001 as g

from rsx_vertex_codec import encode_cmp32_be

import supra_optical_profile_v003 as optics

sha=lambda b:hashlib.sha256(b).hexdigest()

def arr(blob,ch,key,cols,dtype):
 d=ch['buffers'][key];return np.frombuffer(blob,dtype=dtype,count=d['count']*cols,offset=d['byte_offset']).reshape(-1,cols).copy()

def encode(chunks,blob,wheel=False):
 vb=bytearray();allp=[];indices=[];coverage=[];offset=0
 for ch in chunks:
  ps=arr(blob,ch,'positions',3,'<f4');ns=arr(blob,ch,'normals',3,'<f4');uv=arr(blob,ch,'uv0',2,'<f4');ids=arr(blob,ch,'indices',1,'<u4').reshape(-1)
  ns=optics.native_normals(ch,ps,ns,uv,ids)
  uv=optics.native_uv(ch,uv)
  assert np.isfinite(ps).all() and np.isfinite(ns).all() and np.isfinite(uv).all()
  assert ids.max()<len(ps) and len(ids)==3*ch['triangles']
  faces=ids.reshape(-1,3);cross=np.cross(ps[faces[:,1]]-ps[faces[:,0]],ps[faces[:,2]]-ps[faces[:,0]])
  dots=(cross*ns[faces].mean(axis=1)).sum(axis=1);weight=float(np.abs(dots).sum())
  alignment=float(dots.sum())/weight if weight>1e-12 else 1.0
  flipped=alignment < -0.99
  if flipped:
   assert ch['role']!='static' and ch['id'] in (1,2,3,4,6,176), 'Unexpected inconsistent source surface'
   faces[:,[1,2]]=faces[:,[2,1]];ids=faces.reshape(-1);alignment=-alignment
  assert alignment>0.95, (ch['id'],alignment)
  for p,n,t in zip(ps,ns,uv):
   vb.extend(struct.pack('>3f',*p));vb.extend(encode_cmp32_be(n,normalize=False));vb.extend(struct.pack('>2f',*t))
   if wheel:vb.extend(bytes((64,64,64,255)))
  indices.extend((ids+offset).tolist());coverage.append(dict(mesh=ch['id'],vertices=len(ps),triangles=len(ids)//3,winding_corrected_against_authored_normals=flipped,normal_winding_agreement=alignment));offset+=len(ps);allp.extend(ps.tolist())
 assert offset<65536 and len(indices)//3<65536
 return dict(vertex=bytes(vb),index=np.array(indices,dtype='>u2').tobytes(),positions=allp,vertices=offset,indices=len(indices),triangles=len(indices)//3,bounds=g.box(allp),coverage=coverage)

class Textures:
 def __init__(self):self.images=[];self.payloads={};self.cache={}
 def add(self,im,label):
  im=im.convert('RGBA');alpha=im.getchannel('A').getextrema();fmt='DXT1' if alpha==(255,255) else 'DXT5';output=io.BytesIO();im.save(output,format='DDS',pixel_format=fmt)
  data=output.getvalue();assert data[:4]==b'DDS ' and data[84:88]==fmt.encode();data=data[128:];key=(sha(data),im.size,fmt)
  if key in self.cache:return self.cache[key]
  i=len(self.images);self.cache[key]=i;self.images.append(dict(id=i,label=label,width=im.width,height=im.height,bytes=len(data),sha256=sha(data),native_format_byte=134 if fmt=='DXT1' else 136,mip_count=1));self.payloads[i]=data;return i
 def solid(self,color,label):return self.add(Image.new('RGBA',(4,4),tuple(color)),label)
