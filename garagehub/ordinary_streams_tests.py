"""Serialized fixtures for final generated-body validation, no game assets."""
from __future__ import annotations

import struct
import unittest
import zlib
import numpy as np

from .errors import GarageError
from .native import corners
from .ordinary_streams import validate_generated_streams
from .util import sha256


def put32(data, at, value):
    struct.pack_into('>I', data, at, value)


class SerializedBodyFixture:
    """A final MDL3/stream pair with one generated and one opaque binding.

Metadata, CPU tables, geometry and two stream-group records are authored
independently from the production editor/attachment writer. Each serialization
uses real raw-deflate CPU spans, a raw GPU span and an uninterpreted extra group.
"""
    def __init__(self):
        self.model = bytearray(4096)
        m = self.model
        m[:4] = b'MDL3'
        struct.pack_into('>IIH', m, 4, len(m), 0, 14)
        put32(m,0x6c,len(m));put32(m,0x40,256);put32(m,0x38,512)
        struct.pack_into('>H',m,0x18,1);struct.pack_into('>H',m,0x14,2)
        struct.pack_into('>III',m,256,960,0,800);m[280:282]=bytes((3,24))
        for at, text in ((900,'position'),(920,'normal'),(940,'map12'),(960,'Flex'),
                         (1300,'/'),(1320,'variant')):
            b=text.encode()+b'\0';m[at:at+len(b)]=b
        struct.pack_into('>I4B',m,800,900,0,3,2,0)
        struct.pack_into('>I4B',m,808,920,12,1,6,0)
        struct.pack_into('>I4B',m,816,940,16,2,2,0)
        struct.pack_into('>Hhh',m,512,0x1809,0,0)
        put32(m,520,3);put32(m,532,3);struct.pack_into('>H',m,550,1)
        # This inherited packed/opaque record is intentionally not decoded.
        struct.pack_into('>Hhh',m,560,0x6001,-1,-1);put32(m,604,0xffff0000)
        put32(m,0xdc,1200);put32(m,1200,2);put32(m,1208,1232)
        struct.pack_into('>III',m,1232,1300,1,1360)
        struct.pack_into('>III',m,1244,1320,1,1440)
        struct.pack_into('>HHI',m,1368,2,1,1408)
        struct.pack_into('>HHI',m,1448,1,0,1488)
        self.cpu=bytearray(122880);self.gpu=bytes(range(256));self.variant=b'OPAQUE OTHER GROUP'
        struct.pack_into('>III',self.cpu,4,len(self.cpu),len(self.cpu)+len(self.gpu),len(self.gpu))
        struct.pack_into('>HHII',self.cpu,20,2,1,32,96)
        self.vp,self.ip,self.bp=108160,108240,108256
        struct.pack_into('>8I',self.cpu,32,0,self.vp,self.ip,self.bp,0,0,0,0)
        struct.pack_into('>8I',self.cpu,64,1,0,0,0,400000,500000,600000,1)
        points=np.array([[0.,0.,0.],[1.,0.,0.],[0.,1.,.5]])
        uv=np.array([[0.,0.],[1.5,-.25],[0.,1.]])
        vertex=b''.join(struct.pack('>3fI2f',*p,0,*t) for p,t in zip(points,uv))
        index=struct.pack('>3H',0,1,2)
        self.cpu[self.vp:self.vp+len(vertex)]=vertex
        self.cpu[self.ip:self.ip+len(index)]=index
        self.cpu[self.bp:self.bp+96]=corners([points.min(0),points.max(0)])
        self.emitted=[{'id':0,'streamed':True,'vertices':3,'triangles':1,
                      'material':0,'fvf':0,'vertex_sha256':sha256(vertex),'index_sha256':sha256(index)}]

    def serialized(self, compressed=True):
        m=bytearray(self.model);parts=[];offset=0
        for i,raw in enumerate((bytes(self.cpu[:61440]),bytes(self.cpu[61440:]),self.gpu)):
            if compressed and i<2:
                co=zlib.compressobj(9,zlib.DEFLATED,-15);stored=co.compress(raw)+co.flush();flags=len(stored)|0x80000000
            else:stored=raw;flags=len(stored)
            struct.pack_into('>II',m,1408+8*i,flags,len(raw));parts.append(stored);offset+=len(stored)
        put32(m,1364,0);put32(m,1444,offset)
        struct.pack_into('>II',m,1488,len(self.variant),len(self.variant))
        return bytes(m),b''.join(parts)+self.variant

    def validate(self):
        return validate_generated_streams(*self.serialized(),self.emitted)


class OrdinaryStreamTests(unittest.TestCase):
    def test_serialized_valid_raw_and_compressed_shapes_preserve_inputs_and_scope(self):
        for compressed in (False,True):
            with self.subTest(compressed=compressed):
                f=SerializedBodyFixture();raw,stream=map(bytearray,f.serialized(compressed))
                before=(bytes(raw),bytes(stream))
                result=validate_generated_streams(raw,stream,f.emitted)
                self.assertTrue(result['passed']);self.assertEqual(result['shape_ids'],[0])
                self.assertEqual(result['other_main_bindings_not_validated'],[1])
                self.assertEqual(result['other_groups_geometry_not_validated'],['variant/0'])
                self.assertFalse(result['runtime_verified']);self.assertFalse(result['inputs_changed'])
                self.assertEqual((bytes(raw),bytes(stream)),before)

    def test_missing_or_duplicate_selected_binding_fails(self):
        for duplicate in (False,True):
            f=SerializedBodyFixture();put32(f.cpu,64 if duplicate else 32,0 if duplicate else 9)
            with self.subTest(duplicate=duplicate),self.assertRaisesRegex(GarageError,'exactly one main-stream binding'):
                f.validate()

    def test_final_counts_must_match_emission_and_triangle_list(self):
        for at,value,width in ((520,4,4),(532,4,4),(550,2,2)):
            f=SerializedBodyFixture();struct.pack_into('>I' if width==4 else '>H',f.model,at,value)
            with self.subTest(offset=at),self.assertRaisesRegex(GarageError,'counts'):
                f.validate()

    def test_serialized_index_cannot_reference_an_absent_vertex(self):
        f=SerializedBodyFixture();struct.pack_into('>H',f.cpu,f.ip+4,3)
        with self.assertRaisesRegex(GarageError,'index refers beyond'):
            f.validate()

    def test_serialized_positions_and_uvs_must_be_finite(self):
        for offset,kind in ((0,'positions'),(16,'UVs')):
            f=SerializedBodyFixture();struct.pack_into('>f',f.cpu,f.vp+offset,float('nan'))
            with self.subTest(kind=kind),self.assertRaisesRegex(GarageError,kind):
                f.validate()

    def test_unknown_or_secondary_fvf_layout_is_not_guessed(self):
        for offset,value in ((281,28),(807,1),(822,6),(820,12),(280,2)):
            f=SerializedBodyFixture();f.model[offset]=value
            with self.subTest(offset=offset),self.assertRaisesRegex(GarageError,'24-byte'):
                f.validate()

    def test_selected_payloads_must_remain_aligned_in_cpu_after_prefix(self):
        for field,value in ((4,108161),(8,64),(12,122864),(4,122880)):
            f=SerializedBodyFixture();put32(f.cpu,32+field,value)
            with self.subTest(field=field,value=value),self.assertRaises(GarageError) as caught:
                f.validate()
            self.assertEqual(caught.exception.code,'ordinary_stream_contract')

    def test_direct_packed_or_auxiliary_selected_binding_is_rejected(self):
        for location,offset in (('model',524),('model',556),('cpu',48)):
            f=SerializedBodyFixture();put32(getattr(f,location),offset,128)
            with self.subTest(location=location,offset=offset),self.assertRaises(GarageError):
                f.validate()

    def test_bounds_must_be_finite_ordered_and_enclose_serialized_positions(self):
        for mutation,pattern in (('nan','bounds contain'),('order','emitter order'),('small','do not enclose')):
            f=SerializedBodyFixture()
            if mutation=='nan':struct.pack_into('>f',f.cpu,f.bp,float('nan'))
            elif mutation=='order':
                a=bytes(f.cpu[f.bp:f.bp+12]);f.cpu[f.bp:f.bp+12]=f.cpu[f.bp+12:f.bp+24];f.cpu[f.bp+12:f.bp+24]=a
            else:f.cpu[f.bp:f.bp+96]=corners([[0,0,0],[.5,.5,.25]])
            with self.subTest(mutation=mutation),self.assertRaisesRegex(GarageError,pattern):
                f.validate()

    def test_generated_vertex_index_or_bounds_payloads_cannot_overlap(self):
        f=SerializedBodyFixture();put32(f.cpu,40,f.vp+16)
        with self.assertRaisesRegex(GarageError,'overlaps generated'):
            f.validate()

    def test_source_receipt_is_required_and_cannot_duplicate_ids(self):
        f=SerializedBodyFixture()
        for emitted in ([],f.emitted*2,[dict(f.emitted[0],streamed=False)],
                        [dict(f.emitted[0],vertex_sha256='missing')]):
            with self.subTest(emitted=emitted),self.assertRaises(GarageError):
                validate_generated_streams(*f.serialized(),emitted)

    def test_valid_but_changed_geometry_fails_source_hash(self):
        f=SerializedBodyFixture();struct.pack_into('>f',f.cpu,f.vp+16,.25)
        with self.assertRaisesRegex(GarageError,'geometry bytes differ'):
            f.validate()

    def test_decoded_allocation_must_match_serialized_header(self):
        f=SerializedBodyFixture();put32(f.cpu,4,len(f.cpu)+128)
        with self.assertRaisesRegex(GarageError,'declared CPU/GPU allocation'):
            f.validate()

    def test_selected_shape_must_exist_in_final_metadata(self):
        f=SerializedBodyFixture();f.emitted[0]['id']=2
        with self.assertRaisesRegex(GarageError,'ID leaves the serialized shape table'):
            f.validate()

