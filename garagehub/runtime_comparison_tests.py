"""Serialized comparison fixtures with the real diagnostic append orchestration.

Only the external executable is replaced by a deterministic in-process archive
fixture. Native headers, filesystem reads, receipt pins, membership, roundtrip
checks and preservation checks use the production implementation. These tests
do not establish real encryption, the Windows packer, or runtime behavior.
"""
from __future__ import annotations

import copy
import json
import struct
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from . import car_pack, car_profiles
from .errors import GarageError
from .runtime_comparison import (BASELINE_LISTING_NAME, MANIFEST_NAME,
                                 build_same_identity_package, reviewed_identity,
                                 verify_comparison_inputs, verify_comparison_manifest)
from .util import atomic_json, hash_file, sha256


def _native(tag):
    """One real unique-pointer FVF, no direct meshes; harmless tag in padding."""
    raw = bytearray(1024)
    raw[:4] = b'MDL3'
    struct.pack_into('>IIH', raw, 4, len(raw), 0, 14)
    struct.pack_into('>I', raw, 0x6c, len(raw))
    struct.pack_into('>H', raw, 0x18, 1)
    struct.pack_into('>I', raw, 0x40, 256)
    struct.pack_into('>III', raw, 256, 820, 0, 600)
    raw[280:282] = bytes((1, 12))
    struct.pack_into('>I4B', raw, 600, 800, 0, 3, 2, 0)
    raw[800:809] = b'position\0'
    raw[820:825] = b'Flex\0'
    raw[900:900 + len(tag)] = tag
    return bytes(raw)


class RuntimeComparisonTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.base = self.root / 'approved'
        self.candidate = self.root / 'current_native'
        self.reference = self.root / 'geometry_audit.json'
        self.output = self.root / 'comparison'
        self.package = self.output / 'package'
        self.packer = self.root / 'fixture.exe'
        self.calls = []
        self.fault = None
        self.after_pack_mutation = None
        self.names = [car_profiles.PROFILE['model_path'] + '/' + name for name in car_profiles.ASSETS]
        self.db = 'specdb/GT6/DB0106.dat'
        self.names_file = 'specdb/GT6/US/CarName.dat'
        self.description = 'description/gt6/JP.rt2'
        self.stock = 'stock/retained/outside_mod_input'
        # Actual approved package semantics: the copied seed TOC is 9/H4/MS,
        # while the emitted TOC is header-derived and has a different path.
        self.base_toc_path = '9/6M/W2'
        self.output_toc_path = '9/6M/W3'
        self.base_toc_index = 64155
        self.output_toc_index = 65008
        self.baseline_bytes = {}
        self.current_bytes = {}
        for index, name in enumerate(self.names):
            tag = ('asset-' + str(index)).encode()
            is_model = Path(name).name in ('body', 'wheel', 'interior')
            self.baseline_bytes[name] = _native(tag) if is_model else b'old-' + tag
            self.current_bytes[name] = (_native(tag + b'-new') if is_model else b'new-' + tag)
            if name.endswith('/interior'):
                self.current_bytes[name] = self.baseline_bytes[name]
        self.baseline_bytes.update({self.db: b'unchanged encrypted physical parameters',
                                    self.names_file: b'unchanged car names',
                                    self.description: b'unchanged descriptions'})
        self.before = {name: {'index': index, 'packed': f'5M/AA/{index:02X}'}
                       for index, name in enumerate(self.baseline_bytes, 1)}
        self.before[self.names_file]['packed'] = '9/H4/HM'
        # Exact real listing schema/values from the rejected full-seed repack.
        self.before[self.description] = {'index': 64184, 'packed': '9/DJ/27', 'flags': 'Compressed',
                                          'segment': 10666082, 'zsize': '000997D6', 'size': '001EAF9E',
                                          'volindex': 4294967295}
        self.before[self.stock] = {'index': 9000, 'packed': '6/BC/DE'}
        for name, data in self.baseline_bytes.items():
            self._write(self.base / 'Input' / name, data)
            self._write(self.base / 'PatchOut' / self.before[name]['packed'], self._cipher(name, data))
        self._write(self.base / 'PatchOut/K/4D', b'approved complete archive header')
        self._write(self.base / 'PatchOut' / self.base_toc_path, b'approved complete archive index')
        self.seed = {name: self._write(self.base / 'BaselineSeed' / name, data)
                     for name, data in {'K/4D': b'original seed header', '9/H4/MS': b'original seed index'}.items()}
        for name, data in self.current_bytes.items():
            self._write(self.candidate / name, data)
        self._write(self.packer, b'MZ fixture only; never executed')
        self._write_listing(self.base / 'packed_entries.txt', self.before)
        baseline_native = {name.removeprefix(car_profiles.PROFILE['model_path'] + '/'): sha256(data)
                           for name, data in self.baseline_bytes.items() if name in self.names}
        for target, value in (('garagehub.car_pack.BASE_HEADER', hash_file(self.base / 'PatchOut/K/4D')),
                              ('garagehub.car_pack.SEED', self.seed),
                              ('garagehub.car_pack.PACKER_SHA', hash_file(self.packer)),
                              ('garagehub.car_profiles.ASSETS', baseline_native)):
            patcher = patch(target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        runner = patch('garagehub.car_pack.execute', side_effect=self._execute)
        runner.start()
        self.addCleanup(runner.stop)
        self.identity = reviewed_identity()
        self.source_sha = sha256(b'one explicitly reviewed source')
        self.build = {'schema': 1, 'passed': True, 'profile_id': self.identity['profile_id'],
                      'kind': 'profile-native-exterior-candidate', 'car_id': self.identity['car_id'],
                      'model_path': self.identity['model_path'], 'source_sha256': self.source_sha,
                      'source_virtual_assets': {name: sha256(data) for name, data in self.current_bytes.items()},
                      'additional_database_entry': False, 'limitations': ['Synthetic archive fixture only.']}
        atomic_json(self.candidate / 'build_receipt.json', self.build)
        self.reference_doc = {'schema': 1, 'passed': True, 'version': 'fixture-current',
                              'source_sha256': self.source_sha,
                              'native_supra_assets': dict(self.build['source_virtual_assets']),
                              'scope': 'Synthetic offline native identity evidence; no runtime result.'}
        atomic_json(self.reference, self.reference_doc)
        self._write_baseline_receipt()
        self.pins = self._pins()

    def tearDown(self):
        self.temp.cleanup()

    def _write(self, path, data):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        return hash_file(path)

    def _cipher(self, name, data):
        return b'fixture-packed:' + name.encode() + b'\0' + data

    def _tree(self, root):
        return {p.relative_to(root).as_posix(): hash_file(p) for p in root.rglob('*') if p.is_file()}

    def _write_listing(self, path, rows):
        text = '\n'.join(f'{name} - Flags: {row.get("flags", "Uncompressed")} | FileIndex: {row["index"]} ({row["packed"]}) '
                         f'| SegIndex: {row.get("segment", 10666082 + row["index"])}, ZSize: {row.get("zsize", "00000400")} '
                         f'| Size: {row.get("size", "00000400")} | VolIndex: {row.get("volindex", 0)}'
                         for name, row in rows.items()) + '\n'
        self._write(path, text.encode())

    def _write_baseline_receipt(self):
        physical = self.base / 'PatchOut'
        receipt = {'passed': True, 'source_roundtrip_match': True, 'candidate_id': 'approved-fixture',
                   'header_sha256': hash_file(physical / 'K/4D'),
                   'files': [{'relative_path': p.relative_to(physical).as_posix(),
                              'bytes': p.stat().st_size, 'sha256': hash_file(p)}
                             for p in sorted(physical.rglob('*')) if p.is_file()],
                   'source_virtual_assets': self._tree(self.base / 'Input')}
        atomic_json(self.base / 'package_receipt.json', receipt)

    def _pins(self):
        return {'identity': dict(self.identity), 'reference_sha256': hash_file(self.reference),
                'reference_version': 'fixture-current',
                'build_receipt_sha256': hash_file(self.candidate / 'build_receipt.json'),
                'baseline_receipt_sha256': hash_file(self.base / 'package_receipt.json')}

    def _preflight(self, **changes):
        return verify_comparison_inputs(self.candidate, self.base, self.reference, **{**self.pins, **changes})

    def _build(self, **changes):
        return build_same_identity_package(self.candidate, self.base, self.reference, self.packer,
                                           self.output, **{**self.pins, **changes})

    def _execute(self, packer, args, work, logname, cancel):
        self.calls.append(args[0])
        value = lambda option: Path(args[args.index(option) + 1])
        if args[0] == 'pack':
            source, destination = value('-p'), value('-o')
            self.assertEqual(value('-i'), self.package / 'AppendSeed')
            self.assertEqual(self._tree(value('-i')), self._tree(self.base / 'PatchOut'))
            selected = {p.relative_to(source).as_posix(): p.read_bytes()
                        for p in source.rglob('*') if p.is_file()}
            self.assertEqual(set(selected), set(self.names), 'The diagnostic must never repack unrelated Input files.')
            self.packed = {**self.baseline_bytes, **selected}
            self.after = copy.deepcopy(self.before)
            for index, name in enumerate(self.names):
                self.after[name] = {'index': 65000 + index, 'packed': f'8/BB/{index:02X}'}
            self._write(destination / 'K/4D', b'fresh comparison header')
            self._write(destination / self.output_toc_path, b'fresh comparison index')
            for name, data in selected.items():
                payload = self._cipher(name, data)
                self._write(destination / self.after[name]['packed'], payload)
            if self.fault == 'unrelated_cipher':
                self._write(destination / self.before[self.db]['packed'], b'unplanned physical mutation')
            if self.fault == 'extra_payload':
                self._write(destination / '4/EE/FF', b'undeclared owner')
            if self.fault == 'seed_as_extra_payload':
                self._write(destination / '9/H4/MS', b'seed is not automatically emitted metadata')
            if self.fault == 'unrelated_listing':
                self.after[self.db]['index'] = 9010
            if self.fault == 'unrelated_segindex':
                self.after[self.description]['segment'] = 10665982
            if self.fault == 'selected_alias':
                old = destination / self.after[self.names[0]]['packed']
                self.after[self.names[0]]['packed'] = self.after[self.db]['packed']
                self._write(destination / self.after[self.names[0]]['packed'], old.read_bytes())
                old.unlink()
            if self.fault == 'reuse_old_native_path':
                old = destination / self.after[self.names[0]]['packed']
                self.after[self.names[0]]['packed'] = self.before[self.names[0]]['packed']
                self._write(destination / self.after[self.names[0]]['packed'], old.read_bytes())
                old.unlink()
            if self.fault == 'reuse_old_index':
                self.after[self.names[0]]['index'] = self.before[self.names[0]]['index']
            if self.fault == 'mutate_append_seed':
                self._write(value('-i') / self.before[self.db]['packed'], b'packer changed its approved append seed')
            if self.fault == 'extra_namespace':
                self.after['stock/unrequested'] = {'index': 9999, 'packed': '4/FF/EE'}
            if self.fault == 'missing_namespace':
                self.after.pop(self.stock)
            if self.after_pack_mutation:
                self.after_pack_mutation()
        elif args[0] == 'listfiles':
            baseline = value('-i') == self.base / 'PatchOut'
            toc_path = self.base_toc_path if baseline else self.output_toc_path
            toc_index = self.base_toc_index if baseline else self.output_toc_index
            if self.fault == 'toc_alias' and not baseline:
                toc_path = self.after[self.db]['packed']
            reported_path = str(value('-i') / toc_path)
            if self.fault == 'toc_wrong_package' and not baseline:
                reported_path = str(self.base / 'PatchOut' / toc_path)
            loaded_index = toc_index + (1 if self.fault == 'toc_index_mismatch' and not baseline else 0)
            lines = [f'[>] Table of Contents Entry Index: {toc_index}',
                     f'[!] Volume Patch Path Table of contents located at: {reported_path}',
                     f'[-] TOC Entry is {loaded_index} which is at {toc_path} - decrypting it']
            if self.fault == 'toc_missing_evidence' and not baseline:
                lines = lines[1:]
            if self.fault == 'toc_duplicate_evidence' and not baseline:
                lines.append(lines[-1])
            self._write(Path(work) / logname, ('\n'.join(lines) + '\n').encode())
            if baseline:
                rows = copy.deepcopy(self.before)
                if self.fault == 'fresh_baseline_listing':
                    rows[self.stock]['index'] = 9050
                self._write_listing(value('-o'), rows)
            else:
                self._write_listing(value('-o'), self.after)
        elif args[0] == 'unpack':
            index = int(args[args.index('--indices') + 1])
            name = next(name for name, row in self.after.items() if row['index'] == index)
            data = self.packed[name]
            if self.fault == 'wrong_extraction' and name == self.names[0]:
                data += b'corrupt'
            self._write(value('-o') / name, data)
        else:
            raise AssertionError('Unexpected external command in fixture: ' + args[0])

    def _assert_rejected_output(self):
        self.assertFalse((self.package / MANIFEST_NAME).exists())
        self.assertTrue((self.package / 'runtime_comparison_FAILED.json').is_file())
        receipt = self.package / 'package_receipt.json'
        if receipt.is_file():
            self.assertFalse(json.loads(receipt.read_text())['passed'])

    def test_valid_package_is_same_identity_and_all_inputs_immutable(self):
        before = {name: self._tree(path) for name, path in (('base', self.base), ('candidate', self.candidate))}
        pins = copy.deepcopy(self.pins)
        result = self._build()
        proof = result['manifest']
        self.assertEqual(proof['identity'], self.identity)
        self.assertEqual(proof['package']['native_assets'], self.build['source_virtual_assets'])
        self.assertEqual(proof['package']['unrelated_physical_payloads_checked'], 3)
        self.assertEqual(proof['package']['unrelated_logical_sources_checked'], 3)
        self.assertEqual(len(proof['package']['changed_native_assets']), 7)
        self.assertEqual(proof['package']['mutable_index_paths_before'], ['9/6M/W2', 'K/4D'])
        self.assertEqual(proof['package']['mutable_index_paths_after'], ['9/6M/W3', 'K/4D'])
        self.assertFalse(proof['runtime_verified'])
        self.assertEqual(pins, self.pins)
        self.assertEqual(before['base'], self._tree(self.base))
        self.assertEqual(before['candidate'], self._tree(self.candidate))
        self.assertTrue((self.package / BASELINE_LISTING_NAME).is_file())
        commands_before = list(self.calls)
        verified = verify_comparison_manifest(result['manifest_path'], result['manifest_sha256'])
        self.assertTrue(verified['verified'])
        self.assertEqual(verified['native_assets'], self.build['source_virtual_assets'])
        self.assertEqual(self.calls, commands_before, 'Reverification must not execute even the packer.')

    def test_package_size_is_derived_from_manifest_not_a_historical_file_count(self):
        for index in range(7):
            name = f'other/retained/{index}'
            data = f'unchanged {index}'.encode()
            self.baseline_bytes[name] = data
            self.before[name] = {'index': 100 + index, 'packed': f'7/AA/{index:02X}'}
            self._write(self.base / 'Input' / name, data)
            self._write(self.base / 'PatchOut' / self.before[name]['packed'], self._cipher(name, data))
        self._write_listing(self.base / 'packed_entries.txt', self.before)
        self._write_baseline_receipt()
        self.pins = self._pins()
        result = self._build()['manifest']['package']
        self.assertEqual(result['physical_file_count'], 29)
        self.assertEqual(result['unrelated_physical_payloads_checked'], 10)

    def test_preflight_has_no_external_commands_or_writes(self):
        before = self._tree(self.root)
        result = self._preflight()
        self.assertTrue(result['ready_for_packaging'])
        self.assertFalse(result['baseline_archive_listing_reverified'])
        self.assertEqual(self.calls, [])
        self.assertEqual(before, self._tree(self.root))

    def test_wrong_independent_receipt_pins_fail_before_output(self):
        for field in ('reference_sha256', 'build_receipt_sha256', 'baseline_receipt_sha256'):
            with self.subTest(field=field), self.assertRaisesRegex(GarageError, 'independently recorded hash'):
                self._build(**{field: '0' * 64})
        self.assertEqual(self.calls, [])
        self.assertFalse(self.output.exists())

    def test_rewritten_baseline_receipt_cannot_authorize_changed_physics(self):
        self._write(self.base / 'Input' / self.db, b'changed physics')
        self._write(self.base / 'PatchOut' / self.before[self.db]['packed'], b'changed physical payload')
        self._write_baseline_receipt()
        with self.assertRaisesRegex(GarageError, 'independently recorded hash'):
            self._build()
        self.assertFalse(self.output.exists())

    def test_baseline_header_alone_cannot_authorize_changed_unrelated_payload(self):
        path = self.base / 'PatchOut' / self.before[self.db]['packed']
        path.write_bytes(b'changed unrelated payload')
        with self.assertRaises(GarageError):
            self._build()
        self.assertFalse(self.output.exists())

    def test_wrong_reference_version_scope_or_source_is_rejected(self):
        for field, value in (('version', 'historical'), ('scope', ''), ('source_sha256', '0' * 64)):
            with self.subTest(field=field):
                doc = copy.deepcopy(self.reference_doc)
                doc[field] = value
                atomic_json(self.reference, doc)
                with self.assertRaises(GarageError):
                    self._preflight(reference_sha256=hash_file(self.reference))

    def test_seven_of_eight_matching_assets_is_insufficient(self):
        doc = copy.deepcopy(self.reference_doc)
        doc['native_supra_assets'][self.identity['model_path'] + '/hq/wheel'] = 'a' * 64
        atomic_json(self.reference, doc)
        with self.assertRaisesRegex(GarageError, 'recorded current eight-asset map'):
            self._preflight(reference_sha256=hash_file(self.reference))

    def test_native_manifest_cannot_omit_or_add_or_reroute_an_asset(self):
        for variant in ('missing', 'extra', 'routed'):
            with self.subTest(variant=variant):
                doc = copy.deepcopy(self.reference_doc)
                assets = doc['native_supra_assets']
                if variant == 'missing':
                    assets.pop(self.names[0])
                elif variant == 'extra':
                    assets[self.db] = 'a' * 64
                else:
                    assets['car/0004/0113/info'] = assets.pop(self.names[0])
                atomic_json(self.reference, doc)
                with self.assertRaisesRegex(GarageError, 'exactly all eight'):
                    self._preflight(reference_sha256=hash_file(self.reference))

    def test_candidate_actual_bytes_not_only_its_receipt_are_checked(self):
        (self.candidate / self.names[0]).write_bytes(b'changed after native audit')
        with self.assertRaisesRegex(GarageError, 'differs from the recorded reference'):
            self._build()
        self.assertFalse(self.output.exists())

    def test_missing_or_extra_candidate_files_fail(self):
        self._write(self.candidate / 'unexpected.db', b'not selected')
        with self.assertRaisesRegex(GarageError, 'only its eight native assets'):
            self._preflight()
        (self.candidate / 'unexpected.db').unlink()
        (self.candidate / self.names[0]).unlink()
        with self.assertRaisesRegex(GarageError, 'only its eight native assets'):
            self._preflight()

    def test_routed_or_database_candidate_is_rejected_even_if_native_map_matches(self):
        for changes in ({'kind': 'identity-native-exterior-candidate'}, {'additional_database_entry': True},
                        {'installation': {'mode': 'replace', 'car_id': 82}}, {'car_id': 2146},
                        {'model_path': 'car/0004/0113'}):
            with self.subTest(changes=changes):
                atomic_json(self.candidate / 'build_receipt.json', {**self.build, **changes})
                with self.assertRaises(GarageError):
                    self._preflight(build_receipt_sha256=hash_file(self.candidate / 'build_receipt.json'))

    def test_caller_identity_is_explicit_and_cannot_be_changed(self):
        for key, value in (('car_id', 2146), ('model_path', 'car/0004/0113'),
                           ('title_id', 'BLUS99999'), ('baseline_header_sha256', '0' * 64)):
            with self.subTest(key=key), self.assertRaisesRegex(GarageError, 'explicitly retain'):
                self._preflight(identity={**self.identity, key: value})

    def test_historical_metadata_may_be_absent_but_conflicting_metadata_is_rejected(self):
        self.assertTrue(self._preflight()['ready_for_packaging'])
        path = self.base / 'package_receipt.json'
        doc = json.loads(path.read_text())
        doc['game_update'] = '01.05'
        atomic_json(path, doc)
        with self.assertRaisesRegex(GarageError, 'different logical identity'):
            self._preflight(baseline_receipt_sha256=hash_file(path))

    def test_duplicate_json_keys_cannot_hide_an_identity_or_asset(self):
        raw = self.reference.read_text()
        self.reference.write_text(raw.replace('"passed": true', '"passed": false, "passed": true'))
        with self.assertRaisesRegex(GarageError, 'unique-key JSON'):
            self._preflight(reference_sha256=hash_file(self.reference))

    def test_extra_baseline_logical_or_physical_files_are_rejected(self):
        for root, name in ((self.base / 'Input', 'unrecorded/input'), (self.base / 'PatchOut', '4/EE/FF')):
            with self.subTest(root=root):
                path = root / name
                self._write(path, b'unrecorded')
                with self.assertRaises(GarageError):
                    self._preflight()
                path.unlink()

    def test_physical_alias_with_unrelated_logical_owner_is_rejected(self):
        bad = copy.deepcopy(self.before)
        bad[self.db]['packed'] = bad[self.names[0]]['packed']
        self._write_listing(self.base / 'packed_entries.txt', bad)
        with self.assertRaisesRegex(GarageError, 'unrelated logical asset aliases'):
            self._preflight()

    def test_unrelated_physical_mutation_or_extra_payload_invalidates_output(self):
        for fault in ('unrelated_cipher', 'extra_payload'):
            with self.subTest(fault=fault):
                self.fault = fault
                self.output = self.root / fault
                self.package = self.output / 'package'
                with self.assertRaisesRegex(GarageError, 'unrelated physical payload|Unowned physical files|Append delta must'):
                    self._build()
                self._assert_rejected_output()

    def test_unrelated_logical_movement_invalidates_output_even_if_plaintext_matches(self):
        self.fault = 'unrelated_listing'
        with self.assertRaisesRegex(GarageError, 'unrelated archive entry moved'):
            self._build()
        self._assert_rejected_output()

    def test_output_selected_payload_cannot_alias_unrelated_owner(self):
        self.fault = 'selected_alias'
        with self.assertRaisesRegex(GarageError, 'unrelated logical asset aliases'):
            self._build()
        self._assert_rejected_output()

    def test_stale_cached_listing_is_detected_by_fresh_baseline_listing(self):
        self.fault = 'fresh_baseline_listing'
        with self.assertRaisesRegex(GarageError, 'fresh listing'):
            self._build()
        self._assert_rejected_output()

    def test_namespace_changes_and_extraction_corruption_remain_fail_closed(self):
        for fault in ('extra_namespace', 'missing_namespace', 'wrong_extraction'):
            with self.subTest(fault=fault):
                self.fault = fault
                self.output = self.root / fault
                self.package = self.output / 'package'
                with self.assertRaises(GarageError):
                    self._build()
                self._assert_rejected_output()

    def test_changed_input_receipt_during_pack_is_not_blessed_by_final_checks(self):
        def mutate():
            path = self.base / 'package_receipt.json'
            doc = json.loads(path.read_text())
            doc['unplanned_change'] = True
            atomic_json(path, doc)
        self.after_pack_mutation = mutate
        with self.assertRaisesRegex(GarageError, 'independently recorded hash'):
            self._build()
        self._assert_rejected_output()

    def test_offline_packer_receipt_cannot_claim_a_runtime_result(self):
        def mutate(path, doc):
            if Path(path) == self.package / 'package_receipt.json' and doc.get('passed') is True:
                doc = {**doc, 'runtime_tested': True}
            return atomic_json(path, doc)
        with patch('garagehub.runtime_comparison.atomic_json', side_effect=mutate):
            with self.assertRaisesRegex(GarageError, 'must not inherit a runtime acceptance claim'):
                self._build()
        self._assert_rejected_output()

    def test_linked_inputs_or_output_ancestors_are_rejected(self):
        alias = self.root / 'candidate_alias'
        try:
            alias.symlink_to(self.candidate, target_is_directory=True)
        except OSError:
            self.skipTest('This test environment cannot create filesystem links.')
        with self.assertRaisesRegex(GarageError, 'filesystem links'):
            verify_comparison_inputs(alias, self.base, self.reference, **self.pins)
        output_parent = self.root / 'output_alias'
        output_parent.symlink_to(self.root, target_is_directory=True)
        self.output = output_parent / 'new_comparison'
        with self.assertRaisesRegex(GarageError, 'filesystem links'):
            self._build()
        self.assertEqual(self.calls, [])

    def test_existing_output_is_never_invalidated(self):
        self.output.mkdir()
        marker = self.output / 'package_receipt.json'
        marker.write_bytes(b'preexisting unrelated evidence')
        with self.assertRaisesRegex(GarageError, 'new isolated comparison output'):
            self._build()
        self.assertEqual(marker.read_bytes(), b'preexisting unrelated evidence')
        self.assertEqual(self.calls, [])

    def test_output_inside_inputs_or_game_save_locations_is_rejected_before_packing(self):
        destinations = [self.base / 'new_comparison', self.candidate / 'new_comparison']
        for component in ('PDIPFS', 'USRDIR', 'dev_hdd0', 'SAVEDATA'):
            parent = self.root / component
            parent.mkdir()
            destinations.append(parent / 'new_comparison')
        for output in destinations:
            with self.subTest(output=output):
                self.output = output
                with self.assertRaises(GarageError):
                    self._build()
                self.assertFalse(output.exists())
        self.assertEqual(self.calls, [])

    def test_post_build_manifest_pin_and_every_native_file_are_rechecked(self):
        result = self._build()
        with self.assertRaisesRegex(GarageError, 'independently recorded hash'):
            verify_comparison_manifest(result['manifest_path'], '0' * 64)
        (self.package / 'Input' / self.names[0]).write_bytes(b'changed since packaging')
        with self.assertRaises(GarageError):
            verify_comparison_manifest(result['manifest_path'], result['manifest_sha256'])

    def test_reverification_rejects_new_unlisted_physical_files(self):
        result = self._build()
        self._write(self.package / 'PatchOut/4/EE/FF', b'appeared after packaging')
        with self.assertRaisesRegex(GarageError, 'unlisted payloads'):
            verify_comparison_manifest(result['manifest_path'], result['manifest_sha256'])

    def test_reverification_rejects_changed_extracted_data_even_with_stable_input(self):
        result = self._build()
        self._write(self.package / 'Verified' / self.db, b'changed verified database')
        with self.assertRaisesRegex(GarageError, 'Independent extraction'):
            verify_comparison_manifest(result['manifest_path'], result['manifest_sha256'])

    def test_seed_and_emitted_toc_are_distinct_and_verified_from_fresh_header_evidence(self):
        self.assertFalse((self.base / 'PatchOut/9/H4/MS').exists())
        self.assertTrue((self.base / 'BaselineSeed/9/H4/MS').is_file())
        result = self._build()['manifest']
        self.assertTrue(result['archive_metadata_ownership_verified'])
        before, after = (result['package'][key] for key in ('archive_metadata_before', 'archive_metadata_after'))
        self.assertEqual(before['entry_index'], 64155)
        self.assertEqual(before['relative_path'], self.base_toc_path)
        self.assertEqual(after['relative_path'], self.output_toc_path)
        self.assertEqual(before['log_sha256'], hash_file(self.package / 'ComparisonWork/baseline_listing.log'))
        self.assertEqual(after['log_sha256'], hash_file(self.package / 'Work/listing.log'))
        self.assertEqual(hash_file(self.package / 'PatchOut/9/H4/HM'), hash_file(self.base / 'PatchOut/9/H4/HM'))

    def test_unowned_seed_payload_does_not_acquire_metadata_exemption(self):
        self.fault = 'seed_as_extra_payload'
        with self.assertRaisesRegex(GarageError, 'Unowned physical files'):
            self._build()
        self._assert_rejected_output()

    def test_toc_load_evidence_must_be_unique_consistent_and_bound_to_exact_package(self):
        for fault in ('toc_missing_evidence', 'toc_duplicate_evidence', 'toc_index_mismatch', 'toc_wrong_package'):
            with self.subTest(fault=fault):
                self.fault = fault
                self.output = self.root / fault
                self.package = self.output / 'package'
                with self.assertRaises(GarageError):
                    self._build()
                self._assert_rejected_output()

    def test_header_toc_cannot_alias_ordinary_payload_even_when_load_log_names_it(self):
        self.fault = 'toc_alias'
        with self.assertRaisesRegex(GarageError, 'metadata aliases an ordinary logical payload'):
            self._build()
        self._assert_rejected_output()

    def test_reverification_pins_actual_toc_load_log_bytes(self):
        result = self._build()
        log = self.package / 'Work/listing.log'
        log.write_bytes(log.read_bytes() + b'changed since verification\n')
        with self.assertRaisesRegex(GarageError, 'package changed since its manifest'):
            verify_comparison_manifest(result['manifest_path'], result['manifest_sha256'])

    def test_failed_final_publication_removes_ready_manifest_and_retains_pack_evidence(self):
        def fail_final_hash(path):
            if Path(path).name == MANIFEST_NAME:
                raise OSError('simulated final manifest read failure')
            return hash_file(path)
        with patch('garagehub.runtime_comparison.hash_file', side_effect=fail_final_hash):
            with self.assertRaisesRegex(OSError, 'final manifest read failure'):
                self._build()
        self._assert_rejected_output()
        original = json.loads((self.package / 'package_receipt_before_comparison.json').read_text())
        self.assertTrue(original['passed'])

    def test_append_retains_prior_native_and_toc_bytes_with_exact_inactive_ownership(self):
        result = self._build()['manifest']['package']
        self.assertEqual(result['packaging_strategy'], 'append-on-approved-package')
        self.assertEqual(result['physical_file_count'], 22)
        inactive = result['inactive_baseline_payloads']
        self.assertEqual(set(inactive), {self.before[name]['packed'] for name in self.names} | {self.base_toc_path})
        for name in self.names:
            old = self.before[name]
            row = inactive[old['packed']]
            self.assertEqual(row['previous_logical_owner'], name)
            self.assertEqual(row['previous_file_index'], old['index'])
            self.assertEqual(row['previous_role'], 'selected-native-payload')
            self.assertEqual(row['sha256'], hash_file(self.base / 'PatchOut' / old['packed']))
            self.assertEqual(hash_file(self.package / 'PatchOut' / old['packed']), row['sha256'])
            self.assertNotIn(old['packed'], {row['packed'] for row in self.after.values()})
            self.assertNotIn(old['index'], {row['index'] for row in self.after.values()})
        self.assertEqual(inactive[self.base_toc_path]['previous_role'], 'header-authenticated-toc')
        self.assertEqual(inactive[self.base_toc_path]['previous_file_index'], self.base_toc_index)
        for name, digest in self._tree(self.base / 'PatchOut').items():
            if name != 'K/4D':
                self.assertEqual(hash_file(self.package / 'PatchOut' / name), digest)

    def test_unrelated_sector_offset_shift_is_rejected_before_merge_or_extraction(self):
        self.fault = 'unrelated_segindex'
        with self.assertRaisesRegex(GarageError, 'unrelated archive entry moved'):
            self._build()
        self.assertEqual(self.before[self.description]['index'], self.after[self.description]['index'])
        self.assertEqual(self.before[self.description]['packed'], self.after[self.description]['packed'])
        self.assertNotIn('unpack', self.calls)
        self.assertFalse((self.package / 'PatchOut').exists())
        self._assert_rejected_output()

    def test_append_cannot_reuse_prior_native_index_or_physical_path(self):
        for fault in ('reuse_old_index', 'reuse_old_native_path'):
            with self.subTest(fault=fault):
                self.fault = fault
                self.output = self.root / fault
                self.package = self.output / 'package'
                with self.assertRaisesRegex(GarageError, 'reused an approved|overwrite an approved'):
                    self._build()
                self.assertFalse((self.package / 'PatchOut').exists())
                self._assert_rejected_output()

    def test_packer_cannot_mutate_the_cloned_approved_append_seed(self):
        before = self._tree(self.base)
        self.fault = 'mutate_append_seed'
        with self.assertRaisesRegex(GarageError, 'append seed after packing'):
            self._build()
        self.assertEqual(before, self._tree(self.base))
        self.assertFalse((self.package / 'PatchOut').exists())
        self._assert_rejected_output()

    def test_inactive_payloads_cannot_be_changed_removed_or_invented_by_rewriting_local_receipt(self):
        for mutation in ('changed', 'removed', 'invented'):
            with self.subTest(mutation=mutation):
                self.output = self.root / mutation
                self.package = self.output / 'package'
                result = self._build()
                name = self.before[self.names[0]]['packed'] if mutation != 'invented' else '4/EE/FF'
                path = self.package / 'PatchOut' / name
                if mutation == 'removed':
                    path.unlink()
                else:
                    self._write(path, b'not the authenticated inactive baseline bytes')
                receipt_path = self.package / 'package_receipt.json'
                receipt = json.loads(receipt_path.read_text())
                receipt['files'] = [row for row in receipt['files'] if row['relative_path'] != name]
                if mutation != 'removed':
                    receipt['files'].append({'relative_path': name, 'bytes': path.stat().st_size, 'sha256': hash_file(path)})
                    receipt['inactive_baseline_payloads'][name] = {'sha256': hash_file(path), 'bytes': path.stat().st_size,
                                                                  'previous_role': 'unproven'}
                atomic_json(receipt_path, receipt)
                with self.assertRaisesRegex(GarageError, 'retained baseline payload'):
                    verify_comparison_manifest(result['manifest_path'], result['manifest_sha256'])


if __name__ == '__main__':
    unittest.main()
