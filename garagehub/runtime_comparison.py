"""Offline, same-identity native comparison packages for the reviewed profile.

This uses the reviewed append-only executable with diagnostic-only orchestration.
The production packer is unchanged. This module cannot install, launch,
send input, touch saves, acquire a lease, or change a scheduler. The caller must
supply independently recorded receipt hashes and an explicit identity. A valid
comparison manifest means that the packaging variables were controlled; it is
not evidence of an observed game state or of runtime acceptance.

The reviewed packer currently supports one native profile. The checks are not
source-model-specific, but accepting another donor/profile requires its own
reviewed packer contract. No native bytes, identity rows, or physics are edited
here. Only a new output directory is written.
"""
from __future__ import annotations

import hashlib
import json
import re
import shutil
from pathlib import Path

from . import car_pack, car_profiles
from .errors import GarageError
from .native import MDL3
from .packages import PFS_RE, source_package
from .util import atomic_bytes, atomic_json, hash_file, safe_rel


MANIFEST_NAME = 'runtime_comparison_manifest.json'
BASELINE_LISTING_NAME = 'comparison_baseline_entries.txt'
SEED_PATHS = frozenset(('K/4D', '9/H4/MS'))
HEADER_PATH = 'K/4D'
_SHA = re.compile(r'[0-9a-f]{64}\Z')


def _need(condition, message):
    if not condition:
        raise GarageError(message, 'runtime_comparison')


def _digest(value, label):
    _need(isinstance(value, str) and bool(_SHA.fullmatch(value)),
          label + ' must be an explicitly recorded SHA-256.')
    return value


def _plain_path(path, *, directory=False, missing=False):
    path = Path(path).absolute()
    for part in (path, *path.parents):
        _need(not part.is_symlink() and not getattr(part, 'is_junction', lambda: False)(),
              'Comparison paths must not contain filesystem links: ' + str(part))
    if not missing:
        _need(path.is_dir() if directory else path.is_file(),
              'Required comparison ' + ('directory' if directory else 'file') + ' is missing: ' + str(path))
    return path.resolve()


def _unique_object(pairs):
    out = {}
    for key, value in pairs:
        _need(key not in out, 'Comparison JSON contains a duplicate key: ' + key)
        out[key] = value
    return out


def _pinned_json(path, expected_sha256):
    path = _plain_path(path)
    _digest(expected_sha256, 'Receipt pin')
    _need(path.stat().st_size <= 16 * 1024**2, 'Comparison receipt exceeds the JSON size budget.')
    raw = path.read_bytes()
    _need(hashlib.sha256(raw).hexdigest() == expected_sha256,
          'Receipt differs from its independently recorded hash: ' + str(path))
    try:
        value = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_unique_object,
                           parse_constant=lambda x: (_ for _ in ()).throw(ValueError(x)))
    except (UnicodeError, ValueError) as exc:
        raise GarageError('Comparison receipt is not valid, unique-key JSON: ' + str(path),
                          'runtime_comparison') from exc
    _need(isinstance(value, dict), 'Comparison receipt must be a JSON object.')
    return value


def _hash_map(value, label):
    _need(isinstance(value, dict) and bool(value), label + ' must be a nonempty asset hash map.')
    out = {}
    folded = set()
    for name, digest in value.items():
        name = safe_rel(name)
        _need(name.casefold() not in folded, label + ' contains a Windows path alias: ' + name)
        folded.add(name.casefold())
        out[name] = _digest(digest, label + ': ' + name)
    return out


def _tree(root):
    """Exact regular-file membership, with Windows aliases rejected on every OS."""
    root = _plain_path(root, directory=True)
    result = {}
    folded = set()
    for path in sorted(root.rglob('*')):
        _plain_path(path, directory=path.is_dir())
        name = safe_rel(path.relative_to(root).as_posix())
        _need(name.casefold() not in folded, 'Comparison tree contains a Windows path alias: ' + name)
        folded.add(name.casefold())
        if path.is_file():
            result[name] = {'sha256': hash_file(path), 'bytes': path.stat().st_size}
    return result


def _tree_hashes(tree):
    return {name: row['sha256'] for name, row in tree.items()}


def _checked_sources(root, expected, label):
    tree = _tree(root)
    _need(_tree_hashes(tree) == expected, label + ' file membership or bytes differ from the pinned receipt.')
    return tree


def reviewed_identity():
    """Return a copy of the identity supported by the current nonrouted packer."""
    p = car_profiles.PROFILE
    return {'profile_id': car_profiles.PROFILE_ID, 'car_id': p['car_id'],
            'model_path': p['model_path'], 'title_id': p['title_id'],
            'game_update': p['game_update'], 'baseline_header_sha256': car_pack.BASE_HEADER}


def _identity(value):
    expected = reviewed_identity()
    _need(isinstance(value, dict) and value == expected and type(value.get('car_id')) is int,
          'The comparison must explicitly retain the reviewed baseline identity, model route, region and update.')
    _need(set(car_pack.SEED) == SEED_PATHS,
          'The reviewed seed-input ownership changed; review the comparison contract before packaging.')
    return dict(value)


def _metadata_identity(receipt, identity, *, required=False):
    fields = ('profile_id', 'car_id', 'model_path', 'title_id', 'game_update')
    for name in fields:
        if name in receipt or (required and name in ('profile_id', 'car_id', 'model_path')):
            _need(receipt.get(name) == identity[name], 'Receipt has a different logical identity: ' + name)
            if name == 'car_id':
                _need(type(receipt[name]) is int, 'A car identity must be an integer, not a Boolean.')


def _listing(path):
    path = _plain_path(path)
    rows = car_pack.listing(path)
    names = set()
    for name, row in rows.items():
        _need(name.casefold() not in names, 'Archive listing contains a Windows logical-name alias: ' + name)
        names.add(name.casefold())
        packed = safe_rel(row['packed'])
        _need(bool(PFS_RE.fullmatch(packed)), 'Archive listing contains an invalid physical path: ' + packed)
    return rows


def _selected_payloads(rows, names, physical, index_paths=frozenset((HEADER_PATH,))):
    _need(set(names) <= set(rows), 'The original model route is missing from the archive namespace.')
    selected = {rows[name]['packed'] for name in names}
    indices = {rows[name]['index'] for name in names}
    _need(len(selected) == len(names) and len(indices) == len(names),
          'Selected native assets must have distinct physical payload and index owners.')
    _need(not selected & index_paths and selected <= set(physical),
          'Selected native payloads are missing or overlap the reviewed archive index.')
    for name, row in rows.items():
        if name not in names:
            _need(row['packed'] not in selected and row['index'] not in indices,
                  'An unrelated logical asset aliases a selected native payload: ' + name)
    return selected


def _physical_package(root, receipt, identity):
    _, manifest = source_package(root, identity['title_id'], identity['game_update'])
    _need(receipt.get('passed') is True and receipt.get('source_roundtrip_match') is True,
          'The package must have a successful recorded extraction check.')
    actual = _tree(root / 'PatchOut')
    expected = {row['path']: {'bytes': row['size'], 'sha256': row['sha256']}
                for row in manifest['files']}
    _need(actual == expected, 'Physical package membership differs from its receipt; unlisted payloads are not allowed.')
    _need(HEADER_PATH in actual, 'The package is missing its archive header.')
    return actual


def _archive_metadata(log_path, patch_root, rows, physical, inactive=None):
    """Authenticate emitted TOC ownership from a fresh pinned-packer load log.

    Seed inputs are not emitted metadata paths. The actual approved package uses
    a different TOC from its BaselineSeed. Reviewed GTToolsSharp commit
    8ad944caf9c1c9c113a070a20bbf668cddc91315 logs ToCNodeIndex in
    Headers/FileDeviceGTFS2Header.cs:66, then derives the relative path using
    PDIPFSPathResolver.GetPathFromSeed in Volumes/GTVolumePFS.cs:246-261.
    Require both index statements and the derived full path to agree; merely
    being absent from the logical listing never establishes metadata ownership.
    """
    log_path = _plain_path(log_path)
    _need(log_path.stat().st_size <= 16 * 1024**2, 'Archive load log exceeds the evidence size budget.')
    raw = log_path.read_bytes()
    try:
        lines = raw.decode('utf-8-sig').splitlines()
    except UnicodeError as exc:
        raise GarageError('Archive load evidence is not valid UTF-8.', 'runtime_comparison') from exc
    patterns = (
        r'\[>\] Table of Contents Entry Index: ([0-9]+)',
        r'\[!\] Volume Patch Path Table of contents located at: (.+)',
        r'\[-\] TOC Entry is ([0-9]+) which is at ([0-9A-Z/]+) - decrypting it',
    )
    matches = [[match.groups() for line in lines if (match := re.fullmatch(pattern, line))]
               for pattern in patterns]
    _need(all(len(found) == 1 for found in matches),
          'Fresh archive load evidence must identify exactly one header-derived TOC index and path.')
    header_index = int(matches[0][0][0])
    loaded_index, toc_path = matches[2][0]
    toc_path = safe_rel(toc_path)
    _need(0 < header_index <= 0xffffffff and header_index == int(loaded_index),
          'The fresh header and loaded TOC entry indices disagree.')
    _need(bool(PFS_RE.fullmatch(toc_path)) and toc_path != HEADER_PATH,
          'The header-derived TOC path is not a distinct valid physical payload.')
    patch_root = _plain_path(patch_root, directory=True)
    reported_path = Path(matches[1][0][0])
    _need(reported_path.is_absolute() and reported_path.resolve() == patch_root / toc_path,
          'The reported TOC location does not belong to this exact physical archive directory.')
    index_paths = {HEADER_PATH, toc_path}
    logical_paths = {row['packed'] for row in rows.values()}
    logical_indices = {row['index'] for row in rows.values()}
    _need(header_index not in logical_indices and not index_paths & logical_paths,
          'Header-derived metadata aliases an ordinary logical payload owner.')
    inactive = set(inactive or ())
    _need(not inactive & (index_paths | logical_paths),
          'An authenticated inactive baseline payload acquired a current archive owner.')
    _need(set(physical) - logical_paths == index_paths | inactive,
          'Unowned physical files must be exactly K/4D, the authenticated header-derived TOC '
          'and individually proven inactive baseline payloads.')
    return {'kind': 'fresh-pinned-packer-header-toc', 'entry_index': header_index,
            'relative_path': toc_path, 'reported_full_path': str(reported_path),
            'header_sha256': physical[HEADER_PATH]['sha256'],
            'toc_sha256': physical[toc_path]['sha256'],
            'log_path': str(log_path), 'log_sha256': hashlib.sha256(raw).hexdigest(),
            'packer_sha256': car_pack.PACKER_SHA}


def verify_comparison_inputs(candidate, baseline, reference_receipt, *, identity,
                             reference_sha256, reference_version, build_receipt_sha256,
                             baseline_receipt_sha256, reference_assets_key='native_supra_assets'):
    """Read-only preflight with independently supplied receipt pins.

    ``reference_receipt`` is a passed static geometry audit with ``version``,
    ``scope``, ``source_sha256`` and the selected complete eight-asset hash map.
    Its version and exact file hash must both be supplied by the caller. The
    historical baseline may omit modern identity metadata; its approved header,
    native template hashes and independently pinned full receipt remain required.
    """
    identity = _identity(identity)
    candidate = _plain_path(candidate, directory=True)
    baseline = _plain_path(baseline, directory=True)
    reference_receipt = _plain_path(reference_receipt)
    _need(candidate != baseline and candidate not in baseline.parents and baseline not in candidate.parents,
          'The native candidate and approved baseline must be separate directories.')
    _need(isinstance(reference_version, str) and bool(reference_version.strip()),
          'An explicit reference application version is required.')
    _need(isinstance(reference_assets_key, str) and bool(reference_assets_key), 'The reference asset-map field is required.')
    reference = _pinned_json(reference_receipt, reference_sha256)
    _need(reference.get('passed') is True and reference.get('version') == reference_version,
          'The pinned geometry reference is not a passed audit of the requested version.')
    _need(isinstance(reference.get('scope'), str) and bool(reference['scope'].strip()),
          'The geometry reference must state the scope of its evidence.')
    source_sha = _digest(reference.get('source_sha256'), 'Geometry reference source')
    selected = {identity['model_path'] + '/' + rel for rel in car_profiles.ASSETS}
    _need(len(selected) == 8, 'The eight-asset native contract changed; review it before comparison.')
    native = _hash_map(reference.get(reference_assets_key), 'Recorded native output')
    _need(set(native) == selected, 'Reference must contain exactly all eight assets at the approved original model route.')

    build = _pinned_json(candidate / 'build_receipt.json', build_receipt_sha256)
    _metadata_identity(build, identity, required=True)
    _need(build.get('passed') is True and build.get('kind') == 'profile-native-exterior-candidate',
          'Comparison requires the original nonrouted native candidate, not a new-entry or replacement package.')
    _need(build.get('additional_database_entry') is False and build.get('installation') is None,
          'The comparison cannot introduce a destination plan or database entry.')
    _need(build.get('source_sha256') == source_sha, 'Native candidate source differs from the pinned geometry reference.')
    _need(_hash_map(build.get('source_virtual_assets'), 'Native build') == native,
          'Native build receipt differs from the recorded current eight-asset map.')
    candidate_tree = _tree(candidate)
    _need(set(candidate_tree) == selected | {'build_receipt.json'},
          'The diagnostic native candidate must contain only its eight native assets and build receipt.')
    for name, digest in native.items():
        _need(candidate_tree[name]['sha256'] == digest, 'Current native asset differs from the recorded reference: ' + name)
        if Path(name).name in ('body', 'wheel', 'interior'):
            MDL3((candidate / name).read_bytes()).validate()

    original = _pinned_json(baseline / 'package_receipt.json', baseline_receipt_sha256)
    _metadata_identity(original, identity)
    _need(original.get('header_sha256') == identity['baseline_header_sha256'],
          'This comparison requires the exact approved baseline header, not a routed descendant.')
    car_pack.baseline(baseline)
    old = _hash_map(original.get('source_virtual_assets'), 'Approved baseline')
    template = {identity['model_path'] + '/' + rel: digest for rel, digest in car_profiles.ASSETS.items()}
    _need(all(old.get(name) == digest for name, digest in template.items()),
          'The approved original native template is missing or differs from its reviewed hashes.')
    baseline_input = _checked_sources(baseline / 'Input', old, 'Approved baseline Input')
    physical = _physical_package(baseline, original, identity)
    seed = _checked_sources(baseline / 'BaselineSeed', car_pack.SEED, 'Approved baseline seed')
    before = _listing(baseline / 'packed_entries.txt')
    _need(set(old) <= set(before), 'A retained baseline logical input is absent from its archive listing.')
    _selected_payloads(before, selected, physical)

    return {
        'schema': 1, 'kind': 'same-identity-comparison-inputs', 'ready_for_packaging': True,
        'identity': identity, 'source_sha256': source_sha, 'native_assets': native,
        'reference_scope': reference['scope'],
        'input_paths': {'candidate': str(candidate), 'baseline': str(baseline),
                        'reference_receipt': str(reference_receipt)},
        'pins': {'reference_sha256': reference_sha256, 'reference_version': reference_version,
                 'reference_assets_key': reference_assets_key,
                 'build_receipt_sha256': build_receipt_sha256,
                 'baseline_receipt_sha256': baseline_receipt_sha256},
        'baseline_sources': old,
        'snapshot': {'candidate': candidate_tree, 'baseline_input': baseline_input,
                     'baseline_physical': physical, 'baseline_seed': seed,
                     'baseline_listing_sha256': hash_file(baseline / 'packed_entries.txt')},
        'baseline_archive_listing_reverified': False,
        'archive_metadata_ownership_verified': False,
        'runtime_verified': False, 'physical_ps3_verified': False,
    }


def _unchanged_inputs(preflight):
    paths = preflight['input_paths']
    current = verify_comparison_inputs(**paths, identity=preflight['identity'], **preflight['pins'])
    _need(current == preflight, 'A pinned comparison input changed during or after packaging.')


def _same_namespace(before, after, native):
    _need(set(after) == set(before), 'The comparison changed the complete logical archive namespace.')
    for name in before:
        if name not in native:
            _need(before[name] == after[name], 'An unrelated archive entry moved or changed: ' + name)


def _append_proof(before, after, native, old_physical, delta, old_metadata, new_metadata):
    """Prove only the eight selected bindings were appended; retain exact old owners."""
    _same_namespace(before, after, native)
    old_index_paths = {HEADER_PATH, old_metadata['relative_path']}
    new_index_paths = {HEADER_PATH, new_metadata['relative_path']}
    old_selected = _selected_payloads(before, native, old_physical, old_index_paths)
    new_selected = _selected_payloads(after, native, delta, new_index_paths)
    _need(set(delta) == new_index_paths | new_selected,
          'Append delta must contain only the eight current native payloads and its authenticated metadata.')
    old_logical_paths = {row['packed'] for row in before.values()}
    old_indices = {row['index'] for row in before.values()} | {old_metadata['entry_index']}
    new_indices = {after[name]['index'] for name in native} | {new_metadata['entry_index']}
    _need(not new_indices & old_indices,
          'The append delta reused an approved file/TOC index instead of allocating new entries.')
    _need(not (set(delta) - {HEADER_PATH}) & (set(old_physical) | old_logical_paths),
          'The append delta would overwrite an approved or unrelated physical payload.')
    current_paths = {row['packed'] for row in after.values()}
    current_indices = {row['index'] for row in after.values()}
    inactive = {}
    for name in sorted(native):
        row = before[name]
        _need(row['packed'] not in current_paths and row['index'] not in current_indices,
              'The prior selected payload still has a current logical owner: ' + name)
        inactive[row['packed']] = {**old_physical[row['packed']], 'previous_role': 'selected-native-payload',
                                   'previous_logical_owner': name, 'previous_file_index': row['index']}
    old_toc = old_metadata['relative_path']
    _need(old_toc not in current_paths and old_metadata['entry_index'] not in current_indices,
          'The previous TOC still has a current ordinary logical owner.')
    inactive[old_toc] = {**old_physical[old_toc], 'previous_role': 'header-authenticated-toc',
                         'previous_logical_owner': None, 'previous_file_index': old_metadata['entry_index']}
    _need(set(inactive) == old_selected | {old_toc}, 'Inactive baseline ownership is incomplete.')
    return {'inactive': inactive, 'selected_before': old_selected, 'selected_after': new_selected,
            'index_before': old_index_paths, 'index_after': new_index_paths}


def _copy_files(source, destination, expected, cancel=None):
    destination.mkdir(parents=True, exist_ok=True)
    _plain_path(destination, directory=True)
    for name, digest in expected.items():
        car_pack.check_cancel(cancel)
        name = safe_rel(name)
        source_path = _plain_path(source / name)
        target = _plain_path(destination / name, missing=True)
        if target.exists():
            _need(target.is_file() and target.stat().st_nlink == 1, 'A staged output is not an independent regular file.')
        target.parent.mkdir(parents=True, exist_ok=True)
        shutil.copyfile(source_path, target)
        _need(hash_file(target) == digest, 'Staged copy differs from its pinned source: ' + name)


def _pack_append(output, preflight, packer, cancel=None, progress=None):
    """Append only the selected assets onto an exact scratch copy of the control.

    Repacking all baseline Input files from the original seed changes unrelated
    SectorOffset/SegIndex values when preceding native lengths change. The pinned
    always_append patch instead preserves existing FileInfoKey records and starts
    new selected entries at NextSectorIndex. Fresh complete listings still have
    to prove that preservation; the implementation is not trusted by assertion.
    """
    notify = lambda message: progress(message) if progress else None
    paths = preflight['input_paths']
    baseline, candidate = Path(paths['baseline']), Path(paths['candidate'])
    native, old = preflight['native_assets'], preflight['baseline_sources']
    old_physical = preflight['snapshot']['baseline_physical']
    physical_hashes = _tree_hashes(old_physical)
    expected = {**old, **native}
    output.mkdir(exist_ok=False)
    notify('Authenticating the complete approved archive before appending current native assets')
    car_pack.execute(packer, ['listfiles', '-i', baseline / 'PatchOut', '-o', output / BASELINE_LISTING_NAME],
                     output / 'ComparisonWork', 'baseline_listing.log', cancel)
    before = _listing(baseline / 'packed_entries.txt')
    fresh = _listing(output / BASELINE_LISTING_NAME)
    _need(before == fresh, 'The cached baseline listing differs from a fresh listing of its approved index.')
    old_metadata = _archive_metadata(output / 'ComparisonWork/baseline_listing.log', baseline / 'PatchOut',
                                     fresh, old_physical)
    _copy_files(baseline / 'Input', output / 'Input', old, cancel)
    _copy_files(candidate, output / 'Input', native, cancel)
    _copy_files(baseline / 'BaselineSeed', output / 'BaselineSeed', car_pack.SEED, cancel)
    _copy_files(baseline / 'PatchOut', output / 'AppendSeed', physical_hashes, cancel)
    _copy_files(candidate, output / 'DeltaInput', native, cancel)
    _checked_sources(output / 'Input', expected, 'Packaged Input')
    _checked_sources(output / 'AppendSeed', physical_hashes, 'Approved append seed')
    _checked_sources(output / 'DeltaInput', native, 'Eight-asset append input')
    notify('Appending only the eight current native assets to the approved archive')
    car_pack.execute(packer, ['pack', '-i', output / 'AppendSeed', '-p', output / 'DeltaInput',
                             '-o', output / 'DeltaPatch'], output / 'Work', 'pack.log', cancel)
    car_pack.execute(packer, ['listfiles', '-i', output / 'DeltaPatch', '-o', output / 'delta_entries.txt'],
                     output / 'Work', 'delta_listing.log', cancel)
    delta_rows = _listing(output / 'delta_entries.txt')
    _same_namespace(before, delta_rows, native)
    delta = _tree(output / 'DeltaPatch')
    _need(all(PFS_RE.fullmatch(name) for name in delta), 'Append delta contains a non-PDIPFS physical path.')
    _selected_payloads(delta_rows, native, delta)
    delta_metadata = _archive_metadata(output / 'Work/delta_listing.log', output / 'DeltaPatch', delta_rows, delta)
    proof = _append_proof(before, delta_rows, native, old_physical, delta, old_metadata, delta_metadata)
    _checked_sources(output / 'AppendSeed', physical_hashes, 'Approved append seed after packing')
    _checked_sources(output / 'DeltaInput', native, 'Eight-asset append input after packing')
    notify('Merging the verified append delta into a fresh complete archive copy')
    _copy_files(baseline / 'PatchOut', output / 'PatchOut', physical_hashes, cancel)
    # The new TOC and payloads are copied before the activation header, entirely
    # inside this new scratch package. No game installation is addressed here.
    ordered_delta = {name: delta[name]['sha256'] for name in sorted(delta, key=lambda p: (p == HEADER_PATH, p))}
    _copy_files(output / 'DeltaPatch', output / 'PatchOut', ordered_delta, cancel)
    car_pack.execute(packer, ['listfiles', '-i', output / 'PatchOut', '-o', output / 'packed_entries.txt'],
                     output / 'Work', 'listing.log', cancel)
    after = _listing(output / 'packed_entries.txt')
    _need(after == delta_rows, 'The merged archive listing differs from its verified append delta.')
    physical = _tree(output / 'PatchOut')
    _need(physical == {**old_physical, **delta}, 'The merged archive changed or omitted a retained baseline payload.')
    _archive_metadata(output / 'Work/listing.log', output / 'PatchOut', after, physical, proof['inactive'])
    for index, (name, digest) in enumerate(expected.items(), 1):
        car_pack.check_cancel(cancel)
        notify(f'Independent extraction check {index} / {len(expected)}')
        car_pack.execute(packer, ['unpack', '-i', output / 'PatchOut', '-o', output / 'Verified',
                                 '--indices', after[name]['index']], output / 'Work',
                         f'extract_{after[name]["index"]}.log', cancel)
        file = _plain_path(output / 'Verified' / name)
        _need(hash_file(file) == digest, 'Packed extraction differs from its pinned logical source: ' + name)
    _checked_sources(output / 'Verified', expected, 'Independent extraction')
    _unchanged_inputs(preflight)
    build = _pinned_json(candidate / 'build_receipt.json', preflight['pins']['build_receipt_sha256'])
    shutil.copyfile(candidate / 'build_receipt.json', output / 'model_build_receipt.json')
    identity = preflight['identity']
    receipt = {
        'passed': True, 'candidate_id': output.parent.name, 'kind': 'same-identity-native-diagnostic-package',
        'packaging_strategy': 'append-on-approved-package',
        **{name: identity[name] for name in ('profile_id', 'car_id', 'model_path', 'title_id', 'game_update')},
        'files': [{'relative_path': name, **row} for name, row in sorted(physical.items())],
        'header_sha256': physical[HEADER_PATH]['sha256'], 'source_virtual_assets': expected,
        'source_roundtrip_match': True, 'all_unrelated_archive_entries_preserved': True,
        'f50_cinque_and_database_byte_identical': True, 'supra_physics_unchanged': True,
        'existing_car_assets_preserved': False, 'additional_car_database_entry': False, 'installation': None,
        'runtime_tested': False, 'physical_ps3_tested': False, 'game_installation_changed': False,
        'source_license': build.get('source_license', {}), 'limitations': build.get('limitations', []),
        'build_receipt': str(candidate / 'build_receipt.json'),
        'changed_virtual_assets': sorted(name for name in native if native[name] != old[name]),
        'appended_native_bindings': sorted(native), 'inactive_baseline_payloads': proof['inactive'],
        'baseline_header_sha256': identity['baseline_header_sha256'],
        'baseline_receipt_sha256': preflight['pins']['baseline_receipt_sha256'],
        'total_payload_bytes': sum(row['bytes'] for row in physical.values()),
    }
    atomic_json(output / 'package_receipt.json', receipt)


def _verify_package(output, preflight):
    output = _plain_path(output, directory=True)
    identity = preflight['identity']
    baseline = Path(preflight['input_paths']['baseline'])
    native = preflight['native_assets']
    expected = {**preflight['baseline_sources'], **native}
    receipt_path = _plain_path(output / 'package_receipt.json')
    receipt_sha = hash_file(receipt_path)
    receipt = _pinned_json(receipt_path, receipt_sha)
    _need(receipt.get('packaging_strategy') == 'append-on-approved-package',
          'The comparison must use an append-on-approved-package diagnostic build.')
    _metadata_identity(receipt, identity)
    _need(all(receipt.get(key) == identity[key] for key in ('profile_id', 'title_id', 'game_update')),
          'The current package receipt is missing its reviewed profile, title or update identity.')
    _need(receipt.get('installation') is None and receipt.get('additional_car_database_entry') is False,
          'The packaged candidate changed its logical destination.')
    _need(receipt.get('source_virtual_assets') == expected,
          'The packaged candidate changed an unrelated logical source or native asset.')
    _need(receipt.get('source_roundtrip_match') is True and
          receipt.get('all_unrelated_archive_entries_preserved') is True and
          receipt.get('f50_cinque_and_database_byte_identical') is True and
          receipt.get('supra_physics_unchanged') is True,
          'The packer did not establish the required nonrouted preservation guarantees.')
    _need(receipt.get('runtime_tested') is False and receipt.get('physical_ps3_tested') is False,
          'An offline comparison package must not inherit a runtime acceptance claim.')
    physical = _physical_package(output, receipt, identity)
    input_tree = _checked_sources(output / 'Input', expected, 'Packaged Input')
    verified_tree = _checked_sources(output / 'Verified', expected, 'Independent extraction')
    _checked_sources(output / 'BaselineSeed', car_pack.SEED, 'Copied baseline seed')
    _checked_sources(output / 'AppendSeed', _tree_hashes(preflight['snapshot']['baseline_physical']),
                     'Approved append seed')
    _checked_sources(output / 'DeltaInput', native, 'Eight-asset append input')
    _need(hash_file(_plain_path(output / 'model_build_receipt.json')) == preflight['pins']['build_receipt_sha256'],
          'The copied model build receipt differs from the pinned current candidate.')
    before = _listing(baseline / 'packed_entries.txt')
    fresh = _listing(output / BASELINE_LISTING_NAME)
    _need(before == fresh, 'The cached baseline listing differs from a fresh listing of its approved index.')
    after = _listing(output / 'packed_entries.txt')
    _same_namespace(before, after, native)
    old_physical = preflight['snapshot']['baseline_physical']
    _selected_payloads(after, native, physical)
    metadata_before = _archive_metadata(output / 'ComparisonWork/baseline_listing.log', baseline / 'PatchOut',
                                        fresh, old_physical)
    delta = _tree(output / 'DeltaPatch')
    delta_rows = _listing(output / 'delta_entries.txt')
    _need(after == delta_rows, 'The merged archive listing differs from its verified append delta.')
    delta_metadata = _archive_metadata(output / 'Work/delta_listing.log', output / 'DeltaPatch', delta_rows, delta)
    proof = _append_proof(before, after, native, old_physical, delta, metadata_before, delta_metadata)
    _need(physical == {**old_physical, **delta},
          'The merged archive changed or omitted a retained baseline payload.')
    _need(receipt.get('inactive_baseline_payloads') == proof['inactive'] and
          receipt.get('appended_native_bindings') == sorted(native),
          'The package receipt does not identify the exact prior owners of its inactive retained payloads.')
    metadata_after = _archive_metadata(output / 'Work/listing.log', output / 'PatchOut', after, physical,
                                       proof['inactive'])
    _need(all(metadata_after[key] == delta_metadata[key] for key in
              ('entry_index', 'relative_path', 'header_sha256', 'toc_sha256')),
          'The merged archive metadata differs from the verified append delta.')
    index_before, index_after = proof['index_before'], proof['index_after']
    selected_before, selected_after = proof['selected_before'], proof['selected_after']
    unrelated_before = {name: row for name, row in old_physical.items()
                        if name not in index_before | selected_before}
    unrelated_after = {name: row for name, row in physical.items()
                       if name not in index_after | selected_after | set(proof['inactive'])}
    _need(unrelated_before == unrelated_after,
          'An unrelated physical payload moved, changed, appeared or disappeared; this is not a controlled comparison.')
    changed = sorted(name for name in native if native[name] != preflight['baseline_sources'][name])
    _need(receipt.get('changed_virtual_assets') == changed, 'The packer changed-asset report disagrees with the exact input hashes.')
    return {
        'path': str(output), 'package_receipt_sha256': receipt_sha,
        'header_sha256': receipt['header_sha256'], 'physical_files': physical,
        'logical_source_assets': expected, 'native_assets': dict(native),
        'input_files': input_tree, 'verified_files': verified_tree,
        'packed_entries_sha256': hash_file(output / 'packed_entries.txt'),
        'fresh_baseline_listing_sha256': hash_file(output / BASELINE_LISTING_NAME),
        'logical_namespace_count': len(after), 'physical_file_count': len(physical),
        'changed_native_assets': changed,
        'unrelated_logical_sources_checked': len(expected) - len(native),
        'unrelated_physical_payloads_checked': len(unrelated_before),
        'retained_baseline_payloads_checked': len(old_physical) - 1,
        'inactive_baseline_payloads': proof['inactive'],
        'packaging_strategy': 'append-on-approved-package',
        'append_delta_files': delta,
        'delta_entries_sha256': hash_file(output / 'delta_entries.txt'),
        'append_delta_metadata': delta_metadata,
        'native_payload_paths_before': sorted(selected_before),
        'native_payload_paths_after': sorted(selected_after),
        'mutable_index_paths': sorted(index_before | index_after),
        'mutable_index_paths_before': sorted(index_before),
        'mutable_index_paths_after': sorted(index_after),
        'archive_metadata_before': metadata_before, 'archive_metadata_after': metadata_after,
    }


def _output_path(output, preflight):
    output = _plain_path(output, missing=True)
    _need(not output.exists(), 'Use a new isolated comparison output directory.')
    _plain_path(output.parent, directory=True)
    _need(not {'pdipfs', 'usrdir', 'dev_hdd0', 'savedata'} & {part.casefold() for part in output.parts},
          'Diagnostic output must be outside installed game, emulator and save-data directories.')
    for source in preflight['input_paths'].values():
        source = Path(source)
        _need(output != source and output not in source.parents and source not in output.parents,
              'Comparison output must be separate from every pinned input.')
    return output


def _reject_output(output, exc):
    """Invalidate only this call's newly created output; retain failure evidence."""
    if not output.is_dir():
        return
    _plain_path(output, directory=True)
    manifest_path = output / MANIFEST_NAME
    if manifest_path.exists():
        _plain_path(manifest_path)
        manifest_path.unlink()
    receipt_path = output / 'package_receipt.json'
    if receipt_path.exists():
        _plain_path(receipt_path)
        raw = receipt_path.read_bytes()
        original_path = _plain_path(output / 'package_receipt_before_comparison.json', missing=True)
        if not original_path.exists():
            atomic_bytes(original_path, raw)
        try:
            receipt = json.loads(raw.decode('utf-8-sig'))
        except (ValueError, UnicodeError):
            receipt = {}
        receipt['passed'] = False
        receipt['runtime_comparison_rejected'] = True
        atomic_json(receipt_path, receipt)
    atomic_json(output / 'runtime_comparison_FAILED.json',
                {'comparison_ready': False, 'runtime_verified': False,
                 'reason': str(exc), 'code': getattr(exc, 'code', type(exc).__name__)})


def build_same_identity_package(candidate, baseline, reference_receipt, packer, output, *,
                                identity, reference_sha256, reference_version,
                                build_receipt_sha256, baseline_receipt_sha256,
                                reference_assets_key='native_supra_assets', cancel=None, progress=None):
    """Create and independently verify one fresh diagnostic package, never install it.

    ``output`` is a new, exclusively created diagnostic workspace; its ``package``
    child is the normal package folder. A fresh baseline listing is produced by
    the pinned packer there. Both complete logical ownership and unrelated
    physical payloads must be unchanged. Failure invalidates the new package receipt. Success adds
    ``runtime_comparison_manifest.json``; pin that file's returned SHA before a
    separate runtime harness calls :func:`verify_comparison_manifest`.
    """
    preflight = verify_comparison_inputs(candidate, baseline, reference_receipt, identity=identity,
                                        reference_sha256=reference_sha256, reference_version=reference_version,
                                        build_receipt_sha256=build_receipt_sha256,
                                        baseline_receipt_sha256=baseline_receipt_sha256,
                                        reference_assets_key=reference_assets_key)
    workspace = _output_path(output, preflight)
    packer = _plain_path(packer)
    _need(packer != workspace and workspace not in packer.parents, 'The packer cannot be inside its new output.')
    tool = car_pack.trusted_packer(packer)
    source_bytes = sum(row['bytes'] for row in preflight['snapshot']['baseline_input'].values())
    native_bytes = sum(preflight['snapshot']['candidate'][name]['bytes'] for name in preflight['native_assets'])
    physical_bytes = sum(row['bytes'] for row in preflight['snapshot']['baseline_physical'].values())
    budget = max(600 * 1024**2, 3 * physical_bytes + 2 * source_bytes + 5 * native_bytes + 64 * 1024**2)
    _need(shutil.disk_usage(workspace.parent).free >= budget,
          'Keep enough free disk space for the complete approved copy, append delta and independent extraction.')
    # Reserve ownership atomically. A racing preexisting output must never be
    # invalidated by our exception handler, even if the lower-level packer refuses it.
    workspace.mkdir(exist_ok=False)
    output = workspace / 'package'
    try:
        _pack_append(output, preflight, packer, cancel, progress)
        _unchanged_inputs(preflight)
        package = _verify_package(output, preflight)
        _need(hash_file(packer) == tool['sha256'], 'The reviewed packer changed during packaging.')
        result = {'schema': 1, 'kind': 'same-identity-runtime-comparison',
                  'comparison_ready': True, 'identity': preflight['identity'],
                  'inputs': preflight, 'package': package, 'packer': tool,
                  'baseline_archive_listing_reverified': True,
                  'archive_metadata_ownership_verified': True,
                  'baseline_inputs_unchanged': True, 'native_inputs_unchanged': True,
                  'logical_namespace_unchanged': True, 'unrelated_logical_assets_unchanged': True,
                  'unrelated_physical_payloads_unchanged': True,
                  'identity_and_physics_unchanged': True,
                  'runtime_verified': False, 'physical_ps3_verified': False,
                  'scope': 'Offline same-identity package and preservation verification only. '
                           'No installation, game launch, input, save operation or observed runtime state.'}
        path = output / MANIFEST_NAME
        atomic_json(path, result)
        return {'package_path': str(output), 'manifest_path': str(path),
                'manifest_sha256': hash_file(path), 'manifest': result}
    except BaseException as exc:
        _reject_output(output, exc)
        raise


def verify_comparison_manifest(path, expected_sha256):
    """Recheck pinned inputs and all packaged/extracted files without running tools.

    This is an experiment precondition, not permission to install or control a
    game. The independent runtime owner must check current ownership, process,
    configuration, experiment-start saves and requested-versus-observed states.
    """
    path = _plain_path(path)
    manifest = _pinned_json(path, expected_sha256)
    _need(manifest.get('schema') == 1 and manifest.get('kind') == 'same-identity-runtime-comparison'
          and manifest.get('comparison_ready') is True,
          'The pinned receipt is not a successful same-identity comparison manifest.')
    _need(manifest.get('runtime_verified') is False and manifest.get('physical_ps3_verified') is False,
          'The package manifest must not claim a runtime result.')
    preflight = manifest.get('inputs')
    _need(isinstance(preflight, dict) and manifest.get('identity') == preflight.get('identity'),
          'Comparison identity and input proof disagree.')
    _need(Path(manifest['package']['path']) == path.parent and path.name == MANIFEST_NAME,
          'Comparison manifest moved outside the exact verified package directory.')
    _unchanged_inputs(preflight)
    package = _verify_package(path.parent, preflight)
    _need(package == manifest['package'], 'The comparison package changed since its manifest was recorded.')
    return {'verified': True, 'manifest_sha256': expected_sha256,
            'identity': dict(preflight['identity']),
            'baseline_header_sha256': preflight['identity']['baseline_header_sha256'],
            'candidate_header_sha256': package['header_sha256'],
            'baseline_physical_files': preflight['snapshot']['baseline_physical'],
            'candidate_physical_files': package['physical_files'],
            'native_assets': dict(preflight['native_assets']),
            'runtime_verified': False}
