"""Shared execution contracts and frozen pre-refactor adapter parity."""
from pathlib import Path
import hashlib
import importlib.util
import json
import subprocess
import sys
import threading
import unittest
from types import SimpleNamespace
from unittest.mock import patch
from integrations.hermes import shared_fast_core as core

ROOT = Path(__file__).resolve().parents[1]
FIXTURE = ROOT / 'tools/fixtures/shared_fast_core/maintenance_contracts.json'


def adapter():
    spec = importlib.util.spec_from_file_location('shared_parity_adapter', ROOT / 'integrations/hermes/plugins/komatso-maintenance-manual/__init__.py')
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


class ParallelTests(unittest.TestCase):
    def test_operations_overlap(self):
        barrier = threading.Barrier(3)
        def operation(i):
            barrier.wait(timeout=2)
            return i
        result, timings = core.run_parallel([(str(i), lambda i=i: operation(i)) for i in range(3)])
        self.assertEqual(result, {'0': 0, '1': 1, '2': 2})
        self.assertEqual(list(timings), ['0', '1', '2'])

    def test_partial_failure_keeps_success_and_hides_exception_details(self):
        for error in [ValueError('secret path'), RuntimeError('secret path'), subprocess.TimeoutExpired('secret path', 1)]:
            def fail(): raise error
            result, _ = core.run_parallel([('good', lambda: {'evidence': [42]}), ('failed', fail)])
            self.assertEqual(result['good'], {'evidence': [42]})
            self.assertFalse(result['failed']['success'])
            self.assertNotIn('secret path', str(result))

    def test_aggregation_is_submission_order_even_when_completion_reverses(self):
        completed = threading.Event()
        def slow():
            self.assertTrue(completed.wait(2))
            return 'first'
        def fast():
            completed.set()
            return 'second'
        result, timings = core.run_parallel([('first', slow), ('second', fast)])
        self.assertEqual(list(result), ['first', 'second'])
        self.assertEqual(list(timings), ['first', 'second'])

    def test_callback_owns_timeout_and_other_evidence_survives(self):
        def deadline():
            subprocess.run([sys.executable, '-c', 'import time;time.sleep(10)'], timeout=.1,
                           creationflags=getattr(subprocess, 'CREATE_NO_WINDOW', 0))
        result, _ = core.run_parallel([('primary', lambda: 'evidence'), ('deadline', deadline)])
        self.assertEqual(result['primary'], 'evidence')
        self.assertFalse(result['deadline']['success'])

    def test_legacy_adapter_unhandled_timeout_still_propagates(self):
        def deadline(): raise subprocess.TimeoutExpired('operation', 240)
        with self.assertRaises(subprocess.TimeoutExpired):
            core.run_parallel([('deadline', deadline)], recoverable_errors=(OSError, ValueError, subprocess.CalledProcessError))

    def test_legacy_empty_batch_still_rejected(self):
        with self.assertRaises(ValueError): core.run_parallel([])


class CoverageTests(unittest.TestCase):
    def test_all_status_and_exhaustion_decisions(self):
        for status, exhausted, expected in [
            ('complete', False, core.NextAction.FINISH), ('complete', True, core.NextAction.FINISH),
            ('truncated', False, core.NextAction.FINISH_READ), ('truncated', True, core.NextAction.FINISH_READ),
            ('incomplete', False, core.NextAction.REFINE), ('incomplete', True, core.NextAction.STOP),
            ('unknown', False, core.NextAction.JUDGE)]:
            with self.subTest(status=status, exhausted=exhausted):
                self.assertEqual(core.coverage_next_action({'status': status}, exhausted=exhausted), expected)
        self.assertEqual(core.coverage_next_action(None), core.NextAction.JUDGE)

    def test_budget_is_domain_config_and_scoped(self):
        counts = {}; lock = threading.Lock()
        for _ in range(2): core.consume_attempt(counts, ('s', 'q'), limit=2, lock=lock, error='limit')
        with self.assertRaisesRegex(ValueError, 'limit'):
            core.consume_attempt(counts, ('s', 'q'), limit=2, lock=lock, error='limit')
        core.consume_attempt(counts, ('other', 'q'), limit=1, lock=lock, error='limit')
        self.assertEqual(counts, {('s', 'q'): 2, ('other', 'q'): 1})

    def test_failure_suppression_pattern_and_lifecycle_belong_to_caller(self):
        failures = set();lock = threading.Lock()
        core.remember_failure(failures, 'turn', 'denied 403', pattern=r'\b403\b', lock=lock)
        core.remember_failure(failures, 'other', '1403', pattern=r'\b403\b', lock=lock)
        self.assertEqual(failures, {'turn'})
        failures.discard('turn')
        self.assertFalse(failures)


class PolicyTests(unittest.TestCase):
    def test_selection_dedup_order_and_unknown_section(self):
        text = '# Scope\n\nAlready loaded.\n\n## Unfamiliar safety\n\nRetain constraint.\n\n## Irrelevant\n\nSkip.\n\n## Data\n\nPublic.\n\nPrivate.'
        result, headings = core.select_policy(text, 'Already loaded.',
            keep_section=lambda h: 'Irrelevant' not in h, keep_paragraph=lambda p: p != 'Private.')
        self.assertEqual(result, '## Unfamiliar safety\n\nRetain constraint.\n\n## Data\n\nPublic.')
        self.assertEqual(headings, ['## Unfamiliar safety', '## Data'])

    def test_core_contains_no_domain_or_profile_branching(self):
        source = Path(core.__file__).read_text(encoding='utf-8').casefold()
        for word in ['maintenance', 'komatsu', 'partbook', 'shop manual', 'manual_sections']:
            self.assertNotIn(word, source)
        self.assertNotIn('if profile', source)


class SidecarTests(unittest.TestCase):
    def test_returns_while_work_is_blocked_and_only_publishes_complete_slot(self):
        release, entered, ready = threading.Event(), threading.Event(), threading.Event()
        slot = {}
        def work():
            entered.set()
            release.wait(2)
            return {'data': 7}
        try:
            self.assertTrue(core.start_sidecar(work, ready=ready, slot=slot,
                on_timeout=lambda: 'timeout', on_error=lambda: 'error', name='fixture-sidecar'))
            self.assertTrue(entered.wait(2))
            self.assertFalse(ready.is_set())
            self.assertEqual(slot, {})
            primary, _ = core.run_parallel([('response', lambda: 'available')])
            self.assertEqual(primary['response'], 'available')
            self.assertFalse(ready.is_set())
        finally:
            release.set()
        self.assertTrue(ready.wait(2))
        self.assertEqual(slot['result'], {'data': 7})
        self.assertGreaterEqual(slot['finished'], slot['started'])

    def test_error_and_deadline_are_adapter_results(self):
        for error, expected in [(TypeError('private'), 'error'), (subprocess.TimeoutExpired('private', 1), 'timeout')]:
            ready, slot = threading.Event(), {}
            def work(): raise error
            core.start_sidecar(work, ready=ready, slot=slot,
                on_timeout=lambda: 'timeout', on_error=lambda: 'error', name='fixture-sidecar')
            self.assertTrue(ready.wait(2))
            self.assertEqual(slot['result'], expected)

    def test_thread_start_failure_returns_without_waiting(self):
        with patch.object(core.threading, 'Thread', side_effect=RuntimeError('no worker')):
            self.assertFalse(core.start_sidecar(lambda: None, ready=threading.Event(), slot={},
                on_timeout=lambda: None, on_error=lambda: None, name='fixture'))


class BindingTests(unittest.TestCase):
    def test_goal_and_tool_ceiling_fields_are_preserved(self):
        task = {'goal': 'Exact original question?', 'toolsets': ['research'], 'context': 'before', 'role': 'technical'}
        bound = core.bind_task(task, context='prepared', goal=task['goal'])
        self.assertEqual(bound['goal'], task['goal'])
        self.assertEqual(bound['toolsets'], task['toolsets'])
        self.assertEqual(bound['role'], task['role'])
        self.assertEqual(task['context'], 'before')

    def test_receipt_is_one_use_and_untrusted_token_cannot_claim(self):
        pending={};lock=threading.Lock()
        token=core.issue_receipt(pending,lock=lock,ttl=3600)
        with lock:
            self.assertFalse(core.claim_receipt(pending,'forged'))
            self.assertTrue(core.claim_receipt(pending,token))
            self.assertFalse(core.claim_receipt(pending,token))

    def test_original_pruning_ttl_preserved(self):
        pending={'stale':0,'current':99}
        with patch.object(core.time,'monotonic',return_value=100):
            token=core.issue_receipt(pending,lock=threading.Lock(),ttl=10)
        self.assertNotIn('stale',pending)
        self.assertIn('current',pending)
        self.assertIn(token,pending)


class AdapterParityTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.plugin=adapter()
        cls.frozen=json.loads(FIXTURE.read_text(encoding='utf-8'))

    def test_policy_slices_exactly_match_pre_refactor(self):
        for case in self.frozen['policies']:
            with self.subTest(question=case['question'], presentation=case['presentation'], enrichment=case['part_enrichment']):
                actual=self.plugin.select_rules(case['source'],case['existing'],case['question'],case['presentation'],case['part_enrichment'])
                self.assertEqual(list(actual),case['result'])

    def test_real_device_policy_slices_match_pre_refactor_hashes(self):
        root=(ROOT/'AGENTS.md').read_text(encoding='utf-8')
        for case in self.frozen['device_policy_hashes']:
            source=(ROOT/case['folder']/'AGENTS.md').read_text(encoding='utf-8')
            result=self.plugin.select_rules(source,root,case['question'],case['presentation'],case['part_enrichment'])
            digest=hashlib.sha256(json.dumps(result,ensure_ascii=False).encode()).hexdigest()
            with self.subTest(model=case['model'],question=case['question']):
                self.assertEqual(digest,case['sha256'])

    def test_next_action_text_is_byte_identical_for_all_states(self):
        for case in self.frozen['coverage']:
            self.assertEqual(self.plugin.evidence_followup(case['coverage'],case['broad']),case['result'])

    def test_public_domain_schemas_unchanged(self):
        self.assertEqual(self.plugin.tool_schema(self.plugin.manual_models()),dict(self.frozen['manual_schema'],description=self.frozen['manual_schema']['description'].replace('Maintenance Parent fast Shop Manual','Shared fast Shop Manual').replace('Not for delegated workers.','Delegated children may use only inherited parent tools.')))
        self.assertEqual(self.plugin.part_schema(self.plugin.part_model_names()),self.frozen['part_schema'])
        self.assertEqual(self.plugin.MAX_RETRIEVES,3)

    def test_future_domain_instantiates_without_loading_existing_domain(self):
        script = '''import sys
from integrations.hermes.shared_fast_core import Operation, register_operations, run_parallel, coverage_next_action
class Host:
    def __init__(self): self.tools=[]
    def register_tool(self, **kw): self.tools.append(kw)
h=Host()
register_operations(h,[Operation('fixture_evidence','fixture',{'name':'fixture_evidence'},lambda a: a,'fixture')])
assert [x['name'] for x in h.tools]==['fixture_evidence']
assert run_parallel([('fixture',lambda:42)])[0]=={'fixture':42}
assert coverage_next_action({'status':'complete'})=='finish'
assert not any('maintenance' in name for name in sys.modules)
'''
        subprocess.run([sys.executable,'-c',script],cwd=ROOT,check=True,
                       creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))

    def test_direct_registration_passes_schema_handler_and_availability_unchanged(self):
        captured=[];handler=lambda args:args;check=lambda:True;schema={'name':'fixture'}
        core.register_operations(SimpleNamespace(register_tool=lambda **kw:captured.append(kw)),
            [core.Operation('fixture','fixture',schema,handler,'description',check)])
        self.assertEqual(captured,[dict(name='fixture',toolset='fixture',schema=schema,
                                       handler=handler,description='description',check_fn=check)])

if __name__ == '__main__': unittest.main()
