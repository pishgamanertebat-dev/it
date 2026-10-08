"""Delivery-failure alerts. Synthetic ledger and fake Bale only."""
from datetime import datetime, timedelta
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
import yaml
from zoneinfo import ZoneInfo

from tools.authorization.delivery_alert import CAPABILITY, RESOURCE, ROLE, migrate
from tools.authorization.store import AuthorizationStore, RecipientResolution, RecipientSetResolution
from tools.authorization.test_authorization import Fixture
from tools.scheduler.delivery_alert import (
    UNCERTAIN_SENTENCE, ensure_activation, scan_delivery_exceptions)
from tools.scheduler.misfire import due_storage_key
from tools.scheduler.runner import connect, tick
from tools.scheduler.tasks import ROOT, TransportUnavailable, overflow_report_date

TZ = ZoneInfo('Asia/Tehran')
JOB_ID = 'driver_daily_mechanical'


class Directory:
    def __init__(self):
        self.operators = ('900',)
        self.recipients = ('A', 'B')
        self.revoked = set()
        self.labels = {'A': 'گیرنده الف', 'B': 'گیرنده ب', '900': 'اپراتور'}

    def resolve_active_recipients(self, capability):
        if capability == CAPABILITY:
            if not self.operators:
                return RecipientSetResolution('no_active_holder')
            return RecipientSetResolution('ready', tuple(self.operators), len(self.operators))
        users = tuple(user for user in self.recipients if user not in self.revoked)
        if not users:
            return RecipientSetResolution('no_eligible_recipient')
        return RecipientSetResolution('ready', users, len(self.recipients), len(self.recipients) - len(users))

    def has_capability(self, user, capability):
        if capability == CAPABILITY:
            return user in self.operators
        return user in self.recipients and user not in self.revoked

    def identity_label(self, user_id):
        return self.labels.get(str(user_id), str(user_id))


class AlertTests(unittest.TestCase):
    def setUp(self):
        root = ROOT / 'runtime/scheduler-tests'
        root.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=root)
        self.addCleanup(self.temp.cleanup)
        self.dir = Path(self.temp.name)
        self.state = self.dir / 'state.sqlite3'
        conn = connect(self.state)
        conn.close()
        self.moment = datetime(2026, 10, 7, 10, 0, tzinfo=TZ)
        self.due = due_storage_key(self.moment)
        self.target = overflow_report_date(self.moment)
        self.store = Directory()
        self.sent = []
        self.factory_calls = 0
        self.fail_send = False
        self.enabled = True

    def job(self):
        return dict(id=JOB_ID, enabled=self.enabled, task='mechanical_driver_daily')

    def after(self, minutes):
        return self.moment + timedelta(minutes=minutes)

    def sender(self):
        self.factory_calls += 1
        harness = self

        class Sender:
            def message(self, user, text):
                if harness.fail_send:
                    raise TransportUnavailable(r'E:\Function\secret.xlsx')
                harness.sent.append((user, text))
                return str(len(harness.sent))

            def close(self):
                pass

        return Sender()

    def scan(self, moment):
        conn = connect(self.state)
        try:
            scan_delivery_exceptions(
                conn, [self.job()], moment, self.store, sender_factory=self.sender)
        finally:
            conn.close()

    def arm(self):
        conn = connect(self.state)
        try:
            self.assertTrue(ensure_activation(conn, self.moment - timedelta(days=1)))
        finally:
            conn.close()

    def add_run(self, status, error=None, *, data_state=None, notice=None, due=None):
        conn = connect(self.state)
        try:
            with conn:
                conn.execute('''INSERT INTO runs(
                    schedule_id,due,recipient,task,status,started,finished,error,attempt,
                    data_state,notice_delivery_state)
                    VALUES (?,?,?,?,?,?,?,?,?,?,?)''',
                    (JOB_ID, due or self.due, 'capability:test', 'mechanical_driver_daily', status,
                     '2026-10-07T06:30:00+00:00', '2026-10-07T06:30:01+00:00', error, 1,
                     data_state, notice))
        finally:
            conn.close()

    def add_receipt(self, recipient, kind, status, message_id=None, due=None):
        conn = connect(self.state)
        try:
            with conn:
                conn.execute('''INSERT INTO receipts(
                    schedule_id,due,recipient_id,delivery_kind,status,message_id,updated)
                    VALUES (?,?,?,?,?,?,?)''',
                    (JOB_ID, due or self.due, recipient, kind, status, message_id, '2026-10-07T06:30:02+00:00'))
        finally:
            conn.close()

    def set_run(self, status, error=None, *, data_state='ready', due=None):
        conn = connect(self.state)
        try:
            with conn:
                conn.execute('UPDATE runs SET status=?, error=?, data_state=? WHERE schedule_id=? AND due=?',
                             (status, error, data_state, JOB_ID, due or self.due))
        finally:
            conn.close()

    def set_receipt(self, recipient, status, message_id=None, *, kind='report', due=None):
        conn = connect(self.state)
        try:
            with conn:
                conn.execute('''UPDATE receipts SET status=?, message_id=?
                    WHERE schedule_id=? AND due=? AND recipient_id=? AND delivery_kind=?''',
                    (status, message_id, JOB_ID, due or self.due, recipient, kind))
        finally:
            conn.close()

    def snapshot(self):
        conn = sqlite3.connect(self.state)
        conn.row_factory = sqlite3.Row
        try:
            runs = [tuple(row) for row in conn.execute('SELECT * FROM runs ORDER BY due')]
            receipts = [tuple(row) for row in conn.execute(
                'SELECT schedule_id,due,recipient_id,delivery_kind,status,message_id FROM receipts ORDER BY due,recipient_id,delivery_kind')]
        finally:
            conn.close()
        return runs, receipts

    def cases(self):
        conn = sqlite3.connect(self.state)
        conn.row_factory = sqlite3.Row
        try:
            return [dict(row) for row in conn.execute('SELECT * FROM delivery_alert_case ORDER BY due, episode')]
        finally:
            conn.close()

    def alert_text(self):
        conn = sqlite3.connect(self.state)
        try:
            body = [str(row) for row in conn.execute(
                'SELECT exception_text, recovery_text, exception_status, recovery_status FROM delivery_alert_case')]
            body.extend(str(row) for row in conn.execute(
                'SELECT status, error, message_id FROM delivery_alert_receipt'))
        finally:
            conn.close()
        return '\n'.join(body)

    def test_all_recipients_sent_sends_no_alert(self):
        self.arm()
        self.add_run('succeeded', data_state='ready')
        self.add_receipt('A', 'report', 'sent', '1')
        self.add_receipt('B', 'report', 'sent', '2')
        self.scan(self.moment)
        self.scan(self.after(30))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.cases(), [])
        self.add_run('succeeded', data_state='ready', due=due_storage_key(self.after(24 * 60)))
        self.add_receipt('A', 'report', 'sent', '3', due=due_storage_key(self.after(24 * 60)))
        self.scan(self.after(24 * 60 + 40))
        self.assertEqual(self.sent, [])

    def test_temporary_failure_resolved_within_grace_sends_no_alert(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', 'transport_unavailable', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        self.scan(self.moment)
        self.scan(self.after(10))
        self.set_receipt('B', 'sent', '7')
        self.set_run('succeeded')
        self.scan(self.after(14))
        self.scan(self.after(40))
        self.assertEqual(self.sent, [])

    def test_one_recipient_still_failed_after_grace_sends_one_alert(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', r'E:\Function\secret.xlsx', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        self.scan(self.moment)
        self.scan(self.after(14))
        self.assertEqual(self.sent, [])
        self.scan(self.after(15))
        self.assertEqual(len(self.sent), 1)
        user, text = self.sent[0]
        self.assertEqual(user, '900')
        self.assertIn('ارسال گزارش کامل نشد', text)
        self.assertIn('گزارش روزانه معایب مکانیکی رانندگان', text)
        self.assertIn(self.target, text)
        self.assertIn('موعد: 10:00', text)
        self.assertIn('گیرنده ب', text)
        self.assertIn('تلاش مجدد ادامه دارد', text)
        self.assertIn('خطای ارسال در بله', text)
        self.assertNotIn('گیرنده الف', text)
        self.assertNotIn('secret', text)
        self.assertNotIn('xlsx', text)
        self.assertNotIn('\\', text)

    def test_grace_starts_when_failure_is_first_observed_not_at_original_due(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', 'transport_unavailable', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        first = self.moment + timedelta(hours=2)
        self.scan(first)
        self.scan(first + timedelta(minutes=14))
        self.assertEqual(self.sent, [])
        self.scan(first + timedelta(minutes=15))
        self.assertEqual(len(self.sent), 1)

    def test_partial_alert_names_only_the_failed_recipient(self):
        self.arm()
        self.add_run('partial', 'transport_unavailable', data_state='ready')
        self.add_receipt('A', 'report', 'sent', '11')
        self.add_receipt('B', 'report', 'failed')
        self.scan(self.moment)
        self.scan(self.after(15))
        self.assertEqual(len(self.sent), 1)
        self.assertIn('گیرنده ب', self.sent[0][1])
        self.assertNotIn('گیرنده الف', self.sent[0][1])

    def test_repeated_ticks_do_not_duplicate_the_alert(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', 'delivery_failed', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        self.scan(self.moment)
        self.scan(self.after(15))
        self.scan(self.after(16))
        self.scan(self.after(30))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.cases()), 1)

    def test_scheduler_restart_does_not_duplicate_the_alert(self):
        self.test_repeated_ticks_do_not_duplicate_the_alert()
        restarted = connect(self.state)
        restarted.close()
        self.scan(self.after(45))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.cases()), 1)

    def test_recovery_is_sent_once_after_a_real_alert(self):
        self.arm()
        self.add_run('partial', 'transport_unavailable', data_state='ready')
        self.add_receipt('A', 'report', 'sent', '11')
        self.add_receipt('B', 'report', 'failed')
        self.scan(self.moment)
        self.scan(self.after(15))
        self.set_receipt('B', 'sent', '22')
        self.set_run('succeeded')
        self.scan(self.after(20))
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(self.sent[1][1].startswith('✅ مشکل ارسال گزارش برطرف شد'))
        self.assertIn(self.target, self.sent[1][1])
        self.assertIn('تمام گیرندگان مورد انتظار اکنون دریافت کرده‌اند.', self.sent[1][1])
        self.scan(self.after(21))
        self.scan(self.after(50))
        self.assertEqual(len(self.sent), 2)

    def test_waiting_for_data_with_notice_sent_does_not_alert_the_operator(self):
        self.arm()
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', notice='sent')
        self.add_receipt('A', 'notice', 'sent', '1')
        self.add_receipt('B', 'notice', 'sent', '2')
        self.scan(self.moment)
        self.scan(self.after(40))
        self.assertEqual(self.sent, [])

    def test_source_lock_and_unattempted_missing_data_are_not_delivery_failures(self):
        self.arm()
        self.add_run('retry_wait', 'PermissionError')
        self.scan(self.moment)
        self.scan(self.after(40))
        self.assertEqual(self.sent, [])
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', due=due_storage_key(self.after(24 * 60)))
        self.scan(self.after(24 * 60))
        self.scan(self.after(24 * 60 + 40))
        self.assertEqual(self.sent, [])

    def test_waiting_for_data_notice_failure_alerts_only_that_recipient(self):
        self.arm()
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', notice='retry_wait')
        self.add_receipt('A', 'notice', 'sent', '1')
        self.add_receipt('B', 'notice', 'failed')
        self.scan(self.moment)
        self.assertEqual(self.sent, [])
        self.scan(self.after(15))
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn('گیرنده ب', text)
        self.assertNotIn('گیرنده الف', text)
        self.assertIn('اعلان نبود اطلاعات به گیرنده نرسید', text)
        self.assertIn('تلاش مجدد ادامه دارد', text)

    def test_no_data_final_failed_notice_alerts_without_claiming_retry(self):
        self.arm()
        self.add_run('no_data_final', 'date_missing', data_state='missing', notice='retry_wait')
        self.add_receipt('A', 'notice', 'sent', '1')
        self.add_receipt('B', 'notice', 'failed')
        self.scan(self.moment)
        self.assertEqual(len(self.sent), 1)
        self.assertIn('بررسی دستی لازم است', self.sent[0][1])
        self.assertNotIn('تلاش مجدد ادامه دارد', self.sent[0][1])
        self.assertNotIn('گیرنده الف', self.sent[0][1])

    def test_uncertain_report_alerts_immediately_and_does_not_resend(self):
        self.arm()
        self.add_run('uncertain', r'E:\Function\secret.xlsx', data_state='ready')
        self.add_receipt('A', 'report', 'sent', '1')
        self.add_receipt('B', 'report', 'uncertain')
        before = self.snapshot()
        self.scan(self.moment)
        self.assertEqual(len(self.sent), 1)
        text = self.sent[0][1]
        self.assertIn(UNCERTAIN_SENTENCE, text)
        self.assertIn('گیرنده ب', text)
        self.assertNotIn('گیرنده الف', text)
        self.assertNotIn('تلاش مجدد ادامه دارد', text)
        self.assertNotIn('secret', text)
        self.assertEqual(self.snapshot(), before)
        self.scan(self.after(5))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(self.snapshot(), before)

    def test_alert_transport_failure_is_bounded_and_retried(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', 'transport_unavailable', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        before = self.snapshot()
        self.fail_send = True
        self.scan(self.moment)
        self.scan(self.after(15))
        self.assertEqual(self.sent, [])
        self.assertGreaterEqual(self.factory_calls, 1)
        self.assertEqual(self.snapshot(), before)
        self.assertNotIn('secret', self.alert_text())
        self.assertNotIn('xlsx', self.alert_text())
        conn = sqlite3.connect(self.state)
        try:
            stored = conn.execute("SELECT error FROM delivery_alert_receipt").fetchall()
        finally:
            conn.close()
        self.assertEqual(stored, [('TransportUnavailable',)])
        self.fail_send = False
        self.scan(self.after(16))
        self.assertEqual(len(self.sent), 1)
        self.scan(self.after(17))
        self.assertEqual(len(self.sent), 1)
        self.assertEqual(len(self.cases()), 1)
        self.assertEqual(self.snapshot(), before)

    def test_undelivered_alert_does_not_produce_recovery(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', 'transport_unavailable', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        self.fail_send = True
        self.scan(self.moment)
        self.scan(self.after(15))
        self.fail_send = False
        self.set_receipt('B', 'sent', '7')
        self.set_run('succeeded')
        self.scan(self.after(20))
        self.scan(self.after(21))
        self.assertEqual(self.sent, [])

    def test_no_data_pending_placeholder_does_not_hide_a_later_send_failure(self):
        self.add_run('waiting_for_data', 'date_missing', data_state='missing', notice='sent')
        self.add_receipt('A', 'notice', 'sent', '1')
        self.add_receipt('B', 'notice', 'sent', '2')
        self.add_receipt('A', 'report', 'pending')
        self.add_receipt('B', 'report', 'pending')
        self.scan(self.moment)
        self.assertEqual(self.sent, [])
        self.set_run('partial', 'transport_unavailable', data_state='ready')
        self.set_receipt('A', 'sent', '3')
        self.set_receipt('B', 'failed')
        observed = self.after(1)
        self.scan(observed)
        self.assertEqual(self.sent, [])
        self.scan(observed + timedelta(minutes=15))
        self.assertEqual(len(self.sent), 1)
        self.assertIn('گیرنده ب', self.sent[0][1])
        self.assertNotIn('گیرنده الف', self.sent[0][1])

    def test_historical_uncertain_rows_do_not_flood_and_new_ones_do(self):
        later = self.moment + timedelta(days=1)
        due2 = due_storage_key(later)
        self.add_run('uncertain', 'uncertain', data_state='ready')
        self.add_receipt('A', 'report', 'uncertain')
        self.add_receipt('B', 'report', 'uncertain')
        self.scan(self.moment)
        self.scan(self.after(30))
        self.assertEqual(self.sent, [])
        self.add_run('uncertain', 'uncertain', data_state='ready', due=due2)
        self.add_receipt('B', 'report', 'uncertain', due=due2)
        self.scan(later)
        self.assertEqual(len(self.sent), 1)
        self.assertIn(overflow_report_date(later), self.sent[0][1])
        self.assertNotIn(self.target, self.sent[0][1])
        self.assertEqual({row['due'] for row in self.cases()}, {due2})
        self.scan(later + timedelta(minutes=10))
        self.assertEqual(len(self.sent), 1)

    def test_disabled_job_is_ignored(self):
        self.arm()
        self.add_run('retry_wait', 'transport_unavailable', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        self.enabled = False
        self.scan(self.moment)
        self.scan(self.after(40))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.cases(), [])

    def test_revoked_recipient_is_not_a_delivery_failure(self):
        self.arm()
        self.add_run('partial', 'transport_unavailable', data_state='ready')
        self.add_receipt('A', 'report', 'sent', '11')
        self.add_receipt('B', 'report', 'revoked')
        self.store.revoked.add('B')
        self.scan(self.moment)
        self.scan(self.after(40))
        self.assertEqual(self.sent, [])

    def test_revoked_operator_receives_nothing(self):
        self.arm()
        self.store.recipients = ('B',)
        self.add_run('retry_wait', 'delivery_failed', data_state='ready')
        self.add_receipt('B', 'report', 'failed')
        self.store.operators = ()
        self.scan(self.moment)
        self.scan(self.after(15))
        self.assertEqual(self.factory_calls, 0)
        self.assertEqual(self.sent, [])
        self.store.operators = ('900',)
        self.scan(self.after(16))
        self.assertEqual([(user, ) for user, _text in self.sent], [('900',)])
        self.assertEqual(len(self.sent), 1)

    def test_non_authorization_resolution_cannot_be_an_operator(self):
        self.arm()
        self.add_run('uncertain', 'uncertain', data_state='ready')
        self.add_receipt('B', 'report', 'uncertain')

        class Loose:
            def resolve_active_recipients(self, capability):
                from types import SimpleNamespace
                return SimpleNamespace(status='ready', recipients=('900', 'B'))

            def has_capability(self, user, capability):
                return True

            def identity_label(self, user_id):
                return 'نام حدسی'

        self.store = Loose()
        self.scan(self.moment)
        self.assertEqual(self.sent, [])
        self.assertEqual(self.factory_calls, 0)

    def test_tick_activates_before_execution_and_scans_after(self):
        path = self.dir / 'tick.yaml'
        path.write_text(yaml.safe_dump(dict(
            version=1, timezone='Asia/Tehran', schedules=[dict(
                id='overflow_daily_test', enabled=True, task='overflow', recipient_role='office_supervisor',
                timezone='Asia/Tehran', trigger=dict(type='cron', hour=9, minute=0), params={},
                misfire=dict(policy='replay', horizon_days=7))])), encoding='utf-8')
        order = []
        auth = Mock()
        auth.resolve_daily_recipient.return_value = RecipientResolution('ready', '101', 1)

        def handler(recipient, params, **kwargs):
            order.append('run')
            kwargs['on_receipt'](recipient, 'sent', '1')

        with patch('tools.scheduler.delivery_alert.ensure_activation',
                   side_effect=lambda *args, **kwargs: order.append('activate') or False), \
                patch('tools.scheduler.delivery_alert.scan_delivery_exceptions',
                      side_effect=lambda *args, **kwargs: order.append('scan')):
            tick(path, self.state, self.moment, {'overflow': (lambda params: None, handler)}, auth)
        self.assertEqual(order, ['activate', 'run', 'scan'])


class CapabilityTests(Fixture, unittest.TestCase):
    def test_capability_grants_only_alert_receipt(self):
        self.auth.migrate_admin(self.directory / 'backups')
        before = self.sql('SELECT role, capability FROM auth_role_capabilities ORDER BY 1, 2')
        migrate(self.auth, self.directory / 'backups', assignments=('101',), actor='test')
        self.assertEqual(self.auth.capabilities_for_role(ROLE), (CAPABILITY,))
        self.assertEqual(self.sql('SELECT resource FROM auth_capabilities WHERE capability=?', (CAPABILITY,)),
                         [(RESOURCE,)])
        self.assertTrue(self.auth.has_capability('101', CAPABILITY))
        self.assertFalse(self.auth.has_capability('202', CAPABILITY))
        self.assertFalse(self.auth.has_capability('101', 'function.read_all'))
        self.assertFalse(self.auth.has_capability('101', 'reports.overflow.read'))
        self.assertFalse(self.auth.has_capability('101', 'reports.driver_daily.read'))
        self.assertFalse(self.auth.function_scope('101').all)
        self.assertEqual(self.auth.function_scope('101').files, ())
        self.assertIsNone(self.auth.resolve_profile('101', '101'))
        self.assertEqual(self.sql('SELECT 1 FROM auth_role_profiles WHERE role=?', (ROLE,)), [])
        self.assertEqual(self.auth.capabilities_for_role('business_admin'), ('function.read_all',))
        after = self.sql('SELECT role, capability FROM auth_role_capabilities ORDER BY 1, 2')
        for row in before:
            self.assertIn(row, after)
        self.assertEqual(self.auth.resolve_active_recipients(CAPABILITY).recipients, ('101',))
        self.sql('UPDATE auth_user_roles SET active=0 WHERE user_id=? AND role=?', ('101', ROLE))
        self.assertFalse(self.auth.has_capability('101', CAPABILITY))
        self.assertEqual(self.auth.resolve_active_recipients(CAPABILITY).status, 'no_active_holder')
        migrate(self.auth, self.directory / 'backups', assignments=(), actor='test-again')
        self.assertEqual(self.sql('SELECT COUNT(*) FROM auth_roles WHERE role=?', (ROLE,)), [(1,)])

    def test_identity_label_uses_registry_name_or_id(self):
        self.sql('ALTER TABLE channel_users ADD COLUMN display_name TEXT')
        self.sql('ALTER TABLE channel_users ADD COLUMN verified_name TEXT')
        self.sql("UPDATE channel_users SET display_name=?, verified_name=? WHERE user_id='101'",
                 ('M@j!D', 'مجید امامی'))
        self.sql("UPDATE channel_users SET display_name=?, verified_name=? WHERE user_id='202'",
                 ('082778', ''))
        self.assertEqual(self.auth.identity_label('101'), 'مجید امامی')
        self.assertEqual(self.auth.identity_label('202'), '202')
        self.sql("UPDATE channel_users SET display_name=?, verified_name=? WHERE user_id='202'",
                 ('حمید', None))
        self.assertEqual(self.auth.identity_label('202'), 'حمید')
        self.sql("UPDATE channel_users SET display_name=?, verified_name=? WHERE user_id='202'",
                 (r'E:\Function\secret.xlsx', 'حمید\nsecret'))
        self.assertEqual(self.auth.identity_label('202'), '202')
        self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='101'")
        self.assertEqual(self.auth.identity_label('101'), '101')


if __name__ == '__main__':
    unittest.main()
