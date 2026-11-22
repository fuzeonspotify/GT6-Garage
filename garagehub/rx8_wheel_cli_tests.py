"""Synthetic CLI path safety regressions; never require installed game files."""
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest import TestCase
from unittest.mock import patch
from .errors import GarageError
from .f50_wheel_adapter import F50_SOURCE_SHA256
from .rx8_wheel_cli import validate_lab_paths

class RX8WheelCLITests(TestCase):
    def setUp(self):
        self.tmp=TemporaryDirectory();self.root=Path(self.tmp.name)
        (self.root/'input').mkdir()
        self.source=self.root/'input/original_f50.glb';self.source.write_bytes(b'glb-placeholder')
        self.donor=self.root/'native_donor';self.donor.mkdir()
        self.out=self.root/'experiment'
        self.patched=patch('garagehub.rx8_wheel_cli.hash_file',return_value=F50_SOURCE_SHA256)
        self.patched.start()
    def tearDown(self):self.patched.stop();self.tmp.cleanup()
    def test_new_separate_lab_output_is_allowed(self):
        self.assertEqual(validate_lab_paths(self.source,self.donor,self.out)[-1],self.out)
    def test_donor_directory_cannot_be_overwritten(self):
        with self.assertRaises(GarageError):validate_lab_paths(self.source,self.donor,self.donor/'experiment')
    def test_adjacent_output_does_not_modify_source(self):
        before=self.source.read_bytes()
        result=validate_lab_paths(self.source,self.donor,self.source.parent/'new_experiment')
        self.assertEqual(result[-1],self.source.parent/'new_experiment')
        self.assertEqual(self.source.read_bytes(),before)
        self.assertFalse(result[-1].exists())
    def test_existing_output_is_not_overwritten(self):
        self.out.mkdir()
        with self.assertRaises(GarageError):validate_lab_paths(self.source,self.donor,self.out)
    def test_game_ancestor_is_not_a_valid_lab_destination(self):
        p=self.root/'USRDIR'/'PDIPFS';p.mkdir(parents=True)
        with self.assertRaises(GarageError):validate_lab_paths(self.source,self.donor,p/'experiment')
    def test_case_insensitive_game_path_rejected(self):
        p=self.root/'usrdir';p.mkdir()
        with self.assertRaises(GarageError):validate_lab_paths(self.source,self.donor,p/'experiment')
    def test_original_source_fingerprint_is_required(self):
        self.patched.stop()
        self.patched=patch('garagehub.rx8_wheel_cli.hash_file',return_value='0'*64);self.patched.start()
        with self.assertRaises(GarageError):validate_lab_paths(self.source,self.donor,self.out)
