"""Stand-alone, single-paint OBJ to GT6 v029 body tutorial. Python standard library only."""
import argparse
import hashlib
import math
from pathlib import Path
import shutil
import struct
import tempfile
import zlib

DRAWS = {'hq': (8, (138, 141)), 'race': (7, (132, 135))}
HASHES = {
    'info': '03f5c8428a055597781ea532759eb3d4716723d56156f45aadeaf8e11774bdd6',
    'interior': '351e5991857562295f55f8d22f2b6925057c5f92c23716478296124bd0eec5da',
    'hq/body': '36fc62925952bdd60afce3816e10f9acd7f40f9f0c189f22e58022680807c831',
    'hq/body_s': 'e885f576d1b60eda59d5602b30be970e6db4d32ce48915f0239295c3382b7bdc',
    'hq/wheel': '4aa8cbae8423b819772a394da0112274d6e7936853392d8982991d1f47274fd7',
    'race/body': 'cecdb582a14871cae164d95d30bf9df48dce4247d275d967d250b0e6f4f1941c',
    'race/body_s': '911a474a37213b141efccf12c9f77cbcf273087c60e69036830c9192079fb566',
    'race/wheel': 'c8dcbfadfcd55bb83538b6f74374335cc57b5cc651d6e844355fc60c8daf38bb',
}
CORNERS = ((1,0,1),(1,0,0),(1,1,0),(1,1,1),(0,0,1),(0,0,0),(0,1,0),(0,1,1))


def check(ok, message):
    if not ok:
        raise ValueError(message)


def u32(data, offset):
    return struct.unpack_from('>I', data, offset)[0]


def u16(data, offset):
    return struct.unpack_from('>H', data, offset)[0]


def geometry(path):
    points, normals, texcoords, vertices, indices, keys = [], [], [], [], [], {}

    def ref(values, field):
        i = int(field)
        check(i != 0 and -len(values) <= i <= len(values), 'Invalid OBJ index')
        return values[i - 1 if i > 0 else i]

    for line in path.read_text(encoding='utf-8-sig').splitlines():
        words = line.split('#', 1)[0].split()
        if not words:
            continue
        kind, values = words[0], words[1:]
        if kind in ('v', 'vn', 'vt'):
            count = 2 if kind == 'vt' else 3
            row = tuple(map(float, values[:count]))
            check(len(row) == count and all(math.isfinite(v) for v in row), 'Non-finite OBJ data')
            {'v': points, 'vn': normals, 'vt': texcoords}[kind].append(row)
        elif kind == 'f':
            check(len(values) == 3, 'Triangulate every face in Blender before exporting.')
            for value in values:
                fields = value.split('/')
                check(len(fields) == 3 and fields[2], 'Export normals for every OBJ corner.')
                p = ref(points, fields[0])
                uv = ref(texcoords, fields[1]) if fields[1] else (0.5, 0.5)
                n = ref(normals, fields[2])
                length = math.hypot(*n)
                check(length > 1e-12, 'Zero OBJ normal')
                q = [max(-limit-1, min(limit, round(v / length * 32767 / step)))
                     for v, limit, step in zip(n, (1023,1023,511), (32,32,64))]
                word = (q[0] & 2047) | ((q[1] & 2047) << 11) | ((q[2] & 1023) << 22)
                vertex = struct.pack('>3fI2f', *p, word, uv[0], 1 - uv[1])
                if vertex not in keys:
                    keys[vertex] = len(vertices)
                    vertices.append(vertex)
                indices.append(keys[vertex])
    check(indices and len(indices) <= 60000 and len(vertices) <= 11299,
          'Use a triangulated shell with at most 20,000 faces and 11,299 split vertices.')
    positions = [struct.unpack_from('>3f', v) for v in vertices]
    bounds = [tuple(fn(axis) for axis in zip(*positions)) for fn in (min, max)]
    return b''.join(vertices), struct.pack(f'>{len(indices)}H', *indices), bounds


def groups(body, stream):
    sep = u32(body, 0xdc)
    base = u32(body, sep + 8)
    result = []

    def add(name, header):
        offset = u32(body, header + 4)
        count, gpu, table = struct.unpack_from('>HHI', body, header + 8)
        spans = []
        for i in range(count + gpu):
            address = table + i * 8
            flags, expanded = struct.unpack_from('>II', body, address)
            size = flags & 0x7fffffff
            stored = stream[offset:offset + size]
            check(len(stored) == size, 'Truncated native stream')
            spans.append((address, flags, expanded, stored))
            offset += size
        result.append((u32(body, header + 4), name, header, count, spans))

    for i in range(u32(body, sep)):
        nameptr, count, first = struct.unpack_from('>III', body, base + i * 12)
        name = body[nameptr:body.index(0, nameptr)].decode('ascii')
        for j in range(count):
            header = first + j * 24
            add(f'{name}/{j}', header)
            for k in range(u32(body, header + 16)):
                add('extra', u32(body, header + 20) + k * 16)
    return sorted(result)


def expanded(span):
    _, flags, size, stored = span
    raw = zlib.decompress(stored, -15) if flags >> 31 else stored
    check(len(raw) == size, 'Bad expanded span size')
    return raw


def write_body(folder, lod, vertex, index, bounds):
    file = folder / lod / 'body'
    body = bytearray(file.read_bytes())
    stream_file = file.with_name('body_s')
    layout = groups(body, stream_file.read_bytes())
    _, _, _, count, spans = next(x for x in layout if x[1] == '//0')
    cpu = bytearray(b''.join(expanded(s) for s in spans[:count]))
    slot, tails = DRAWS[lod]
    shape = u32(body, 0x38) + 48 * slot
    table = u32(cpu, 24)
    row = next(table + 32 * i for i in range(u16(cpu, 20)) if u32(cpu, table + 32 * i) == slot)
    vp, ip, bp = struct.unpack_from('>III', cpu, row + 4)
    check(len(vertex) <= u32(body, shape + 8) * 24 and len(index) <= u32(body, shape + 20) * 2,
          'Mesh exceeds the original paint slot.')
    cpu[vp:vp + len(vertex)] = vertex
    cpu[ip:ip + len(index)] = index
    for i, corner in enumerate(CORNERS):
        struct.pack_into('>3f', cpu, bp + 12*i, *(bounds[side][axis] for axis,side in enumerate(corner)))
    struct.pack_into('>I', body, shape + 8, len(vertex)//24)
    struct.pack_into('>I', body, shape + 20, len(index)//2)
    struct.pack_into('>H', body, shape + 38, len(index)//6)

    for mid, context, delta in zip((0,5), (0,17), tails):
        model = u32(body, 0x30) + mid * 48
        cmd, size = struct.unpack_from('>II', body, model + 24)
        tail = bytes(body[cmd + delta:cmd + size])
        check(tail[0] == 14 and tail[-1] == 0, 'Unexpected mechanical program.')
        old = list(struct.iter_unpack('>3f', body[u32(body, model+20):u32(body, model+20)+96]))
        check(all(min(p[a] for p in old)-1e-5 <= bounds[0][a] <= bounds[1][a] <= max(p[a] for p in old)+1e-5
                  for a in range(3)), 'Fit your mesh inside the donor body bounds in Blender.')
        program = (bytes((59,95,17)) if mid == 5 else b'') + bytes((59,slot,context)) + tail
        body[cmd:cmd+size] = program.ljust(size,b'\0')
        struct.pack_into('>I', body, model+28, len(program))

    output = bytearray()
    for _, name, header, na, entries in layout:
        struct.pack_into('>I', body, header+4, len(output))
        cursor = 0
        for i,(address,flags,size,stored) in enumerate(entries):
            if name == '//0' and i < na:
                raw = bytes(cpu[cursor:cursor+size])
                compressor = zlib.compressobj(9, zlib.DEFLATED, -15)
                stored = compressor.compress(raw) + compressor.flush()
                check(zlib.decompress(stored,-15) == raw, 'CPU span failed to round trip.')
                flags = len(stored) | 0x80000000
                cursor += size
            struct.pack_into('>II',body,address,flags,size)
            output.extend(stored)
    file.write_bytes(body)
    stream_file.write_bytes(output)
    _,_,_,na,rows = next(x for x in groups(file.read_bytes(),stream_file.read_bytes()) if x[1]=='//0')
    reopened = b''.join(expanded(s) for s in rows[:na])
    check(reopened == cpu and reopened[vp:vp+len(vertex)] == vertex and reopened[ip:ip+len(index)] == index,
          'The final body stream differs from the OBJ.')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('obj', type=Path)
    parser.add_argument('template', type=Path, help='Folder directly containing info, interior, hq, race')
    parser.add_argument('input', type=Path, help='A new, empty output path for GTToolsSharp pack -p')
    args = parser.parse_args()
    check(not args.input.exists(), 'Choose a new output folder.')
    buffers = geometry(args.obj)
    args.input.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=args.input.parent) as tmp:
        inputs = Path(tmp)/'Input'
        folder = inputs/'car/0004/0006'
        for name, digest in HASHES.items():
            source = args.template/name
            check(hashlib.sha256(source.read_bytes()).hexdigest() == digest, f'Unsupported donor {name}.')
            target = folder/name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source,target)
        for lod in DRAWS:
            write_body(folder,lod,*buffers)
        inputs.rename(args.input)
    print(f'Wrote both body levels and the remaining native car files under {args.input}.')


if __name__ == '__main__':
    main()
