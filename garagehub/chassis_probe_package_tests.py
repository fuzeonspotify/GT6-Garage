"""Diagnostic-package policy tests with real SQLite and synthetic archive I/O.

Only external decoding/crypto/packer execution is replaced. These tests exercise
the real package orchestration, independent SQLite delta verification, manifests,
membership, native/source hashes, append proof and failed-publication handling.
They do not establish real Salsa encryption, Windows packer or game behaviour.
"""
from __future__ import annotations

from contextlib import closing
import copy
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch

from . import car_pack, chassis_probe_package as package, packages
from . import runtime_comparison_tests as archive_fixtures
from .errors import GarageError
from .specdb_tests import fixture as database_fixture
from .util import atomic_json, hash_file, sha256


class ChassisProbePackageTests(unittest.TestCase):
    def setUp(self):
        # Compose the existing archive fixture without inheriting/rerunning its
        # unrelated native-comparison tests in this module.
        self.f = archive_fixtures.RuntimeComparisonTests(methodName='runTest')
        self.f.setUp()
        self.addCleanup(self.f.doCleanups)
        self.addCleanup(self.f.tearDown)
        self.f.package = self.f.root / 'private_chassis_probe'
        self.output = self.f.package
        self.prefix = b'synthetic-salsa-fixture:'
        source = self.f.root / 'database_source.sqlite'
        database_fixture(source)
        with closing(sqlite3.connect(source)) as database:
            database.executescript('''
                ALTER TABLE CHASSIS ADD COLUMN treadF INTEGER DEFAULT 1520;
                ALTER TABLE CHASSIS ADD COLUMN treadR INTEGER DEFAULT 1525;
                UPDATE CHASSIS SET wheelbase=2550 WHERE UseCar=82;
            ''')
            database.commit()
        encrypted = self.prefix + source.read_bytes()
        self.f.baseline_bytes[self.f.db] = encrypted
        self.f._write(self.f.base / 'Input' / self.f.db, encrypted)
        self.f._write(self.f.base / 'PatchOut' / self.f.before[self.f.db]['packed'],
                      self.f._cipher(self.f.db, encrypted))
        self.f._write_baseline_receipt()
        self.pin = hash_file(self.f.base / 'package_receipt.json')
        for target, side_effect in (('garagehub.chassis_probe_package.database_context', self._database_context),
                                     ('garagehub.car_pack.execute', self._execute)):
            patched = patch(target, side_effect=side_effect)
            patched.start()
            self.addCleanup(patched.stop)

    def _database_context(self, baseline, packer, cache, cancel):
        self.assertEqual(Path(baseline), self.f.base)
        cache = Path(cache)
        encrypted = (self.f.base / 'Input' / self.f.db).read_bytes()
        self.assertTrue(encrypted.startswith(self.prefix))
        decoded = cache / 'source.sqlite'
        self.f._write(decoded, encrypted[len(self.prefix):])
        atomic_json(cache / 'decoded.json', {'encrypted_sha256': sha256(encrypted),
                                             'plain_sha256': hash_file(decoded)})
        return {'database': str(decoded), 'encrypted_sha256': sha256(encrypted),
                'baseline_header': car_pack.BASE_HEADER}

    def _execute(self, packer, args, work, logname, cancel):
        value = lambda option: Path(args[args.index(option) + 1])
        if args[0] == 'cryptsalsa':
            self.f.calls.append('cryptsalsa')
            path = value('-i')
            data = path.read_bytes()
            path.write_bytes(data[len(self.prefix):] if data.startswith(self.prefix) else self.prefix + data)
            self.f._write(Path(work) / logname, b'Synthetic reversible byte transform, not Salsa.\n')
        elif args[0] == 'pack':
            self.f.calls.append('pack')
            self.assertEqual(value('-i'), self.output / 'AppendSeed')
            self.assertEqual(self.f._tree(value('-i')), self.f._tree(self.f.base / 'PatchOut'))
            selected = {path.relative_to(value('-p')).as_posix(): path.read_bytes()
                        for path in value('-p').rglob('*') if path.is_file()}
            self.assertEqual(set(selected), {self.f.db}, 'A chassis experiment may append only its database.')
            self.f.packed = {**self.f.baseline_bytes, **selected}
            self.f.after = copy.deepcopy(self.f.before)
            self.f.after[self.f.db] = {'index': 65000, 'packed': '8/BB/00'}
            destination = value('-o')
            self.f._write(destination / 'K/4D', b'fresh DB-only diagnostic header')
            self.f._write(destination / self.f.output_toc_path, b'fresh DB-only diagnostic index')
            self.f._write(destination / '8/BB/00', self.f._cipher(self.f.db, selected[self.f.db]))
            if self.f.fault == 'native_listing_changed':
                self.f.after[self.f.names[0]]['index'] = 9010
            if self.f.fault == 'extra_payload':
                self.f._write(destination / '4/EE/FF', b'undeclared diagnostic payload')
            if self.f.fault == 'native_payload_changed':
                self.f._write(destination / self.f.before[self.f.names[0]]['packed'], b'changed native asset')
        else:
            # Real listing parsers, TOC ownership checks and source extraction
            # guards consume the same fixture transport as native comparisons.
            self.f._execute(packer, args, work, logname, cancel)

    def build(self, **overrides):
        return package.build_chassis_probe_package(
            self.f.base, self.f.packer, self.output,
            **{'baseline_receipt_sha256': self.pin, **overrides})

    def verify(self, pin=None):
        manifest = self.output / package.MANIFEST_NAME
        return package.verify_chassis_probe_manifest(manifest, pin or hash_file(manifest))

    def assert_rejected_publication(self):
        self.assertFalse((self.output / 'package_receipt.json').exists())
        self.assertFalse((self.output / package.MANIFEST_NAME).exists())
        failure = json.loads((self.output / 'FAILED.json').read_text())
        self.assertFalse(failure['release_ready'])
        self.assertTrue(failure['installed_game_unchanged'])

    def test_valid_db_only_package_preserves_baseline_and_every_native_asset(self):
        before = self.f._tree(self.f.base)
        result = self.build()
        self.assertTrue(result['verified'])
        self.assertTrue(result['native_assets_byte_identical'])
        self.assertFalse(result['player_installable'])
        self.assertFalse(result['runtime_relationship_established'])
        self.assertEqual(result['database_verification']['validation']['changed_cell_count'], 1)
        for name in self.f.names:
            self.assertEqual((self.output / 'Verified' / name).read_bytes(), self.f.baseline_bytes[name])
        self.assertEqual(self.f._tree(self.f.base), before)
        self.assertEqual(self.f._tree(self.output / 'DeltaInput'),
                         {self.f.db: hash_file(self.output / 'DeltaInput' / self.f.db)})
        self.assertEqual(self.f.calls.count('cryptsalsa'), 4,
                         'Build verification must freshly decode approved and extracted encrypted databases.')
        self.assertTrue(self.verify()['verified'])
        self.assertEqual(self.f.calls.count('cryptsalsa'), 6)

    def test_stale_baseline_pin_stops_before_output_and_external_commands(self):
        with self.assertRaises(GarageError):
            self.build(baseline_receipt_sha256='0' * 64)
        self.assertFalse(self.output.exists())
        self.assertEqual(self.f.calls, [])

    def test_different_baseline_header_is_not_accepted_with_a_fresh_receipt_pin(self):
        self.f._write(self.f.base / 'PatchOut/K/4D', b'different unreviewed header')
        self.f._write_baseline_receipt()
        with self.assertRaises(GarageError):
            self.build(baseline_receipt_sha256=hash_file(self.f.base / 'package_receipt.json'))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.f.calls, [])

    def test_baseline_cannot_omit_a_native_source_even_with_a_fresh_receipt_pin(self):
        missing = self.f.base / 'Input' / self.f.names[0]
        missing.unlink()
        self.f._write_baseline_receipt()
        with self.assertRaisesRegex(GarageError, 'eight native assets'):
            self.build(baseline_receipt_sha256=hash_file(self.f.base / 'package_receipt.json'))
        self.assertFalse(self.output.exists())
        self.assertEqual(self.f.calls, [])

    def test_mutated_manifest_is_rejected_by_the_original_pin(self):
        self.build()
        path = self.output / package.MANIFEST_NAME
        pin = hash_file(path)
        manifest = json.loads(path.read_text())
        manifest['player_installable'] = True
        atomic_json(path, manifest)
        with self.assertRaises(GarageError):
            self.verify(pin)
        with self.assertRaises(GarageError):
            self.verify()  # A new pin cannot change the diagnostic-only contract.

    def test_changed_evidence_and_unlisted_delta_input_are_rejected(self):
        self.build()
        log = self.output / 'Work/listing.log'
        old = log.read_bytes()
        log.write_bytes(old + b'changed evidence\n')
        with self.assertRaises(GarageError):
            self.verify()
        log.write_bytes(old)
        self.f._write(self.output / 'DeltaInput' / self.f.names[0], b'extra native asset')
        with self.assertRaises(GarageError):
            self.verify()

    def test_decoded_lineage_receipt_is_pinned_even_if_json_meaning_is_unchanged(self):
        self.build()
        manifest = json.loads((self.output / package.MANIFEST_NAME).read_text())
        decoded = Path(manifest['source_database']).parent / 'decoded.json'
        decoded.write_bytes(decoded.read_bytes() + b'\n')
        with self.assertRaises(GarageError):
            self.verify()

    def test_verifier_redecodes_both_actual_encrypted_inputs_without_modifying_package(self):
        self.build()
        before = self.f._tree(self.output)
        for target in ('baseline.sqlite', 'candidate.sqlite'):
            with self.subTest(target=target):
                def wrong_decode(packer, args, work, logname, cancel):
                    self._execute(packer, args, work, logname, cancel)
                    decoded = Path(args[args.index('-i') + 1])
                    if args[0] == 'cryptsalsa' and decoded.name == target:
                        decoded.write_bytes(decoded.read_bytes() + b'bad independent decode')
                with patch('garagehub.car_pack.execute', side_effect=wrong_decode):
                    with self.assertRaisesRegex(GarageError, 'does not decode'):
                        self.verify()
        self.assertEqual(self.f._tree(self.output), before)

    def test_player_source_and_export_paths_reject_private_diagnostic(self):
        self.build()
        exported = self.f.root / 'player_install.zip'
        with self.assertRaises(GarageError) as caught:
            packages.source_package(self.output)
        self.assertEqual(caught.exception.code, 'diagnostic_package')
        with self.assertRaises(GarageError):
            packages.export_package(self.output, exported)
        self.assertFalse(exported.exists())
        self.assertFalse(exported.with_name(exported.name + '.partial').exists())

    def test_native_listing_deviation_rejects_the_append_before_publication(self):
        self.f.fault = 'native_listing_changed'
        with self.assertRaises(GarageError):
            self.build()
        self.assert_rejected_publication()

    def test_unplanned_native_payload_or_extra_payload_cannot_be_appended(self):
        for fault in ('native_payload_changed', 'extra_payload'):
            with self.subTest(fault=fault):
                self.f.fault = fault
                self.output = self.f.root / fault
                self.f.package = self.output
                with self.assertRaises(GarageError):
                    self.build()
                self.assert_rejected_publication()

    def test_extra_database_cell_is_independently_rejected_before_pack(self):
        real_build = package.build_chassis_probe

        def changed_twice(source, output, plan):
            result = real_build(source, output, plan)
            with closing(sqlite3.connect(output)) as database:
                database.execute('UPDATE CHASSIS SET treadR=treadR+1 WHERE ID=101')
                database.commit()
            return result

        with patch.object(package, 'build_chassis_probe', side_effect=changed_twice):
            with self.assertRaises(GarageError):
                self.build()
        self.assertNotIn('pack', self.f.calls)
        self.assert_rejected_publication()

    def test_corrupt_native_extraction_prevents_a_successful_receipt(self):
        self.f.fault = 'wrong_extraction'
        with self.assertRaises(GarageError):
            self.build()
        self.assert_rejected_publication()

    def test_failed_final_verification_withdraws_receipt_and_manifest(self):
        with patch.object(package, 'verify_chassis_probe_manifest',
                          side_effect=GarageError('Injected final verification failure.')):
            with self.assertRaises(GarageError):
                self.build()
        self.assert_rejected_publication()
        self.assertTrue((self.output / 'package_receipt.json.rejected').is_file())
        self.assertTrue((self.output / (package.MANIFEST_NAME + '.rejected')).is_file())


if __name__ == '__main__':
    unittest.main()
