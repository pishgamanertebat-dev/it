"""Staging lifecycle tests; no live paths, subprocesses, or mine I/O."""
import argparse
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import tempfile
import unittest
from unittest.mock import Mock, patch

SCRIPT = Path(os.environ.get('KOMATSO_SYNC_SCRIPT',
    'C:/Users/win-10/AppData/Local/hermes/scripts/mine_file_sync.py'))
spec = importlib.util.spec_from_file_location('sync_cleanup_fixture', SCRIPT)
sync = importlib.util.module_from_spec(spec)
spec.loader.exec_module(sync)


class CleanupTests(unittest.TestCase):
    def setUp(self):
        parent = Path('E:/KomatsoAI/runtime/repairs-entry-tests')
        parent.mkdir(exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(self.temp.cleanup)
        root = Path(self.temp.name)
        self.args = argparse.Namespace(home=root/'home', dest=root/'local')
        (self.args.home/'state').mkdir(parents=True)
        self.args.dest.mkdir()
        self.partial = self.args.dest/('.mine-sync-'+'a'*32+'.partial')
        self.partial.write_bytes(b'')
        self.report = self.args.home/'state/mine-sync-run-fixture.json'
        self.report.write_text(json.dumps(dict(temporary=str(self.partial))))
        self.record = dict(report_path=str(self.report), termination_confirmed=True)

    def test_confirmed_attempt_cleans_registered_partial(self):
        self.assertTrue(sync.cleanup_report(self.args, self.record))
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.report.exists())

    def test_parent_crash_before_first_worker_report_can_recover(self):
        self.report.write_bytes(b'')
        self.assertTrue(sync.cleanup_report(self.args, self.record))
        self.assertFalse(self.report.exists())
        # There was no registered pointer; arbitrary files are not swept.
        self.assertTrue(self.partial.exists())

    def test_active_or_unknown_worker_is_never_cleaned(self):
        for outcome in ('alive', 'unknown'):
            with patch.object(sync, 'prior_worker_status', return_value=outcome):
                self.assertFalse(sync.cleanup_report(self.args, self.record))
            self.assertTrue(self.partial.exists())
            self.assertTrue(self.report.exists())

    def test_late_termination_reconciles_and_cleans_before_new_attempt(self):
        self.record['termination_confirmed'] = False
        sync.lifecycle_path(self.args).write_text(json.dumps(self.record))
        with patch.object(sync, 'prior_worker_status', return_value='terminated'):
            self.assertIsNone(sync.worker_guard(self.args))
        self.assertFalse(self.partial.exists())
        self.assertFalse(self.report.exists())

    def test_sharing_violation_keeps_reference_and_next_guard_retries(self):
        sync.lifecycle_path(self.args).write_text(json.dumps(self.record))
        real_unlink = Path.unlink
        def denied(path, *args, **kwargs):
            if path == self.partial:
                raise PermissionError('fixture active handle')
            return real_unlink(path, *args, **kwargs)
        with patch.object(Path, 'unlink', denied):
            self.assertEqual(sync.worker_guard(self.args)['failure_classification'], 'staging_cleanup_pending')
        self.assertTrue(self.report.exists())
        self.assertIsNone(sync.worker_guard(self.args))
        self.assertFalse(self.partial.exists())

    def test_outside_path_or_reparse_is_never_deleted(self):
        other = self.args.home/('.mine-sync-'+'b'*32+'.partial')
        other.write_bytes(b'preserve')
        self.report.write_text(json.dumps(dict(temporary=str(other))))
        self.assertFalse(sync.cleanup_report(self.args, self.record))
        self.assertTrue(other.exists())
        self.report.write_text(json.dumps(dict(temporary=str(self.partial))))
        with patch.object(sync, 'reparse', side_effect=lambda p: p == self.partial):
            self.assertFalse(sync.cleanup_report(self.args, self.record))
        self.assertTrue(self.partial.exists())

    def test_fault_merge_keeps_parent_heartbeat_alive(self):
        process = Mock(returncode=0)
        process.communicate.side_effect = [subprocess.TimeoutExpired('fixture', 1),
            subprocess.TimeoutExpired('fixture', 1), (b'{"status":"merged"}', b'')]
        process.poll.return_value = 0
        pulse = Mock()
        with patch.object(sync.subprocess, 'Popen', return_value=process):
            sync.publish_fault_report(self.partial, self.args.dest/'drivers.xlsx', pulse=pulse)
        self.assertEqual(pulse.call_count, 3)
        process.kill.assert_not_called()


if __name__ == '__main__':
    unittest.main()
