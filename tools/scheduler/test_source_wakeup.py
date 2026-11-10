"""Accepted Sync versions wake bounded retries without bypassing safety holds."""
from datetime import timedelta
import json
from unittest.mock import Mock,patch
import unittest
import yaml
from .test_machinery_performance import Harness,TEHRAN
from .runner import _wake_after_sync,_expire_missing_reports
from .misfire import due_storage_key


class WakeTests(Harness,unittest.TestCase):
    def test_new_sync_version_wakes_waiting_due_but_does_not_release_safety_hold(self):
        self.job=dict(self.job,id='maintenance_daily_report',task='maintenance_daily_report')
        now=self.old+timedelta(minutes=2);key=(self.job['id'],due_storage_key(self.old))
        with self.conn:
            self.conn.execute('INSERT INTO runs(schedule_id,due,recipient,task,status,started,error,next_attempt_at) VALUES (?,?,?,?,?,?,?,?)',
                (*key,'capability:maintenance',self.job['task'],'waiting_for_data',due_storage_key(self.old),'no_target_date_records',due_storage_key(now+timedelta(minutes=15))))
        state=self.directory/'sync.json'
        def save(digest):
            state.write_text(json.dumps(dict(outage=False,last_run=dict(source_enumerated=True,finished_at=now.isoformat(),
                maintenance_guard=dict(status='imported',hash=digest)))),'utf8')
        save('new-source-version');_wake_after_sync(self.conn,self.job,now,state_path=state)
        self.assertEqual(self.conn.execute('SELECT next_attempt_at FROM runs').fetchone()[0],due_storage_key(now))
        with self.conn:
            self.conn.execute('UPDATE runs SET next_attempt_at=?',(due_storage_key(now+timedelta(minutes=15)),))
            self.conn.execute("INSERT INTO report_recovery_hold VALUES (?,?,'held','real_safety_conflict',?,?,?,NULL)",(*key,now.isoformat(),now.isoformat(),'{}'))
        save('another-source-version');_wake_after_sync(self.conn,self.job,now,state_path=state)
        self.assertEqual(self.conn.execute('SELECT next_attempt_at FROM runs').fetchone()[0],due_storage_key(now+timedelta(minutes=15)))

    def test_source_stale_uses_technical_replay_and_never_missing_expiry(self):
        self.seed(status='retry_wait',error='source_stale')
        self.assertEqual(_expire_missing_reports(self.conn,self.job,self.old+timedelta(hours=40)),set())
        self.assertIn(self.old,self.plan(self.old+timedelta(days=6)))

    def test_connectivity_recovery_also_wakes_same_accepted_hash(self):
        self.job=dict(self.job,id='maintenance_daily_report',task='maintenance_daily_report')
        now=self.old+timedelta(minutes=2);key=(self.job['id'],due_storage_key(self.old))
        with self.conn:self.conn.execute('INSERT INTO runs(schedule_id,due,recipient,task,status,started,error,next_attempt_at) VALUES (?,?,?,?,?,?,?,?)',
            (*key,'capability:maintenance',self.job['task'],'retry_wait',due_storage_key(self.old),'source_unavailable',due_storage_key(now+timedelta(minutes=15))))
        state=self.directory/'same-hash-recovery.json'
        value=dict(outage=False,last_run=dict(source_enumerated=True,finished_at=now.isoformat(),
            maintenance_guard=dict(status='unchanged',hash='same-hash')))
        state.write_text(json.dumps(value),'utf8');_wake_after_sync(self.conn,self.job,now,state_path=state)
        with self.conn:self.conn.execute('UPDATE runs SET next_attempt_at=?',(due_storage_key(now+timedelta(minutes=15)),))
        value['last_recovery_at']=now.isoformat();state.write_text(json.dumps(value),'utf8')
        _wake_after_sync(self.conn,self.job,now,state_path=state)
        self.assertEqual(self.conn.execute('SELECT next_attempt_at FROM runs').fetchone()[0],due_storage_key(now))

    def test_bad_pdf_is_terminal_and_not_technical_retry(self):
        from .misfire import row_actionable
        self.assertFalse(row_actionable(dict(status='failed',error='pdf_validation'),self.old))

    def test_next_tick_after_sync_delivers_maintenance_before_backoff_without_manual_hold(self):
        from .runner import load_config,tick
        from .tasks import TASKS,overflow_report_date
        from .recovery_review import hold_backlog
        from tools.authorization.maintenance import CAPABILITY
        from tools.authorization.store import RecipientSetResolution
        from tools.fleet.maintenance_daily import prepared
        task='maintenance_daily_report'
        config=dict(version=1,timezone='Asia/Tehran',preflight=dict(enabled=False),schedules=[dict(
            id=task,task=task,enabled=True,recipient_capability=CAPABILITY,timezone='Asia/Tehran',
            misfire=dict(policy='replay',horizon_days=7),trigger=dict(type='cron',hour=15,minute=0),params={})])
        self.config.write_text(yaml.safe_dump(config),'utf8');job=load_config(self.config)[2][0]
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('397185913','514458396'))
        store.identity_label.side_effect=lambda u:u
        calls=[];online=False
        def deliver(recipient,params,**kwargs):
            calls.append(params['date'])
            if not online:return dict(status='failed',reason='source_stale')
            for user in ('397185913','514458396'):kwargs['on_receipt'](user,'sent','test-'+user)
            return dict(status='succeeded',sent_count=2)
        registry=dict(TASKS);registry[task]=(lambda p:None,deliver)
        state=self.directory/'fresh-sync.json'
        now=self.old+timedelta(minutes=2)
        state.write_text(json.dumps(dict(outage=False,last_run=dict(source_enumerated=True,finished_at=now.isoformat(),
            maintenance_guard=dict(status='imported',hash='accepted-new-version')))),'utf8')
        with patch('tools.scheduler.runner._delivery_notifier'),patch('tools.scheduler.preflight.run_preflights'),\
             patch('tools.scheduler.completion_reminder.run_reminders'),patch.object(prepared,'SYNC_STATE',state):
            tick(self.config,self.state,self.old,registry,store)
            row=self.conn.execute('SELECT * FROM runs').fetchone()
            self.assertEqual(row['data_state'],'source_stale')
            added=hold_backlog(self.conn,[job],self.old+timedelta(seconds=38),store)
            self.assertFalse(any(r['due']==due_storage_key(self.old) for r in added))
            with self.conn:self.conn.execute('UPDATE runs SET next_attempt_at=?',(due_storage_key(now+timedelta(minutes=15)),))
            online=True
            tick(self.config,self.state,now,registry,store)
        self.assertEqual(calls,[overflow_report_date(self.old)]*2)
        self.assertEqual(self.conn.execute('SELECT status FROM runs').fetchone()[0],'succeeded')


if __name__=='__main__':unittest.main()
