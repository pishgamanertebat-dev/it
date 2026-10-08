"""Catch-up, transport retry, and high-frequency misfire policy."""
from datetime import datetime, timedelta, timezone
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import Mock, patch
from zoneinfo import ZoneInfo

import yaml

from tools.authorization.store import RecipientResolution
from tools.scheduler.misfire import backoff_delay, due_storage_key
from tools.scheduler.runner import connect, load_config, tick
from tools.scheduler.tasks import ROOT, TransportUnavailable, TransportUncertain, BaleSender

TZ = ZoneInfo('Asia/Tehran')


class CatchUpTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / 'runtime/scheduler-tests'
        runtime.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'schedules.yaml'
        self.state = Path(self.temp.name) / 'state.sqlite3'
        self.calls = []
        self.auth = Mock()
        self.auth.resolve_daily_recipient.return_value = RecipientResolution('ready', '101', 1)

    def write(self, jobs, grace=900):
        self.path.write_text(yaml.safe_dump(dict(
            version=1, timezone='Asia/Tehran', misfire_grace_seconds=grace, schedules=jobs)), encoding='utf-8')

    def overflow(self, **extra):
        job = dict(id='overflow_daily_test', enabled=True, task='overflow', recipient_role='office_supervisor',
                   timezone='Asia/Tehran', trigger=dict(type='cron', hour=9, minute=0), params={},
                   misfire=dict(policy='replay', horizon_days=7))
        job.update(extra)
        return job

    def driver(self, **extra):
        job = dict(id='driver_daily_office_supervisor', enabled=True, task='driver_daily',
                   recipient_role='office_supervisor', timezone='Asia/Tehran',
                   trigger=dict(type='cron', hour=10, minute=0), params={},
                   misfire=dict(policy='replay', horizon_days=7))
        job.update(extra)
        return job

    def handler(self, recipient, params, **kwargs):
        self.calls.append((recipient, params.get('date'), params.get('occurrence')))
        on_receipt = kwargs.get('on_receipt')
        if on_receipt is not None:
            on_receipt(recipient, 'sent', '42')
        return None

    def registry(self):
        return {'overflow': (lambda p: None, self.handler), 'driver_daily': (lambda p: None, self.handler),
                'repairs': (lambda p: None, self.handler)}

    def tick_at(self, moment):
        return tick(self.path, self.state, moment, self.registry(), self.auth)

    def rows(self):
        conn = sqlite3.connect(self.state)
        try:
            conn.row_factory = sqlite3.Row
            return [dict(row) for row in conn.execute('SELECT * FROM runs ORDER BY schedule_id, due')]
        finally:
            conn.close()

    def test_gateway_down_across_one_morning_report_recovers_original_due(self):
        self.write([self.overflow()])
        moment = datetime(2026, 10, 7, 10, 10, tzinfo=TZ)
        self.tick_at(moment)
        self.tick_at(moment)
        self.assertEqual(self.calls, [('101', '1405/07/14', None)])
        self.assertEqual(self.rows()[0]['due'], '2026-10-07T05:30:00+00:00')
        self.assertEqual(self.rows()[0]['status'], 'succeeded')

    def test_gateway_down_across_nine_and_ten_recovers_both_once(self):
        self.write([self.overflow(), self.driver()])
        moment = datetime(2026, 10, 7, 10, 5, tzinfo=TZ)
        self.tick_at(moment)
        self.tick_at(moment + timedelta(minutes=1))
        self.assertEqual(self.calls, [('101', '1405/07/14', None), ('101', '1405/07/14', None)])
        self.assertEqual({row['schedule_id'] for row in self.rows()},
                         {'overflow_daily_test', 'driver_daily_office_supervisor'})

    def test_already_sent_occurrence_is_not_resent(self):
        self.write([self.overflow()])
        conn = connect(self.state)
        conn.execute('''INSERT INTO runs(schedule_id,due,recipient,task,status,started,finished)
                        VALUES (?,?,?,?,?,?,?)''',
                     ('overflow_daily_test', '2026-10-07T05:30:00+00:00', 'role:office_supervisor',
                      'overflow', 'succeeded', 't', 't'))
        conn.commit()
        conn.close()
        self.tick_at(datetime(2026, 10, 7, 10, 10, tzinfo=TZ))
        self.assertEqual(self.calls, [])

    def test_bale_down_waits_then_sends_after_backoff(self):
        self.write([self.overflow()])
        phase = {'down': True}

        def send(recipient, params, **kwargs):
            self.calls.append(recipient)
            if phase['down']:
                return {'status': 'failed', 'reason': 'transport_unavailable', 'retry_recipients': [recipient],
                        'sent_count': 0, 'failed_count': 1}
            kwargs['on_receipt'](recipient, 'sent', '7')
            return None

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        start = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        self.assertEqual(self.tick_at(start), 0)
        self.assertEqual(self.rows()[0]['status'], 'retry_wait')
        self.assertEqual(backoff_delay(1), 60)
        phase['down'] = False
        self.tick_at(start + timedelta(seconds=30))
        self.assertEqual(len(self.calls), 1)
        self.tick_at(start + timedelta(seconds=61))
        self.assertEqual(len(self.calls), 2)
        self.assertEqual(self.rows()[0]['status'], 'succeeded')

    def test_reconnect_does_not_duplicate_a_sent_recipient(self):
        self.write([self.overflow()])
        seen = []

        def send(recipient, params, **kwargs):
            closed = kwargs.get('closed') or {}
            if recipient in closed.get('sent', ()):
                seen.append('duplicate')
                return None
            seen.append('send')
            kwargs['on_receipt'](recipient, 'sent', '9')
            return None

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        moment = datetime(2026, 10, 7, 9, 1, tzinfo=TZ)
        self.tick_at(moment)
        self.tick_at(moment + timedelta(minutes=5))
        self.assertEqual(seen, ['send'])

    def test_partial_fanout_retries_only_the_missing_recipient(self):
        self.write([self.overflow()])
        log = []
        allow_b = {'ok': False}

        def send(recipient, params, **kwargs):
            on_receipt = kwargs['on_receipt']
            closed = kwargs.get('closed') or {}
            sent = set(closed.get('sent') or ())
            if 'A' not in sent:
                on_receipt('A', 'sent', '11')
                log.append('A')
            if 'B' not in sent:
                if not allow_b['ok']:
                    on_receipt('B', 'failed', None)
                    log.append('B-fail')
                    return {'status': 'failed', 'reason': 'transport_unavailable', 'retry_recipients': ['B'],
                            'sent_count': 1, 'failed_count': 1}
                on_receipt('B', 'sent', '22')
                log.append('B')
            return {'status': 'succeeded', 'sent_count': 2}

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        start = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        self.tick_at(start)
        self.assertEqual(self.rows()[0]['status'], 'partial')
        allow_b['ok'] = True
        self.tick_at(start + timedelta(seconds=61))
        self.assertEqual(log, ['A', 'B-fail', 'B'])
        self.assertEqual(self.rows()[0]['status'], 'succeeded')

    def test_revoked_recipient_is_not_sent_while_pending(self):
        self.write([self.overflow()])
        eligible = {'A', 'B'}
        sent = []
        phase = {'up': False}

        def send(recipient, params, **kwargs):
            if not phase['up']:
                return {'status': 'failed', 'reason': 'transport_unavailable',
                        'retry_recipients': ['A', 'B'], 'sent_count': 0, 'failed_count': 2}
            for user in ('A', 'B'):
                if user not in eligible:
                    kwargs['on_receipt'](user, 'revoked', None)
                    continue
                kwargs['on_receipt'](user, 'sent', '1')
                sent.append(user)
            return {'status': 'succeeded', 'sent_count': len(sent)}

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        start = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        self.tick_at(start)
        eligible.remove('B')
        phase['up'] = True
        self.tick_at(start + timedelta(seconds=61))
        self.assertEqual(sent, ['A'])
        conn = sqlite3.connect(self.state)
        try:
            states = dict(conn.execute('SELECT recipient_id,status FROM receipts'))
        finally:
            conn.close()
        self.assertEqual(states['A'], 'sent')
        self.assertEqual(states['B'], 'revoked')

    def test_uncertain_transport_is_not_retried(self):
        self.write([self.overflow()])

        def send(recipient, params, **kwargs):
            self.calls.append(recipient)
            kwargs['on_receipt'](recipient, 'uncertain', None)
            return {'status': 'failed', 'reason': 'delivery_failed', 'uncertain_recipients': [recipient],
                    'sent_count': 0, 'failed_count': 1}

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        start = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        self.assertEqual(self.tick_at(start), 1)
        self.tick_at(start + timedelta(hours=2))
        self.assertEqual(self.calls, ['101'])
        self.assertEqual(self.rows()[0]['status'], 'uncertain')

    def test_missing_exact_date_does_not_fall_back_and_stays_retryable(self):
        self.write([self.overflow()])

        def send(recipient, params, **kwargs):
            self.calls.append(params['date'])
            self.assertEqual(params['date'], '1405/07/14')
            return {'status': 'skipped', 'reason': 'date_missing'}

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        self.tick_at(datetime(2026, 10, 7, 10, 10, tzinfo=TZ))
        self.assertEqual(self.calls, ['1405/07/14'])
        self.assertEqual(self.rows()[0]['status'], 'waiting_for_data')
        self.assertEqual(self.rows()[0]['error'], 'date_missing')

    def test_writer_lock_retries_without_marking_success(self):
        self.write([self.overflow()])
        phase = {'locked': True}

        def send(recipient, params, **kwargs):
            self.calls.append(phase['locked'])
            if phase['locked']:
                raise PermissionError('writer lock')
            kwargs['on_receipt'](recipient, 'sent', '3')
            return None

        self.registry = lambda: {'overflow': (lambda p: None, send)}
        start = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        self.tick_at(start)
        self.assertEqual(self.rows()[0]['status'], 'retry_wait')
        self.assertEqual(self.rows()[0]['error'], 'PermissionError')
        phase['locked'] = False
        self.tick_at(start + timedelta(seconds=61))
        self.assertEqual(self.calls, [True, False])
        self.assertEqual(self.rows()[0]['status'], 'succeeded')

    def test_forward_outage_recovers_each_missed_day_and_not_a_hole_before_watermark(self):
        self.write([self.overflow()])
        conn = connect(self.state)
        conn.execute('''INSERT INTO runs(schedule_id,due,recipient,task,status,started,finished)
                        VALUES (?,?,?,?,?,?,?)''',
                     ('overflow_daily_test', due_storage_key(datetime(2026, 10, 6, 9, 0, tzinfo=TZ)),
                      'role:office_supervisor', 'overflow', 'succeeded', 't', 't'))
        conn.commit()
        conn.close()
        self.tick_at(datetime(2026, 10, 8, 9, 30, tzinfo=TZ))
        self.assertEqual(self.calls, [('101', '1405/07/14', None), ('101', '1405/07/15', None)])

    def test_disabled_schedule_never_catches_up(self):
        self.write([self.overflow(enabled=False)])
        self.tick_at(datetime(2026, 10, 7, 18, 0, tzinfo=TZ))
        self.assertEqual(self.calls, [])
        self.assertEqual(self.rows(), [])

    def test_older_than_horizon_is_not_replayed(self):
        self.write([self.overflow(misfire=dict(policy='replay', horizon_days=1))])
        conn = connect(self.state)
        conn.execute('''INSERT INTO runs(schedule_id,due,recipient,task,status,started,finished)
                        VALUES (?,?,?,?,?,?,?)''',
                     ('overflow_daily_test', due_storage_key(datetime(2026, 9, 1, 9, 0, tzinfo=TZ)),
                      'role:office_supervisor', 'overflow', 'succeeded', 't', 't'))
        conn.commit()
        conn.close()
        self.tick_at(datetime(2026, 10, 7, 12, 0, tzinfo=TZ))
        self.assertEqual([call[1] for call in self.calls], ['1405/07/14'])

    def test_on_time_replay_runs_once(self):
        self.write([self.overflow()])
        moment = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        self.tick_at(moment)
        self.tick_at(moment + timedelta(minutes=1))
        self.assertEqual(len(self.calls), 1)

    def test_eight_hour_high_frequency_downtime_does_not_replay_every_slot(self):
        start = datetime(2026, 10, 7, 1, 0, tzinfo=TZ)
        now = datetime(2026, 10, 7, 9, 0, tzinfo=TZ)
        for policy in ('latest_only', 'skip_missed'):
            with self.subTest(policy=policy):
                self.calls.clear()
                state = Path(self.temp.name) / f'{policy}.sqlite3'
                self.write([dict(id='sync', task='repairs', recipient='101', misfire=dict(policy=policy),
                                 trigger=dict(type='cron', hour='*', minute='*/2'))])
                tick(self.path, state, now, self.registry(), None)
                self.assertEqual(len(self.calls), 1)
                conn = sqlite3.connect(state)
                try:
                    dues = [row[0] for row in conn.execute('SELECT due FROM runs')]
                finally:
                    conn.close()
                self.assertEqual(dues, [due_storage_key(now)])
                self.assertNotEqual(dues[0], due_storage_key(start))

    def test_replay_on_two_minute_schedule_is_refused(self):
        self.write([dict(id='sync', task='repairs', recipient='101',
                         misfire=dict(policy='replay', horizon_days=1),
                         trigger=dict(type='cron', hour='*', minute='*'))])
        with self.assertLogs('tools.scheduler.runner', level='ERROR') as logs:
            self.tick_at(datetime(2026, 10, 7, 12, 0, tzinfo=TZ))
        self.assertEqual(self.calls, [])
        self.assertTrue(any('frequency_flood' in line for line in logs.output))

    def test_legacy_ledger_columns_migrate_and_success_stays_terminal(self):
        self.write([self.overflow()])
        conn = sqlite3.connect(self.state)
        conn.execute('''CREATE TABLE runs (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient TEXT NOT NULL,
            task TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL,
            finished TEXT, error TEXT, PRIMARY KEY(schedule_id, due))''')
        conn.execute('INSERT INTO runs VALUES (?,?,?,?,?,?,?,?)',
                     ('overflow_daily_test', '2026-10-07T05:30:00+00:00', 'role:office_supervisor',
                      'overflow', 'succeeded', 't', 't', None))
        conn.commit()
        conn.close()
        self.tick_at(datetime(2026, 10, 7, 11, 0, tzinfo=TZ))
        self.assertEqual(self.calls, [])
        self.assertIn('attempt', self.rows()[0])


class TransportClassificationTests(unittest.TestCase):
    def test_connect_timeout_retries_and_read_timeout_does_not(self):
        import requests
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'image.png'
            path.write_bytes(b'test')
            with patch.dict('os.environ', {'BALE_BOT_TOKEN': 'private-token'}):
                sender = BaleSender()
            self.addCleanup(sender.close)
            with patch.object(sender.session, 'post', side_effect=requests.exceptions.ConnectTimeout('x')):
                with self.assertRaises(TransportUnavailable):
                    sender.photo('101', path, '')
            with patch.object(sender.session, 'post', side_effect=requests.exceptions.ReadTimeout('private-token')):
                with self.assertRaises(TransportUncertain) as caught:
                    sender.photo('101', path, '')
            self.assertNotIn('private-token', str(caught.exception))


class ProductionScheduleTests(unittest.TestCase):
    def test_report_jobs_replay_and_mine_sync_stays_latest_only(self):
        raw = yaml.safe_load((ROOT / 'settings/schedules.yaml').read_text(encoding='utf-8'))
        jobs = {job['id']: job for job in raw['schedules']}
        replay = {
            'overflow_daily_test', 'driver_daily_office_supervisor', 'overflow_daily_mechanical',
            'driver_daily_mechanical', 'driver_daily_metalwork', 'maintenance_daily_report'}
        for name in replay:
            self.assertEqual(jobs[name]['misfire'], {'policy': 'replay', 'horizon_days': 7})
            self.assertTrue(jobs[name]['enabled'])
            self.assertEqual(jobs[name]['params'], {})
        self.assertFalse(jobs['repairs_daily']['enabled'])
        self.assertFalse(jobs['metalwork_daily']['enabled'])
        self.assertNotIn('misfire', jobs['repairs_daily'])
        self.assertNotIn('misfire', jobs['metalwork_daily'])
        self.assertEqual(jobs['overflow_daily_test']['recipient_capability'], 'reports.overflow.daily_receive')
        self.assertEqual(jobs['driver_daily_office_supervisor']['recipient_capability'], 'reports.driver_daily.daily_receive')
        self.assertEqual(jobs['overflow_daily_mechanical']['recipient_capability'],
                         'reports.overflow.mechanical_daily_receive')
        self.assertEqual(jobs['driver_daily_mechanical']['recipient_capability'],
                         'reports.driver_daily.mechanical_daily_receive')
        self.assertEqual(jobs['driver_daily_metalwork']['recipient_capability'],
                         'reports.driver_daily.metalwork_daily_receive')
        self.assertEqual(jobs['maintenance_daily_report']['recipient_capability'],
                         'reports.maintenance.daily_receive')
        self.assertNotIn('recipient', jobs['overflow_daily_test'])
        external = {item['id']: item for item in raw['external_misfire']}
        self.assertEqual(external['3ccb658aed3c'], {'id': '3ccb658aed3c', 'name': 'mine-file-sync', 'policy': 'latest_only'})
        load_config(ROOT / 'settings/schedules.yaml')


if __name__ == '__main__':
    unittest.main()
