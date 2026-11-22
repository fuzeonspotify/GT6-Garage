"""Owned-PC native/package proof for the developer-only wheel-free shell route.

Authors a small genuine GLB fixture with body and source lamps, no source
wheels. Builds native assets and a separate database identity in fresh folders.
It never installs a package, launches a game or controls another process.
This is a reproducible engineering fixture, not an independently authored car.
"""
from __future__ import annotations

import argparse
import collections
import copy
import datetime
import hashlib
import json
import struct
import sys
import time
from pathlib import Path

import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from garagehub.body_streams import groups
from garagehub.car_pack import baseline, pack_candidate, trusted_packer
from garagehub.car_profiles import ASSETS, PROFILE, validate_template
from garagehub.identity_pipeline import identity_plan, route_candidate
from garagehub.native import MDL3
from garagehub.template_shell import approve_shell, build_shell, review_shell
from garagehub.template_shell_independent_tests import shell_fixture, source_snapshot
from garagehub.util import atomic_json, hash_file


class LeaseCancel:
    def __init__(self, hub, token):
        self.hub, self.token = Path(hub), token

    def is_set(self):
        try:
            lease = json.loads((self.hub/'AUTORUN_LEASE/owner.json').read_text())
            expiry = datetime.datetime.fromisoformat(lease['expires_utc'])
            return (lease['token'] != self.token
                    or datetime.datetime.now(datetime.timezone.utc) >= expiry
                    or (self.hub/'STOP_AUTORUN').exists())
        except (OSError, KeyError, ValueError):
            return True

    def require(self):
        if self.is_set():
            raise RuntimeError('Current matching lease and absent STOP marker are required.')


def oriented_triangle(points):
    # Independent comparison: cyclic permutations retain orientation; reversed
    # winding does not. Micrometre rounding only handles emitted float32.
    vertices = tuple(tuple(round(float(x), 6) for x in p) for p in points)
    return min(vertices[i:]+vertices[:i] for i in range(3))


def decode_generated_body(model_path, stream_path, emitted):
    raw, stream = model_path.read_bytes(), stream_path.read_bytes()
    model = MDL3(raw)
    model.validate()
    main = next(row for row in groups(raw, stream) if row['label'] == '//0')
    cpu = b''.join(row['raw'] for row in main['records'][:main['na']])
    count, table = struct.unpack_from('>H', cpu, 20)[0], struct.unpack_from('>I', cpu, 24)[0]
    bindings = {}
    for i in range(count):
        row = struct.unpack_from('>8I', cpu, table+32*i)
        assert row[0] not in bindings, 'Duplicate final serialized binding'
        bindings[row[0]] = row
    layouts, decoded = model.fvfs(), []
    for expected in emitted:
        sid = expected['id']
        at = model.u32(0x38)+48*sid
        layout = layouts[model.s16(at+2)]
        count, indices = model.u32(at+8), model.u32(at+20)
        vp, ip = (bindings[sid][1:3] if expected['streamed']
                  else (model.u32(at+12), model.u32(at+24)))
        payload = cpu if expected['streamed'] else raw
        position = next(f for f in layout['fields'] if f['name'] == 'position')
        assert position['format'] == 2 and position['components'] == 3
        points = np.ndarray((count, 3), dtype='>f4', buffer=payload,
                            offset=vp+position['offset'], strides=(layout['stride'], 4)).copy()
        faces = np.frombuffer(payload, dtype='>u2', count=indices, offset=ip).reshape(-1, 3).copy()
        assert faces.max() < len(points)
        decoded.append({'shape': sid, 'material': model.s16(at+4),
                        'streamed': expected['streamed'], 'positions': points.tolist(),
                        'faces': faces.tolist()})
    return decoded


def verify_mechanical_suffix(template_path, output_path, lod):
    before, after = template_path.read_bytes(), output_path.read_bytes()
    old, new = MDL3(before), MDL3(after)
    proof = []
    for index in range(old.u16(0x10)):
        op, nptr = old.u32(0x30)+48*index, new.u32(0x30)+48*index
        old_code = old.span(old.u32(op+24), old.u32(op+28))
        new_code = new.span(new.u32(nptr+24), new.u32(nptr+28))
        if index in (0, 5):
            suffix = old_code[PROFILE['mechanical_offsets'][lod][str(index)]:]
            assert new_code.endswith(suffix), 'Native mechanical suffix changed'
            proof.append({'model': index, 'suffix_sha256': hashlib.sha256(suffix).hexdigest(),
                          'suffix_bytes': len(suffix), 'retained_exactly': True})
        else:
            assert new_code == old_code, 'Unrelated native model program changed'
    return {'mechanical': proof, 'unrelated_programs_unchanged': True}


def run(args):
    cancel = LeaseCancel(args.hub_root, args.lease_token)
    cancel.require()
    output = args.output.resolve()
    assert not output.exists(), 'Use a new proof folder'
    for protected in (args.template, args.baseline):
        p = protected.resolve()
        assert p != output and p not in output.parents and output not in p.parents
    template = Path(validate_template(args.template)['root'])
    before_template = {rel: hash_file(template/rel) for rel in ASSETS}
    before_baseline = baseline(args.baseline)
    before_packer = trusted_packer(args.packer)
    baseline_receipt = json.loads((args.baseline/'package_receipt.json').read_text())
    previous_assets = {key: value for key, value in baseline_receipt['source_virtual_assets'].items()
                       if key.startswith('car/')}
    output.mkdir(parents=True)
    started = time.time()

    def progress(message):
        cancel.require()
        print(message, flush=True)

    scene, project = shell_fixture()
    project['name'] = 'Template Shell Engineering Proof'
    project['license'] = {'author': 'GT6 Garage engineering fixture',
                          'title': 'Wheel-free body with authored source lamps',
                          'source': 'Procedurally authored controlled GLB fixture',
                          'license': 'Project QA fixture; not a production vehicle asset'}
    initial = source_snapshot(scene)
    project_before = copy.deepcopy(project)
    assert not project['inspection']['wheel_centers']
    assert all(row['role'] not in ('rim', 'tyre', 'valve', 'brake_rotor', 'brake_caliper')
               for row in project['inspection']['parts'])
    placement = {'scale': 1., 'translation_metres': [0., .35, 0.]}
    review = review_shell(scene, project, placement)
    assert review['ready_for_candidate'], review['blockers']
    assert review['maximum_wheel_centre_error_metres'] is None
    decision = approve_shell(scene, project, placement, {}, review['review_digest'], True)
    (output/'source.glb').write_bytes(scene.raw)
    atomic_json(output/'project.json', project)
    atomic_json(output/'review.json', review)
    atomic_json(output/'decision.json', decision)
    native = build_shell(scene, project, template, output/'native', decision,
                         cancel=cancel, progress=progress)
    assert source_snapshot(scene) == initial and project == project_before
    root = output/'native'/PROFILE['model_path']
    for lod in ('hq', 'race'):
        assert hash_file(root/lod/'wheel') == before_template[lod+'/wheel']
    assert hash_file(root/'interior') == before_template['interior']

    # The fixture uses +Z forward; the independent known proper rotation is a
    # half-turn about Y. This oracle does not call the product's axes/fit code.
    matrix = np.diag([-1., 1., -1.])
    translation = np.array([0., .35, 0.])
    expected = collections.Counter(oriented_triangle((p.positions.astype(float)@matrix.T+translation)[face])
                                   for p in scene.parts for face in p.indices)
    decoded_lods, geometry_proof = {}, {}
    for lod in ('hq', 'race'):
        rows = decode_generated_body(root/lod/'body', root/lod/'body_s',
                                     native['body'][lod]['added_shapes'])
        got = collections.Counter(oriented_triangle(np.asarray(row['positions'])[face])
                                  for row in rows if row['streamed'] for face in row['faces'])
        assert got == expected, 'Serialized base geometry differs from explicit source placement'
        decoded_lods[lod] = rows
        geometry_proof[lod] = {'source_triangles': sum(expected.values()),
                              'serialized_base_triangles': sum(got.values()),
                              'oriented_triangle_multiset_equal': True,
                              'comparison_quantization_metres': .000001,
                              'direct_lamp_triangles': sum(len(r['faces']) for r in rows if not r['streamed']),
                              'programs': verify_mechanical_suffix(template/lod/'body', root/lod/'body', lod)}
    atomic_json(output/'decoded_source_geometry.json', {
        'kind': 'serialized_fixture_body_geometry', 'scope': 'Generated static source and lamp shapes only',
        'game_footage': False, 'inherited_wheel_geometry_decoded': False, 'lods': decoded_lods})

    plan = identity_plan(project, args.baseline, args.packer, output/'database_cache', cancel)
    assert plan['ready'] and plan['mode'] == 'new_entry'
    routed = route_candidate(output/'native', args.baseline, args.packer,
                             output/'database_cache', plan, output/'identity', cancel, progress)
    package = pack_candidate(output/'identity', args.baseline, args.packer,
                             output/'package', cancel, progress)
    assert routed['database_crypto_roundtrip'] and routed['additional_database_entry']
    assert package['source_roundtrip_match'] and package['all_unrelated_archive_entries_preserved']
    assert all(package['source_virtual_assets'][k] == v for k, v in previous_assets.items())
    assert {rel: hash_file(template/rel) for rel in ASSETS} == before_template
    assert baseline(args.baseline) == before_baseline
    assert trusted_packer(args.packer) == before_packer
    cancel.require()
    receipt = {'schema': 1, 'kind': 'template_shell_actual_native_and_new_entry_proof',
        'passed': True, 'created_utc': datetime.datetime.now(datetime.timezone.utc).isoformat(),
        'environment': {'python': sys.version.split()[0], 'platform': sys.platform},
        'fixture': {'kind': 'procedurally_authored_controlled_glb', 'independent_production_vehicle': False,
                    'source_sha256': scene.digest, 'source_parts': len(scene.parts),
                    'source_triangles': scene.triangles, 'source_wheels': 0,
                    'source_lamp_surfaces': True, 'source_unchanged': True,
                    'source_wheel_centres': {}, 'placement': placement},
        'native_assets': native['source_virtual_assets'], 'complete_native_asset_count': len(native['source_virtual_assets']),
        'template_wheel_assets_byte_identical': True, 'template_interior_byte_identical': True,
        'serialized_geometry': geometry_proof,
        'identity': {'mode': plan['mode'], 'car_id': plan['car_id'], 'model_path': plan['model_path'],
                     'display_name': plan['display_name'], 'crypto_roundtrip_passed': True},
        'database_validation': routed['database'],
        'package': {'path': str(output/'package'), 'source_count': len(package['source_virtual_assets']),
                    'header_sha256': package['header_sha256'], 'all_extractions_match': True,
                    'previous_car_assets_preserved': len(previous_assets),
                    'all_unrelated_archive_entries_preserved': True},
        'inputs_unchanged': True, 'installed_application_changed': False,
        'game_installation_changed': False, 'game_launched': False,
        'runtime_verified': False, 'physical_ps3_tested': False,
        'production_gui_enabled': False, 'template_lamp_kit_implemented': False,
        'accepted_cars': 0, 'required_accepted_cars': 2,
        'scope': 'Developer native/identity/package feasibility for a controlled wheel-free source with authored lamps; no player GUI or game acceptance.',
        'elapsed_seconds': time.time()-started}
    atomic_json(output/'native_proof.json', receipt)
    print(json.dumps({'passed': True, 'receipt': str(output/'native_proof.json'),
                      'car_id': plan['car_id'], 'model_path': plan['model_path'],
                      'previous_car_assets_preserved': len(previous_assets),
                      'extractions': len(package['source_virtual_assets']),
                      'runtime_verified': False}), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--hub-root', type=Path, required=True)
    parser.add_argument('--lease-token', required=True)
    parser.add_argument('--template', type=Path, required=True)
    parser.add_argument('--baseline', type=Path, required=True)
    parser.add_argument('--packer', type=Path, required=True)
    parser.add_argument('--output', type=Path, required=True)
    args = parser.parse_args()
    try:
        run(args)
    except Exception as exc:
        if args.output.is_dir():
            atomic_json(args.output/'PROOF_FAILED.json', {'passed': False, 'reason': str(exc),
                        'kind': 'template_shell_native_proof_failure', 'game_installation_changed': False})
        raise
