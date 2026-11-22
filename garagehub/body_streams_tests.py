"""Portable synthetic full-path fixture for reviewed native CPU/GPU stream attachment.

No proprietary game files, Blender, RPCS3 or installed car assets required.
"""
from __future__ import annotations
import struct
import unittest

from .body_streams import attach, groups
from .native import MDL3
from .errors import GarageError


def u32(b, p, x):
    struct.pack_into('>I', b, p, x)


def row(b, p, values):
    struct.pack_into('>8I', b, p, *values)


class FixtureEditor:
    """Only the metadata allocation/write API required by actual attach()."""
    def __init__(self, raw, replacement):
        self.raw = raw
        self.m = MDL3(raw)
        self.b = bytearray(raw)
        self.stream_shapes = [replacement]
        self.disabled_static_shapes = set()

    def alloc(self, data, alignment=16):
        self.b.extend(bytes(-len(self.b) % alignment))
        at = len(self.b)
        self.b.extend(data)
        return at

    def put(self, at, data):
        if not 0 <= at <= len(self.b)-len(data):
            raise ValueError('Fixture metadata write outside allocation')
        self.b[at:at+len(data)] = data

    def u32(self, at, value):
        self.put(at, struct.pack('>I', value))

    def serialized(self):
        b = bytearray(self.b)
        u32(b, 4, len(b))
        u32(b, 0x6c, len(b))
        return bytes(b)


def native_fixture(cpu_size=163840, replacement_bytes=36):
    # MDL3 envelope, one native FVF, three streaming shape entries, one
    # main CPU+GPU group, and real serialized stream record headers.
    mdl = bytearray(2304)
    mdl[:4] = b'MDL3'
    struct.pack_into('>H', mdl, 12, 14)
    u32(mdl, 4, len(mdl))
    u32(mdl, 0x6c, len(mdl))
    struct.pack_into('>H', mdl, 0x18, 1)
    u32(mdl, 0x40, 256)
    struct.pack_into('>III', mdl, 256, 820, 1, 600)
    mdl[256+24:256+26] = bytes((1, 12))
    struct.pack_into('>I4B', mdl, 600, 800, 0, 3, 2, 0)
    mdl[800:809] = b'position\x00'
    mdl[820:825] = b'Flex\x00'
    u32(mdl, 0x38, 896)
    struct.pack_into('>H', mdl, 0x14, 3)
    struct.pack_into('>hII', mdl, 896+2*48+2, 0, 0, 0)  # overwritten below
    u32(mdl, 896+2*48+8, 3)
    u32(mdl, 896+2*48+20, 3)
    u32(mdl, 0xdc, 1200)
    u32(mdl, 1200, 1)
    u32(mdl, 1208, 1232)
    u32(mdl, 1232, 1300)
    u32(mdl, 1236, 1)
    u32(mdl, 1240, 1360)
    mdl[1300:1302] = b'/\x00'
    u32(mdl, 1364, 0)
    struct.pack_into('>HHI', mdl, 1368, 3, 1, 1408)

    cpu = bytearray(cpu_size)
    gpu = bytes((i * 19 + 7) % 256 for i in range(384))
    struct.pack_into('>III', cpu, 4, cpu_size, cpu_size+len(gpu), len(gpu))
    struct.pack_into('>HHII', cpu, 20, 3, 1, 32, 128)
    u32(cpu, 132, 2048)  # legal retained prefix texture pointer

    row(cpu, 32, (0, 1024, 2048, 4096, 0, 0, 0, 0))
    row(cpu, 64, (1, 116000, 116160, 116256, 0, 0, 0, 0))
    row(cpu, 96, (2, 121000, 121128, 121200, 0, 0, 0, 0))
    cpu[121000:121036] = b'V'*36
    cpu[121128:121134] = b'I'*6
    cpu[121200:121296] = b'B'*96

    chunks = [bytes(cpu[:61440]), bytes(cpu[61440:122880]),
              bytes(cpu[122880:]), gpu]
    for i, chunk in enumerate(chunks):
        struct.pack_into('>II', mdl, 1408+i*8, len(chunk), len(chunk))
    replacement = {'id': 1, 'vertex': b'v'*replacement_bytes,
                   'index': b'\x00\x00\x00\x01\x00\x02', 'bounds': b'b'*96}
    return bytes(mdl), b''.join(chunks), cpu, gpu, replacement


def rebuilt(editor, source_stream):
    output, report = attach(editor, source_stream)
    parsed = groups(editor.serialized(), output)
    main = next(g for g in parsed if g['label'] == '//0')
    cpu = b''.join(r['raw'] for r in main['records'][:main['na']])
    other = b''.join(r['stored'] for r in main['records'][main['na']:])
    return cpu, other, output, report, main


class BodyStreamAttachTests(unittest.TestCase):
    def setUp(self):
        self.raw, self.stream, self.old, self.gpu, self.replacement = native_fixture()

    def test_full_attach_preserves_allocation_prefix_binding_order_and_gpu(self):
        editor = FixtureEditor(self.raw, self.replacement)
        cpu, gpu, output, report, main = rebuilt(editor, self.stream)
        self.assertEqual(len(cpu), len(self.old))
        self.assertEqual(gpu, self.gpu)
        self.assertEqual(report['main_cpu_bytes_after'], len(self.old))
        self.assertTrue(report['native_allocation_preserved'])
        self.assertEqual(main['nb'], 1)
        self.assertEqual([struct.unpack_from('>I', cpu, 32+32*i)[0] for i in range(3)], [0,1,2])
        self.assertEqual(cpu[32:64], self.old[32:64])
        self.assertEqual(cpu[128:108160], self.old[128:108160])
        self.assertEqual(cpu[:32], self.old[:32])
        self.assertEqual(report['retained_inactive_static_shapes'], [2])
        old_retained = (self.old[121000:121036],self.old[121128:121134],self.old[121200:121296])
        for sid, expected in [(1,(self.replacement['vertex'],self.replacement['index'],self.replacement['bounds'])),
                              (2,old_retained)]:
            a = struct.unpack_from('>8I', cpu, 32+32*sid)
            self.assertEqual(tuple(cpu[p:p+len(part)] for p,part in zip(a[1:4],expected)), expected)
        self.assertEqual(report['bindings'][0]['shape'], 1)
        self.assertEqual(report['bindings'][1]['shape'], 2)
        self.assertEqual(len(groups(editor.serialized(), output)), 1)

    def test_large_replacement_fails_instead_of_growing_native_allocation(self):
        raw, stream, old, gpu, replacement = native_fixture(replacement_bytes=90000)
        with self.assertRaisesRegex(GarageError, 'exceeds this native stream allocation'):
            attach(FixtureEditor(raw,replacement),stream)

    def test_unreviewed_shape_binding_fails_without_emitting_output(self):
        shape = dict(self.replacement,id=99)
        with self.assertRaisesRegex(GarageError, 'unsupported stream bindings'):
            attach(FixtureEditor(self.raw,shape),self.stream)

    def test_duplicate_stream_shape_rows_rejected(self):
        altered = bytearray(self.stream)
        u32(altered, 64, 0)
        with self.assertRaisesRegex(GarageError, 'Duplicate native streamed shape'):
            attach(FixtureEditor(self.raw,self.replacement),bytes(altered))

    def test_protected_unknown_static_payload_refused(self):
        altered = bytearray(self.stream)
        u32(altered, 96+16, 88)  # auxiliary pointer/state must not be discarded
        with self.assertRaisesRegex(GarageError, 'Unclassified non-static stream payload'):
            attach(FixtureEditor(self.raw,self.replacement),bytes(altered))

    def test_unparsed_or_overlapping_spans_rejected(self):
        with self.assertRaisesRegex(GarageError, 'Unparsed bytes'):
            groups(self.raw,self.stream+b'trailing')
        mdl = bytearray(self.raw)
        u32(mdl, 1364, 1)
        with self.assertRaisesRegex(GarageError, 'Overlapping or missing body spans'):
            groups(bytes(mdl),self.stream+b'\x00')


class NativeResourceReuseTests(unittest.TestCase):
    def test_existing_encoded_image_is_reused_without_gpu_growth(self):
        import io
        from PIL import Image
        from .native_editor import Editor
        from .util import sha256
        im = Image.new('RGBA',(4,4),(25,75,125,255))
        out = io.BytesIO()
        im.save(out,format='DDS',pixel_format='DXT1')
        payload = out.getvalue()[128:]
        key = (sha256(payload),4,4,'DXT1')
        editor = Editor.__new__(Editor)
        editor.texture_edge = None
        editor.image_cache = {key: 7}
        editor.image_count = 8
        editor.reused_images = []
        editor.images = []
        editor.tail = bytearray(b'RETAINED GPU SOURCE')
        iid = editor.image(im,'reused-texture-fixture')
        self.assertEqual(iid,7)
        self.assertEqual(editor.tail,b'RETAINED GPU SOURCE')
        self.assertEqual(editor.images,[])
        self.assertEqual(editor.reused_images[0]['sha256'],key[0])

    def test_equivalent_sampler_is_reused_not_appended_twice(self):
        from .native_editor import Editor
        editor = Editor.__new__(Editor)
        image = bytearray(32)
        image[9:11] = bytes((134,1))
        struct.pack_into('>HH',image,12,4,4)
        base = bytearray(68)
        editor.image_rows = [image]
        editor.image_count = 1
        editor.images = []
        editor.param_count = 1
        editor.param_rows = [base]
        editor.sampler_cache = {bytes(base[:64]):0}
        editor.string = lambda text: 700
        first = editor.sampler(0,0)
        self.assertEqual(first,1)
        self.assertEqual(editor.sampler(0,0),first)
        self.assertEqual(len(editor.param_rows),2)
        self.assertEqual(struct.unpack_from('>I',editor.param_rows[1],56)[0],0)
