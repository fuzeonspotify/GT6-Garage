"""Build the EU 1.22 wheel-shop resource edits from the original local game files."""
import hashlib
import re
import sqlite3
import struct
import zlib
from pathlib import Path


MENU_PATH = 'projects/gt6/gtauto/gtauto.adc'
MENU_SHA = '47387b77a7e4a57da5284610c5d2fe39da7a9a11d3977ee9047bf7ef776fff0c'
PACKAGE_PATH = 'projects/gt6/gtauto/gtauto.mpackage'
PACKAGE_SHA = '2eb69c1a401fbda58ae8a079f481696e6375c2e76ce575b7d171f38f396d9ed4'
COMPONENTS = {'WheelRoot.adc':'c3961200424cfc9011d3806d1672bbaef42af55fc143657312821b31b465e9f3',
              'PaintRoot.adc':'49616c1def4608ecb85cc536c478100e041ffe7eedf76957d93a0dc4a5f2ea21'}
DATABASE_SHA = 'aa808d7abd5be3a01f28ac4363e6a61d989cdab1e6cc58c33df0e4f44d046165'
SHOP_VIEW = """CREATE VIEW GT6GARAGE_RIM_LIMITS AS
    SELECT CarCode, FrontMinDiameter, RearMinDiameter, FrontMaxDiameter, RearMaxDiameter,
           max(-40, 40 - min(FrontMinDiameter, RearMinDiameter)) AS MinimumOffset
    FROM WHEEL"""
SHOP_QUERY = 'SELECT * FROM GT6GARAGE_RIM_LIMITS WHERE CarCode=?'


def edit_database(path):
    """The factory game treats its MinDiameter columns as the zero-offset size."""
    assert hashlib.sha256(path.read_bytes()).hexdigest() == DATABASE_SHA
    db = sqlite3.connect(path)
    try:
        with db:
            db.execute("""UPDATE WHEEL SET FrontMaxDiameter=FrontMinDiameter+40,
                         RearMaxDiameter=RearMinDiameter+40
                         WHERE FrontExchangeable=1 AND RearExchangeable=1
                           AND FrontMaxDiameter>0 AND RearMaxDiameter>0""")
            ceiling = db.execute('SELECT max(max(FrontMaxDiameter,RearMaxDiameter)) FROM WHEEL').fetchone()[0]
            db.execute("""UPDATE WHEEL_EX SET MaxDiameter=max(MaxDiameter,?)
                         WHERE WheelType & 1 AND MinDiameter=40 AND MaxDiameter=240""",(ceiling,))
            db.execute(SHOP_VIEW)
        assert db.execute('PRAGMA integrity_check').fetchall() == [('ok',)]
    finally:
        db.close()


def integer(value):
    return struct.pack('<i', value)


def varuint(value):
    if value < 128:
        return bytes([value])
    if value < 0x4000:
        return bytes([0x80 | value >> 8, value & 255])
    raise ValueError('Symbol exceeds two-byte encoding')


def edit_menu(original, disassembly):
    """Only the two wheel-list routines and appended text symbols differ in the ADC."""
    digest = hashlib.sha256(original).hexdigest()
    assert digest in (MENU_SHA,*COMPONENTS.values())
    rows = []
    pattern = re.compile(r'^\s*([0-9A-F]+)\|\s*\d+\|\s*(\d+)\| (.+)$')
    for line in disassembly.splitlines():
        match = pattern.match(line)
        if match:
            rows.append((int(match[1],16), int(match[2]), match[3]))
    ops = {op: original[op:rows[index+1][0]-4] for index,(op,_,_) in enumerate(rows[:-1])}
    # The original symbol IDs are stable. Append the new SQL and label fragments.
    def read_number(offset):
        first = original[offset]
        return (first,offset+1) if first<128 else (((first&63)<<8)|original[offset+1],offset+2)
    count,pos = read_number(8)
    count_end=pos
    symbol={}
    for index in range(count):
        length,pos = read_number(pos)
        symbol[original[pos:pos+length].decode('utf8','replace')]=varuint(index)
        pos += length
    operators=['__ge__','__add__','__mod__','__div__']
    symbols = [s for s in (SHOP_QUERY,'MinimumOffset','F ',' / R ',' in',*operators) if s not in symbol]
    extra = b''.join(varuint(len(s.encode()))+s.encode() for s in symbols)
    symbol.update({s:varuint(count+i) for i,s in enumerate(symbols)})
    changes = [(8,count_end,varuint(count+len(symbols))),(pos,pos,extra)]
    const = lambda n: b'\x0c'+integer(n)
    string = lambda s: b'\x1d'+symbol[s]
    ge, add, mod, divide = [b'\x05'+symbol[s] for s in operators]
    branch = re.compile(r'^(?:JUMP(?:_IF_FALSE|_IF_TRUE)?|LOGICAL_AND|LOGICAL_OR):')

    matches=[(i-row[1],row[1]) for i,row in enumerate(rows) if row[2].startswith('STRING_CONST: SELECT FrontMinDiameter, FrontMaxDiameter, RearMinDiameter, RearMaxDiameter FROM WHEEL WHERE CarCode=?')]
    assert len(matches)==(2 if digest==MENU_SHA else 1)
    for first,sql_index in matches:
        assert sql_index in (2,8)
        menu=sql_index==8
        length=391 if menu else 123
        start=rows[first][0]
        header=start-8
        items = rows[first:first+length]
        assert [r[1] for r in items] == list(range(length))
        original_op = [ops[r[0]] for r in items]
        replacement = {i:[op] for i,op in enumerate(original_op)}
        sql, limit, factor, guard = (8,48,79,94) if menu else (2,40,51,66)
        assert items[sql][2].startswith('STRING_CONST: SELECT FrontMinDiameter')
        assert original_op[limit] == const(3) and original_op[factor] == const(10)
        replacement[sql] = [string(SHOP_QUERY)]
        replacement[limit] = [const(9)]
        # (((i + 4) % 9) - 4) * 10 keeps the factory category first, including
        # the stock rim that the game inserts into the first category.
        replacement[factor] = [const(4),add,const(9),mod,const(-4),add,const(10)]
        inch = original_op[84 if menu else 56]
        info = original_op[15 if menu else 9]
        get_floor = b'\x25'+integer(1)+symbol['MinimumOffset']
        replacement[guard] = [original_op[guard],inch,info,get_floor,ge,original_op[guard]]
        if menu:
            # Display absolute front/rear inches using the same values as the preview.
            f_eval = b'\x26'+original_op[308][1:]
            r_eval = b'\x26'+original_op[309][1:]
            label = [string('F '), f_eval, const(10), divide, string(' / R '),
                     r_eval, const(10), divide, string(' in'), b'\x1e'+integer(5)]
            assert original_op[308][0] == original_op[309][0] == 0x24
            assert any(name.startswith('STRING_PUSH: StringIndex=3') and ops[addr][0]==label[-1][0] for addr,_,name in rows)
            replacement.update({337+i:[part] for i,part in enumerate(label)})
        positions = [0]
        for i in range(length):
            positions.append(positions[-1]+len(replacement[i]))
        body = bytearray()
        for i,(address,_,name) in enumerate(items):
            for op in replacement[i]:
                if branch.match(name) and op == original_op[i]:
                    old_target, = struct.unpack('<i',op[1:])
                    assert 0 <= old_target <= length
                    op = op[:1]+integer(positions[old_target])
                body.extend(original[address-4:address])
                body.extend(op)
        assert struct.unpack_from('<i',original,header)[0] == length
        changes.extend([(header,header+4,integer(positions[-1])),
                        (start-4,rows[first+length][0]-4,bytes(body))])
    data = bytearray(original)
    for begin,end,value in sorted(changes,reverse=True):
        data[begin:end] = value
    return bytes(data)


def package_entries(raw):
    magic,version,count,toc=struct.unpack_from('<4sIII',raw)
    assert magic==b'MPKG' and version==0 and toc+12*count==len(raw)
    entries=[]
    end=16
    for index in range(count):
        start,data,size=struct.unpack_from('<III',raw,toc+12*index)
        assert start==end and raw[data-1]==0 and start<data<=data+size<=toc
        entries.append((raw[start:data-1].decode('ascii'),raw[data:data+size]))
        end=data+size
    assert end==toc
    return entries


def edit_package(original, component_dir):
    assert hashlib.sha256(original).hexdigest()==PACKAGE_SHA
    entries=package_entries(original)
    payload=bytearray(original[:16])
    table=bytearray()
    changed=set()
    for name,compressed in entries:
        component=name.rsplit('/',1)[-1]
        if component in COMPONENTS:
            plain=zlib.decompress(compressed,-15)
            assert hashlib.sha256(plain).hexdigest()==COMPONENTS[component]
            edited=edit_menu(plain,(component_dir/component).with_suffix('.ad.diss').read_text('utf-8-sig'))
            encoder=zlib.compressobj(9,wbits=-15)
            compressed=encoder.compress(edited)+encoder.flush()
            assert zlib.decompress(compressed,-15)==edited
            changed.add(component)
        start=len(payload)
        payload.extend(name.encode('ascii')+b'\0')
        table.extend(struct.pack('<III',start,len(payload),len(compressed)))
        payload.extend(compressed)
    assert changed==set(COMPONENTS)
    struct.pack_into('<I',payload,12,len(payload))
    result=bytes(payload+table)
    output=package_entries(result)
    assert [a[0] for a in entries]==[b[0] for b in output]
    assert all(a==b for a,b in zip(entries,output) if a[0].rsplit('/',1)[-1] not in COMPONENTS)
    return result
