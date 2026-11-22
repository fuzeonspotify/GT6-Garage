import numpy as np

PROFILES={
 'windscreen_side':((18,26,24),128,34),
 'rear_window':((14,22,20),128,48),
 'clear_inner_lens':((12,15,17),88,14),
 'instrument_cover':((10,14,17),80,10),
 'headlamp_outer':((7,10,12),112,18),
 'headlamp_secondary':((7,10,12),72,10),
 'foglamp_outer':((7,10,12),100,18),
 'rear_outer_cover':((6,8,10),100,20),
 'amber_lens':((190,72,3),88,174),
 'amber_side_reflector':((176,58,2),80,222),
 'amber_bulb':((176,65,3),68,146),
 'red_lens':((162,5,7),100,188),
 'red_internal':((128,3,5),64,140),
 'red_side_reflector':((148,4,6),84,226),
 'plate_lamp_cover':((14,18,20),80,14),
 'projector_lens':((19,28,40),140,32),
}

GROUP_PROFILES={6:['windscreen_side','rear_window','clear_inner_lens','instrument_cover'],12:['headlamp_outer','headlamp_secondary','foglamp_outer','rear_outer_cover'],14:['amber_lens','amber_side_reflector','amber_bulb'],16:['red_lens','red_internal','red_side_reflector'],25:['plate_lamp_cover'],27:['projector_lens']}

CHUNK_PROFILE={15:'windscreen_side',139:'rear_window',18:'clear_inner_lens',23:'instrument_cover',29:'clear_inner_lens',69:'clear_inner_lens',123:'clear_inner_lens',43:'headlamp_outer',44:'headlamp_secondary',48:'foglamp_outer',58:'rear_outer_cover',62:'amber_lens',96:'amber_bulb',124:'amber_side_reflector',125:'amber_bulb',71:'red_lens',143:'red_internal',163:'red_internal',117:'red_side_reflector',137:'plate_lamp_cover',142:'projector_lens',173:'projector_lens'}

GLASS_ORDER={25:0,27:1,14:2,16:3,6:4,12:5}

CELL=16

def role_for(ch):
 mid=ch['material_id']
 if mid not in GROUP_PROFILES:return None
 role=CHUNK_PROFILE[ch['id']]
 assert role in GROUP_PROFILES[mid], (ch['id'],mid,role)
 return role

def native_uv(ch,uv):
 if ch['role']=='static' and ch['material_id']==2:
  index=METAL_ROLE_INDEX.get(ch['id'],0);out=np.empty_like(uv);out[:,0]=(index+.5)/4;out[:,1]=.5;return out
 role=role_for(ch)
 if role is None:return uv
 roles=GROUP_PROFILES[ch['material_id']];index=roles.index(role)
 # Authored glass has uniform factors, no source colour/opacity texture.
 # Stable cell-center UVs allow different physical parts to share one draw.
 out=np.empty_like(uv);out[:,0]=(index+.5)/4;out[:,1]=.5
 return out

def build_maps(mat,tex):
 from PIL import Image
 mid=mat['id'];assert mid in GROUP_PROFILES and mat['base_color_texture'] is None
 tint=Image.new('RGBA',(CELL*4,CELL),(0,0,0,0));opacity=Image.new('RGBA',tint.size,(0,255,0,0))
 for index,role in enumerate(GROUP_PROFILES[mid]):
  rgb,response,alpha=PROFILES[role];box=(index*CELL,0,(index+1)*CELL,CELL)
  tint.paste((*rgb,response),box);opacity.paste((0,255,0,alpha),box)
 return tex.add(tint,f'optical_{mid}_tint_response'),tex.add(opacity,f'optical_{mid}_opacity')

def order(mid,materials):
 return (1,GLASS_ORDER[mid]) if materials[mid]['family']=='glass' else (0,mid)

def manifest():
 return dict(version=3,rear_lamp_housing_rgba=[32,36,42,69],reflector_normal_map_source_meshes=[64,106],reflector_normal_map_strength=.70,profiles={name:dict(tint_srgb=list(v[0]),response_alpha=v[1],opacity_alpha=v[2]) for name,v in PROFILES.items()},chunk_roles=CHUNK_PROFILE,glass_draw_order=[x[0] for x in sorted(GLASS_ORDER.items(),key=lambda x:x[1])],
  rationale='Dark neutral tint removes diffuse white haze; separated reflection and opacity retain view-dependent native reflections. Red/amber lens absorption is authored independently from clear outer covers. Inner optical pieces precede outer covers to avoid a transparent outer surface hiding the detail behind it.',
  geometry_policy='All positions, indices and component counts retained. Only optical/metal uniform-colour UVs use palette cell centres. Supplied reflector normal map is baked into existing split normals for source meshes64 and106 only. Original authoring inputs remain intact.',
  native_shader_sha256='85c2368dade92812d48834d2c141e08f62d39cc6300a48bfa7f05a2db31c3f59',physical_transmission_equivalence=False)

METAL_PIXELS=[(231,231,231,69),(66,73,80,69),(184,194,204,69),(205,210,216,69)]

METAL_ROLE_INDEX={38:1,60:1,97:1,168:2,106:2,64:3}

def build_metal_map(tex):
 from PIL import Image
 im=Image.new('RGBA',(64,16))
 for i,color in enumerate(METAL_PIXELS):im.paste(color,(16*i,0,16*(i+1),16))
 return tex.add(im,'optical_lamp_metal_tint_response')

_NORMAL_MAP=None

NORMAL_AUDIT={}

def native_normals(ch,ps,ns,uv,ids):
 """Bake the supplied reflector normal map into existing source split normals.
 No new triangles, vertices, textures at runtime, or invented displacement.
 glTF source UV uses image top-left and the original tangent-space normal map.
 """
 if ch['id'] not in (64,106) or ch['role']!='static':return ns
 from pathlib import Path
 from PIL import Image
 global _NORMAL_MAP
 if _NORMAL_MAP is None:
  path=Path.cwd()/'authoring/neutral/textures/image_04.png'
  _NORMAL_MAP=np.asarray(Image.open(path).convert('RGB'),dtype=np.float64)/127.5-1
 faces=ids.reshape(-1,3);p0,p1,p2=ps[faces[:,0]],ps[faces[:,1]],ps[faces[:,2]]
 q0,q1,q2=uv[faces[:,0]],uv[faces[:,1]],uv[faces[:,2]];e1=p1-p0;e2=p2-p0;d1=q1-q0;d2=q2-q0
 den=d1[:,0]*d2[:,1]-d1[:,1]*d2[:,0];valid=np.abs(den)>1e-12;safe=np.where(valid,den,1)
 T=(e1*d2[:,1,None]-e2*d1[:,1,None])/safe[:,None];B=(e2*d1[:,0,None]-e1*d2[:,0,None])/safe[:,None]
 area=np.linalg.norm(np.cross(e1,e2),axis=1);T/=np.maximum(np.linalg.norm(T,axis=1)[:,None],1e-12);B/=np.maximum(np.linalg.norm(B,axis=1)[:,None],1e-12)
 T*=area[:,None]*valid[:,None];B*=area[:,None]*valid[:,None]
 tv=np.zeros_like(ps,dtype=np.float64);bv=np.zeros_like(tv)
 for k in range(3):np.add.at(tv,faces[:,k],T);np.add.at(bv,faces[:,k],B)
 n=ns.astype(np.float64);tv-=n*(n*tv).sum(axis=1)[:,None];length=np.linalg.norm(tv,axis=1);usable=length>1e-10;tv/=np.maximum(length[:,None],1e-12)
 sign=np.where((np.cross(n,tv)*bv).sum(axis=1)<0,-1,1);bv=np.cross(n,tv)*sign[:,None]
 h,w=_NORMAL_MAP.shape[:2];x=(uv[:,0]%1)*w-.5;y=(uv[:,1]%1)*h-.5;ix=np.floor(x).astype(int);iy=np.floor(y).astype(int);fx=(x-ix)[:,None];fy=(y-iy)[:,None]
 tex=(_NORMAL_MAP[iy%h,ix%w]*(1-fx)+_NORMAL_MAP[iy%h,(ix+1)%w]*fx)*(1-fy)+(_NORMAL_MAP[(iy+1)%h,ix%w]*(1-fx)+_NORMAL_MAP[(iy+1)%h,(ix+1)%w]*fx)*fy
 mapped=tv*tex[:,0,None]+bv*tex[:,1,None]+n*tex[:,2,None];mapped/=np.maximum(np.linalg.norm(mapped,axis=1)[:,None],1e-12)
 result=n*.30+mapped*.70;result/=np.maximum(np.linalg.norm(result,axis=1)[:,None],1e-12);result[~usable]=n[~usable]
 # Reject excessive/invalid tangent frames instead of inverting a surface.
 dots=(result*n).sum(axis=1);assert np.isfinite(result).all() and dots.min()>.50,(ch['id'],float(dots.min()))
 NORMAL_AUDIT.setdefault(ch['id'],[]).append(dict(vertices=len(ps),triangles=len(faces),valid_tangent_vertices=int(usable.sum()),min_dot_original=float(dots.min()),mean_dot_original=float(dots.mean()),source_image='image_04.png',strength=.70))
 return result.astype('<f4')
