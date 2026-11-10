"""Manager obligations are independent of recipient notices and retain old receipts."""
import unittest
from .delivery_alert import scan_delivery_exceptions
from .runner import connect
from .tasks import TransportUncertain
from . import test_delivery_alert as harness


class SourceNoticeTests(unittest.TestCase):
    setUp=harness.AlertTests.setUp
    job=harness.AlertTests.job
    after=harness.AlertTests.after
    sender=harness.AlertTests.sender
    add_run=harness.AlertTests.add_run
    add_receipt=harness.AlertTests.add_receipt
    set_receipt=harness.AlertTests.set_receipt
    set_run=harness.AlertTests.set_run
    arm=harness.AlertTests.arm
    snapshot=harness.AlertTests.snapshot

    def scan(self,minute=0,factory=None):
        c=connect(self.state)
        try:scan_delivery_exceptions(c,[self.job()],self.after(minute),self.store,sender_factory=factory or self.sender)
        finally:c.close()

    def missing(self,notice='suppressed'):
        self.arm();self.add_run('waiting_for_data','date_missing',data_state='missing',notice=notice)
        for user in self.store.recipients:self.add_receipt(user,'report','pending')

    def test_suppressed_missing_alert_has_grace_and_is_restart_idempotent(self):
        self.missing();before=self.snapshot()
        self.scan();self.scan(14);self.assertEqual(self.sent,[])
        self.scan(15);self.scan(30);self.scan(60)
        self.assertEqual(len(self.sent),1)
        self.assertIn('در انتظار تکمیل',self.sent[0][1]);self.assertEqual(self.snapshot(),before)

    def test_notice_sent_is_not_report_success_or_recovery(self):
        self.missing('sent')
        for user in self.store.recipients:self.add_receipt(user,'notice','sent','123')
        self.scan();self.scan(15);self.scan(30)
        self.assertEqual(len(self.sent),1);self.assertFalse(self.sent[0][1].startswith('✅'))

    def test_new_technical_failure_after_missing_starts_own_grace_and_case(self):
        self.missing('sent');self.scan();self.scan(15)
        self.set_run('retry_wait','PermissionError',data_state='missing')
        self.scan(16);self.scan(30);self.assertEqual(len(self.sent),1)
        self.scan(31);self.scan(32);self.assertEqual(len(self.sent),2)
        self.assertNotIn('در انتظار تکمیل داده',self.sent[-1][1])
        c=connect(self.state)
        try:self.assertEqual(c.execute('SELECT count(DISTINCT fingerprint) FROM delivery_alert_case').fetchone()[0],2)
        finally:c.close()

    def test_deadline_has_independent_immediate_final_alert_once(self):
        self.missing();self.scan();self.scan(15)
        self.set_run('no_data_final','missing_date_expired',data_state='missing')
        self.scan(16);self.scan(17)
        self.assertEqual(len(self.sent),2);self.assertIn('بدون ارسال بسته شد',self.sent[-1][1])
        self.assertNotIn('تلاش مجدد ادامه دارد',self.sent[-1][1])

    def test_previous_source_alert_receipts_adopted_without_sending_again(self):
        self.missing('sent')
        c=connect(self.state)
        with c:
            for user in self.store.recipients:c.execute('INSERT INTO source_notice_alert_receipt VALUES (?,?,?,?,?,?,?,?)',
                (harness.JOB_ID,self.due,user,'900','sent','124',None,self.due))
        old=list(c.execute('SELECT * FROM source_notice_alert_receipt'));c.close()
        self.scan();self.scan(15);self.assertEqual(self.sent,[])
        for user in self.store.recipients:self.set_receipt(user,'sent','125')
        self.set_run('succeeded');self.scan(16)
        self.assertEqual(len(self.sent),1);self.assertTrue(self.sent[0][1].startswith('✅'))
        c=connect(self.state)
        try:self.assertEqual([tuple(r) for r in c.execute('SELECT * FROM source_notice_alert_receipt')],[tuple(r) for r in old])
        finally:c.close()

    def test_uncertain_manager_send_never_blind_retries(self):
        self.missing();self.scan()
        class Sender:
            def message(self,*args):raise TransportUncertain()
            def close(self):pass
        self.scan(15,Sender);self.scan(16);self.scan(60)
        self.assertEqual(self.sent,[])
        c=connect(self.state)
        try:self.assertEqual(c.execute('SELECT status FROM delivery_alert_receipt').fetchone()[0],'uncertain')
        finally:c.close()

    def test_recovery_only_reaches_operator_with_real_prior_alert_receipt(self):
        self.missing();self.scan();self.scan(15)
        self.store.operators=('900','901')
        for user in self.store.recipients:self.set_receipt(user,'sent','126')
        self.set_run('succeeded');self.scan(16)
        self.assertEqual([u for u,t in self.sent if t.startswith('✅')],['900'])

    def test_failed_manager_connection_retries_without_touching_report_receipts(self):
        self.missing();before=self.snapshot();self.scan()
        self.fail_send=True;self.scan(15)
        self.fail_send=False;self.scan(16);self.scan(17)
        self.assertEqual(len(self.sent),1);self.assertEqual(self.snapshot(),before)

if __name__=='__main__':unittest.main()
