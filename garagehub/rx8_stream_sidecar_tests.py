"""Pure RX-8 experimental sidecar-clearing regressions."""
from __future__ import annotations
import struct,unittest
from .rx8_stream_guard_tests import make_cpu
from .rx8_stream_sidecar_lab import clear_auxiliary
from .errors import GarageError

class RX8SidecarTests(unittest.TestCase):
 def test_selected_auxiliary_cleared_only(self):
    cpu=make_cpu();other=bytes(cpu[32+10*32:32+11*32])
    new,records=clear_auxiliary(cpu,[0,3,23])
    self.assertEqual(len(records),3)
    self.assertEqual(new[32+10*32:32+11*32],other)
    for sid in (0,3,23):
        at=32+32*sid
        self.assertEqual(new[at:at+12],cpu[at:at+12])
        self.assertEqual(new[at+12:at+32],bytes(20))
    self.assertEqual(len(new),len(cpu))
 def test_clean_target_is_idempotent(self):
    cpu=make_cpu();a,_=clear_auxiliary(cpu,[3]);b,r=clear_auxiliary(a,[3])
    self.assertEqual(a,b);self.assertFalse(r[0]['had_auxiliary'])
 def test_mutation_does_not_change_input(self):
    cpu=make_cpu();old=bytes(cpu);clear_auxiliary(cpu,[3]);self.assertEqual(bytes(cpu),old)
 def test_refuses_live_index_or_vertex_pointer(self):
    for rel in (4,8):
        cpu=make_cpu();struct.pack_into('>I',cpu,32+3*32+rel,200)
        with self.assertRaises(GarageError):clear_auxiliary(cpu,[3])
 def test_refuses_unrecognized_target(self):
    with self.assertRaises(GarageError):clear_auxiliary(make_cpu(),[41])
