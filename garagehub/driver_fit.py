"""Per-car native seating targets for the supported Supra CAR5 driver rig."""
import math
import struct

from .errors import GarageError


# CAR5 driver record in the v029 Supra. Groups are the wheel, pedal targets and hip.
# Each triple is in car space, X to the right, Y up, Z toward the rear.
TARGETS = {'steering': 0, 'left_foot': 7, 'right_foot': 10, 'seat': 13}
NATIVE_BLOCK_SHA256 = '1024de95130a5d6d2ad24561ebfd4c1c0f298674867bf88ac7d2b3a3479eb1e3'


def normalize_driver(value):
    if value is None:
        return None
    if not isinstance(value, dict) or not {'seat_metres','reach'} <= set(value) or set(value)-{'seat_metres','reach','targets'}:
        raise GarageError('Set the seat centre and reach for this driver.', 'driver_fit')
    seat, reach = value['seat_metres'], value['reach']
    def number(n):
        return type(n) in (int, float) and math.isfinite(n)
    if not isinstance(seat, (list, tuple)) or len(seat) != 3 or not all(number(n) for n in seat):
        raise GarageError('Driver placement needs three finite coordinates in metres.', 'driver_fit')
    if not all(lo <= n <= hi for n, (lo, hi) in zip(seat, ((-2,2),(-1,2),(-4,4)))):
        raise GarageError('Choose a seat inside the supported cabin coordinates.', 'driver_fit')
    if not number(reach) or not .6 <= reach <= 1.3:
        raise GarageError('Choose a driver reach between 60% and 130%.', 'driver_fit')
    result={'seat_metres': [float(n) for n in seat], 'reach': float(reach)}
    targets=value.get('targets',{})
    if not isinstance(targets,dict) or set(targets)-{'steering','left_foot','right_foot'}:
        raise GarageError('Select the steering wheel or a pedal to change its position.', 'driver_fit')
    for name, point in targets.items():
        if not isinstance(point,(list,tuple)) or len(point)!=3 or not all(number(n) and lo<=n<=hi for n,(lo,hi) in zip(point,((-2,2),(-1,2),(-4,4)))):
            raise GarageError('The steering wheel and pedals need three coordinates inside the cabin limits.', 'driver_fit')
        result.setdefault('targets',{})[name]=[float(n) for n in point]
    return result


def fit_driver(raw, settings=None):
    """Read or place the animated driver, leaving unrelated CAR5 data byte-identical."""
    from hashlib import sha256
    if len(raw) != 17756 or raw[:4] != b'CAR5' or struct.unpack_from('>II', raw, 12) != (1, 24):
        raise GarageError('Driver placement requires the supported Supra car information.', 'driver_fit')
    offset, = struct.unpack_from('>I', raw, 24 + 80)
    if offset+144 > len(raw) or sha256(raw[offset:offset+144]).hexdigest() != NATIVE_BLOCK_SHA256:
        raise GarageError('The native driver rig differs from the reviewed Supra pose.', 'driver_fit')
    original = {name: list(struct.unpack_from('>3f', raw, offset+4*index)) for name,index in TARGETS.items()}
    pose = normalize_driver(settings)
    updated = bytearray(raw)
    if pose is not None:
        for name, index in TARGETS.items():
            target = [t+pose['reach']*(v-base) for t,v,base in zip(pose['seat_metres'],original[name],original['seat'])]
            struct.pack_into('>3f', updated, offset+4*index, *pose.get('targets',{}).get(name,target))
    written = {name: list(struct.unpack_from('>3f', updated, offset+4*index)) for name,index in TARGETS.items()}
    return bytes(updated), {'mode':'placed' if pose else 'inherited','settings':pose,
        'native_targets':written, 'reference_targets':original, 'coordinate_system':'right, up, back in metres',
        'record_offset':offset, 'driver_model':raw[128:132].decode('ascii'), 'helmet_model':raw[140:144].decode('ascii')}
