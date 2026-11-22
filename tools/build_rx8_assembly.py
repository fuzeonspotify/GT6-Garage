"""Build a non-installable combined native diagnostic from an explicit saved project.

The original project, dispositions and donor assets are read-only inputs. The
only write destination is a newly reserved output directory. This command does
not find installed games, edit saves/physics, install assets, or run a game.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import importlib.metadata
import json
from pathlib import Path
import sys
import traceback


# Keep source-tree bytecode caches outside this command's write behavior.
sys.dont_write_bytecode = True
SOURCE_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(SOURCE_ROOT))
POLICY = 'historical_reference_only'


def parser():
    result = argparse.ArgumentParser(description=__doc__)
    result.add_argument('--project', type=Path, required=True,
                        help='Explicit saved .gt6car preparation archive.')
    result.add_argument('--donor', type=Path, required=True,
                        help='Original RX8 directory containing info, interior, hq/ and race/.')
    result.add_argument('--output', type=Path, required=True,
                        help='New, separate directory under an existing parent; never reused.')
    result.add_argument('--fit-policy', choices=(POLICY,), required=True,
                        help='Explicit historical diagnostic choice; it does not solve body/hub fit.')
    result.add_argument('--dispositions', type=Path, required=True,
                        help='JSON object of caller-reviewed blocked/excluded part IDs; {} is allowed.')
    result.add_argument('--review-note', default='',
                        help='Optional explanation retained in the fit receipt.')
    return result


def _utc():
    return datetime.now(timezone.utc).isoformat()


def _sha(data):
    return hashlib.sha256(data).hexdigest()


def _file_sha(path):
    digest = hashlib.sha256()
    with path.open('rb') as stream:
        for block in iter(lambda: stream.read(1024*1024), b''):
            digest.update(block)
    return digest.hexdigest()


def _new_bytes(path, value):
    with path.open('xb') as stream:
        stream.write(value)


def _new_json(path, value):
    _new_bytes(path, (json.dumps(value, indent=2, allow_nan=False)+'\n').encode('utf-8'))


def _pairs(items):
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError('Duplicate JSON key in dispositions: '+key)
        result[key] = value
    return result


def _nonfinite(value):
    raise ValueError('Non-finite number in dispositions: '+value)


def read_dispositions(raw):
    result = json.loads(raw.decode('utf-8-sig'), object_pairs_hook=_pairs,
                        parse_constant=_nonfinite)
    # Also reject finite-looking exponent literals that overflow to Infinity.
    json.dumps(result, allow_nan=False)
    if not isinstance(result, dict):
        raise ValueError('Dispositions must be a JSON object keyed by source part ID.')
    for pid, decision in result.items():
        if (not pid or not isinstance(decision, dict)
                or set(decision)-{'disposition', 'reason', 'provenance'}
                or decision.get('disposition') not in ('blocked', 'excluded')
                or not isinstance(decision.get('reason'), str)
                or not decision['reason'].strip()):
            raise ValueError('Each disposition needs blocked/excluded and a nonempty reason: '+pid)
    return result


def validate_paths(project, donor, dispositions, output):
    project, donor, dispositions = (Path(path).expanduser().resolve(strict=True)
                                   for path in (project, donor, dispositions))
    requested = Path(output).expanduser()
    # lexists-like check catches a dangling output symlink as well.
    if requested.exists() or requested.is_symlink():
        raise ValueError('Output already exists. Preserve that attempt and choose a fresh directory.')
    parent = requested.parent.resolve(strict=True)
    if not parent.is_dir():
        raise ValueError('Output parent must be an existing directory.')
    output = parent/requested.name
    if output.exists() or output.is_symlink():
        raise ValueError('Resolved output already exists. Choose a fresh directory.')
    if not project.is_file() or project.suffix.lower() != '.gt6car':
        raise ValueError('Choose an explicit saved .gt6car preparation archive.')
    if not dispositions.is_file() or not donor.is_dir():
        raise ValueError('Dispositions must be a file and donor must be an original asset directory.')
    if output == donor or output.is_relative_to(donor) or donor.is_relative_to(output):
        raise ValueError('Output must be separate from the complete original donor directory.')
    for path in (project, dispositions):
        if path == output or path.is_relative_to(output) or output.is_relative_to(path):
            raise ValueError('Output must be separate from the project and disposition inputs.')
    return project, donor, dispositions, output


def _read_input(path, cap):
    if not path.is_file() or path.stat().st_size > cap:
        raise ValueError('Input is missing, is not a regular file, or exceeds its bound: '+str(path))
    raw = path.read_bytes()
    if len(raw) > cap:
        raise ValueError('Input grew beyond its bound while being read: '+str(path))
    return raw, {'path': str(path), 'bytes': len(raw), 'sha256': _sha(raw)}


def _verify_inputs(inputs):
    results = {}
    for name, original in inputs.items():
        path = Path(original['path'])
        try:
            current = {'bytes': path.stat().st_size, 'sha256': _file_sha(path)}
            current['unchanged'] = (current['bytes'] == original['bytes']
                                    and current['sha256'] == original['sha256'])
        except OSError as exc:
            current = {'unchanged': False, 'error': str(exc)}
        results[name] = current
    return results


def _limitations(receipt):
    lines = ['# Combined native assembly diagnostic', '',
        '**Not installable. A successful command verifies candidate authoring; it does not accept a complete vehicle or runtime fit.**', '',
        'The source body retains its dimensions and XZ-centred convention. Wheel and brake parts use the existing audited rigid local frames and axial terms. The historical reference does not establish current mount height, loaded tyres, steering, suspension, or game acceptance.', '',
        '## Source coverage', '',
        '| LOD | Emitted triangles | Inherited source tyre triangles | Excluded triangles | Blocked triangles |',
        '|---|---:|---:|---:|---:|']
    for lod, record in receipt['lods'].items():
        totals = record['plan']['triangle_dispositions']
        lines.append(f"| {lod} | {totals['emit']} | {totals['inherited']} | {totals['excluded']} | {totals['blocked']} |")
    plan = receipt['lods']['hq']['plan']
    counts = Counter(row['disposition'] for row in plan['coverage'])
    lines += ['', f"All {plan['source_parts']} source parts are accounted for: "+', '.join(
        f'{counts[status]} {status}' for status in ('emit', 'inherited', 'excluded', 'blocked'))+'.',
        'Accounting includes blocked and inherited parts; it does not mean all source geometry was emitted. Intentional lamp-state redraws are reported separately.', '',
        '## Recorded component decisions', '']
    decisions = [row for row in plan['coverage'] if row['disposition'] in ('blocked', 'excluded')]
    if decisions:
        for row in decisions:
            lines.append(f"- {row['id']}: {row['disposition']}; {row['source_triangles']} triangles. {row['reason']}")
    else:
        lines.append('No source component is individually blocked or excluded in this plan. The fit, capability and runtime limitations still apply.')
    lines += ['', '## Remaining limitations', '']
    grouped = defaultdict(set)
    for issue in plan['blockers']:
        grouped[issue['message']].update(issue['parts'])
    for message, parts in grouped.items():
        lines.append('- '+message+(f' Affects {len(parts)} source part(s); exact IDs are in the full receipt.' if parts else ''))
    lines += ['', '## Inspect and reproduce', '',
        '`assembly_receipt.json` contains each source disposition, per-corner historical residual, material recipe, native draw ownership, and preservation check. `run_receipt.json` records the explicit input paths/hashes, code hashes, environment and output hashes.', '',
        '`native/` contains diagnostic native files only. This command creates no installation package, changes no selected replacement physics, and performs no game, save, or runtime-input action. Keep this directory separate from any installed game.', '']
    return '\n'.join(lines)


def run(args):
    from garagehub.donor_profiles import RX8_ASSETS
    from garagehub.projects import Store
    from garagehub.rx8_assembly import build_assembly
    from garagehub.util import MAX_PROJECT_BYTES

    project, donor, disposition_path, output = validate_paths(
        args.project, args.donor, args.dispositions, args.output)
    project_raw, project_input = _read_input(project, MAX_PROJECT_BYTES)
    disposition_raw, disposition_input = _read_input(disposition_path, 1024*1024)
    dispositions = read_dispositions(disposition_raw)
    inputs = {'project': project_input, 'dispositions': disposition_input}
    originals = {}
    for name, expected in RX8_ASSETS.items():
        path = (donor/name).resolve(strict=True)
        if not path.is_relative_to(donor):
            raise ValueError('A donor asset resolves outside its selected original directory: '+name)
        originals[name], inputs['donor/'+name] = _read_input(path, 16*1024*1024)
        if inputs['donor/'+name]['sha256'] != expected:
            raise ValueError('Original RX8 fingerprint mismatch: '+name+'. Prior lab outputs are not donors.')
    # All path, disposition syntax and original identity checks precede writes.
    output.mkdir()  # Fails if another process reserved this name after preflight.
    report = {'schema': 1, 'kind': 'combined_rx8_assembly_cli_diagnostic',
        'passed': False, 'started_utc': _utc(), 'output_directory': str(output),
        'fit_decision': {'policy': args.fit_policy, 'review_note': args.review_note},
        'dispositions': dispositions, 'inputs': inputs,
        'python_version': sys.version, 'dependency_versions': {},
        'code_sha256': {}, 'output_asset_sha256': {},
        'scope': 'Passed means source-accounted native serialization checks completed; it is not complete-vehicle or runtime acceptance.',
        'installable': False, 'ready_for_production': False, 'runtime_verified': False,
        'full_car_import_accepted': False, 'physics_changed': False,
        'game_launched': False, 'runtime_input_performed': False,
        'installation_performed': False}
    try:
        for name in ('numpy', 'Pillow', 'fast-simplification'):
            try:
                report['dependency_versions'][name] = importlib.metadata.version(name)
            except importlib.metadata.PackageNotFoundError:
                report['dependency_versions'][name] = None
        paths = sorted((SOURCE_ROOT/'garagehub').glob('*.py'))+[Path(__file__).resolve()]
        report['code_sha256'] = {str(path.relative_to(SOURCE_ROOT)): _file_sha(path) for path in paths}
        _new_json(output/'started_receipt.json', report)
        _new_json(output/'component_dispositions.json', dispositions)
        store = Store(output/'project_workspace')
        summary = store.import_archive(project_raw)
        scene = store.scene(summary['id'])
        assets, assembly = build_assembly(scene, summary['inspection'], originals,
            fit_decision=report['fit_decision'], dispositions=dispositions)
        before_publish = _verify_inputs(inputs)
        if not all(item['unchanged'] for item in before_publish.values()):
            raise RuntimeError('An original input changed during compilation; candidate assets were not published.')
        if set(assets) != set(RX8_ASSETS):
            raise RuntimeError('Combined compiler returned an unexpected asset inventory.')
        for name, data in assets.items():
            path = output/'native'/name
            path.parent.mkdir(parents=True, exist_ok=True)
            _new_bytes(path, data)
            if _file_sha(path) != assembly['output_asset_sha256'][name]:
                raise RuntimeError('Written native asset hash differs from its compiler receipt: '+name)
        _new_json(output/'assembly_receipt.json', assembly)
        _new_json(output/'source_project_summary.json', summary)
        _new_bytes(output/'LIMITATIONS.md', _limitations(assembly).encode('utf-8'))
        report.update(passed=True, project_id=summary['id'], source_sha256=scene.digest,
            source_parts=len(scene.parts), source_triangles=scene.triangles,
            assembly_receipt_sha256=_file_sha(output/'assembly_receipt.json'),
            output_asset_sha256=dict(assembly['output_asset_sha256']),
            all_source_parts_accounted=all(record['all_source_parts_accounted'] for record in assembly['lods'].values()),
            full_source_geometry_emitted=all(record['plan']['full_source_geometry_emitted'] for record in assembly['lods'].values()))
    except BaseException as exc:
        report['failure'] = {'type': type(exc).__name__, 'message': str(exc),
                             'traceback': traceback.format_exc()}
        raise
    finally:
        report['finished_utc'] = _utc()
        report['original_inputs_after'] = _verify_inputs(inputs)
        report['original_inputs_unchanged'] = all(item['unchanged'] for item in report['original_inputs_after'].values())
        if not report['original_inputs_unchanged']:
            report['passed'] = False
        _new_json(output/'run_receipt.json', report)
    if not report['passed']:
        raise RuntimeError('Final original-input verification failed; preserve this failed diagnostic and inspect run_receipt.json.')
    return report


def main(argv=None):
    args = parser().parse_args(argv)
    try:
        report = run(args)
    except KeyboardInterrupt:
        print('Interrupted. Preserve any created output as a failed attempt; choose a fresh directory next time.', file=sys.stderr)
        return 130
    except Exception as exc:
        print('Assembly diagnostic failed: '+str(exc), file=sys.stderr)
        print('No installation was performed. Preserve any created output and its failure receipt.', file=sys.stderr)
        return 1
    print(json.dumps({key: report[key] for key in (
        'passed', 'output_directory', 'source_parts', 'source_triangles',
        'all_source_parts_accounted', 'full_source_geometry_emitted',
        'original_inputs_unchanged', 'installable', 'runtime_verified')}, indent=2))
    print('Non-installable diagnostic written. Read LIMITATIONS.md and the full assembly receipt before reviewing the native output.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
