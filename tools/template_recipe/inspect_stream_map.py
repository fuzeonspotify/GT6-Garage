import sys

from pathlib import Path

import json, struct, zlib, hashlib

def decode_span(chunk, expanded, compressed):
    if not compressed:
        if len(chunk) != expanded: raise ValueError("Uncompressed size mismatch")
        return chunk, None
    good=[]
    for wbits in (-15,15,31):
        try:
            dec=zlib.decompressobj(wbits)
            raw=dec.decompress(chunk,expanded+1)
            if dec.eof and not dec.unused_data and not dec.unconsumed_tail and len(raw)==expanded:
                good.append((raw,wbits))
        except zlib.error: pass
    if len(good)!=1: raise ValueError("Nonunique/failed span decompression")
    return good[0]
