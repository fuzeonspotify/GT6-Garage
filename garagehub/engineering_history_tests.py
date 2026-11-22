"""Catch stale or overclaimed engineering history after future refactors."""
import copy
import tempfile
import unittest
from pathlib import Path

from .engineering_history import collected_test_ids, validate_history


class EngineeringHistoryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        (self.root / 'checks.cjs').write_text("checks.push('Retains selected mode');\n", encoding='utf-8')
        self.test_id = 'garagehub.fixture.ExampleTests.test_actual_behavior'
        self.document = {
            'schema_version': 1, 'updated_at_utc': '2026-11-18T00:00:00Z',
            'baseline_version': '0.4.0', 'acceptance': {'accepted': 0, 'required': 2},
            'evidence_catalog': {'proof': {'kind': 'synthetic', 'scope': 'Fixture only.'}},
            'entries': [{
                'id': 'GT6-001', 'title': 'Retain mode', 'component': 'settings',
                'status': 'fixed', 'symptom': 'Mode changed unexpectedly.',
                'cause': {'certainty': 'confirmed', 'description': 'A stale response won.'},
                'fix': 'Serialize requests.', 'prevention_rules': ['Keep current edits.'],
                'implementation': ['web/app.js'], 'coverage_gaps': [],
                'regressions': [
                    {'kind': 'python_unittest', 'id': self.test_id},
                    {'kind': 'node_behavior', 'file': 'checks.cjs', 'label': 'Retains selected mode'}],
                'evidence': ['proof'], 'verification_scope': 'Synthetic settings only.',
                'history': [{'date': '2026-11-18', 'change': 'Fixed and tested.', 'evidence': ['proof']}],
            }],
        }

    def audit(self):
        return validate_history(self.document, [self.test_id], self.root)

    def test_valid_references_do_not_claim_runtime_or_node_execution(self):
        result = self.audit()
        self.assertTrue(result['passed'], result['errors'])
        self.assertFalse(result['runtime_verified'])
        self.assertFalse(result['node_checks_executed'])
        self.assertFalse(result['historical_pc_evidence_revalidated'])

    def test_deleted_or_uncollected_python_regression_fails_gate(self):
        self.document['entries'][0]['regressions'][0]['id'] += '_deleted'
        self.assertFalse(self.audit()['passed'])

    def test_renamed_node_check_fails_gate(self):
        (self.root / 'checks.cjs').write_text("checks.push('Different behavior');\n", encoding='utf-8')
        self.assertFalse(self.audit()['passed'])

    def test_commented_or_quoted_retired_node_checks_do_not_satisfy_reference(self):
        call = "checks.push('Retains selected mode')"
        for text in ('// '+call, '/* '+call+' */', 'const retired="'+call+'";', '`'+call+'`;'):
            with self.subTest(text=text):
                (self.root / 'checks.cjs').write_text(text, encoding='utf-8')
                self.assertFalse(self.audit()['passed'])

    def test_fixed_status_rejects_unconfirmed_cause(self):
        self.document['entries'][0]['cause']['certainty'] = 'hypothesis'
        self.assertFalse(self.audit()['passed'])
        self.document['entries'][0]['status'] = 'open'
        self.assertTrue(self.audit()['passed'])

    def test_duplicate_issue_and_missing_evidence_fail_gate(self):
        self.document['entries'].append(copy.deepcopy(self.document['entries'][0]))
        self.document['entries'][0]['history'][0]['evidence'] = ['missing']
        errors = self.audit()['errors']
        self.assertTrue(any('Duplicate' in x for x in errors))
        self.assertTrue(any('Unknown evidence' in x for x in errors))

    def test_uncovered_fix_requires_explicit_gap(self):
        self.document['entries'][0]['regressions'] = []
        self.assertFalse(self.audit()['passed'])
        self.document['entries'][0]['coverage_gaps'] = ['A native fixture is still required.']
        result = self.audit()
        self.assertTrue(result['passed'])
        self.assertEqual(result['coverage_gaps'][0]['id'], 'GT6-001')

    def test_evidence_and_node_paths_cannot_escape_bundle(self):
        for field in ('node', 'evidence'):
            with self.subTest(field=field):
                original = copy.deepcopy(self.document)
                if field == 'node':
                    self.document['entries'][0]['regressions'][1]['file'] = '../checks.cjs'
                else:
                    self.document['evidence_catalog']['proof']['portable_path'] = '../outside.json'
                self.assertFalse(self.audit()['passed'])
                self.document = original

    def test_changed_portable_evidence_fails_recorded_hash(self):
        import hashlib
        path = self.root / 'receipt.json'
        path.write_bytes(b'{"passed": true}')
        self.document['evidence_catalog']['proof'].update(
            portable_path='receipt.json', sha256=hashlib.sha256(path.read_bytes()).hexdigest())
        self.assertTrue(self.audit()['passed'])
        path.write_bytes(b'{"passed": false}')
        self.assertFalse(self.audit()['passed'])

    def test_collection_reads_nested_suite_without_running_it(self):
        suite = unittest.TestSuite([unittest.TestSuite([self.__class__('test_renamed_node_check_fails_gate')])])
        ids = collected_test_ids(suite)
        self.assertEqual(ids, [self.__class__('test_renamed_node_check_fails_gate').id()])
        self.assertEqual(suite.countTestCases(), 1)
