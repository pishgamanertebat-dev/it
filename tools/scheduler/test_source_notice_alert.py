"""Missing-day operator alerts use fake transport and an isolated delivery ledger."""
import sqlite3
import unittest

from .delivery_alert import scan_delivery_exceptions
from .misfire import due_storage_key
from .runner import connect
from .tasks import TransportUncertain
from . import test_delivery_alert as harness


class SourceNoticeTests(unittest.TestCase):
    setUp = harness.AlertTests.setUp
    job = harness.AlertTests.job
    after = harness.AlertTests.after
    sender = harness.AlertTests.sender
    add_run = harness.AlertTests.add_run
    add_receipt = harness.AlertTests.add_receipt
    set_receipt = harness.AlertTests.set_receipt
    set_run = harness.AlertTests.set_run
    arm = harness.AlertTests.arm

    def scan(self, moment=None):
        conn = connect(self.state)
        try:
            scan_delivery_exceptions(conn, [self.job()], moment or self.moment,
                                     self.store, sender_factory=self.sender)
        finally:
            conn.close()

    def missing(self):
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', notice='sent')
        for user in self.store.recipients:
            self.add_receipt(user, 'report', 'pending')
            self.add_receipt(user, 'notice', 'sent', '123')

    def states(self):
        conn = sqlite3.connect(self.state)
        try:
            return conn.execute('SELECT recipient_id,operator_id,status FROM source_notice_alert_receipt').fetchall()
        finally:
            conn.close()

    def test_immediate_short_named_alert_once_per_recipient_after_restart(self):
        self.arm(); self.missing(); self.scan()
        self.assertEqual(len(self.sent), 2)
        for user, text in self.sent:
            self.assertEqual(user, '900')
            self.assertIn(self.target, text)
            self.assertIn('گزارش روزانه معایب مکانیکی رانندگان', text)
            self.assertIn('به دلیل به‌روز نبودن اطلاعات', text)
            self.assertLess(len(text), 300)
        self.assertIn('گیرنده الف', self.sent[0][1])
        self.assertNotIn('گیرنده ب', self.sent[0][1])
        self.assertIn('گیرنده ب', self.sent[1][1])
        self.scan(self.after(1)); self.scan(self.after(30))
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all(row[2] == 'sent' for row in self.states()))

    def test_existing_notices_are_baselined_but_later_notices_alert(self):
        self.missing(); self.scan(); self.assertEqual(self.sent, [])
        due = due_storage_key(self.after(24 * 60))
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', notice='sent', due=due)
        self.add_receipt('A', 'notice', 'sent', '124', due=due)
        self.scan(self.after(24 * 60))
        self.assertEqual(len(self.sent), 1)

    def test_later_recipient_notice_adds_only_that_recipient(self):
        self.arm(); self.missing()
        conn = connect(self.state)
        with conn:
            conn.execute("UPDATE receipts SET status='failed' WHERE recipient_id='B' AND delivery_kind='notice'")
        conn.close()
        self.scan(); self.assertEqual(len(self.sent), 1)
        conn = connect(self.state)
        with conn:
            conn.execute("UPDATE receipts SET status='sent' WHERE recipient_id='B' AND delivery_kind='notice'")
        conn.close()
        self.scan(self.after(1)); self.scan(self.after(2))
        self.assertEqual(len(self.sent), 2)

    def test_failed_operator_connection_retries_without_resending_recipient_notices(self):
        self.arm(); self.missing(); self.fail_send = True; self.scan()
        self.assertEqual(self.sent, [])
        self.assertTrue(all(row[2] == 'failed' for row in self.states()))
        self.fail_send = False; self.scan(self.after(1)); self.scan(self.after(2))
        self.assertEqual(len(self.sent), 2)
        conn = connect(self.state)
        self.assertEqual(conn.execute("SELECT count(*) FROM receipts WHERE delivery_kind='notice' AND message_id='123'").fetchone()[0], 2)
        conn.close()

    def test_ambiguous_operator_send_and_interrupted_claim_are_not_duplicated(self):
        self.arm(); self.missing()
        class Sender:
            def message(self, *args): raise TransportUncertain()
            def close(self): pass
        conn = connect(self.state)
        scan_delivery_exceptions(conn, [self.job()], self.moment, self.store, sender_factory=Sender)
        with conn:
            conn.execute("UPDATE source_notice_alert_receipt SET status='sending' WHERE recipient_id='B'")
        conn.close()
        self.scan(self.after(1)); self.scan(self.after(3)); self.scan(self.after(30))
        self.assertEqual(self.sent, [])
        self.assertTrue(all(row[2] == 'uncertain' for row in self.states()))

    def test_sent_report_and_revoked_recipient_are_excluded(self):
        self.arm(); self.missing(); self.set_receipt('A', 'sent', '456')
        self.store.revoked.add('B'); self.scan()
        self.assertEqual(self.sent, [])

    def test_missing_without_successful_recipient_notice_does_not_alert(self):
        self.arm()
        self.add_run('waiting_for_data', 'date_missing', data_state='missing')
        self.add_receipt('A', 'notice', 'uncertain')
        self.scan()
        # The existing transport warning is separate from the new source-data message.
        self.assertTrue(all('به دلیل به‌روز نبودن اطلاعات' not in text for _, text in self.sent))
        self.assertEqual(self.states(), [])

    def test_disabled_job_and_absent_operator_do_not_send(self):
        self.arm(); self.missing(); self.enabled = False; self.scan()
        self.assertEqual(self.sent, [])
        self.enabled = True; self.store.operators = (); self.scan()
        self.assertEqual(self.sent, [])
        self.store.operators = ('900',); self.scan()
        self.assertEqual(len(self.sent), 2)

    def test_existing_transport_alert_still_fires_for_failed_notice(self):
        self.arm(); self.missing()
        conn = connect(self.state)
        with conn:
            conn.execute("UPDATE receipts SET status='failed' WHERE recipient_id='B' AND delivery_kind='notice'")
        conn.close()
        self.scan(); self.scan(self.after(15))
        self.assertEqual(len(self.sent), 2)
        self.assertIn('به دلیل به‌روز نبودن اطلاعات', self.sent[0][1])
        self.assertIn('اعلان نبود اطلاعات به گیرنده نرسید', self.sent[1][1])


if __name__ == '__main__':
    unittest.main()
