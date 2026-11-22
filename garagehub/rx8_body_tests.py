"""Synthetic regression tests; packaged app tests do not rely on user's donor files."""
from __future__ import annotations
import tempfile,unittest
from pathlib import Path
from types import SimpleNamespace
from .errors import GarageError
from .rx8_body_materials import same_sampler_bindings,experiment as material_experiment
from .rx8_body_geometry_lab import experiment as geometry_experiment

class MockEditor:
    banks=2
    def texture_keys(self,template,bank):
        if bank==0:
            return [('diffuseMapSampler',4),('ambientMapSampler',4),
                    ('alphaMapSampler',8),('normalMapSampler',10)]
        return [('diffuseMapSampler',3),('ambientMapSampler',3),
                ('alphaMapSampler',7),('specularMapSampler',11)]

class RX8BodyTests(unittest.TestCase):
    def test_both_bank_alias_sets_completed(self):
        result=same_sampler_bindings(MockEditor(),15,
                 {'diffuseMapSampler':23,'alphaMapSampler':42})
        self.assertEqual(result,{'diffuseMapSampler':23,'ambientMapSampler':23,
                                 'alphaMapSampler':42})
    def test_one_bank_cannot_silently_split_glass_alpha(self):
        class E(MockEditor):
            def texture_keys(self,template,bank):
                x=super().texture_keys(template,bank)
                if bank==1:return [(k,3 if k=='alphaMapSampler' else v)for k,v in x]
                return x
        with self.assertRaises(GarageError):
            same_sampler_bindings(E(),15,{'diffuseMapSampler':23,'alphaMapSampler':42})
    def test_one_bank_must_not_silently_drop_aliases(self):
        class E(MockEditor):
            banks=1
        with self.assertRaises(GarageError):
            same_sampler_bindings(E(),15,{'diffuseMapSampler':23})
    def test_unreviewed_sampler_is_rejected(self):
        with self.assertRaises(GarageError):
            same_sampler_bindings(MockEditor(),15,{'emissionMapSampler':123})
    def test_source_fingerprint_checked_before_donor(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);source=root/'wrong.glb';source.write_bytes(b'not a donor')
            with self.assertRaises(GarageError):
                geometry_experiment(source,root,root/'out')
            self.assertFalse((root/'out').exists())
            with self.assertRaises(GarageError):
                material_experiment(source,root,root/'out')
            self.assertFalse((root/'out').exists())
    def test_cannot_write_into_game_install(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'PDIPFS'/'candidate';out.parent.mkdir()
            with self.assertRaises(GarageError):
                material_experiment(root/'not_a_real_source.glb',root,out)
            with self.assertRaises(GarageError):
                geometry_experiment(root/'not_a_real_source.glb',root,out)
            self.assertFalse(out.exists())
    def test_existing_output_protected(self):
        with tempfile.TemporaryDirectory() as tmp:
            root=Path(tmp);out=root/'existing';out.mkdir()
            with self.assertRaises(GarageError):
                geometry_experiment(root/'unknown.glb',root,out)
            with self.assertRaises(GarageError):
                material_experiment(root/'unknown.glb',root,out)
            self.assertTrue(out.is_dir())
