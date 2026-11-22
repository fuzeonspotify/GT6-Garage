"""Check that the maintained bug ledger still points to real regression checks.

This audit validates references, not the historical evidence or in-game result.
Python tests and browser/Node checks must still be executed in their own scopes.
"""
from __future__ import annotations

import hashlib
import json
import re
import sys
from pathlib import Path, PurePosixPath


def resource_root():
    return Path(getattr(sys, '_MEIPASS', Path(__file__).resolve().parents[1]))


def collected_test_ids(suite):
    """Read a suite before unittest consumes it; preserve actual collected IDs."""
    import unittest
    result = []
    for item in suite:
        if isinstance(item, unittest.TestSuite):
            result.extend(collected_test_ids(item))
        elif item is not None:
            result.append(item.id())
    return result


def _local_file(root, relative):
    if not isinstance(relative, str) or '\\' in relative:
        raise ValueError('Expected a relative POSIX file path.')
    p = PurePosixPath(relative)
    if p.is_absolute() or '..' in p.parts or ':' in relative or not p.parts:
        raise ValueError('File path escapes the project.')
    path = (root / relative).resolve()
    if not path.is_relative_to(root.resolve()):
        raise ValueError('File path escapes the project.')
    return path


def node_check_labels(source):
    """Read this repository's literal checks.push labels from JavaScript code.

    Comments, quoted strings and template text cannot impersonate a live call.
    Computed labels are intentionally unsupported: the ledger needs stable text.
    """
    pattern = re.compile(r'''//[^\n]*|/\*[\s\S]*?\*/|"(?:\\.|[^"\\])*"|'(?:\\.|[^'\\])*'|`(?:\\.|[^`\\])*`|[A-Za-z_$][\w$]*|[^\s]''')
    tokens = []
    for match in pattern.finditer(source):
        value = match.group()
        if value.startswith(('//', '/*')):
            continue
        if value.startswith(('"', "'", '`')):
            # Existing behavior labels contain no escapes. Unknown/derived
            # string forms cannot silently satisfy an exact ledger reference.
            if value[0] != '`' and '\\' not in value[1:-1]:
                tokens.append(('literal', value[1:-1]))
            else:
                tokens.append(('opaque_string', ''))
        else:
            tokens.append(('code', value))
    prefix = [('code', 'checks'), ('code', '.'), ('code', 'push'), ('code', '(')]
    return {tokens[i+4][1] for i in range(max(0, len(tokens)-5))
            if tokens[i:i+4] == prefix and tokens[i+4][0] == 'literal'
            and tokens[i+5] == ('code', ')')}


def validate_history(document, test_ids, root):
    root = Path(root)
    errors = []
    python_refs = set()
    node_refs = set()
    gap_entries = []
    known_tests = set(test_ids)
    checked_node_files = {}

    def error(where, message):
        errors.append(f'{where}: {message}')

    if not isinstance(document, dict):
        document = {}
        error('ledger', 'Expected a JSON object.')
    if document.get('schema_version') != 1:
        error('ledger', 'Unsupported or missing schema_version; expected 1.')
    for key in ('updated_at_utc', 'baseline_version'):
        if not isinstance(document.get(key), str) or not document[key].strip():
            error('ledger', f'Missing {key}.')
    acceptance = document.get('acceptance', {})
    if (not isinstance(acceptance, dict)
            or type(acceptance.get('accepted')) is not int
            or type(acceptance.get('required')) is not int
            or not 0 <= acceptance['accepted'] <= acceptance['required']
            or acceptance['required'] <= 0):
        error('ledger', 'Acceptance must state integer accepted/required counts.')
    catalog = document.get('evidence_catalog', {})
    if not isinstance(catalog, dict):
        error('evidence_catalog', 'Expected an object keyed by evidence ID.')
        catalog = {}
    for key, value in catalog.items():
        if not isinstance(value, dict) or not value.get('kind') or not value.get('scope'):
            error(f'evidence {key}', 'Evidence needs a kind and an explicit verification scope.')
            continue
        if value.get('portable_path'):
            try:
                path = _local_file(root, value['portable_path'])
                if not path.is_file():
                    error(f'evidence {key}', 'Portable evidence file is missing.')
                elif value.get('sha256'):
                    if hashlib.sha256(path.read_bytes()).hexdigest() != value['sha256']:
                        error(f'evidence {key}', 'Portable evidence SHA-256 differs from the recorded receipt.')
            except (ValueError, OSError) as exc:
                error(f'evidence {key}', str(exc))

    def evidence_refs(refs, where):
        if not isinstance(refs, list):
            error(where, 'Evidence references must be a list.')
            return
        for ref in refs:
            if not isinstance(ref, str) or ref not in catalog:
                error(where, f'Unknown evidence ID {ref!r}.')

    entries = document.get('entries', [])
    if not isinstance(entries, list) or not entries:
        error('ledger', 'Expected a nonempty entries list.')
        entries = []
    seen = set()
    for entry in entries:
        if not isinstance(entry, dict):
            error('entry', 'Expected an object.')
            continue
        ident = entry.get('id')
        where = str(ident or 'entry')
        if not isinstance(ident, str) or not re.fullmatch(r'GT6-\d{3,}', ident):
            error(where, 'Expected a stable ID such as GT6-001.')
        elif ident in seen:
            error(where, 'Duplicate stable issue ID.')
        else:
            seen.add(ident)
        for key in ('title', 'component', 'symptom', 'fix', 'verification_scope'):
            if not isinstance(entry.get(key), str) or not entry[key].strip():
                error(where, f'Missing {key}.')
        status = entry.get('status')
        if status not in ('fixed', 'contained', 'open'):
            error(where, 'Status must be fixed, contained, or open.')
        cause = entry.get('cause', {})
        if (not isinstance(cause, dict)
                or cause.get('certainty') not in ('confirmed', 'unknown', 'hypothesis')
                or not cause.get('description')):
            error(where, 'Cause needs a description and explicit certainty.')
            cause = {}
        if status == 'fixed' and cause.get('certainty') != 'confirmed':
            error(where, 'A fixed issue must have a confirmed cause; retain uncertain work as contained/open.')
        for key in ('prevention_rules', 'implementation', 'coverage_gaps'):
            values = entry.get(key)
            if not isinstance(values, list) or any(not isinstance(v, str) or not v.strip() for v in values):
                error(where, f'{key} must be a list of nonempty strings.')
        gaps = entry.get('coverage_gaps') or []
        if gaps:
            gap_entries.append({'id': ident, 'gaps': gaps})
        refs = entry.get('regressions', [])
        if not isinstance(refs, list):
            error(where, 'Regressions must be a list.')
            refs = []
        if status == 'fixed' and not refs and not gaps:
            error(where, 'A fixed issue needs a regression reference or an explicit coverage gap.')
        for ref in refs:
            if not isinstance(ref, dict):
                error(where, 'Each regression reference must be an object.')
                continue
            if ref.get('kind') == 'python_unittest':
                target = ref.get('id')
                if not isinstance(target, str) or target not in known_tests:
                    error(where, f'Regression is not collected by selftest: {target!r}.')
                else:
                    python_refs.add(target)
            elif ref.get('kind') == 'node_behavior':
                filename, label = ref.get('file'), ref.get('label')
                if not isinstance(label, str) or not label:
                    error(where, 'Node behavior reference needs an exact check label.')
                    continue
                try:
                    path = _local_file(root, filename)
                    if filename not in checked_node_files:
                        # This repository uses literal checks.push labels. Reject
                        # stale labels rather than treating comments as checks.
                        text = path.read_text(encoding='utf-8')
                        checked_node_files[filename] = node_check_labels(text)
                    if label not in checked_node_files[filename]:
                        error(where, f'Node check label no longer exists in {filename}: {label!r}.')
                    else:
                        node_refs.add((filename, label))
                except (ValueError, OSError, UnicodeError) as exc:
                    error(where, f'Cannot inspect Node regression: {exc}')
            else:
                error(where, f'Unsupported regression kind {ref.get("kind")!r}.')
        evidence_refs(entry.get('evidence', []), where)
        history = entry.get('history')
        if not isinstance(history, list) or not history:
            error(where, 'Expected a dated history of changes.')
            history = []
        for event in history:
            if not isinstance(event, dict) or not event.get('date') or not event.get('change'):
                error(where, 'History entries need date and change.')
                continue
            evidence_refs(event.get('evidence', []), where + ' history')
    return {
        'passed': not errors,
        'schema_version': 1,
        'entries_checked': len(entries),
        'python_tests_collected': len(known_tests),
        'python_regression_references_checked': len(python_refs),
        'node_behavior_references_checked': len(node_refs),
        'errors': errors,
        'coverage_gaps': gap_entries,
        'node_checks_executed': False,
        'historical_pc_evidence_revalidated': False,
        'runtime_verified': False,
        'scope': 'Ledger structure, evidence references and actual collected regression names. '
                 'Reference validation does not run Node checks or prove historical or in-game results.',
    }


def audit_engineering_history(test_ids=None, root=None):
    root = Path(root) if root is not None else resource_root()
    if test_ids is None:
        from .selftest import test_suite
        test_ids = collected_test_ids(test_suite())
    try:
        document = json.loads((root / 'docs/engineering_history.json').read_text(encoding='utf-8'))
    except (OSError, ValueError, UnicodeError) as exc:
        return {'passed': False, 'errors': [f'Cannot read engineering ledger: {exc}'],
                'runtime_verified': False, 'scope': 'Ledger unavailable; no tests or runtime checks performed.'}
    return validate_history(document, test_ids, root)
