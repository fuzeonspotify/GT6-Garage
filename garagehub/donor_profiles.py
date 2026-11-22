"""Hash-pinned native donor contracts for independent car profiles.

Inspection only. Passing structural inspection does NOT authorize replacing
another car's visual slot, nor does it imply that an exporter supports the
donor's mechanical/material/stream architecture.
"""
from __future__ import annotations
from pathlib import Path
from .errors import GarageError
from .util import hash_file
from .native import MDL3
from .native_editor import Editor
from . import body_streams

RX8_ID='rx8-bces01893-gt6-122-f50-research'
RX8_MODEL_PATH='car/0001/0001'
RX8_ASSETS={
 'info':'8ddde5623762d782ddc48cd3a27642a1858dbb71212367380aefa2a731595aff',
 'interior':'4f186bb271029e6e129b9e12b2115e62280ad3bcd483780f6f95bcd55d5bf4d2',
 'hq/body':'ac5d4f091cad2c32ff0cd9e6e149b700f0bb5526eca9bfd7331e9a929180f38e',
 'hq/body_s':'6c792b8c9a524bb64564d052cfe6b5fbd947aa2f6517cc8dbcbfc94969fcea58',
 'hq/wheel':'dcf5dd04d08654f946a6683a8495e9218103263b0b302dce21f6eb3b2b0e7f9c',
 'race/body':'e18651d9bfba18b70e4eb8f9add9e56a6df71a3cd75bf3389087caa6bb8dcf59',
 'race/body_s':'aa9e8b0dc74a098e8042c129b058322c00884fa67b6d54f6319a568f7c40bdd6',
 'race/wheel':'a76bea101ab8674f877e5bb4f024cbd082523f67b2d8f0413aa47386a6fa983d',
}
# These are physically inspected data/layout facts, not converted roles.
RX8_LAYOUTS={
 'hq/body':(11,102,10,2,65),
 'race/body':(11,102,10,2,68),
 'hq/wheel':(8,8,1,1,5),
 'race/wheel':(8,16,5,1,13),
}

def locate_rx8(root:Path)->Path:
    root=Path(root)
    for p in (root,root/RX8_MODEL_PATH,root/'native'/RX8_MODEL_PATH):
        if (p/'hq/body').is_file() and (p/'race/wheel').is_file():
            return p
    raise GarageError('Choose the original, fully extracted RX-8 donor under car/0001/0001; never choose the installed main game or a partially converted F50.','donor_path')

def inspect_rx8(root:Path)->dict:
    p=locate_rx8(root)
    if p.is_symlink() or (hasattr(p,'is_junction') and p.is_junction()):
        raise GarageError('A linked or junction donor is not accepted. Select an owned, local, original extraction.','donor_path')
    files={};structure={}
    for rel,digest in RX8_ASSETS.items():
        item=p/rel
        if not item.is_file() or item.is_symlink() or item.stat().st_size>16*1024**2 or hash_file(item)!=digest:
            raise GarageError(f'RX-8 donor {rel} does not match the reviewed original (not an F50 replacement). No data was modified.','donor_identity')
        files[rel]={'sha256':digest,'bytes':item.stat().st_size}
        if rel in RX8_LAYOUTS:
            m=MDL3(item.read_bytes());m.validate();e=Editor(m.data)
            actual=(m.u16(0x10),m.u16(0x14),m.u16(0x18),e.banks,e.bank_size)
            if actual!=RX8_LAYOUTS[rel]:
                raise GarageError(f'RX-8 native {rel} layout is incompatible with the reviewed profile.','donor_layout')
            structure[rel]={'models':actual[0],'shapes':actual[1],'fvfs':actual[2],
                            'banks':actual[3],'bank_size':actual[4],'images':e.image_count}
    for lod in ('hq','race'):
        mesh=Path(p/lod/'body').read_bytes();stream=Path(p/lod/'body_s').read_bytes()
        names={g['label'] for g in body_streams.groups(mesh,stream)}
        if not {'//0','custom_caliper0/0'}.issubset(names):
            raise GarageError('Reviewed RX-8 body stream lacks the expected static/mechanical groups.','donor_stream')
        structure[lod+'/body']['stream_labels']=sorted(names)
    return {'profile':RX8_ID,'donor_root':str(p),'verified_source_contract':True,
            'files':files,'structure':structure,'model_path':RX8_MODEL_PATH,
            'native_export_supported':False,'new_database_entry_created':False,
            'safety_blockers':[
                'Two material banks rather than seven, with different native shape/model and stream layouts; the Supra exporter cannot target this donor.',
                'F50 textured-rim and native lamp branch/material conversions still require their own profile.',
                'The RX-8 donor identity is not a distinct F50 SpecDB entry; do not overwrite existing F50/Cinque or original identities.',
            ]}
