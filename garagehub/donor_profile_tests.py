"""Portable donor profile safety tests; never require copyrighted game data."""
from __future__ import annotations
import tempfile,unittest
from pathlib import Path
from .donor_profiles import RX8_ASSETS,RX8_LAYOUTS,RX8_MODEL_PATH,locate_rx8,inspect_rx8
from .errors import GarageError

class DonorProfileTests(unittest.TestCase):
    def test_donor_contract_covers_distinct_8_assets(self):
        self.assertEqual(len(RX8_ASSETS),8)
        self.assertEqual(len(set(RX8_ASSETS.values())),8)
        self.assertEqual(set(RX8_LAYOUTS),{'hq/body','hq/wheel','race/body','race/wheel'})
        self.assertEqual(RX8_LAYOUTS['hq/body'][3],2)
        self.assertEqual(RX8_LAYOUTS['race/body'][3],2)
        self.assertEqual(RX8_MODEL_PATH,'car/0001/0001')
    def test_missing_original_donor_rejected(self):
        with tempfile.TemporaryDirectory() as t:
            with self.assertRaises(GarageError):inspect_rx8(Path(t))
    def test_fake_native_files_cannot_pass_hash_verification(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/RX8_MODEL_PATH
            (p/'hq').mkdir(parents=True);(p/'race').mkdir()
            (p/'hq/body').write_bytes(b'not a verified MDL3 donor')
            (p/'race/wheel').write_bytes(b'not a native rim')
            self.assertEqual(locate_rx8(t),p)
            with self.assertRaises(GarageError):inspect_rx8(t)
    def test_unrecognized_path_not_silently_selected(self):
        with tempfile.TemporaryDirectory() as t:
            p=Path(t)/'car/0004/0006';(p/'hq').mkdir(parents=True);(p/'race').mkdir()
            (p/'hq/body').write_bytes(b'Supra');(p/'race/wheel').write_bytes(b'Supra')
            with self.assertRaises(GarageError):locate_rx8(t)
