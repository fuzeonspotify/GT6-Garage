"""Restore the base game's packed race-wheel draw records from the owned v029 asset.

The v029 writer retained the complete native packed geometry and texture data.
Only the four rim records, model bounds and indices into the appended section
need restoring. The expected SHA-256 authenticates every byte of the result.
"""
import struct

from .car_profiles import ASSETS
from .errors import GarageError
from .native import MDL3
from .util import sha256

STOCK_SHA256 = '8afd50cf6c2b068b321878c4aeee5448b785e67a24bd1fa2799ff67969e6fd46'
STOCK_SHOWROOM_SHA256 = 'd7566473f4c9d8162b901330c735d590b42e4fa6be07292c52f9e3f5d9842281'


def restore_stock_showroom_wheel(raw):
    """Recover the preserved factory model and texture streams from the v029 HQ wheel."""
    identity = sha256(raw)
    if identity == STOCK_SHOWROOM_SHA256:
        return raw
    if identity != ASSETS['hq/wheel']:
        raise GarageError('Choose the reviewed Supra showroom wheel.', 'template_identity')
    modified = MDL3(raw)
    original_cut, original_size = 0x8580, 0x1e000
    data = bytearray(raw[:original_cut] + raw[modified.cut:modified.cut+original_size-original_cut])
    # Remove the appended rim, valve, material and draw tables. All packed
    # factory vertices, indices and texture pixels come from the input file.
    fields = {
        4:original_size, 0x14:0x00080008, 0x18:0x00010000,
        0x38:0x740, 0x3c:0xcc0, 0x40:0xd00, 0x6c:original_cut,
        0x8c:0xffff0004, 0x90:0x1560, 0xe4:0x00050002, 0xe8:0x01020001,
        0xec:0xda0, 0xf8:0x1050, 0x124:original_cut, 0x164:original_cut,
        0x184:0x8a00, 0x1ac:original_cut, 0x10a8:1, 0x10b0:0x10d4,
        0x10b4:original_cut, 0x10d4:0x8a80}
    # The overwritten model bounds differ slightly from the packed shape bounds.
    xy = ((0x3d6e0d40,0x3e567f30),(0xbbb213e0,0x3e7173f6),
          (0xbbb21386,0xbe73f675),(0x3d6e0d40,0xbe512d8b))
    bounds = b''.join(struct.pack('>III',x,y,z) for z in (0x3e7071c9,0xbe7071c9) for x,y in xy)
    for corner in range(4):
        model = 0x200 + corner*48
        start = 0x380 + corner*0x70
        fields[model+24] = start+96
        fields[model+28] = 4
        fields[0x1580+0x40*corner] = 0x1780+2*corner
        fields[0x15b0+0x40*corner] = 0x10000
        data[start:start+96] = bounds
    for offset,value in fields.items():
        struct.pack_into('>I',data,offset,value)
    result = bytes(data)
    if sha256(result) != STOCK_SHOWROOM_SHA256:
        raise GarageError('Restored showroom wheel differs from the original GT6 wheel.', 'wheel_assets')
    return result


def restore_stock_race_wheel(raw):
    identity = sha256(raw)
    if identity == STOCK_SHA256:
        return raw
    if identity != ASSETS['race/wheel']:
        raise GarageError('Select the supported Supra race-wheel template.', 'template_identity')
    native = MDL3(raw)
    retained_size = 0xBC00
    extra = native.cut - retained_size
    result = bytearray(raw[:retained_size] + raw[native.cut:])

    def word(offset, value):
        struct.pack_into('>I', result, offset, value)

    word(4, len(result))
    struct.pack_into('>H', result, 0x18, 1)
    word(0x40, 0x1500)  # Original FVF table
    word(0xF8, 0x1E00)  # Original material lookup
    word(0x1FF8, 0x201C)  # Original texture descriptors
    tail_pointers = (0x6C, 0x124, 0x164, 0x184, 0x1A4, 0x28C, 0x1FFC)
    for offset in tail_pointers:
        word(offset, native.u32(offset) - extra)
    for offset in range(0, 7 * 0x20, 0x20):
        word(0x201C + offset, native.u32(native.u32(0x1FF8) + offset) - extra)

    # These four XY corners bound the native rim at both ends of its Z span.
    # Store the original float32 words so the oriented bounds round-trip exactly.
    xy = ((0x3D6E0D40, 0x3E7BBA2E), (0xBC06DE9F, 0x3E6E3674),
          (0xBC06DE71, 0xBE70353C), (0x3D6E0D3F, 0xBE7A4946))
    bounds = b''.join(struct.pack('>3I', x, y, z)
                      for z in (0x3E760F73, 0xBE760F65) for x, y in xy)
    for corner in range(4):
        model = native.u32(0x30) + 48 * corner
        word(model, 0x3EB0913C)
        at = native.u32(model + 20)
        result[at:at + len(bounds)] = bounds
        shape = native.u32(0x38) + 48 * (1 + 3 * corner)
        box = 0xC20 + 0x1E0 * corner
        struct.pack_into('>12I', result, shape, 0x1001FFFF, corner << 16,
                         0, 0, 0, 0, 0, 0, 0, 0xFF000000, box, box + 0x60)
    restored = bytes(result)
    if sha256(restored) != STOCK_SHA256:
        raise GarageError('Stock race-wheel restoration failed its native file check.', 'template_identity')
    MDL3(restored).validate()
    return restored
