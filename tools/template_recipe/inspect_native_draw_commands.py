import sys

from pathlib import Path

import json, struct, collections

def instruction(data,p,end):
    assert 0<=p<end<=len(data);op=data[p]
    row={"offset":p,"opcode":op,"name":{0:"End",3:"CallShapeByte",4:"CallShapeUShort",5:"LODRenderSwitch",
        6:"UnresolvedOp6",9:"JumpByte",10:"JumpShort",14:"UnresolvedOp14",15:"UnresolvedOp15",
        44:"CallVM",53:"UnresolvedOp53",54:"VMBranch",59:"CallShape2Byte",60:"CallShape2UShort"}.get(op,"Unknown")}
    if op in (0,14,15,44):n=1
    elif op in (3,6):n=2;row["argument"]=data[p+1]
    elif op in (4,53):n=3;row["argument"]=struct.unpack_from(">H",data,p+1)[0]
    elif op==59:n=3;row.update(shape_id=data[p+1],context=data[p+2])
    elif op==60:n=5;row.update(shape_id=struct.unpack_from(">H",data,p+1)[0],context=struct.unpack_from(">H",data,p+3)[0])
    elif op in (9,10):
        n=2 if op==9 else 3
        offset=data[p+1] if op==9 else struct.unpack_from(">h",data,p+1)[0]
        row.update(relative_offset=offset,target=p+n+offset)
    elif op in (5,54):
        selector=struct.unpack_from(">H",data,p+1)[0];count=data[p+3];n=4+2*count
        targets=[]
        for i in range(count):
            field=p+4+i*2;offset=struct.unpack_from(">h",data,field)[0]
            targets.append({"field":field,"relative_offset":offset,"target":field+2+offset})
        row.update(selector_or_register=selector,branch_count=count,branches=targets)
    else:raise ValueError("Unvalidated opcode "+str(op)+" at "+str(p))
    assert p+n<=end
    row.update(bytes=n,raw_hex=data[p:p+n].hex(),next=p+n)
    if op==3:row["shape_id"]=row["argument"]
    if op==4:row["shape_id"]=row["argument"]
    return row
