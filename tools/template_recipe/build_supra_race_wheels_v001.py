from pathlib import Path

import sys, json, struct, hashlib, shutil

import build_supra_optics_v003 as core

from PIL import Image

import numpy as np

g=core.g

def add_fvfs(meta,materials):
 raw=meta.raw;oldn=g.u16(raw,0x18);oldp=g.u32(raw,0x40);orig=raw[oldp:oldp+120*oldn];assert oldn==1
 names={n:meta.string(n) for n in ('position','normal','map12','colorSet1')}
 fields={'position':(0,3,2,0),'normal':(12,1,6,0),'map12':(16,2,2,0),'colorSet1':(24,4,4,0)}
 fp=meta.alloc('race_source_wheel_fields',b''.join(struct.pack('>I4B',names[n],*fields[n]) for n in sorted(fields)))
 rows=[];mapping={}
 for mat in materials:
  assert {x['name']:x['register'] for x in mat['vertex_semantics']}=={'position':0,'normal':2,'map12':8,'colorSet1':3}
  key=tuple(mat['shader_key']);assert key not in mapping
  row=bytearray(orig[:120]);ap=meta.alloc('race_source_wheel_array',raw[g.u32(row,116):g.u32(row,116)+16])
  struct.pack_into('>III',row,0,meta.string(key[0]),key[1],fp);row[24:26]=bytes((4,28));struct.pack_into('>I',row,116,ap)
  mapping[key]=oldn+len(rows);rows.append(row)
 table=meta.alloc('race_wheel_vertex_layouts',orig+b''.join(rows));meta.s16(0x18,oldn+len(rows),'race_FVF_count');meta.s32(0x40,table,'race_FVF_table');return mapping
