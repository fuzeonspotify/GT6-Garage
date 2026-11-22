import sys

from pathlib import Path

import json, struct, zlib

from inspect_stream_map import decode_span

def streams(mdl,data,expected_count):
    groups=[];records=[];sep=mdl.u32(0xdc)
    def add(label,hp):
        offset=mdl.u32(hp+4);na,nb=mdl.unpack("HH",hp+8);ptr=mdl.u32(hp+12)
        assert na+nb<65536;mdl.range(ptr,(na+nb)*8)
        g=dict(label=label,header=hp,na=na,nb=nb,table=ptr,records=[])
        for i in range(na+nb):
            mp=ptr+i*8;flags,expanded=mdl.unpack("II",mp);size=flags&0x7fffffff
            assert offset+size<=len(data)
            stored=data[offset:offset+size];raw,wbits=decode_span(stored,expanded,bool(flags>>31))
            rec=dict(group=label,index=i,header=hp,metadata=mp,offset=offset,stored=stored,raw=raw,wbits=wbits,flags=flags)
            records.append(rec);g["records"].append(rec);offset+=size
        groups.append(g)
    n=mdl.u32(sep);keys=mdl.u32(sep+8)
    for i in range(n):
        kp=keys+i*12;name=mdl.string(mdl.u32(kp));ng=mdl.u32(kp+4);gp=mdl.u32(kp+8)
        for j in range(ng):
            hp=gp+j*24;add(name+"/"+str(j),hp)
            ne=mdl.u32(hp+16);ep=mdl.u32(hp+20)
            for k in range(ne):add(name+"/"+str(j)+"/extra"+str(k),ep+k*16)
    cursor=0
    for r in sorted(records,key=lambda x:x["offset"]):
        assert r["offset"]==cursor;cursor+=len(r["stored"])
    assert cursor==len(data) and len(records)==expected_count
    return groups,records
