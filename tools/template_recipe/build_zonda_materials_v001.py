from __future__ import annotations

import sys

from pathlib import Path

import argparse, collections, copy, importlib.util, json, struct

import build_zonda_geometry_v001 as g

def span_count(raw):
 sep=g.u32(raw,0xdc);keys=g.u32(raw,sep+8);count=0
 for i in range(g.u32(raw,sep)):
  p=keys+12*i;groups=g.u32(raw,p+8)
  for j in range(g.u32(raw,p+4)):
   h=groups+24*j;count+=g.u16(raw,h+8)+g.u16(raw,h+10);ep=g.u32(raw,h+20)
   for k in range(g.u32(raw,h+16)):count+=g.u16(raw,ep+16*k+8)+g.u16(raw,ep+16*k+10)
 return count

def shape_row(template,geo,fvf,material,flags,*,vp=0,ip=0,bp=0):
 row=bytearray(template);g.need(len(row)==48,'Shape record length')
 g.need(not any(row[6:8]+row[16:20]+row[28:36]),'Uninspected shape residual')
 g.need(flags in(0x1809,0x3809),'Unreviewed direct material shape flags')
 struct.pack_into('>Hhh',row,0,flags,fvf,material);struct.pack_into('>II',row,8,geo['vertices'],vp)
 struct.pack_into('>II',row,20,geo['indices'],ip);struct.pack_into('>HHII',row,36,0,geo['triangles'],bp,0)
 return row

def update_model_materials(meta,model,materials):
 q=g.u32(meta.raw,0xb0)+64*model['id'];oldp=g.u32(meta.raw,q);oldn=g.u16(meta.raw,q+48);ids=sorted(set(materials))
 p=meta.alloc('source_material_ids_model_'+str(model['id']),struct.pack('>'+'H'*len(ids),*ids))
 meta.s32(q,p,'model_source_material_list');meta.s16(q+48,len(ids),'model_source_material_count')
 return {'model':model['id'],'old_ids':list(struct.unpack_from('>'+'H'*oldn,meta.raw,oldp)),'new_ids':ids,'new_pointer':p}
