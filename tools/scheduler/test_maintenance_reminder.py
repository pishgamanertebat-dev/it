"""Maintenance reminders use synthetic sources, identities and transport."""
from contextlib import closing, contextmanager
import sqlite3
import unittest
from unittest.mock import Mock, patch

import yaml

from tools.authorization import MECH_DRIVER_RECEIVE, METALWORK_DRIVER_RECEIVE
from tools.authorization.store import RecipientSetResolution
from tools.authorization import test_authorization as auth_harness
from . import test_completion_reminder as harness
from .completion_reminder import (
    reminder_recipients, probe_maintenance, validate_maintenance_config,
)
from .runner import load_config

USERS = ('1732374823', '654806764', '387679249')


class MaintenanceReminderTests(unittest.TestCase):
    sender = harness.CompletionReminderTests.sender
    check = harness.CompletionReminderTests.check
    rows = harness.CompletionReminderTests.rows

    def setUp(self):
        harness.CompletionReminderTests.setUp(self)
        self.store = Mock()
        self.revoked = set()
        def resolve(capability):
            users = (USERS[:2] if capability == MECH_DRIVER_RECEIVE else
                     USERS[2:] if capability == METALWORK_DRIVER_RECEIVE else ('397185913', '514458396'))
            users = tuple(u for u in users if u not in self.revoked)
            return RecipientSetResolution('ready' if users else 'no_active_holder', users)
        self.store.resolve_active_recipients.side_effect = resolve
        self.store.has_capability.side_effect = lambda u, c: u in USERS[:2] and u not in self.revoked

    def job(self):
        return dict(id='maintenance_daily_report', task='maintenance_daily_report', enabled=True,
                    maintenance_reminder_hours=(11, 15), maintenance_reminder_recipients=USERS)

    def test_eleven_and_fifteen_only_exact_three_no_office_messages(self):
        for hour, minute in [(8, 0), (9, 0), (10, 59), (11, 0), (11, 1), (12, 0), (15, 0), (15, 1), (16, 0)]:
            self.check(hour, minute)
        self.assertEqual([u for u, text in self.sent], list(USERS) * 2)
        self.assertTrue(all('تعمیرات ۱۴۰۵' in text and '1405/07/14' in text for u, text in self.sent))
        self.assertTrue(all('گزارش روزانه رانندگان' not in text for u, text in self.sent))

    def test_registered_afternoon_activity_stops_second_message(self):
        self.check(11)
        self.check(15, result={'status': 'ready'})
        self.assertEqual([u for u, text in self.sent], list(USERS))

    def test_pdf_receipts_do_not_replace_source_completion_check(self):
        with closing(sqlite3.connect(self.state)) as conn, conn:
            conn.execute("INSERT INTO receipts VALUES (?,?,?,'report','sent','old-id',?)",
                         (self.job()['id'], self.due, '397185913', self.moment.isoformat()))
        self.check(11)
        self.assertEqual([u for u, text in self.sent], list(USERS))

    def test_production_probe_is_selected_without_driver_probe(self):
        with patch('tools.scheduler.completion_reminder.probe_maintenance', return_value={'status': 'date_missing'}) as probe, \
             patch('tools.scheduler.completion_reminder.probe') as driver:
            # Bypass the helper's injected checker for this integration boundary.
            from .completion_reminder import run_reminders
            with closing(harness.connect(self.state)) as conn:
                run_reminders(conn, [self.job()], self.moment.replace(hour=11), self.store,
                              sender_factory=self.sender)
        probe.assert_called_once_with('1405/07/14')
        driver.assert_not_called()
        self.assertEqual([u for u, text in self.sent], list(USERS))

    def test_source_error_does_not_send_completion_accusation(self):
        self.check(11, checker=Mock(side_effect=ValueError('invalid-source')))
        self.assertEqual(self.sent, [])

    def test_registration_or_role_revoked_before_send_is_excluded(self):
        def factory():
            sender = self.sender()
            sender.check_connection.side_effect = lambda: self.revoked.add(USERS[2])
            return sender
        self.check(11, factory=factory)
        self.assertEqual([u for u, text in self.sent], list(USERS[:2]))

    def test_mechanical_and_maintenance_slots_have_independent_receipts(self):
        mechanical = dict(id='driver_daily_mechanical', task='mechanical_driver_daily',
                          mechanical_reminder_hours=(8, 15))
        self.check(15, jobs=[mechanical, self.job()])
        self.check(15, 1, jobs=[mechanical, self.job()])
        self.assertEqual([u for u, text in self.sent], list(USERS[:2]) + list(USERS))
        self.assertEqual(len(self.rows()), 5)

    def test_config_wires_only_maintenance_job(self):
        config = self.dir / 'maintenance.yaml'
        raw_job = dict(id='maintenance_daily_report', task='maintenance_daily_report',
                       recipient_capability='reports.maintenance.daily_receive', timezone='Asia/Tehran',
                       trigger=dict(type='cron', hour=12, minute=0), params={})
        config.write_text(yaml.safe_dump(dict(version=1, timezone='Asia/Tehran',
            maintenance_reminders=dict(hours=[11, 15], recipients=list(USERS)),
            schedules=[raw_job])), encoding='utf-8')
        job = load_config(config)[2][0]
        self.assertEqual(job['maintenance_reminder_hours'], (11, 15))
        self.assertEqual(job['maintenance_reminder_recipients'], USERS)
        self.assertEqual(job['mechanical_reminder_hours'], ())

    def test_config_requires_explicit_distinct_ids(self):
        self.assertEqual(validate_maintenance_config(None), ((), ()))
        self.assertEqual(validate_maintenance_config({'recipients': list(USERS)}), ((11, 15), USERS))
        for raw in ({}, {'recipients': []}, {'recipients': [123]}, {'recipients': ['0']},
                    {'recipients': [USERS[0], USERS[0]]}, {'recipients': list(USERS), 'hours': [11, 11]},
                    {'recipients': list(USERS), 'extra': True}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_maintenance_config(raw)

    def test_probe_uses_actual_activity_not_date_only_or_latest_fallback(self):
        from tools.fleet.maintenance_daily import report as maintenance
        from tools.fleet.maintenance_daily.test_report import workbook, package
        for include_activity in (False, True):
            rows = [['1405/07/13', 801, 'مکانیک', 'فعالیت قبل', None], ['1405/07/14']]
            if include_activity:
                rows.append(['1405/07/14', 801, 'مکانیک', 'فعالیت هدف', None])
            book, layout = workbook([('801', rows)])
            data = package(book)
            book.close()
            @contextmanager
            def snapshot():
                yield data, 'fixture'
            scan = maintenance.scan
            with patch.object(maintenance, 'source_snapshot', snapshot), \
                 patch.object(maintenance, 'scan', side_effect=lambda b, d: scan(b, d, layout=layout)), \
                 patch.object(maintenance, 'render') as render:
                result = probe_maintenance('1405/07/14')
            self.assertEqual(result['status'], 'ready' if include_activity else 'date_missing')
            self.assertEqual(result['latest_date'], '1405/07/14' if include_activity else '1405/07/13')
            render.assert_not_called()


class ReminderIdentityTests(auth_harness.Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        from tools.authorization import MECHANICAL_MANAGER
        self.auth.migrate_admin(self.directory / 'backups')
        self.auth.migrate_mechanical(self.directory / 'backups', assignments=(('101', MECHANICAL_MANAGER),))
        self.auth.migrate_metalwork(self.directory / 'backups', assignments=('202',))
        self.job = dict(task='maintenance_daily_report', maintenance_reminder_recipients=('101', '202', '303'))

    def test_identity_checks_do_not_grant_file_read_or_pdf_subscription(self):
        from tools.authorization import MAINTENANCE_RECORDS_READ
        from tools.authorization.maintenance import CAPABILITY
        self.assertEqual(reminder_recipients(self.job, self.auth), ('101', '202'))
        self.assertFalse(self.auth.has_capability('202', MAINTENANCE_RECORDS_READ))
        self.assertNotEqual(self.auth.resolve_active_recipients(CAPABILITY).status, 'ready')

    def test_unapproved_or_nonprivate_identity_is_excluded(self):
        self.sql("UPDATE channel_users SET registration_status='pending_approval' WHERE user_id='202'")
        self.assertEqual(reminder_recipients(self.job, self.auth), ('101',))
        self.sql("UPDATE channel_users SET registration_status='approved',chat_id='999' WHERE user_id='202'")
        self.assertEqual(reminder_recipients(self.job, self.auth), ('101',))


if __name__ == '__main__':
    unittest.main()
