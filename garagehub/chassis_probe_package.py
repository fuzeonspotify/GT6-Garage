"""Offline, DB-only append packages for a one-field chassis experiment.

This deliberately has a separate contract from runtime_comparison's eight
native assets. It cannot install, launch, change saves or feed the player
importer. Only the approved baseline and an independently verified SQLite
probe are accepted. All native assets and unrelated archive records stay exact.
"""
from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from . import car_pack, car_profiles, runtime_comparison as archive
from .chassis_probe import build_chassis_probe, plan_chassis_probe, verify_chassis_probe
from .identity_pipeline import DATABASE_KEY, DATABASE_PATH, database_context
from .packages import PFS_RE, source_package
from .util import atomic_json, hash_file


MANIFEST_NAME = 'chassis_probe_manifest.json'
KIND = 'same-identity-chassis-diagnostic-package'


def _diagnostic_physical_package(output, receipt, identity):
    _, manifest = source_package(output, identity['title_id'], identity['game_update'], allow_diagnostic=True)
    actual = archive._tree(output / 'PatchOut')
    expected = {row['path']: {'bytes': row['size'], 'sha256': row['sha256']} for row in manifest['files']}
    archive._need(actual == expected and archive.HEADER_PATH in actual,
                  'Physical diagnostic membership differs from its receipt.')
    return actual


def _baseline(path, receipt_sha256):
    path = archive._plain_path(path, directory=True)
    receipt = archive._pinned_json(path / 'package_receipt.json', receipt_sha256)
    identity = archive.reviewed_identity()
    archive._metadata_identity(receipt, identity)
    physical = archive._physical_package(path, receipt, identity)
    archive._need(receipt['header_sha256'] == car_pack.BASE_HEADER,
                  'Chassis experiments require the exact approved v029 baseline.')
    sources = archive._hash_map(receipt.get('source_virtual_assets'), 'Baseline source assets')
    archive._need(DATABASE_PATH in sources, 'The approved baseline database is missing.')
    native = {identity['model_path'] + '/' + name: digest for name, digest in car_profiles.ASSETS.items()}
    archive._need(len(native) == 8 and all(sources.get(name) == digest for name, digest in native.items()),
                  'The approved eight native assets are missing or differ from their reviewed profile hashes.')
    archive._checked_sources(path / 'Input', sources, 'Approved input assets')
    archive._checked_sources(path / 'BaselineSeed', car_pack.SEED, 'Approved original seed')
    return {'path': str(path), 'receipt_sha256': receipt_sha256, 'physical': physical,
            'sources': sources, 'listing_sha256': hash_file(path / 'packed_entries.txt'),
            'identity': identity}


def _isolated_output(path, baseline):
    path = archive._plain_path(path, missing=True)
    archive._need(not path.exists() and path.parent.is_dir(), 'Choose a fresh diagnostic output in an existing directory.')
    archive._need(path != baseline and baseline not in path.parents and path not in baseline.parents,
                  'The diagnostic output must be separate from the approved package.')
    return path


def build_chassis_probe_package(baseline, packer, output, *, baseline_receipt_sha256,
                               trial='wheelbase_delta_mm', delta_mm=300, cancel=None, progress=None):
    """Produce a private diagnostic package; never export/install it as a car."""
    packer = archive._plain_path(packer)
    car_pack.trusted_packer(packer)
    checked = _baseline(baseline, baseline_receipt_sha256)
    baseline = Path(checked['path'])
    output = _isolated_output(output, baseline)
    archive._need(trial in ('wheelbase_delta_mm', 'tread_front_delta_mm'),
                  'A package requires one explicit changed chassis trial.')
    output.mkdir()
    notify = progress or (lambda message: None)
    try:
        notify('Decoding the authenticated baseline database into an isolated diagnostic workspace')
        context = database_context(baseline, packer, output / 'DatabaseCache', cancel)
        archive._need(context['encrypted_sha256'] == checked['sources'][DATABASE_PATH]
                      and context['baseline_header'] == car_pack.BASE_HEADER,
                      'The decoded database does not belong to this exact approved baseline.')
        source_db = Path(context['database'])
        db_work = output / 'DatabaseWork'
        db_work.mkdir()
        plan = plan_chassis_probe(source_db, car_id=checked['identity']['car_id'], trial=trial, delta_mm=delta_mm)
        atomic_json(db_work / 'plan.json', plan)
        changed_db = db_work / 'changed.sqlite'
        build_chassis_probe(source_db, changed_db, plan)
        verify_chassis_probe(source_db, changed_db, plan)
        encrypted = output / 'DeltaInput' / DATABASE_PATH
        encrypted.parent.mkdir(parents=True)
        shutil.copyfile(changed_db, encrypted)
        car_pack.execute(packer, ['cryptsalsa', '-i', encrypted, '--key', DATABASE_KEY], db_work, 'encode.log', cancel)
        roundtrip = db_work / 'roundtrip.sqlite'
        shutil.copyfile(encrypted, roundtrip)
        car_pack.execute(packer, ['cryptsalsa', '-i', roundtrip, '--key', DATABASE_KEY], db_work, 'roundtrip.log', cancel)
        archive._need(hash_file(roundtrip) == hash_file(changed_db), 'The probe database failed independent crypto roundtrip.')
        selected = {DATABASE_PATH: hash_file(encrypted)}
        archive._need(selected[DATABASE_PATH] != checked['sources'][DATABASE_PATH], 'The changed diagnostic database is identical to baseline.')
        expected = {**checked['sources'], **selected}
        physical_hashes = archive._tree_hashes(checked['physical'])
        car_pack.execute(packer, ['listfiles', '-i', baseline / 'PatchOut', '-o', output / 'baseline_entries.txt'],
                         output / 'Work', 'baseline_listing.log', cancel)
        before = archive._listing(output / 'baseline_entries.txt')
        archive._need(before == archive._listing(baseline / 'packed_entries.txt'), 'The approved listing is stale.')
        old_metadata = archive._archive_metadata(output / 'Work/baseline_listing.log', baseline / 'PatchOut', before, checked['physical'])
        archive._copy_files(baseline / 'Input', output / 'Input', checked['sources'], cancel)
        archive._copy_files(output / 'DeltaInput', output / 'Input', selected, cancel)
        archive._copy_files(baseline / 'BaselineSeed', output / 'BaselineSeed', car_pack.SEED, cancel)
        archive._copy_files(baseline / 'PatchOut', output / 'AppendSeed', physical_hashes, cancel)
        notify('Appending only the changed database; retaining every native asset and unrelated archive record')
        car_pack.execute(packer, ['pack', '-i', output / 'AppendSeed', '-p', output / 'DeltaInput', '-o', output / 'DeltaPatch'],
                         output / 'Work', 'pack.log', cancel)
        car_pack.execute(packer, ['listfiles', '-i', output / 'DeltaPatch', '-o', output / 'delta_entries.txt'],
                         output / 'Work', 'delta_listing.log', cancel)
        after = archive._listing(output / 'delta_entries.txt')
        delta = archive._tree(output / 'DeltaPatch')
        archive._need(all(PFS_RE.fullmatch(name) for name in delta), 'The diagnostic delta contains an invalid archive path.')
        new_metadata = archive._archive_metadata(output / 'Work/delta_listing.log', output / 'DeltaPatch', after, delta)
        proof = archive._append_proof(before, after, selected, checked['physical'], delta, old_metadata, new_metadata)
        archive._copy_files(baseline / 'PatchOut', output / 'PatchOut', physical_hashes, cancel)
        ordered = {name: delta[name]['sha256'] for name in sorted(delta, key=lambda name: (name == archive.HEADER_PATH, name))}
        archive._copy_files(output / 'DeltaPatch', output / 'PatchOut', ordered, cancel)
        car_pack.execute(packer, ['listfiles', '-i', output / 'PatchOut', '-o', output / 'packed_entries.txt'],
                         output / 'Work', 'listing.log', cancel)
        archive._need(archive._listing(output / 'packed_entries.txt') == after, 'The merged diagnostic index changed.')
        physical = archive._tree(output / 'PatchOut')
        archive._need(physical == {**checked['physical'], **delta}, 'An approved physical payload changed or disappeared.')
        archive._archive_metadata(output / 'Work/listing.log', output / 'PatchOut', after, physical, proof['inactive'])
        for ordinal, (name, digest) in enumerate(expected.items(), 1):
            notify(f'Independent diagnostic extraction {ordinal} / {len(expected)}')
            car_pack.execute(packer, ['unpack', '-i', output / 'PatchOut', '-o', output / 'Verified', '--indices', after[name]['index']],
                             output / 'Work', f'extract_{after[name]["index"]}.log', cancel)
            archive._need(hash_file(output / 'Verified' / name) == digest, 'Diagnostic extraction differs: ' + name)
        receipt = {'passed': True, 'candidate_id': output.name, 'kind': KIND, 'developer_only': True,
                   'player_installable': False, 'product_car': False, 'additional_car_database_entry': False,
                   'installation': None, 'source_roundtrip_match': True, 'all_unrelated_archive_entries_preserved': True,
                   'native_assets_byte_identical': True, 'supra_physics_unchanged': False,
                   'runtime_tested': False, 'runtime_relationship_established': False, 'physical_ps3_tested': False,
                   'game_installation_changed': False, 'source_virtual_assets': expected,
                   'header_sha256': physical[archive.HEADER_PATH]['sha256'],
                   'files': [{'relative_path': name, **value} for name, value in sorted(physical.items())],
                   'changed_virtual_assets': [DATABASE_PATH], 'allowed_database_change': plan['allowed_change'],
                   'baseline_header_sha256': car_pack.BASE_HEADER, 'packaging_strategy': 'append-on-approved-package',
                   'total_payload_bytes': sum(value['bytes'] for value in physical.values()),
                   **{key: value for key, value in checked['identity'].items() if key != 'baseline_header_sha256'}}
        atomic_json(output / 'package_receipt.json', receipt)
        evidence_names = ['package_receipt.json', 'DatabaseWork/plan.json', 'DatabaseWork/changed.sqlite',
                          'DatabaseWork/changed.sqlite.probe.json', 'DatabaseWork/roundtrip.sqlite',
                          'baseline_entries.txt', 'delta_entries.txt', 'packed_entries.txt',
                          'Work/baseline_listing.log', 'Work/delta_listing.log', 'Work/listing.log']
        evidence = {name: hash_file(output / name) for name in evidence_names}
        manifest = {'schema': 1, 'kind': KIND, 'developer_only': True, 'player_installable': False,
                    'runtime_relationship_established': False, 'baseline': checked,
                    'identity': checked['identity'], 'package_root': str(output),
                    'source_database': str(source_db), 'source_database_sha256': hash_file(source_db),
                    'decoded_receipt_sha256': hash_file(source_db.parent / 'decoded.json'),
                    'packer': str(packer), 'packer_sha256': car_pack.PACKER_SHA,
                    'evidence': evidence, 'selected_assets': selected,
                    'inactive_baseline_payloads': proof['inactive']}
        atomic_json(output / MANIFEST_NAME, manifest)
        verified = verify_chassis_probe_manifest(output / MANIFEST_NAME, hash_file(output / MANIFEST_NAME))
        notify('Database-only diagnostic package verified; no game has been launched or installed')
        return verified
    except Exception as exc:
        # Keep failed build evidence, but never leave an apparently usable package.
        for name in ('package_receipt.json', MANIFEST_NAME):
            path = output / name
            if path.is_file():
                path.rename(output / (name + '.rejected'))
        atomic_json(output / 'FAILED.json', {'release_ready': False, 'installed_game_unchanged': True,
                                           'error_type': type(exc).__name__, 'error': str(exc)})
        raise


def verify_chassis_probe_manifest(path, expected_sha256):
    """Recheck the complete package proof without installing or launching."""
    path = archive._plain_path(path)
    manifest = archive._pinned_json(path, expected_sha256)
    output = path.parent
    archive._need(type(manifest.get('schema')) is int and manifest['schema'] == 1 and manifest.get('kind') == KIND
                  and manifest.get('developer_only') is True and manifest.get('player_installable') is False
                  and manifest.get('runtime_relationship_established') is False,
                  'The chassis manifest has a different experiment contract.')
    archive._need(Path(manifest['package_root']).resolve() == output, 'The diagnostic package was moved after verification.')
    car_pack.trusted_packer(archive._plain_path(manifest['packer']))
    archive._need(manifest['packer_sha256'] == car_pack.PACKER_SHA, 'The diagnostic packer identity changed.')
    baseline = _baseline(manifest['baseline']['path'], manifest['baseline']['receipt_sha256'])
    archive._need(baseline == manifest['baseline'] and manifest['identity'] == baseline['identity'],
                  'The approved baseline or comparison identity changed.')
    evidence = archive._hash_map(manifest['evidence'], 'Diagnostic evidence')
    required = {'package_receipt.json', 'DatabaseWork/plan.json', 'DatabaseWork/changed.sqlite',
                'DatabaseWork/changed.sqlite.probe.json', 'DatabaseWork/roundtrip.sqlite',
                'baseline_entries.txt', 'delta_entries.txt', 'packed_entries.txt',
                'Work/baseline_listing.log', 'Work/delta_listing.log', 'Work/listing.log'}
    archive._need(set(evidence) == required, 'The diagnostic evidence set is incomplete.')
    for name, digest in evidence.items():
        archive._need(hash_file(archive._plain_path(output / name)) == digest, 'Diagnostic evidence changed: ' + name)
    source_db = archive._plain_path(manifest['source_database'])
    archive._need(output in source_db.parents and hash_file(source_db) == manifest['source_database_sha256'],
                  'The private decoded baseline changed.')
    decoded = archive._pinned_json(source_db.parent / 'decoded.json', manifest['decoded_receipt_sha256'])
    archive._need(decoded == {'encrypted_sha256': baseline['sources'][DATABASE_PATH],
                             'plain_sha256': manifest['source_database_sha256']},
                  'Decoded database lineage differs from the approved encrypted asset.')
    plan = archive._pinned_json(output / 'DatabaseWork/plan.json', evidence['DatabaseWork/plan.json'])
    archive._need(plan['selection']['car_id'] == baseline['identity']['car_id']
                  and plan['request']['trial'] in ('wheelbase_delta_mm', 'tread_front_delta_mm'),
                  'The database experiment targets a different car or unsupported trial.')
    verification = verify_chassis_probe(source_db, output / 'DatabaseWork/changed.sqlite', plan)
    archive._need(evidence['DatabaseWork/changed.sqlite'] == evidence['DatabaseWork/roundtrip.sqlite'],
                  'The encrypted candidate failed its recorded independent decode.')
    selected = archive._hash_map(manifest['selected_assets'], 'Selected diagnostic asset')
    archive._need(set(selected) == {DATABASE_PATH} and selected[DATABASE_PATH] != baseline['sources'][DATABASE_PATH],
                  'The experiment must append exactly one changed database asset.')
    expected = {**baseline['sources'], **selected}
    for folder, assets in (('DeltaInput', selected), ('Input', expected), ('Verified', expected),
                           ('AppendSeed', archive._tree_hashes(baseline['physical'])), ('BaselineSeed', car_pack.SEED)):
        archive._checked_sources(output / folder, assets, 'Diagnostic ' + folder)
    # Re-decode the actual approved encrypted input and the independently
    # extracted candidate. Recorded plaintext files or a self-consistent
    # decoded.json cannot establish this relationship on their own.
    with tempfile.TemporaryDirectory(prefix='gt6_chassis_verify_') as temporary:
        temporary = Path(temporary)
        encrypted_sources = (
            ('baseline', Path(baseline['path']) / 'Input' / DATABASE_PATH, manifest['source_database_sha256']),
            ('candidate', output / 'Verified' / DATABASE_PATH, evidence['DatabaseWork/changed.sqlite']),
        )
        for label, encrypted_path, plain_digest in encrypted_sources:
            encrypted_digest = hash_file(encrypted_path)
            decoded_path = temporary / (label + '.sqlite')
            shutil.copyfile(encrypted_path, decoded_path)
            car_pack.execute(Path(manifest['packer']), ['cryptsalsa', '-i', decoded_path, '--key', DATABASE_KEY],
                             temporary / 'Work', label + '_decode.log', None)
            archive._need(hash_file(decoded_path) == plain_digest and hash_file(encrypted_path) == encrypted_digest,
                          'Actual encrypted ' + label + ' database does not decode to the pinned SQLite bytes.')
    before = archive._listing(output / 'baseline_entries.txt')
    after = archive._listing(output / 'delta_entries.txt')
    archive._need(before == archive._listing(Path(baseline['path']) / 'packed_entries.txt')
                  and after == archive._listing(output / 'packed_entries.txt'), 'The diagnostic archive listings changed.')
    delta = archive._tree(output / 'DeltaPatch')
    old_metadata = archive._archive_metadata(output / 'Work/baseline_listing.log', Path(baseline['path']) / 'PatchOut', before, baseline['physical'])
    new_metadata = archive._archive_metadata(output / 'Work/delta_listing.log', output / 'DeltaPatch', after, delta)
    proof = archive._append_proof(before, after, selected, baseline['physical'], delta, old_metadata, new_metadata)
    archive._need(proof['inactive'] == manifest['inactive_baseline_payloads'], 'The old database/TOC ownership changed.')
    receipt = archive._pinned_json(output / 'package_receipt.json', evidence['package_receipt.json'])
    archive._metadata_identity(receipt, baseline['identity'], required=True)
    physical = _diagnostic_physical_package(output, receipt, baseline['identity'])
    archive._need(physical == {**baseline['physical'], **delta}, 'Unrelated physical archive payloads changed.')
    archive._archive_metadata(output / 'Work/listing.log', output / 'PatchOut', after, physical, proof['inactive'])
    archive._need(receipt.get('kind') == KIND and receipt.get('developer_only') is True
                  and receipt.get('player_installable') is False and receipt.get('native_assets_byte_identical') is True
                  and receipt.get('supra_physics_unchanged') is False
                  and receipt.get('runtime_relationship_established') is False and receipt.get('runtime_tested') is False
                  and receipt.get('source_virtual_assets') == expected
                  and receipt.get('allowed_database_change') == plan['allowed_change']
                  and receipt.get('changed_virtual_assets') == [DATABASE_PATH], 'The diagnostic receipt overstates or misidentifies the result.')
    return {'verified': True, 'identity': baseline['identity'], 'baseline_header_sha256': car_pack.BASE_HEADER,
            'candidate_header_sha256': physical[archive.HEADER_PATH]['sha256'],
            'baseline_physical_files': baseline['physical'], 'candidate_physical_files': physical,
            'allowed_database_change': plan['allowed_change'], 'database_verification': verification,
            'source_assets_verified': len(expected), 'native_assets_byte_identical': True,
            'actual_encrypted_databases_independently_decoded': True,
            'runtime_relationship_established': False, 'player_installable': False}
