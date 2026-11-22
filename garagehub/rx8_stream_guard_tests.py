"""Offline regressions for source-backed stream ownership contracts."""
from __future__ import annotations
import struct,unittest
from .errors import GarageError
from .rx8_stream_guard import parse_cpu_rows

def make_cpu():
    cpu=bytearray(32+88*32)
    struct.pack_into('>H',cpu,20,88)
    struct.pack_into('>I',cpu,24,32)
    for i in range(88):
        sid=i if i<40 else i+14
        row=[sid,0,0,2848+i*96,2944+i*96,2960+i*96,3216+i*96,1] if i<40 else [sid,0,0,0,0,0,0,0]
        struct.pack_into('>8I',cpu,32+32*i,*row)
    return cpu

class RX8StreamGuardTests(unittest.TestCase):
    def test_known_metadata_is_visible_not_claimed_empty(self):
        report=parse_cpu_rows(make_cpu(),[0,3,23])
        self.assertEqual(report['live_vertex_index_conflicts'],0)
        self.assertEqual(report['slots_with_nonzero_auxiliary_metadata'],[0,3,23])
        self.assertTrue(report['auxiliary_metadata_unreviewed'])
        self.assertFalse(report['installable'])
    def test_nonzero_vertex_pointer_rejected(self):
        cpu=make_cpu();struct.pack_into('>I',cpu,32+3*32+4,54288)
        with self.assertRaises(GarageError):parse_cpu_rows(cpu,[3])
    def test_nonzero_index_pointer_rejected(self):
        cpu=make_cpu();struct.pack_into('>I',cpu,32+3*32+8,54528)
        with self.assertRaises(GarageError):parse_cpu_rows(cpu,[3])
    def test_duplicate_row_rejected(self):
        cpu=make_cpu();struct.pack_into('>I',cpu,32+4*32,3)
        with self.assertRaises(GarageError):parse_cpu_rows(cpu,[3])
    def test_missing_binding_rejected(self):
        cpu=make_cpu()
        with self.assertRaises(GarageError):parse_cpu_rows(cpu,[40])
    def test_repeated_target_slot_rejected(self):
        with self.assertRaises(GarageError):parse_cpu_rows(make_cpu(),[3,3])
    def test_target_outside_static_range_rejected(self):
        with self.assertRaises(GarageError):parse_cpu_rows(make_cpu(),[40])
    def test_truncated_table_rejected(self):
        cpu=make_cpu();struct.pack_into('>I',cpu,24,len(cpu)-31)
        with self.assertRaises(GarageError):parse_cpu_rows(cpu,[3])
    def test_wrong_layout_count_rejected(self):
        cpu=make_cpu();struct.pack_into('>H',cpu,20,87)
        with self.assertRaises(GarageError):parse_cpu_rows(cpu,[3])
    def test_inactive_auxiliary_row_is_reported_as_clean(self):
        cpu=make_cpu();struct.pack_into('>5I',cpu,32+3*32+12,0,0,0,0,0)
        report=parse_cpu_rows(cpu,[3])
        self.assertFalse(report['auxiliary_metadata_unreviewed'])
    def test_guard_does_not_mutate_source(self):
        cpu=make_cpu();old=bytes(cpu);parse_cpu_rows(cpu,[3]);self.assertEqual(bytes(cpu),old)
