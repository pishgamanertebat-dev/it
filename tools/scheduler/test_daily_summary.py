"""Ledger snapshots and fake management transport; no operational Excel access."""
from datetime import timedelta
import json
import unittest
from .runner import connect
from .daily_summary import ensure_schema,scan_daily_summary,snapshot,format_messages
from . import test_delivery_alert as harness
from .tasks import TransportUncertain


class SummaryTests(unittest.TestCase):
    setUp=harness.AlertTests.setUp
    job=harness.AlertTests.job
    sender=harness.AlertTests.sender
    add_run=harness.AlertTests.add_run
    add_receipt=harness.AlertTests.add_receipt
    set_run=harness.AlertTests.set_run
    set_receipt=harness.AlertTests.set_receipt
    arm=harness.AlertTests.arm

    def scan(self,minute=450,days=0,factory=None):
        c=connect(self.state)
        try:scan_daily_summary(c,[self.job()],self.moment+timedelta(minutes=minute,days=days),self.store,sender_factory=factory or self.sender)
        finally:c.close()

    def missing(self):
        self.arm();self.add_run('waiting_for_data','date_missing',data_state='missing',notice='suppressed')
        for u in self.store.recipients:self.add_receipt(u,'report','pending')

    def test_1730_tehran_snapshot_once_across_restarts_and_each_next_day(self):
        self.missing();self.scan(449);self.assertEqual(self.sent,[])
        self.scan();self.scan(451);self.scan(900)
        self.assertEqual(len(self.sent),1);self.assertIn(self.target,self.sent[0][1])
        self.scan(days=1);self.assertEqual(len(self.sent),2)

    def test_retry_uses_frozen_snapshot_and_leaves_real_receipts_unchanged(self):
        self.missing();self.fail_send=True;self.scan()
        c=connect(self.state);before=tuple(c.execute('SELECT * FROM manager_daily_snapshot').fetchone());receipts=[tuple(r) for r in c.execute('SELECT * FROM receipts')];c.close()
        self.set_run('failed','pdf_validation');self.fail_send=False;self.scan(451)
        c=connect(self.state)
        try:
            self.assertEqual(tuple(c.execute('SELECT * FROM manager_daily_snapshot').fetchone()),before)
            self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM receipts')],receipts)
        finally:c.close()
        self.assertIn('منتظر: 1',self.sent[0][1])

    def test_unresolved_older_than_seven_days_survives_and_correction_is_visible(self):
        self.missing();c=connect(self.state);ensure_schema(c)
        with c:
            old=(self.moment-timedelta(days=20)).isoformat()
            c.execute('UPDATE runs SET due=?,status=?,error=?',(old,'no_data_final','missing_date_expired'))
            c.execute('UPDATE receipts SET due=?',(old,))
            c.execute('INSERT INTO report_correction_review VALUES (?,?,?,?,?,?,?,?)',
                (self.job()['id'],old,'A','metalwork','needed','PDF content','{}',old))
        data=snapshot(c,[self.job()],self.moment,self.store);c.close()
        self.assertEqual(data['counts']['closed'],1);self.assertEqual(len(data['unresolved']),1)
        self.assertEqual(data['unresolved'][0]['corrections'][0]['status'],'needed')
        self.assertIn('اصلاحیه','\n'.join(format_messages(data)))

    def test_sent_parent_without_both_office_artifacts_is_not_complete(self):
        self.missing();self.set_run('succeeded')
        for u in self.store.recipients:self.set_receipt(u,'sent','123')
        job=dict(self.job(),task='driver_daily',recipient_capability='reports.driver_daily.daily_receive')
        c=connect(self.state);ensure_schema(c)
        data=snapshot(c,[job],self.moment,self.store);c.close()
        self.assertEqual(data['counts']['complete'],0);self.assertEqual(data['counts']['unverified'],1)

    def test_owner_hold_and_missing_historical_proof_are_distinct_from_technical_error(self):
        self.missing();c=connect(self.state);ensure_schema(c)
        self.addCleanup(c.close)
        with c:c.execute('INSERT INTO report_recovery_hold VALUES (?,?,?,?,?,?,?,?)',
            (self.job()['id'],self.due,'held','owner_review',self.due,self.due,'{}',None))
        data=snapshot(c,[self.job()],self.moment,self.store)
        self.assertEqual(data['counts']['review'],1);self.assertEqual(data['counts']['error'],0)
        with c:
            c.execute('DELETE FROM report_recovery_hold')
            c.execute("UPDATE runs SET status='succeeded',error=NULL")
        data=snapshot(c,[self.job()],self.moment,self.store)
        self.assertEqual(data['counts']['unverified'],1);self.assertEqual(data['counts']['error'],0)

    def test_uncertain_or_interrupted_summary_is_not_resent(self):
        self.missing()
        class Sender:
            def message(self,*args):raise TransportUncertain()
            def close(self):pass
        self.scan(factory=Sender);self.scan(451);self.assertEqual(self.sent,[])
        c=connect(self.state)
        with c:c.execute("UPDATE manager_daily_receipt SET status='sending'")
        c.close();self.scan(454)
        c=connect(self.state)
        try:self.assertEqual(c.execute('SELECT status FROM manager_daily_receipt').fetchone()[0],'uncertain')
        finally:c.close()
        self.assertEqual(self.sent,[])

    def test_every_large_summary_part_has_stable_independent_receipt(self):
        self.missing();c=connect(self.state)
        with c:
            for i in range(1,40):
                due=(self.moment-timedelta(days=i)).isoformat()
                c.execute('INSERT INTO runs SELECT schedule_id,?,recipient,task,status,started,finished,error,attempt,next_attempt_at,data_state,notice_delivery_state,error_detail FROM runs LIMIT 1',(due,))
        c.close();self.scan();sent=len(self.sent);self.assertGreater(sent,1)
        self.assertTrue(all(len(t)<3500 for _,t in self.sent));self.scan(451)
        self.assertEqual(len(self.sent),sent)

if __name__=='__main__':unittest.main()
