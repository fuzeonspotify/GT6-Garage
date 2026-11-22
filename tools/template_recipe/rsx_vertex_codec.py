from __future__ import annotations

import sys

import hashlib, json, math, statistics, struct

from pathlib import Path

def signed(value: int, bits: int) -> int:
    return value - (1 << bits) if value & (1 << (bits - 1)) else value

def decode_cmp32_be(raw: bytes) -> tuple[float, float, float]:
    """Exact RPCS3 RSX CMP32 fetch, before the game's vertex shader."""
    if len(raw) != 4: raise ValueError("CMP32 requires exactly four bytes")
    word = int.from_bytes(raw, "big")
    q = (signed(word & 2047, 11), signed((word >> 11) & 2047, 11),
         signed((word >> 22) & 1023, 10))
    return (q[0] * 32 / 32767.0, q[1] * 32 / 32767.0, q[2] * 64 / 32767.0)

def encode_cmp32_be(values, *, normalize=False) -> bytes:
    """Nearest representable CMP32. Optional vector normalization is explicit.

    Includes the native -32768/32767 endpoint. The quantizer used by Sony's
    original asset builder is not claimed; decoded native values round-trip.
    """
    v = tuple(float(x) for x in values)
    if len(v) != 3 or not all(math.isfinite(x) for x in v):
        raise ValueError("Expected three finite values")
    if normalize:
        length = math.sqrt(sum(x*x for x in v))
        if length < 1e-20: raise ValueError("Cannot normalize a zero vector")
        v = tuple(x / length for x in v)
    if any(abs(x) > 1.0001 for x in v):
        raise ValueError("CMP normal components must be within the normalized range")
    q = [max(lo, min(hi, round(x * 32767.0 / shift)))
         for x, lo, hi, shift in zip(v, (-1024,-1024,-512), (1023,1023,511), (32,32,64))]
    return ((q[0] & 2047) | ((q[1] & 2047) << 11) | ((q[2] & 1023) << 22)).to_bytes(4,"big")
