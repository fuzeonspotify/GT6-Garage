from __future__ import annotations

import argparse

import hashlib

import json

import math

from pathlib import Path

import re

import struct

class MDL3:
    def __init__(self, path: Path):
        self.path = path
        self.data = path.read_bytes()
        assert self.data[:4] == b'MDL3', 'Expected big-endian MDL3'
        self.range(0, 0xe4)
        assert self.u16(0x0c) == 14, 'Only MDL3 version14 has been validated'
        self.model_count = self.u16(0x10)
        self.shape_count = self.u16(0x14)
        self.fvf_count = self.u16(0x18)
        assert self.shape_count < 65536 and self.fvf_count < 1024
        self.shape_pointer = self.u32(0x38)
        self.fvf_pointer = self.u32(0x40)
        self.range(self.shape_pointer, self.shape_count * 0x30)
        self.range(self.fvf_pointer, self.fvf_count * 0x78)
        # The key table is sorted by name. Its second word is the shape ID.
        self.shape_names = {}
        kp = self.u32(0x3c)
        self.range(kp, self.u16(0x16) * 8)
        for i in range(self.u16(0x16)):
            name_pointer, shape_id = self.unpack('II', kp + i * 8)
            assert shape_id < self.shape_count and shape_id not in self.shape_names
            self.shape_names[shape_id] = self.string(name_pointer)
        self.fvfs = []
        for i in range(self.fvf_count):
            p = self.fvf_pointer + i * 0x78
            fp = self.u32(p + 8)
            count, stride = self.data[p + 0x18:p + 0x1a]
            self.range(fp, count * 8)
            fields = []
            for j in range(count):
                q = fp + j * 8
                offset, components, storage, stream = self.data[q + 4:q + 8]
                fields.append(dict(name=self.string(self.u32(q)), offset=offset,
                                   components=components, storage=storage, stream=stream))
            self.fvfs.append(dict(id=i, name=self.string(self.u32(p)), stride=stride, fields=fields))

    def range(self, p, size):
        assert 0 <= p <= len(self.data) and 0 <= size <= len(self.data) - p, (p, size, len(self.data))

    def unpack(self, fmt, p):
        self.range(p, struct.calcsize('>' + fmt))
        return struct.unpack_from('>' + fmt, self.data, p)

    def u16(self, p):
        return self.unpack('H', p)[0]

    def u32(self, p):
        return self.unpack('I', p)[0]

    def string(self, p):
        self.range(p, 1)
        end = self.data.index(0, p)
        assert end - p < 16384
        return self.data[p:end].decode('ascii')

    def shape(self, shape_id):
        p = self.shape_pointer + shape_id * 0x30
        flags, fvf, material = self.unpack('Hhh', p)
        nv, vp = self.unpack('II', p + 8)
        ni, ip = self.unpack('II', p + 0x14)
        nt = self.u16(p + 0x26)
        result = dict(id=shape_id, record_offset=p, name=self.shape_names.get(shape_id),
                      flags=flags, fvf=fvf, material=material, vertex_count=nv,
                      vertex_pointer=vp, index_count=ni, index_pointer=ip,
                      triangle_count_field=nt, packed_reference=self.u32(p + 0x2c))
        if fvf < 0:
            result['status'] = 'packed_mesh_not_decoded'
            return result
        if not vp or not ip:
            result['status'] = 'missing_separate_buffer'
            return result
        assert fvf < len(self.fvfs)
        desc = self.fvfs[fvf]
        position = [f for f in desc['fields'] if f['name'] == 'position']
        assert len(position) == 1
        field = position[0]
        assert (field['components'], field['storage'], field['stream']) == (3, 2, 0), field
        assert field['offset'] + 12 <= desc['stride']
        self.range(vp, nv * desc['stride'])
        self.range(ip, ni * 2)
        assert nv <= 65536 and ni % 3 == 0 and ni // 3 == nt
        vertices = [self.unpack('fff', vp + i * desc['stride'] + field['offset']) for i in range(nv)]
        assert all(math.isfinite(c) for v in vertices for c in v)
        indices = self.unpack('H' * ni, ip)
        assert all(i < nv for i in indices)
        triangles = [indices[i:i + 3] for i in range(0, ni, 3)]
        bounds = [[min(v[k] for v in vertices) for k in range(3)],
                  [max(v[k] for v in vertices) for k in range(3)]]
        degenerates = 0
        for a, b, c in triangles:
            u = [vertices[b][k] - vertices[a][k] for k in range(3)]
            v = [vertices[c][k] - vertices[a][k] for k in range(3)]
            cross = (u[1] * v[2] - u[2] * v[1], u[2] * v[0] - u[0] * v[2], u[0] * v[1] - u[1] * v[0])
            degenerates += sum(x*x for x in cross) < 1e-16
        result.update(status='direct_geometry_decoded', vertex_stride=desc['stride'],
                      source_bounds=bounds, degenerate_triangles=degenerates,
                      vertices=vertices, triangles=triangles)
        return result
