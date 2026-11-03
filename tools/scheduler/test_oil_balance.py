"""Oil balance schedule/authorization/reminders with isolated DB and fake Bale."""
from contextlib import closing
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import yaml

from tools.authorization.oil_balance import CAPABILITY, REMINDER_CAPABILITY, REPORT_USER, REMINDER_USER, migrate
from tools.authorization.store import RecipientSetResolution
from tools.authorization.test_authorization import Fixture
from tools.fleet.oil_balance import delivery
from tools.scheduler.completion_reminder import run_reminders
from tools.scheduler.report_errors import ReportFailure
from tools.scheduler.runner import connect, load_config, tick
from tools.scheduler.tasks import TransportUnavailable, TransportUncertain

TEHRAN = ZoneInfo('Asia/Tehran')
TARGET = '1405/08/11'


def config():
    return dict(version=1, timezone='Asia/Tehran', oil_balance_reminders=dict(hours=[11, 16]),
                schedules=[dict(id='oil_balance_daily_report', task='oil_balance_daily_report', enabled=True,
                recipient_capability=CAPABILITY, timezone='Asia/Tehran', misfire=dict(policy='replay', horizon_days=7),
                trigger=dict(type='cron', hour=15, minute=0, start_date='2026-11-03T00:00:00+03:30'), params={})])


class AuthorizationTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.auth.migrate_admin(self.directory/'backups')
        for user in (REPORT_USER, REMINDER_USER):
            self.sql('INSERT INTO channel_users VALUES (?,?,?,?)', ('bale', user, user, 'approved'))

    def test_receive_only_separate_named_recipients_no_file_or_profile_grants(self):
        migrate(self.auth, self.directory/'backups')
        self.assertEqual(self.auth.resolve_active_recipients(CAPABILITY).recipients, (REPORT_USER,))
        self.assertEqual(self.auth.resolve_active_recipients(REMINDER_CAPABILITY).recipients, (REMINDER_USER,))
        for user in (REPORT_USER, REMINDER_USER):
            self.assertFalse(self.auth.function_scope(user))
        self.assertFalse(self.sql("SELECT 1 FROM auth_role_profiles WHERE role LIKE 'oil_balance_%'"))
        events = self.sql('SELECT * FROM auth_events')
        migrate(self.auth, self.directory/'backups')
        self.assertEqual(self.sql('SELECT * FROM auth_events'), events)

    def test_revoked_registration_or_nonprivate_chat_excluded(self):
        migrate(self.auth, self.directory/'backups')
        self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id=?", (REPORT_USER,))
        self.sql("UPDATE channel_users SET chat_id='999' WHERE user_id=?", (REMINDER_USER,))
        self.assertEqual(self.auth.resolve_active_recipients(CAPABILITY).recipients, ())
        self.assertEqual(self.auth.resolve_active_recipients(REMINDER_CAPABILITY).recipients, ())

    def test_unapproved_identity_rolls_back_extension(self):
        self.sql("UPDATE channel_users SET registration_status='pending_approval' WHERE user_id=?", (REMINDER_USER,))
        with self.assertRaises(ValueError):
            migrate(self.auth, self.directory/'backups')
        self.assertEqual(self.auth.resolve_active_recipients(CAPABILITY).status, 'no_active_holder')

    def test_conflicting_named_report_assignment_is_refused(self):
        migrate(self.auth, self.directory/'backups')
        self.auth.assign_roles([('101', 'oil_balance_daily_recipient')], actor='fixture')
        with self.assertRaises(ValueError):
            migrate(self.auth, self.directory/'backups')


class OilSchedulerTests(unittest.TestCase):
    def setUp(self):
        root = Path('runtime/oil-balance-tests')
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.state = self.directory/'state.sqlite3'
        self.config = self.directory/'schedules.yaml'
        self.config.write_text(yaml.safe_dump(config()), encoding='utf-8')
        self.job = load_config(self.config)[2][0]
        self.now = datetime(2026, 11, 3, 15, tzinfo=TEHRAN)
        self.revoked = set()
        self.store = Mock()
        def resolve(capability):
            users = ((REPORT_USER,) if capability == CAPABILITY else
                     (REMINDER_USER,) if capability == REMINDER_CAPABILITY else ())
            users = tuple(u for u in users if u not in self.revoked)
            return RecipientSetResolution('ready' if users else 'no_active_holder', users)
        self.store.resolve_active_recipients.side_effect = resolve
        self.store.identity_label.side_effect = lambda u: u
        self.sender = Mock()
        self.sender.document.return_value = 'pdf-42'
        self.sender.message.return_value = 'reminder-7'

    def fake_build(self, target, directory):
        self.assertEqual(target, TARGET)
        path = Path(directory)/'selected.pdf'
        path.write_bytes(b'selected-pdf-only')
        return {'status': 'ready', 'pdf': str(path), 'report': dict(date=target, source_sha256='fixture', rows_matched=2)}

    def tick(self, hour=15, minute=0, build=None, source=None):
        with patch.object(delivery, 'build', side_effect=build or self.fake_build), \
             patch.object(delivery, 'validate_pdf', return_value={'pages': 1, 'pdf_sha256': 'fixture'}), \
             patch.object(delivery, 'verify_source_digest'), \
             patch('tools.fleet.oil_balance.reminder.probe', return_value=source or {'status':'ready'}), \
             patch('tools.scheduler.tasks.BaleSender', return_value=self.sender):
            return tick(self.config, self.state, self.now.replace(hour=hour, minute=minute), authorization=self.store)

    def receipts(self):
        with closing(sqlite3.connect(self.state)) as conn:
            return conn.execute('SELECT recipient_id,status,message_id FROM receipts').fetchall()

    def reminders(self, hour, result=None, *, minute=0, checker=None, factory=None):
        with closing(connect(self.state)) as conn:
            run_reminders(conn, [self.job], self.now.replace(hour=hour, minute=minute), self.store,
                          checker=checker or Mock(return_value=result or {'status': 'date_missing'}),
                          sender_factory=factory or (lambda: self.sender))

    def test_no_pdf_before_fifteen_exact_yesterday_and_no_duplicate_across_ticks(self):
        self.tick(14, 59)
        self.sender.document.assert_not_called()
        self.tick(15)
        self.tick(15, 1)
        self.sender.document.assert_called_once()
        args = self.sender.document.call_args.args
        self.assertEqual(args[0], REPORT_USER)
        self.assertIn(TARGET, args[2])
        self.assertTrue(str(args[1]).endswith('.pdf'))
        self.assertEqual(self.receipts(), [(REPORT_USER, 'sent', 'pdf-42')])
        with closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute('SELECT due,status FROM runs').fetchall(), [('2026-11-03T11:30:00+00:00', 'succeeded')])

    def test_missing_target_waits_and_late_update_delivers_original_day(self):
        def missing(target, directory):
            return {'status': 'waiting_for_data', 'report': {'available_dates': ['1405/08/10']}}
        self.tick(build=missing)
        self.sender.document.assert_not_called()
        self.sender.message.assert_not_called()
        self.tick(16, build=missing, source={'status':'date_missing'})
        self.sender.document.assert_not_called()
        self.assertIn('ردیف تعویض روغن', self.sender.message.call_args.args[1])
        self.tick(16, 2)
        self.tick(16, 3)
        self.sender.document.assert_called_once()
        self.assertEqual(self.receipts(), [(REPORT_USER, 'sent', 'pdf-42')])

    def test_transport_disconnect_retries_only_after_backoff(self):
        self.sender.check_connection.side_effect = TransportUnavailable('offline')
        self.tick()
        self.sender.document.assert_not_called()
        self.sender.check_connection.side_effect = None
        self.tick(15, 2)
        self.sender.document.assert_called_once()

    def test_uncertain_upload_is_never_sent_again(self):
        self.sender.document.side_effect = TransportUncertain('unknown')
        self.tick()
        self.tick(15, 10)
        self.sender.document.assert_called_once()
        self.assertEqual(self.receipts(), [(REPORT_USER, 'uncertain', None)])

    def test_missing_message_id_is_uncertain(self):
        self.sender.document.return_value = None
        self.tick()
        self.tick(15, 2)
        self.sender.document.assert_called_once()
        self.assertEqual(self.receipts(), [(REPORT_USER, 'uncertain', None)])

    def test_source_changed_and_role_revoked_stop_before_upload(self):
        for revoke in (True, False):
            with self.subTest(revoke=revoke):
                self.sender.reset_mock()
                self.revoked.clear()
                if revoke:
                    self.sender.check_connection.side_effect = lambda: self.revoked.add(REPORT_USER)
                    digest = None
                else:
                    self.sender.check_connection.side_effect = None
                    digest = ReportFailure('source_changed', stage='source')
                with patch.object(delivery, 'build', side_effect=self.fake_build), \
                     patch.object(delivery, 'validate_pdf', return_value={'pages':1,'pdf_sha256':'fixture'}), \
                     patch.object(delivery, 'verify_source_digest', side_effect=digest), \
                     patch('tools.scheduler.tasks.BaleSender', return_value=self.sender):
                    if revoke:
                        delivery.deliver('capability:'+CAPABILITY, {'date':TARGET}, authorization=self.store)
                    else:
                        with self.assertRaises(ReportFailure):
                            delivery.deliver('capability:'+CAPABILITY, {'date':TARGET}, authorization=self.store)
                self.sender.document.assert_not_called()

    def test_exact_hadayati_slots_independent_and_once(self):
        for hour, minute in [(10,59), (11,0), (11,1), (12,0), (15,0), (15,1), (16,0), (16,1), (17,0)]:
            self.reminders(hour, minute=minute)
        calls = self.sender.message.call_args_list
        self.assertEqual([call.args[0] for call in calls], [REMINDER_USER, REMINDER_USER])
        self.assertIn('۱۵:۰۰', calls[0].args[1])
        self.assertNotIn('۱۲ ظهر', calls[0].args[1])
        self.assertIn('به آقای امامی ارسال نشده', calls[1].args[1])
        self.assertTrue(all(TARGET in call.args[1] for call in calls))

    def test_prepared_source_is_quiet_before_due_but_requires_receipt_at_sixteen(self):
        self.reminders(11, {'status':'ready'})
        self.sender.message.assert_not_called()
        self.reminders(16, {'status':'ready'})
        self.sender.message.assert_called_once()
        message = self.sender.message.call_args.args[1]
        self.assertIn('فایل آماده است', message)
        self.assertIn('در صف پیگیری ارسال خودکار', message)
        self.assertNotIn('فایل را تکمیل', message)

    def test_confirmed_pdf_suppresses_afternoon_warning(self):
        self.tick()
        checker = Mock(side_effect=AssertionError('Receipt avoids source read'))
        self.reminders(16, checker=checker)
        checker.assert_not_called()
        self.sender.message.assert_not_called()

    def test_missing_sections_or_locked_file_send_actual_error_not_false_missing_date(self):
        for code in ('source_section_missing', 'source_locked', 'source_formula_unverified'):
            # A distinct fresh slot/database for each error case.
            self.state = self.directory/(code+'.sqlite3')
            self.sender.reset_mock()
            self.reminders(11, checker=Mock(side_effect=ReportFailure(code, stage='source')))
            self.sender.message.assert_called_once()
            message = self.sender.message.call_args.args[1]
            self.assertIn('فایل قابل استفاده نیست', message)
            self.assertNotIn('ردیف تعویض روغن برای این تاریخ', message)

    def test_reminder_revocation_and_uncertain_do_not_retry(self):
        self.sender.message.side_effect = TransportUncertain('unknown')
        self.reminders(11)
        self.reminders(11, minute=5)
        self.sender.message.assert_called_once()
        self.revoked.add(REMINDER_USER)
        self.reminders(16)
        self.sender.message.assert_called_once()

    def test_afternoon_warning_distinguishes_uncertain_report_receipt(self):
        with closing(connect(self.state)) as conn, conn:
            conn.execute("INSERT INTO receipts VALUES (?,?,?,'report','uncertain',NULL,?)",
                         (self.job['id'], '2026-11-03T08:30:00+00:00', REPORT_USER, self.now.isoformat()))
        self.reminders(16)
        message = self.sender.message.call_args.args[1]
        self.assertIn('تأیید نشده', message)
        self.assertNotIn('هنوز ارسال نشده', message)
        self.assertIn('ارسال مجدد خودکار متوقف است', message)

    def test_reminder_registration_rechecked_after_connection(self):
        self.sender.check_connection.side_effect = lambda: self.revoked.add(REMINDER_USER)
        self.reminders(11)
        self.sender.message.assert_not_called()

    def test_old_noon_delivery_remains_closed_after_clock_change(self):
        old_due = '2026-11-03T08:30:00+00:00'
        with closing(connect(self.state)) as conn, conn:
            conn.execute("""INSERT INTO runs(schedule_id,due,recipient,task,status,started,finished)
                VALUES (?,?,?,?,'succeeded',?,?)""", (self.job['id'], old_due, 'capability:'+CAPABILITY,
                self.job['task'], old_due, old_due))
            conn.execute("INSERT INTO receipts VALUES (?,?,?,'report','sent','noon-receipt',?)",
                         (self.job['id'], old_due, REPORT_USER, old_due))
        self.tick(15)
        self.tick(16)
        self.sender.document.assert_not_called()
        self.sender.message.assert_not_called()
        self.assertEqual(self.receipts(), [(REPORT_USER,'sent','noon-receipt')])
        with closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute('SELECT due FROM runs').fetchall(), [(old_due,)])

    def test_old_noon_pending_run_keeps_original_due_for_late_delivery(self):
        old_due = '2026-11-03T08:30:00+00:00'
        with closing(connect(self.state)) as conn, conn:
            conn.execute("""INSERT INTO runs(schedule_id,due,recipient,task,status,started,error,data_state)
                VALUES (?,?,?,?,'waiting_for_data',?,'date_missing','missing')""",
                (self.job['id'],old_due,'capability:'+CAPABILITY,self.job['task'],old_due))
            conn.execute("INSERT INTO receipts VALUES (?,?,?,'report','pending',NULL,?)",
                         (self.job['id'],old_due,REPORT_USER,old_due))
        self.tick(15)
        self.tick(16)
        self.sender.document.assert_called_once()
        self.sender.message.assert_not_called()
        with closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute('SELECT due,status FROM runs').fetchall(), [(old_due,'succeeded')])

    def test_ready_source_with_failed_delivery_warns_at_sixteen_then_sends_from_queue(self):
        self.sender.check_connection.side_effect = TransportUnavailable('offline')
        self.tick()
        self.sender.document.assert_not_called()
        self.sender.check_connection.side_effect = None
        self.reminders(16, {'status':'ready'})
        self.assertIn('در صف پیگیری ارسال خودکار', self.sender.message.call_args.args[1])
        self.tick(16, 2)
        self.tick(16, 3)
        self.sender.document.assert_called_once()
        self.sender.message.assert_called_once()

    def test_invalid_schedules_cannot_change_recipient_time_or_disable_receipts(self):
        for change in ({'recipient':'455740857'}, {'recipient_capability':REMINDER_CAPABILITY},
                       {'trigger':dict(type='cron',hour=15,minute=5)}, {'misfire':dict(policy='latest_only')},
                       {'params':dict(date=TARGET)}):
            raw=config()
            raw['schedules'][0].update(change)
            self.config.write_text(yaml.safe_dump(raw), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(self.config)

    def test_tehran_previous_day_month_and_year_boundaries(self):
        from tools.scheduler.tasks import overflow_report_date
        self.assertEqual(overflow_report_date(datetime(2026,11,3,0,30,tzinfo=TEHRAN)), TARGET)
        self.assertEqual(overflow_report_date(datetime(2026,10,23,12,tzinfo=TEHRAN)), '1405/07/30')
        self.assertEqual(overflow_report_date(datetime(2026,3,21,12,tzinfo=TEHRAN)), '1404/12/29')


if __name__ == '__main__':
    unittest.main()
