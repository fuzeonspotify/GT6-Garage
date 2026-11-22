"""Lossless GT6 NDB0 long/short-name append for an explicit new identity.

Layout reference:
https://github.com/Nenkai/GT-File-Specifications-Documentation/blob/master/Formats/PS3/GT6/NDB0_GT6.bt

The existing reviewed writer is preserved: ID-sorted records, locale-owned
rank order, original prefix compression and unknown header words. Mutation
requires exact no-op and reverse-append roundtrips. No files are written here.
"""
from __future__ import annotations
from dataclasses import dataclass,replace
import hashlib,struct
from typing import Sequence
from .errors import GarageError

MAX_NDB_BYTES=64*1024**2
MAX_ENTRIES=100_000
MAX_STRING_BYTES=16_384
MAX_NEW_NAME_BYTES=1024


def require(condition:bool,message:str)->None:
    if not condition:raise GarageError(message,'car_names')


def uint(data:bytes,offset:int)->int:
    require(type(offset) is int and 0<=offset<=len(data)-4,'Truncated NDB0 integer.')
    return struct.unpack_from('>I',data,offset)[0]


def cstring(data:bytes,offset:int,section_start:int,section_end:int)->bytes:
    require(section_start<=offset<section_end<=len(data),'NDB0 string leaves its own section.')
    require(offset%4==0,'Unaligned NDB0 string pointer.')
    end=data.find(b'\0',offset,min(section_end,offset+MAX_STRING_BYTES+1))
    require(end>=0,'NDB0 string is unterminated within its section or exceeds the length limit.')
    value=data[offset:end]
    try:value.decode('utf-8')
    except UnicodeDecodeError as exc:raise GarageError('NDB0 name is not valid UTF-8.','car_names') from exc
    return value


def align(out:bytearray,boundary:int,fill:int=0)->None:
    out.extend(bytes([fill])*(-len(out)%boundary))


@dataclass(frozen=True)
class Entry:
    car_id:int
    rank:int
    name:bytes
    prefixes:tuple[int,int,int,int]

    def full_name(self,prefix_strings:Sequence[bytes])->str:
        return b' '.join([prefix_strings[i] for i in self.prefixes if i!=255]+[self.name]).decode('utf-8')


@dataclass(frozen=True)
class Tree:
    header:bytes
    entries:tuple[Entry,...]


@dataclass(frozen=True)
class NDB0:
    header:bytes
    trees:tuple[Tree,...]
    prefixes:tuple[bytes,...]

    @classmethod
    def parse(cls,data:bytes)->'NDB0':
        require(isinstance(data,bytes) and 32<=len(data)<=MAX_NDB_BYTES,'Expected a bounded NDB0 name file.')
        require(data[:4]==b'NDB0','Expected NDB0 car names.')
        tree_count=uint(data,12)
        require(tree_count==2,'Expected GT6 long and short name maps.')
        offsets=[uint(data,16+i*4) for i in range(tree_count)]
        require(offsets==sorted(set(offsets)),'NDB0 map offsets must be unique and ordered.')
        require(offsets[0]>=32 and all(offset%16==0 and offset<=len(data)-16 for offset in offsets),'Invalid or unaligned NDB0 map offset.')
        prefix_offset=uint(data,8)
        require(offsets[-1]+16<=prefix_offset<=len(data)-4 and prefix_offset%16==0,'Unexpected NDB0 prefix-map placement.')
        prefix_count=uint(data,prefix_offset)
        require(prefix_count<=255,'NDB0 prefix count exceeds its byte index format.')
        prefix_records_end=prefix_offset+4+4*prefix_count
        require(prefix_records_end<=len(data),'Truncated NDB0 prefix-pointer table.')
        prefixes=tuple(cstring(data,uint(data,prefix_offset+4+i*4),prefix_records_end,len(data)) for i in range(prefix_count))
        trees=[]
        for index,offset in enumerate(offsets):
            count=uint(data,offset+4)
            require(0<count<=MAX_ENTRIES,'Invalid or excessive NDB0 name-map count.')
            end=offsets[index+1] if index+1<tree_count else prefix_offset
            record_end=offset+16+count*16
            require(record_end<=end<=len(data),'NDB0 name records exceed their own section.')
            entries=[]
            for row in range(count):
                car_id,rank,name_offset,*indices=struct.unpack_from('>III4B',data,offset+16+row*16)
                require(car_id<0xFFFFFFFF,'NDB0 contains a reserved car ID.')
                require(all(i==255 or i<prefix_count for i in indices),'NDB0 name references an unknown prefix.')
                entries.append(Entry(car_id,rank,cstring(data,name_offset,record_end,end),tuple(indices)))
            ids=[entry.car_id for entry in entries]
            require(ids==sorted(set(ids)),'NDB0 car IDs must be unique and sorted.')
            require(sorted(entry.rank for entry in entries)==list(range(count)),'NDB0 name ranks must be a complete permutation.')
            trees.append(Tree(data[offset:offset+16],tuple(entries)))
        require([e.car_id for e in trees[0].entries]==[e.car_id for e in trees[1].entries],'NDB0 long and short name maps contain different car IDs.')
        return cls(data[:offsets[0]],tuple(trees),prefixes)

    def serialize(self)->bytes:
        out=bytearray(self.header)
        require(len(out)>=32 and len(out)%16==0,'Unaligned NDB0 main header.')
        struct.pack_into('>I',out,12,len(self.trees))
        for index,tree in enumerate(self.trees):
            align(out,16,0x5E);offset=len(out)
            struct.pack_into('>I',out,16+index*4,offset)
            out.extend(tree.header)
            struct.pack_into('>I',out,offset+4,len(tree.entries))
            record_start=len(out);out.extend(b'\0'*(16*len(tree.entries)))
            for row,entry in enumerate(tree.entries):
                string_offset=len(out);out.extend(entry.name+b'\0');align(out,4)
                struct.pack_into('>III4B',out,record_start+row*16,entry.car_id,entry.rank,string_offset,*entry.prefixes)
            align(out,16,0x5E)
        prefix_offset=len(out);struct.pack_into('>I',out,8,prefix_offset)
        out.extend(struct.pack('>I',len(self.prefixes)))
        prefix_records=len(out);out.extend(b'\0'*(4*len(self.prefixes)))
        for index,prefix in enumerate(self.prefixes):
            struct.pack_into('>I',out,prefix_records+index*4,len(out))
            out.extend(prefix+b'\0');align(out,4)
        align(out,16)
        require(len(out)<=MAX_NDB_BYTES,'Updated NDB0 exceeds the name-file limit.')
        return bytes(out)


def _lossless(data:bytes)->NDB0:
    parsed=NDB0.parse(data)
    require(parsed.serialize()==data,'Name file differs from the reviewed lossless NDB0 layout; refusing rewrite.')
    return parsed


def name_ids(raw:bytes)->set[int]:
    """Read consistent IDs only from a supported losslessly writable name file."""
    return {entry.car_id for entry in _lossless(raw).trees[0].entries}


def _new_name(value:str)->bytes:
    require(isinstance(value,str) and bool(value.strip()) and value.isprintable(),'Car name must contain printable characters and cannot be blank.')
    try:raw=value.encode('utf-8')
    except UnicodeEncodeError as exc:raise GarageError('Car name cannot be encoded as UTF-8.','car_names') from exc
    require(len(raw)<=MAX_NEW_NAME_BYTES,f'Car name exceeds {MAX_NEW_NAME_BYTES} UTF-8 bytes.')
    return raw


def append_car_names(raw:bytes,car_id:int,long_name:str,short_name:str,*,after_id:int)->tuple[bytes,dict]:
    """Append one identity after an explicit existing locale-sort anchor.

    No donor name, ID or replacement mode is selected implicitly. Original
    names and their locale-specific relative order survive byte-for-byte.
    """
    require(type(car_id) is int and 0<=car_id<0xFFFFFFFF,'Invalid new car ID.')
    require(type(after_id) is int and 0<=after_id<0xFFFFFFFF,'Invalid name-sort anchor ID.')
    encoded=(_new_name(long_name),_new_name(short_name))
    original=_lossless(raw);trees=[];insertions=[]
    ids={e.car_id for e in original.trees[0].entries}
    require(car_id not in ids,f'Car ID {car_id} already has a name; choose a free identity.')
    require(after_id in ids,f'Name-sort anchor {after_id} is missing.')
    require(len(ids)<MAX_ENTRIES,'NDB0 name-map capacity reached.')
    for tree,name in zip(original.trees,encoded):
        anchor=next(e for e in tree.entries if e.car_id==after_id);rank=anchor.rank+1
        entries=[replace(e,rank=e.rank+(e.rank>=rank)) for e in tree.entries]
        entries.append(Entry(car_id,rank,name,(255,)*4));entries.sort(key=lambda e:e.car_id)
        trees.append(replace(tree,entries=tuple(entries)));insertions.append(rank)
    updated=replace(original,trees=tuple(trees));output=updated.serialize();reparsed=_lossless(output)
    require(reparsed.prefixes==updated.prefixes and [t.entries for t in reparsed.trees]==[t.entries for t in updated.trees],'Serialized name append changed names or ordering.')
    restored=[]
    for tree,rank in zip(reparsed.trees,insertions):
        entries=tuple(replace(e,rank=e.rank-(e.rank>rank)) for e in tree.entries if e.car_id!=car_id)
        restored.append(replace(tree,entries=entries))
    require(replace(reparsed,trees=tuple(restored)).serialize()==raw,'Name append changed unrelated original data.')
    receipt={'car_id':car_id,'long_name':long_name,'short_name':short_name,'sort_anchor_id':after_id,
             'inserted_ranks':insertions,'original_counts':[len(t.entries) for t in original.trees],
             'new_counts':[len(t.entries) for t in reparsed.trees],'prefix_count':len(reparsed.prefixes),
             'original_sha256':hashlib.sha256(raw).hexdigest(),'output_sha256':hashlib.sha256(output).hexdigest(),
             'original_bytes':len(raw),'output_bytes':len(output),'noop_roundtrip_exact':True,
             'reverse_append_roundtrip_exact':True,'existing_relative_sort_order_preserved':True,
             'existing_names_and_prefixes_preserved':True,'long_short_ids_match':True}
    return output,receipt
