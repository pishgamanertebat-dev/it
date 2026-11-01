"""Isolated source probes, scheduler state, and fake Bale transport only."""
from contextlib import closing
import sqlite3
import unittest
from unittest.mock import Mock, patch

import yaml

from .completion_reminder import run_reminders, validate_config, probe
from .runner import connect, load_config, tick
from .tasks import TransportUnavailable, TransportUncertain
from . import test_delivery_alert as harness


class CompletionReminderTests(unittest.TestCase):
    setUp = harness.AlertTests.setUp

    def job(self):
        return dict(id='driver_daily_mechanical', enabled=True, task='mechanical_driver_daily',
                    mechanical_reminder_hours=(8, 15))

    def sender(self):
        sender = Mock()
        sender.message.side_effect = lambda u, t: self.sent.append((u, t)) or str(len(self.sent))
        return sender

    def check(self, hour, minute=0, result=None, checker=None, factory=None, jobs=None):
        conn = connect(self.state)
        try:
            run_reminders(conn, jobs if jobs is not None else [self.job()],
                          self.moment.replace(hour=hour, minute=minute), self.store,
                          checker=checker or Mock(return_value=result or {'status': 'date_missing', 'latest_date': '1405/07/12'}),
                          sender_factory=factory or self.sender)
        finally:
            conn.close()

    def rows(self):
        with closing(sqlite3.connect(self.state)) as conn:
            conn.row_factory = sqlite3.Row
            return [dict(r) for r in conn.execute('SELECT * FROM completion_reminder_receipts')]

    def test_two_slots_only_each_recipient_once_across_connections(self):
        checker = Mock(return_value={'status': 'date_missing'})
        for hour, minute in [(7, 59), (8, 0), (8, 1), (9, 0), (11, 0), (12, 0), (15, 0), (15, 1), (16, 0)]:
            self.check(hour, minute, checker=checker)
        self.assertEqual([u for u, t in self.sent], ['A', 'B', 'A', 'B'])
        self.assertEqual(checker.call_count, 2)
        self.assertTrue(all('1405/07/14' in t for u, t in self.sent))
        self.assertNotIn('900', [u for u, t in self.sent])
        self.assertEqual({r['status'] for r in self.rows()}, {'sent'})

    def test_blank_section_still_reminds_when_empty_pdf_was_delivered(self):
        with closing(connect(self.state)) as conn, conn:
            for user in ('A', 'B'):
                conn.execute("INSERT INTO receipts VALUES (?,?,?,'report','sent','pdf-id',?)",
                             (self.job()['id'], self.due, user, self.moment.isoformat()))
        self.check(8, result={'status': 'no_defects'})
        self.assertEqual(len(self.sent), 2)
        self.assertTrue(all('بخش مکانیکی این تاریخ خالی است' in t for u, t in self.sent))

    def test_ready_source_never_reminds_even_if_pdf_not_sent(self):
        factory = Mock(side_effect=AssertionError('no transport'))
        checker = Mock(return_value={'status': 'ready'})
        self.check(8, checker=checker, factory=factory)
        self.check(8, 1, checker=checker, factory=factory)
        self.check(15, checker=checker, factory=factory)
        self.assertEqual(checker.call_count, 2)
        factory.assert_not_called()
        self.assertEqual({r['status'] for r in self.rows()}, {'not_needed'})

    def test_completed_before_afternoon_stops_second_reminder(self):
        self.check(8)
        self.check(15, result={'status': 'ready'})
        self.assertEqual(len(self.sent), 2)

    def test_latest_date_does_not_replace_exact_previous_day(self):
        checker = Mock(return_value={'status': 'date_missing', 'latest_date': '1405/07/15'})
        self.check(8, checker=checker)
        checker.assert_called_once_with('1405/07/14')
        self.assertTrue(all('1405/07/14' in t and '1405/07/15' in t for u, t in self.sent))

    def test_source_error_never_accuses_missing_data_and_can_recover(self):
        self.check(8, checker=Mock(side_effect=PermissionError('private-path')))
        self.assertEqual(self.sent, [])
        self.assertEqual(self.rows(), [])
        self.check(8, 1)
        self.assertEqual(len(self.sent), 2)

    def test_failed_recipient_retries_once_without_repeating_success(self):
        def factory():
            sender = self.sender()
            original = sender.message.side_effect
            sender.message.side_effect = lambda u, t: (_ for _ in ()).throw(TransportUnavailable()) if u == 'B' else original(u, t)
            return sender
        self.check(8, factory=factory)
        self.check(8, 1)
        self.assertEqual([u for u, t in self.sent], ['A'])
        self.check(8, 5)
        self.check(8, 6)
        self.assertEqual([u for u, t in self.sent], ['A', 'B'])

    def test_failed_send_rechecks_filled_source_before_retry(self):
        sender = self.sender()
        sender.check_connection.side_effect = TransportUnavailable()
        self.check(8, factory=lambda: sender)
        self.check(8, 5, result={'status': 'ready'})
        self.assertEqual(self.sent, [])
        self.assertEqual({r['status'] for r in self.rows()}, {'not_needed'})

    def test_uncertain_outcome_never_blindly_repeats_same_slot(self):
        sender = self.sender()
        sender.message.side_effect = TransportUncertain()
        self.check(8, factory=lambda: sender)
        self.check(8, 5)
        self.assertEqual(sender.message.call_count, 2)
        self.assertEqual(self.sent, [])
        self.assertEqual({r['status'] for r in self.rows()}, {'uncertain'})
        self.check(15)
        self.assertEqual(len(self.sent), 2)

    def test_interrupted_send_frozen_after_restart(self):
        self.check(8)
        with closing(sqlite3.connect(self.state)) as conn, conn:
            conn.execute("UPDATE completion_reminder_receipts SET status='sending',message_id=NULL")
        self.sent.clear()
        self.check(8, 1)
        self.check(8, 3)
        self.check(8, 5)
        self.assertEqual(self.sent, [])
        self.assertEqual({r['status'] for r in self.rows()}, {'uncertain'})

    def test_revocation_after_connection_prevents_message(self):
        def factory():
            sender = self.sender()
            sender.check_connection.side_effect = lambda: self.store.revoked.add('B')
            return sender
        self.check(8, factory=factory)
        self.assertEqual([u for u, t in self.sent], ['A'])

    def test_disabled_schedule_does_not_probe(self):
        job = dict(self.job(), enabled=False)
        checker = Mock()
        self.check(8, checker=checker, jobs=[job])
        checker.assert_not_called()

    def test_probe_inspects_only_mechanical_section(self):
        report = {'date': '1405/07/14', 'latest_date': '1405/07/14', 'sections': {
            'mechanical': {'status': 'no_defects'}, 'metalwork': {'status': 'ready'}}}
        with patch('integrations.hermes.function_domain.driver_report.load_driver', return_value=report):
            self.assertEqual(probe('1405/07/14')['status'], 'no_defects')

    def test_tick_loads_hours_and_sends_at_eight_before_pdf_due(self):
        config = self.dir / 'config.yaml'
        raw_job = dict(id='driver_daily_mechanical', task='mechanical_driver_daily',
                       recipient_capability='reports.driver_daily.mechanical_daily_receive',
                       timezone='Asia/Tehran', trigger=dict(type='cron', hour=9, minute=0), params={})
        config.write_text(yaml.safe_dump(dict(version=1, timezone='Asia/Tehran',
            mechanical_reminders=dict(hours=[8, 15]), schedules=[raw_job])), encoding='utf-8')
        self.assertEqual(load_config(config)[2][0]['mechanical_reminder_hours'], (8, 15))
        with patch('tools.scheduler.completion_reminder.probe', return_value={'status': 'date_missing'}), \
             patch('tools.scheduler.delivery_alert._default_sender', self.sender):
            self.assertEqual(tick(config, self.state, self.moment.replace(hour=8), authorization=self.store), 0)
        self.assertEqual([u for u, t in self.sent], ['A', 'B'])
        with closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute('SELECT count(*) FROM receipts').fetchone()[0], 0)
            self.assertEqual(conn.execute('SELECT count(*) FROM runs').fetchone()[0], 0)

    def test_strict_config(self):
        self.assertEqual(validate_config(None), ())
        self.assertEqual(validate_config({'enabled': True, 'hours': [8, 15]}), (8, 15))
        self.assertEqual(validate_config({'enabled': False}), ())
        for raw in ({'hours': [8, 8]}, {'hours': ['8']}, {'enabled': 'yes'}, {'hours': [24]}, {'typo': True}):
            with self.subTest(raw=raw), self.assertRaises(ValueError):
                validate_config(raw)


if __name__ == '__main__':
    unittest.main()
