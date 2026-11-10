"""Historical holds never rewrite reports/receipts or block a future occurrence."""
from datetime import timedelta
from unittest.mock import Mock
import json
import unittest
from . import test_machinery_performance as harness
from .recovery_review import hold_backlog
from .misfire import due_storage_key
from .runner import _expire_missing_reports
from tools.authorization.store import RecipientSetResolution


class ReviewTests(unittest.TestCase):
    setUp=harness.Harness.setUp
    rows=harness.Harness.rows
    execute_at=harness.Harness.execute_at

    def store(self):
        store=Mock();store.resolve_active_recipients.return_value=RecipientSetResolution('ready',harness.REPORT_USERS)
        store.identity_label.side_effect=lambda u:u
        return store

    def test_hold_old_ready_report_but_deliver_new_independently(self):
        self.execute_at(self.old)
        hold_backlog(self.conn,[self.job],self.old+timedelta(hours=36),self.store())
        before=self.rows()[due_storage_key(self.old)]
        calls=self.execute_at(self.new+timedelta(hours=13),ready={harness.overflow_report_date(self.old),harness.overflow_report_date(self.new)})
        self.assertIn((harness.overflow_report_date(self.new),self.new.isoformat()),calls)
        self.assertNotIn((harness.overflow_report_date(self.old),self.old.isoformat()),calls)
        self.assertEqual(self.rows()[due_storage_key(self.old)],before)
        self.assertEqual(self.rows()[due_storage_key(self.new)]['status'],'succeeded')

    def test_historical_hold_is_persistent_idempotent_and_not_reclassified_as_missing_expiry(self):
        self.execute_at(self.old);before=self.rows()[due_storage_key(self.old)]
        now=self.old+timedelta(hours=36)
        added=hold_backlog(self.conn,[self.job],now,self.store());self.assertEqual(len(added),1)
        row=dict(self.conn.execute('SELECT * FROM report_recovery_hold').fetchone())
        self.assertEqual(json.loads(row['evidence'])['previous_run'],before)
        self.assertEqual(hold_backlog(self.conn,[self.job],now,self.store()),[])
        self.assertEqual(_expire_missing_reports(self.conn,self.job,self.old+timedelta(hours=40)),set())
        self.assertEqual(self.rows()[due_storage_key(self.old)],before)
        self.assertEqual(dict(self.conn.execute('SELECT * FROM report_recovery_hold').fetchone()),row)

    def test_already_delivered_report_is_not_held(self):
        self.execute_at(self.old,ready={harness.overflow_report_date(self.old)})
        self.assertEqual(hold_backlog(self.conn,[self.job],self.old+timedelta(hours=1),self.store()),[])

    def test_hold_reuses_original_due_after_clock_change(self):
        self.execute_at(self.old)
        from apscheduler.triggers.cron import CronTrigger
        self.job['trigger']=CronTrigger(hour=14,timezone=harness.TEHRAN,start_date=self.old.replace(hour=0))
        hold_backlog(self.conn,[self.job],self.old+timedelta(hours=36),self.store())
        self.assertIn(due_storage_key(self.old),[r[0] for r in self.conn.execute('SELECT due FROM report_recovery_hold')])

    def test_38_seconds_after_due_and_entire_36h_window_are_automatic(self):
        self.execute_at(self.old)
        for delta in (timedelta(seconds=38),timedelta(hours=35,minutes=59)):
            self.assertEqual(hold_backlog(self.conn,[self.job],self.old+delta,self.store()),[])
        self.assertEqual(self.conn.execute('SELECT count(*) FROM report_recovery_hold').fetchone()[0],0)

    def test_technical_failure_keeps_existing_replay_horizon(self):
        self.execute_at(self.old)
        with self.conn:self.conn.execute("UPDATE runs SET status='retry_wait',error='source_stale'")
        hold_backlog(self.conn,[self.job],self.old+timedelta(days=6),self.store())
        self.assertIsNone(self.conn.execute('SELECT 1 FROM report_recovery_hold WHERE due=?',(due_storage_key(self.old),)).fetchone())

if __name__=='__main__':unittest.main()
