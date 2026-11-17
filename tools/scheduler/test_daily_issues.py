"""Issue-only delivery with isolated ledgers and fake Bale; no operational Excel."""
from datetime import datetime, timedelta, timezone
import hashlib
import unittest

from . import test_delivery_alert as harness
from .daily_issues import collect_issues, ensure_schema, format_message, scan_daily_issues
from .runner import connect
from .tasks import TransportUncertain


class DailyIssueTests(unittest.TestCase):
    setUp = harness.AlertTests.setUp
    job = harness.AlertTests.job
    sender = harness.AlertTests.sender
    add_run = harness.AlertTests.add_run
    add_receipt = harness.AlertTests.add_receipt
    set_run = harness.AlertTests.set_run
    set_receipt = harness.AlertTests.set_receipt
    arm = harness.AlertTests.arm

    def scan(self, minute=540, days=0, factory=None):
        conn = connect(self.state)
        try:
            scan_daily_issues(conn, [self.job()], self.moment + timedelta(minutes=minute, days=days),
                              self.store, sender_factory=factory or self.sender)
        finally:
            conn.close()

    def missing(self):
        self.arm()
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', notice='suppressed')
        for user in self.store.recipients:
            self.add_receipt(user, 'report', 'pending')

    def issues(self, jobs=None, now=None):
        conn = connect(self.state)
        try:
            ensure_schema(conn)
            return collect_issues(conn, jobs or [self.job()], now or self.moment, self.store)
        finally:
            conn.close()

    def test_only_at_1900_tehran_once_per_day_across_restarts(self):
        self.missing()
        for minute in (450, 539):  # Former 17:30 and one minute before 19:00.
            self.scan(minute)
        self.assertEqual(self.factory_calls, 0)
        self.scan()
        self.scan(541)
        self.scan(800)
        self.assertEqual(self.sent, [('900', f'گزارش روزانه رانندگان2.xlsx | {self.target}: داده این تاریخ کامل نیست')])
        self.scan(days=1)
        self.assertEqual(len(self.sent), 2)

    def test_utc_clock_obeys_tehran_boundary(self):
        self.missing()
        conn = connect(self.state)
        self.addCleanup(conn.close)
        for minute in (29, 30):
            moment = datetime(2026, 10, 7, 15, minute, tzinfo=timezone.utc)
            scan_daily_issues(conn, [self.job()], moment, self.store, sender_factory=self.sender)
            self.assertEqual(len(self.sent), minute - 29)

    def test_no_issue_sends_nothing_including_no_all_clear_text(self):
        self.arm()
        self.add_run('succeeded')
        for user in self.store.recipients:
            self.add_receipt(user, 'report', 'sent', '123')
        self.scan()
        self.scan(541)
        self.assertEqual(self.factory_calls, 0)
        self.assertEqual(self.issues(), [])

    def test_closed_disabled_and_future_runs_are_absent(self):
        self.missing()
        self.set_run('no_data_final', 'missing_date_expired')
        self.assertEqual(self.issues(), [])
        self.set_run('failed', 'pdf_export')
        self.assertEqual(self.issues([dict(self.job(), enabled=False)]), [])
        self.assertEqual(self.issues(now=self.moment - timedelta(seconds=1)), [])

    def test_owner_closed_dates_disappear_without_changing_any_history(self):
        self.missing()
        conn = connect(self.state)
        self.addCleanup(conn.close)
        ensure_schema(conn)
        for due in ('2026-11-07T11:30:00+00:00', '2026-11-08T11:30:00+00:00'):
            with conn:
                conn.execute("UPDATE runs SET due=?,status='failed',error='source_invalid'", (due,))
                conn.execute('UPDATE receipts SET due=?', (due,))
            tables = ('runs', 'receipts', 'office_artifact_receipts', 'report_recovery_hold')
            before = {name: [tuple(r) for r in conn.execute('SELECT * FROM ' + name)] for name in tables}
            self.assertEqual(collect_issues(conn, [self.job()], datetime(2026, 11, 10, tzinfo=timezone.utc), self.store), [])
            self.assertEqual(before, {name: [tuple(r) for r in conn.execute('SELECT * FROM ' + name)] for name in tables})

    def test_deduplicates_jobs_and_recipients_and_keeps_source_cell(self):
        self.missing()
        conn = connect(self.state)
        with conn:
            conn.execute('''UPDATE runs SET status='failed',error='source_driver_type_missing',
                error_detail='{"sheet":"8","row":28,"column":1}' ''')
            conn.execute('INSERT INTO runs SELECT ?,due,recipient,task,status,started,finished,error,attempt,next_attempt_at,data_state,notice_delivery_state,error_detail FROM runs', ('second_driver_job',))
        conn.close()
        issues = self.issues([self.job(), dict(self.job(), id='second_driver_job')])
        self.assertEqual(len(issues), 1)
        message = format_message(issues)
        self.assertEqual(len(message.splitlines()), 1)
        self.assertIn('گزارش روزانه رانندگان2.xlsx', message)
        self.assertIn('سلول A28', message)
        self.assertIn('ستون نوع دستگاه', message)
        self.assertNotIn('گیرنده', message)

    def test_many_old_open_dates_are_compacted_into_one_line_not_multipart(self):
        self.missing()
        conn = connect(self.state)
        with conn:
            for i in range(1, 40):
                due = (self.moment - timedelta(days=i)).isoformat()
                conn.execute('INSERT INTO runs SELECT schedule_id,?,recipient,task,status,started,finished,error,attempt,next_attempt_at,data_state,notice_delivery_state,error_detail FROM runs LIMIT 1', (due,))
        conn.close()
        self.scan()
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.sent[0][1].splitlines()), 1)
        self.assertLess(len(self.sent[0][1]), 180)
        self.assertIn(' تا ', self.sent[0][1])

    def test_date_ranges_preserve_gaps_and_month_boundaries(self):
        text = format_message([('file.xlsx', date, 'ایراد') for date in
                               ('1405/06/30', '1405/06/31', '1405/07/01', '1405/07/03')])
        self.assertEqual(text, 'file.xlsx | 1405/06/30 تا 1405/07/01، 1405/07/03: ایراد')

    def test_incomplete_office_artifacts_remain_an_issue_despite_parent_sent(self):
        self.missing()
        self.set_run('succeeded')
        for user in self.store.recipients:
            self.set_receipt(user, 'sent', '123')
        job = dict(self.job(), task='driver_daily', recipient_capability='reports.driver_daily.daily_receive')
        self.assertIn('رسید کامل ارسال موجود نیست', format_message(self.issues([job])))

    def test_open_correction_visible_resolved_corrections_absent(self):
        self.missing()
        self.set_run('succeeded')
        for user in self.store.recipients:
            self.set_receipt(user, 'sent', '123')
        conn = connect(self.state)
        ensure_schema(conn)
        with conn:
            conn.execute('INSERT INTO report_correction_review VALUES (?,?,?,?,?,?,?,?)',
                         (self.job()['id'], self.due, 'A', 'metalwork', 'needs_content_review', 'private detail', '{}', self.due))
        conn.close()
        self.assertEqual(self.issues(), [('گزارش روزانه رانندگان2.xlsx', self.target, 'محتوای PDF آهنگری نیازمند بررسی')])
        conn = connect(self.state)
        with conn:
            conn.execute("UPDATE report_correction_review SET status='resolved'")
        conn.close()
        self.assertEqual(self.issues(), [])

    def test_held_history_is_absent_without_replaying_or_creating_recipients(self):
        self.missing()
        conn = connect(self.state)
        with conn:
            old = (self.moment - timedelta(days=20)).isoformat()
            conn.execute('UPDATE runs SET due=?,status=?,error=NULL', (old, 'succeeded'))
            conn.execute('DELETE FROM receipts')
            conn.execute('INSERT INTO report_recovery_hold VALUES (?,?,?,?,?,?,?,?)',
                         (self.job()['id'], old, 'held', 'owner_review', old, old, '{}', None))
        conn.close()
        self.assertEqual(self.issues(), [])
        conn = connect(self.state)
        self.assertEqual(conn.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)
        conn.close()

    def test_uncertain_delivery_is_not_misreported_as_a_source_error(self):
        self.missing()
        self.set_run('uncertain', 'source_invalid')
        self.assertEqual(self.issues()[0][2], 'نتیجه ارسال در بله نامشخص است')

    def test_failed_send_retries_same_message_and_keeps_report_receipts_unchanged(self):
        self.missing()
        self.fail_send = True
        self.scan()
        conn = connect(self.state)
        before = tuple(conn.execute('SELECT * FROM manager_daily_issues').fetchone())
        receipts = [tuple(r) for r in conn.execute('SELECT * FROM receipts')]
        conn.close()
        self.set_run('failed', 'pdf_export')
        self.fail_send = False
        self.scan(541)
        conn = connect(self.state)
        self.assertEqual(tuple(conn.execute('SELECT * FROM manager_daily_issues').fetchone()), before)
        self.assertEqual([tuple(r) for r in conn.execute('SELECT * FROM receipts')], receipts)
        conn.close()
        self.assertIn('داده این تاریخ کامل نیست', self.sent[0][1])

    def test_uncertain_or_interrupted_issue_message_is_never_resent(self):
        self.missing()
        class Sender:
            def message(self, *args):
                raise TransportUncertain()
            def close(self):
                pass
        self.scan(factory=Sender)
        self.scan(541)
        conn = connect(self.state)
        with conn:
            conn.execute("UPDATE manager_daily_issue_receipt SET status='sending'")
        conn.close()
        self.scan(544)
        conn = connect(self.state)
        self.assertEqual(conn.execute('SELECT status FROM manager_daily_issue_receipt').fetchone()[0], 'uncertain')
        conn.close()
        self.assertEqual(self.factory_calls, 0)

    def legacy(self, status):
        conn = connect(self.state)
        with conn:
            conn.execute('CREATE TABLE manager_daily_receipt (day,part,operator_id,status,message_id,error,updated)')
            conn.execute('INSERT INTO manager_daily_receipt VALUES (?,?,?,?,?,?,?)',
                         ('2026-10-07', 0, '900', status, '123', None, self.due))
            # A saved old four-page message must never be retried by the replacement.
            conn.execute('CREATE TABLE manager_daily_snapshot (day,messages)')
            conn.execute('INSERT INTO manager_daily_snapshot VALUES (?,?)', ('2026-10-07', 'LONG OLD TEXT'))
        conn.close()

    def test_legacy_delivered_or_ambiguous_day_prevents_second_daily_message(self):
        for state in ('sent', 'uncertain', 'sending'):
            with self.subTest(state=state):
                self.setUp()
                self.missing()
                self.legacy(state)
                self.scan()
                self.assertEqual(self.factory_calls, 0)

    def test_unsent_legacy_text_is_retired_and_only_short_replacement_sends(self):
        self.missing()
        self.legacy('failed')
        self.scan()
        self.assertEqual(len(self.sent), 1)
        self.assertNotIn('LONG OLD TEXT', self.sent[0][1])
        conn = connect(self.state)
        self.assertEqual(conn.execute('SELECT messages FROM manager_daily_snapshot').fetchone()[0], 'LONG OLD TEXT')
        self.assertEqual(conn.execute('SELECT status FROM manager_daily_receipt').fetchone()[0], 'failed')
        conn.close()

    def test_tampered_digest_cannot_send(self):
        self.missing()
        conn = connect(self.state)
        ensure_schema(conn)
        with conn:
            conn.execute('INSERT INTO manager_daily_issues VALUES (?,?,?,?)',
                         ('2026-10-07', self.due, hashlib.sha256(b'original').hexdigest(), 'tampered'))
        conn.close()
        with self.assertRaisesRegex(RuntimeError, 'integrity'):
            self.scan()
        self.assertEqual(self.factory_calls, 0)


if __name__ == '__main__':
    unittest.main()
