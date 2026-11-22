"""Synthetic regressions for independently observed packed-stream contracts."""
from __future__ import annotations

import struct
import unittest

from .errors import GarageError
from .packed_streams import audit_packed_streams


def make_packed_fixture():
    model = bytearray(0x300)
    model[:4] = b'MDL3'
    struct.pack_into('>I', model, 4, len(model))
    struct.pack_into('>H', model, 12, 14)
    struct.pack_into('>H', model, 0x14, 2)
    struct.pack_into('>I', model, 0x38, 0x140)
    struct.pack_into('>I', model, 0x6c, len(model))
    struct.pack_into('>I', model, 0xd0, 0xe4)
    model[0xe4:0xe8] = b'PMSH'
    struct.pack_into('>H', model, 0xe4 + 16, 2)
    struct.pack_into('>I', model, 0xe4 + 24, 0x220)
    for sid in range(2):
        shape, ref, mesh = 0x140 + sid * 48, 0x1a0 + sid * 64, 0x220 + sid * 48
        struct.pack_into('>h', model, shape + 2, -1)
        struct.pack_into('>I', model, shape + 44, ref)
        struct.pack_into('>II', model, ref + 0x30, 0x12345678, sid)
        model[mesh + 6] = 1
    cpu = bytearray(0x300)
    struct.pack_into('>III', cpu, 4, len(cpu), len(cpu), 0)
    struct.pack_into('>HHII', cpu, 20, 2, 0, 32, 0)
    corners = [(-1., 0., -2.), (1., 0., -2.), (1., 2., -2.), (-1., 2., -2.),
               (-1., 0., 2.), (1., 0., 2.), (1., 2., 2.), (-1., 2., 2.)]
    aos = [value for corner in corners for value in corner]
    soa = [corners[block * 4 + vertex][axis]
           for block in range(2) for axis in range(3) for vertex in range(4)]
    for sid in range(2):
        bounds, opaque, descriptor, packed_bounds = [x + sid * 256 for x in (96, 192, 208, 256)]
        struct.pack_into('>8I', cpu, 32 + sid * 32, sid, 0, 0,
                         bounds, opaque, descriptor, packed_bounds, 1)
        struct.pack_into('>24f', cpu, bounds, *aos)
        struct.pack_into('>4I', cpu, opaque, 0x01300003, 0x1999000a, 0, 0)
        struct.pack_into('>8I7H2B', cpu, descriptor,
                         640, 0, 0, 656, 0, 672, 688, 0,
                         0, 4, 0, 4, 0, 1, 1, 11, 0)
        struct.pack_into('>24f', cpu, packed_bounds, *soa)
    cpu[640:704] = bytes(range(64))
    return model, cpu


class PackedStreamTests(unittest.TestCase):
    def test_observed_packed_layout_and_bounds_transpose(self):
        model, cpu = make_packed_fixture()
        report = audit_packed_streams(model, cpu)
        self.assertEqual(report['packed_binding_count'], 2)
        self.assertTrue(report['model_binding_consistent'])
        self.assertTrue(all(row['single_component_bounds_match'] for row in report['packed_rows']))
        self.assertEqual(report['packed_rows'][0]['descriptors'][0]['index_bytes'], 16)
        self.assertEqual(report['packed_rows'][0]['descriptors'][0]['commands_bytes'], 16)
        self.assertFalse(report['installable'])

    def test_packed_reference_uses_index_after_extra_word(self):
        model, cpu = make_packed_fixture()
        report = audit_packed_streams(model, cpu)
        self.assertEqual([x['native_packed_mesh']['mesh_index'] for x in report['packed_rows']], [0, 1])
        self.assertEqual(report['packed_rows'][1]['native_packed_mesh']['index_field_offset'], 0x34)

    def test_shared_payloads_are_legal_and_expose_retained_owners(self):
        model, cpu = make_packed_fixture()
        report = audit_packed_streams(model, cpu, selected_slots=[0])
        self.assertEqual(report['selected_to_retained_shared_targets'], [640, 656, 672, 688])
        self.assertEqual(report['shared_payload_targets'][0]['shapes'], [0, 1])
        self.assertFalse(report['all_payload_extents_known'])
        self.assertFalse(report['payload_reclamation_allowed'])

    def test_unbounded_vertex_payload_is_not_guessed_from_next_pointer(self):
        model, cpu = make_packed_fixture()
        report = audit_packed_streams(model, cpu)
        refs = [x for x in report['payload_references'] if x['field'] == 'packed_vertex']
        self.assertTrue(all(x['byte_length'] is None and not x['full_extent_known'] for x in refs))

    def test_descriptor_table_truncation_rejected(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>I', cpu, 32 + 20, len(cpu) - 16)
        with self.assertRaises(GarageError):
            audit_packed_streams(model, cpu)

    def test_index_range_checked_using_quadword_count(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>H', cpu, 208 + 42, 0xffff)
        with self.assertRaises(GarageError):
            audit_packed_streams(model, cpu)

    def test_command_range_checked_using_quadword_count(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>I', cpu, 208 + 24, len(cpu) - 8)
        with self.assertRaises(GarageError):
            audit_packed_streams(model, cpu)

    def test_count_with_null_payload_is_rejected(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>I', cpu, 208 + 20, 0)
        with self.assertRaises(GarageError):
            audit_packed_streams(model, cpu)

    def test_native_component_count_mismatch_is_reported(self):
        model, cpu = make_packed_fixture()
        model[0x220 + 6] = 2
        report = audit_packed_streams(model, cpu)
        self.assertIn('native_component_count_mismatch', {x['code'] for x in report['issues']})
        self.assertFalse(report['model_binding_consistent'])

    def test_changed_bounds_are_reported_without_mutation(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>f', cpu, 256, -2.)
        before = bytes(cpu)
        report = audit_packed_streams(model, cpu)
        self.assertIn('bounds_pair_mismatch', {x['code'] for x in report['issues']})
        self.assertEqual(bytes(cpu), before)

    def test_nonfinite_bounds_are_rejected(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>f', cpu, 96, float('nan'))
        with self.assertRaises(GarageError):
            audit_packed_streams(model, cpu)

    def test_inline_conversion_with_original_packed_binding_is_reported(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>h', model, 0x140 + 2, 0)
        struct.pack_into('>I', model, 0x140 + 12, 0x280)
        struct.pack_into('>I', model, 0x140 + 44, 0)
        report = audit_packed_streams(model, cpu)
        self.assertEqual({x['code'] for x in report['issues']},
                         {'ordinary_fvf_with_packed_binding', 'inline_geometry_with_packed_binding',
                          'missing_packed_reference'})
        self.assertFalse(report['model_binding_consistent'])

    def test_mixed_stream_ownership_is_rejected(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>I', cpu, 32 + 4, 640)
        with self.assertRaises(GarageError):
            audit_packed_streams(model, cpu)

    def test_duplicate_shape_and_invalid_packed_index_rejected(self):
        for kind in ('shape', 'packed_index'):
            with self.subTest(kind=kind):
                model, cpu = make_packed_fixture()
                if kind == 'shape':
                    struct.pack_into('>I', cpu, 64, 0)
                else:
                    struct.pack_into('>I', model, 0x1a0 + 0x34, 2)
                with self.assertRaises(GarageError):
                    audit_packed_streams(model, cpu)

    def test_audit_keeps_inputs_and_unknown_block_unchanged(self):
        model, cpu = make_packed_fixture()
        before_model, before_cpu = bytes(model), bytes(cpu)
        report = audit_packed_streams(model, cpu, selected_slots=[0])
        self.assertEqual(bytes(model), before_model)
        self.assertEqual(bytes(cpu), before_cpu)
        self.assertFalse(report['opaque_binding_semantics_reviewed'])

    def test_empty_binding_is_not_reported_as_packed(self):
        model, cpu = make_packed_fixture()
        struct.pack_into('>7I', cpu, 32 + 4, *([0] * 7))
        report = audit_packed_streams(model, cpu)
        self.assertEqual(report['binding_classes']['0'], 'empty_binding')
        self.assertEqual(report['packed_binding_count'], 1)

