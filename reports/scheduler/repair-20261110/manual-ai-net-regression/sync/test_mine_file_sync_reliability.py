"""Offline reliability coverage; all paths belong to temporary local fixtures."""
import argparse
import ctypes
import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time
import unittest
from unittest.mock import patch
from datetime import datetime, timezone

spec=importlib.util.spec_from_file_location('baseline_tests',Path(__file__).with_name('test_mine_file_sync.py'))
base=importlib.util.module_from_spec(spec);spec.loader.exec_module(base)
sync=base.sync

class ReliabilityTests(unittest.TestCase):
    setUp=base.MineSyncTests.setUp
    tearDown=base.MineSyncTests.tearDown
    add=base.MineSyncTests.add
    cli=base.MineSyncTests.cli
    def args(self):
        return argparse.Namespace(source=self.source,dest=self.destination,home=self.home,dry_run=False,report=False,
                                  backoff_minutes=(2,4,8,10),backoff_jitter=.1)
    def state(self):
        return json.loads((self.home/'state/mine-file-sync.json').read_text())
    def stamp(self,n):
        return datetime.fromtimestamp(n,timezone.utc).isoformat()
    def outage(self,n=1000):
        with patch.object(sync,'utcnow',return_value=self.stamp(n)), patch.object(sync,'reachable',side_effect=TimeoutError('fixture offline')), patch.object(sync.random,'random',return_value=0):
            self.assertEqual(sync.run(self.args()),0)
        return self.state()

    def test_healthy_and_lifecycle_identity(self):
        self.add('report.xlsx')
        self.assertEqual(self.cli().returncode,0)
        state=self.state()
        self.assertTrue(state['sync_healthy'])
        self.assertEqual(state['health_status'],'success')
        life=state['worker_lifecycle']
        self.assertTrue(life['termination_confirmed'])
        self.assertEqual(life['bounded_wait_result'],'signaled_and_reaped')
        self.assertTrue(life['worker_pid'] and life['creation_identity'])
        self.assertEqual(life['worker_exit_code'],0)
        self.assertTrue(life['attempt_finished_at'])
        self.assertEqual(self.cli().returncode,0)
        self.assertEqual(self.state()['last_run']['copied'],0)

    def test_outage_repeated_cron_ticks_and_max_backoff(self):
        state=self.outage()
        self.assertEqual(sync.parse_time(state['next_attempt_at']),1120)
        self.assertFalse(state['sync_healthy'])
        self.assertEqual(state['consecutive_source_failures'],1)
        state=self.outage(1120)
        self.assertEqual(sync.parse_time(state['next_attempt_at']),1360)
        for now in [1121,1240,1359]:
            with patch.object(sync,'utcnow',return_value=self.stamp(now)), patch.object(sync,'reachable',side_effect=AssertionError('TCP forbidden')), patch.object(sync,'supervise',side_effect=AssertionError('spawn forbidden')), patch.object(sync,'sync_tree',side_effect=AssertionError('Excel forbidden')):
                self.assertEqual(sync.run(self.args()),0)
            skipped=self.state()
            self.assertEqual(skipped['health_status'],'backoff_skip')
            self.assertFalse(skipped['sync_healthy'])
            self.assertEqual(skipped['last_attempt_at'],self.stamp(1120))
            self.assertEqual(skipped['consecutive_source_failures'],2)
        for now,delay in [(1360,480),(1840,600),(2440,600),(3040,600)]:
            state=self.outage(now)
            self.assertEqual(sync.parse_time(state['next_attempt_at'])-now,delay)
        self.assertEqual(state['outage_since'],self.stamp(1000))

    def test_jitter_and_configuration_boundaries(self):
        for count,base_delay in [(1,120),(2,240),(3,480),(4,600),(100,600)]:
            self.assertEqual(sync.backoff_delay(count,(2,4,8,10),.1,0),base_delay)
            self.assertAlmostEqual(sync.backoff_delay(count,(2,4,8,10),.1,1),base_delay*.9)
        self.assertEqual(sync.backoff_delay(10,(1,3,5),0,1),300)
        for extra in [('--backoff-minutes','2,30'),('--backoff-minutes','nan'),('--backoff-jitter','nan'),('--backoff-minutes','4,2')]:
            self.assertEqual(self.cli(*extra).returncode,2)

    def test_restart_persisted_outage_and_clock_jumps(self):
        self.outage()
        with patch.object(sync,'utcnow',return_value=self.stamp(1050)), patch.object(sync,'reachable',side_effect=AssertionError('offline skip')):
            sync.run(self.args())
        state=self.state()
        self.assertEqual(state['health_status'],'backoff_skip')
        self.assertEqual(sync.retry_remaining(state,900,600),70)
        self.assertEqual(sync.parse_time(state['next_attempt_at']),970)
        state['next_attempt_at']=self.stamp(100000)
        state['last_clock_at']=self.stamp(900)
        self.assertEqual(sync.retry_remaining(state,901,600),600)
        self.assertEqual(sync.retry_remaining(state,1000000,600),0)

    def test_stale_state_migrates_additively(self):
        path=self.home/'state/mine-file-sync.json'
        sync.atomic_json(path,{'schema_version':3,'flatten_bindings':{'keep':'relative'},'custom':'preserve',
                              'consecutive_source_failures':'bad','source':str(self.source),'destination':str(self.destination)})
        self.outage()
        state=self.state()
        self.assertEqual(state['custom'],'preserve')
        self.assertEqual(state['flatten_bindings'],{'keep':'relative'})
        self.assertEqual(state['schema_version'],3)
        self.assertEqual(state['reliability_schema_version'],1)
        self.assertEqual(state['consecutive_source_failures'],1)
        self.assertIn('last_success_at',state)

    def test_tcp_success_is_not_recovery(self):
        self.source.rmdir()
        self.outage()
        with patch.object(sync,'utcnow',return_value=self.stamp(1120)), patch.object(sync,'reachable',return_value=None):
            self.assertEqual(sync.run(self.args()),0)
        self.assertTrue(self.state()['outage'])
        self.assertEqual(self.state()['consecutive_source_failures'],2)
        self.assertEqual(self.state()['last_run']['failure_classification'],'source_outage')

    def test_recovery_enumeration_resets_and_copies_fresh_data(self):
        self.outage()
        self.add('fresh.xlsx',b'fresh')
        with patch.object(sync,'utcnow',return_value=self.stamp(1120)), patch.object(sync,'reachable',return_value=None):
            sync.run(self.args())
        state=self.state()
        self.assertFalse(state['outage'])
        self.assertEqual(state['consecutive_source_failures'],0)
        self.assertIsNone(state['next_attempt_at'])
        self.assertEqual((self.destination/'fresh.xlsx').read_bytes(),b'fresh')
        self.assertTrue(state['last_run']['source_enumerated'])

    def test_locked_workbook_partial_recovers_network_without_success_stamp(self):
        src=self.add('locked.xlsx')
        self.outage()
        with src.open('r+b'), patch.object(sync,'utcnow',return_value=self.stamp(1120)), patch.object(sync,'reachable',return_value=None):
            sync.run(self.args())
        state=self.state()
        self.assertEqual(state['health_status'],'partial')
        self.assertFalse(state['outage'])
        self.assertFalse(state['sync_healthy'])
        self.assertIsNone(state['last_success_at'])
        self.assertEqual(state['last_run']['skipped_locked'],1)

    def test_worker_timeout_confirmed_and_unknown_io_not_network(self):
        real_popen=subprocess.Popen
        def stalled(command,**kw):
            report=command[command.index('--worker-result')+1]
            payload='import pathlib,time;pathlib.Path('+repr(report)+').write_text('+repr(json.dumps(sync.empty_result()))+');time.sleep(60)'
            return real_popen([sys._base_executable,'-c',payload],**kw)
        with patch.object(sync.subprocess,'Popen',stalled),patch.object(sync,'STALL_SECONDS',.3):
            result=sync.supervise(self.args())
        self.assertEqual(result['failure_classification'],'io_timeout_unknown')
        life=result['worker_lifecycle']
        self.assertTrue(life['kill_requested'])
        self.assertTrue(life['termination_confirmed'])
        self.assertEqual(life['kill_result'],'request_succeeded')
        self.assertEqual(life['timeout_reason'],'inactivity')

    def test_kill_reap_failure_blocks_respawn(self):
        class Unkillable:
            pid=123456; returncode=None
            def poll(self):return None
            def kill(self):raise OSError('fixture kill denied')
            def wait(self,timeout):raise subprocess.TimeoutExpired('fixture',timeout)
        with patch.object(sync.subprocess,'Popen',return_value=Unkillable()),patch.object(sync,'process_identity',return_value='133000000000000000'),patch.object(sync,'STALL_SECONDS',0):
            result=sync.supervise(self.args())
        self.assertEqual(result['status'],'worker_blocked')
        self.assertFalse(result['worker_lifecycle']['termination_confirmed'])
        self.assertEqual(result['worker_lifecycle']['bounded_wait_result'],'deadline_exceeded')
        with patch.object(sync,'prior_worker_status',return_value='unknown'),patch.object(sync,'reachable',side_effect=AssertionError('no network')),patch.object(sync.subprocess,'Popen',side_effect=AssertionError('no duplicate worker')):
            sync.run(self.args())
        self.assertEqual(self.state()['health_status'],'worker_blocked')

    def test_corrupt_or_spawn_intent_journal_blocks_without_network(self):
        path=sync.lifecycle_path(self.args());path.parent.mkdir(exist_ok=True)
        for text in ['{bad',json.dumps({'termination_confirmed':False,'worker_pid':None})]:
            path.write_text(text)
            with patch.object(sync,'reachable',side_effect=AssertionError('no TCP')),patch.object(sync,'supervise',side_effect=AssertionError('no spawn')):
                sync.run(self.args())
            self.assertEqual(self.state()['health_status'],'worker_blocked')

    def test_live_handle_identity_reuse_and_exit(self):
        p=subprocess.Popen([sys._base_executable,'-c','import time;time.sleep(60)'],creationflags=subprocess.CREATE_NO_WINDOW)
        try:
            record={'worker_pid':p.pid,'creation_identity':sync.process_identity(p),'termination_confirmed':False}
            self.assertEqual(sync.prior_worker_status(record),'alive')
            self.assertEqual(sync.prior_worker_status({**record,'creation_identity':'wrong'}),'identity_replaced')
        finally:
            p.kill();p.wait(timeout=5)
        self.assertEqual(sync.prior_worker_status(record),'terminated')

    def test_parent_crash_journal_prevents_duplicate(self):
        script=Path(sync.__file__)
        payload='''import importlib.util,sys,subprocess,json
+from pathlib import Path
+spec=importlib.util.spec_from_file_location('sync',sys.argv[1]);s=importlib.util.module_from_spec(spec);spec.loader.exec_module(s)
+real=s.subprocess.Popen
+def slow(command,**kw):
+ report=command[command.index('--worker-result')+1]
+ code='import pathlib,time;pathlib.Path('+repr(report)+').write_text('+repr(json.dumps(s.empty_result()))+');time.sleep(60)'
+ return real([sys._base_executable,'-c',code],**kw)
+s.subprocess.Popen=slow
+s.main(['--source',sys.argv[2],'--dest',sys.argv[3],'--home',sys.argv[4]])
+'''.replace('\n+','\n')
        parent=subprocess.Popen([sys._base_executable,'-c',payload,str(script),str(self.source),str(self.destination),str(self.home)],creationflags=subprocess.CREATE_NO_WINDOW)
        record=None
        try:
            deadline=time.monotonic()+10
            while time.monotonic()<deadline:
                try:
                    record=json.loads(sync.lifecycle_path(self.args()).read_text())
                    if record.get('creation_identity'):break
                except (OSError,ValueError):pass
                time.sleep(.05)
            self.assertTrue(record and record.get('creation_identity'))
            parent.kill();parent.wait(timeout=5)
            with patch.object(sync,'reachable',side_effect=AssertionError('guard before network')),patch.object(sync,'supervise',side_effect=AssertionError('no duplicate')):
                sync.run(self.args())
            self.assertEqual(self.state()['health_status'],'worker_blocked')
        finally:
            if parent.poll() is None:parent.kill();parent.wait(timeout=5)
            if record and record.get('creation_identity'):
                api=sync.windows_process_api();handle=api.OpenProcess(0x100000|0x1000|1,False,record['worker_pid'])
                if handle:
                    try:
                        self.assertEqual(sync.creation_identity(api,handle),record['creation_identity'])
                        api.TerminateProcess.argtypes=[ctypes.c_void_p,ctypes.c_uint]
                        self.assertTrue(api.TerminateProcess(handle,1))
                        self.assertEqual(api.WaitForSingleObject(handle,5000),0)
                    finally:api.CloseHandle(handle)
        self.assertIsNone(sync.worker_guard(self.args()))

    def test_real_process_restart_during_backoff(self):
        self.outage(time.time())
        result=self.cli('--report')
        self.assertEqual(result.returncode,0)
        report=json.loads(result.stdout)
        self.assertEqual(report['status'],'backoff_skip')
        self.assertFalse(report['attempted'])
        self.assertFalse(report['sync_healthy'])
        self.assertFalse(sync.lifecycle_path(self.args()).exists())

    def test_access_failure_is_not_connectivity_outage(self):
        error=PermissionError('fixture source access denied')
        args=self.args();args.worker_result=self.home/'state/report.json'
        with patch.object(sync,'sync_tree',side_effect=error):
            self.assertEqual(sync.worker(args),0)
        result=json.loads(args.worker_result.read_text())
        self.assertEqual(result['failure_classification'],'source_access')
        with patch.object(sync,'supervise',return_value=result),patch.object(sync,'reachable',return_value=None):
            sync.run(self.args())
        self.assertEqual(self.state()['consecutive_source_failures'],0)
        self.assertIsNone(self.state()['next_attempt_at'])

    def test_run_budget_kill_is_distinct_from_outage(self):
        real=subprocess.Popen
        def slow(command,**kw):
            report=command[command.index('--worker-result')+1]
            code='import pathlib,time;pathlib.Path('+repr(report)+').write_text('+repr(json.dumps(sync.empty_result()))+');time.sleep(60)'
            return real([sys._base_executable,'-c',code],**kw)
        with patch.object(sync.subprocess,'Popen',slow),patch.object(sync,'MAX_RUN_SECONDS',.3):
            result=sync.supervise(self.args())
        self.assertEqual(result['status'],'time_budget')
        self.assertEqual(result['failure_classification'],'run_budget')
        self.assertTrue(result['worker_lifecycle']['termination_confirmed'])
        self.assertEqual(result['worker_lifecycle']['timeout_reason'],'run_budget')

    def test_confirmed_source_enumeration_timeout_gets_backoff(self):
        real=subprocess.Popen
        def stalled_source(command,**kw):
            report=command[command.index('--worker-result')+1]
            snapshot=dict(sync.empty_result(),phase='source_enumeration')
            code='import pathlib,time;pathlib.Path('+repr(report)+').write_text('+repr(json.dumps(snapshot))+');time.sleep(60)'
            return real([sys._base_executable,'-c',code],**kw)
        with patch.object(sync.subprocess,'Popen',stalled_source),patch.object(sync,'STALL_SECONDS',.3):
            self.assertEqual(sync.run(self.args()),0)
        state=self.state()
        self.assertEqual(state['last_failure_classification'],'source_timeout')
        self.assertEqual(state['consecutive_source_failures'],1)
        self.assertTrue(state['next_attempt_at'])
        self.assertTrue(state['worker_lifecycle']['termination_confirmed'])

    def test_defaults_source_destination_preserved(self):
        self.assertEqual(str(sync.SOURCE),r'\\192.168.20.251\share' + chr(92))
        self.assertEqual(str(sync.DEST),r'E:\Function')

if __name__=='__main__':unittest.main(verbosity=2)
