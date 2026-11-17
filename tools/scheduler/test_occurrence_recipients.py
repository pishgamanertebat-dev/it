"""Current subscribers must never become recipients of an old occurrence."""
from datetime import timedelta
import unittest
from . import test_delivery_alert as harness
from .runner import connect
from .delivery_alert import occurrence_recipients,_delivery_summary
from .daily_issues import ensure_schema,collect_issues


class OccurrenceRecipientTests(unittest.TestCase):
    setUp=harness.AlertTests.setUp
    job=harness.AlertTests.job
    add_run=harness.AlertTests.add_run
    add_receipt=harness.AlertTests.add_receipt
    arm=harness.AlertTests.arm
    def test_archived_receipt_limits_old_recipients_despite_new_subscriber(self):
        self.arm();self.add_run('succeeded');self.add_receipt('A','report','sent','123')
        c=connect(self.state);self.addCleanup(c.close)
        run=c.execute('SELECT * FROM runs').fetchone()
        self.assertEqual(occurrence_recipients(c,self.job(),run,self.store,self.moment+timedelta(days=2)),('A',))
        ensure_schema(c);issues=collect_issues(c,[self.job()],self.moment+timedelta(days=2),self.store)
        self.assertEqual(issues,[])
    def test_old_numeric_recipient_is_preserved_when_no_receipt_exists(self):
        self.arm();self.add_run('succeeded');c=connect(self.state);self.addCleanup(c.close)
        with c:c.execute("UPDATE runs SET recipient='777'")
        run=c.execute('SELECT * FROM runs').fetchone()
        self.assertEqual(occurrence_recipients(c,self.job(),run,self.store,self.moment+timedelta(days=2)),('777',))
    def test_old_role_without_proof_never_uses_current_subscribers(self):
        self.arm();self.add_run('succeeded');c=connect(self.state);self.addCleanup(c.close)
        run=c.execute('SELECT * FROM runs').fetchone()
        self.assertEqual(occurrence_recipients(c,self.job(),run,self.store,self.moment+timedelta(days=2)),())
    def test_legacy_parent_receipts_keep_their_report_identity_and_message_ids(self):
        self.arm();self.add_run('succeeded');self.add_receipt('A','report','sent','123,124')
        c=connect(self.state);self.addCleanup(c.close)
        with c:c.execute("UPDATE runs SET recipient='role:office_supervisor'")
        run=c.execute('SELECT * FROM runs').fetchone()
        job=dict(self.job(),task='driver_daily',recipient_capability='reports.driver_daily.daily_receive')
        rows=_delivery_summary(c,job,run,('A',),self.store)
        self.assertEqual(rows[0]['status'],'sent');self.assertEqual(rows[0]['message_id'],'123,124')

if __name__=='__main__':unittest.main()
