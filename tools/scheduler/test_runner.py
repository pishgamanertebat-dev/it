from datetime import datetime, timedelta
from contextlib import closing
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest.mock import patch
from zoneinfo import ZoneInfo

import yaml

from .runner import ROOT, latest_due, load_config, tick
from .tasks import BaleSender, overflow

TZ = ZoneInfo('Asia/Tehran')
NOW = datetime(2026, 9, 16, 9, 0, tzinfo=TZ)


class SchedulerTests(unittest.TestCase):
    def setUp(self):
        runtime = ROOT / 'runtime/scheduler-tests'
        runtime.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=runtime)
        self.addCleanup(self.temp.cleanup)
        self.path = Path(self.temp.name) / 'schedules.yaml'
        self.state = Path(self.temp.name) / 'state.sqlite3'
        self.sent = []
        self.registry = {'overflow': (lambda p: None, lambda r, p: self.sent.append(r))}
        self.job = dict(id='daily', task='overflow', recipient='455740857',
                        trigger=dict(type='cron', hour=9, minute=0))
        self.write([self.job])

    def write(self, jobs):
        self.path.write_text(yaml.safe_dump(dict(version=1, timezone='Asia/Tehran',
                                                 misfire_grace_seconds=900, schedules=jobs)), encoding='utf-8')

    def run_tick(self, now=NOW):
        return tick(self.path, self.state, now, self.registry)

    def test_exact_time_and_no_early_send(self):
        self.run_tick(NOW - timedelta(seconds=1))
        self.assertEqual(self.sent, [])
        self.run_tick()
        self.assertEqual(self.sent, ['455740857'])

    def test_restart_deduplicates_and_next_day_sends(self):
        self.run_tick()
        self.run_tick(NOW + timedelta(minutes=1))
        self.assertEqual(self.sent, ['455740857'])
        self.run_tick(NOW + timedelta(days=1))
        self.assertEqual(len(self.sent), 2)

    def test_catch_up_boundary(self):
        self.run_tick(NOW + timedelta(minutes=15))
        self.assertEqual(len(self.sent), 1)

    def test_expired_run_not_sent(self):
        self.run_tick(NOW + timedelta(minutes=15, seconds=1))
        self.assertEqual(self.sent, [])

    def test_disabled_and_reload(self):
        self.write([dict(self.job, enabled=False)])
        self.run_tick()
        self.assertEqual(self.sent, [])
        self.write([self.job])
        self.run_tick()
        self.assertEqual(len(self.sent), 1)

    def test_second_recipient(self):
        self.write([self.job, dict(self.job, id='second', recipient='123')])
        self.run_tick()
        self.assertEqual(self.sent, ['455740857', '123'])

    def test_one_shot(self):
        self.write([dict(self.job, trigger=dict(type='date', run_at='2026-09-16 09:00:00'))])
        self.run_tick()
        self.run_tick(NOW + timedelta(days=1))
        self.assertEqual(len(self.sent), 1)

    def test_weekday_filter(self):
        self.write([dict(self.job, trigger=dict(type='cron', hour=9, minute=0, day_of_week='thu'))])
        self.run_tick()  # Wednesday
        self.assertEqual(self.sent, [])
        self.run_tick(NOW + timedelta(days=1))
        self.assertEqual(len(self.sent), 1)

    def test_invalid_config_prevents_partial_execution(self):
        for invalid in [dict(self.job), dict(self.job, id='bad', task='shell'),
                        dict(self.job, id='bad', trigger=dict(type='cron', hour=25, minute=0)),
                        dict(self.job, id='bad', enabled='false'), dict(self.job, id='bad', typo=True)]:
            with self.subTest(invalid=invalid):
                self.write([self.job, invalid])
                with self.assertRaises(ValueError):
                    self.run_tick()
                self.assertEqual(self.sent, [])

    def test_failure_recorded_without_blind_retry_and_other_job_runs(self):
        def send(recipient, params):
            if recipient == '455740857':
                raise RuntimeError('private-url-secret')
            self.sent.append(recipient)
        self.registry['overflow'] = (lambda p: None, send)
        self.write([self.job, dict(self.job, id='second', recipient='123')])
        self.assertEqual(self.run_tick(), 1)
        self.run_tick(NOW + timedelta(minutes=1))
        self.assertEqual(self.sent, ['123'])
        with closing(sqlite3.connect(self.state)) as conn:
            self.assertEqual(conn.execute("SELECT status,error FROM runs WHERE schedule_id='daily'").fetchone(),
                             ('failed', 'RuntimeError'))

    def test_coalesces_multiple_missed_occurrences(self):
        self.write([dict(self.job, trigger=dict(type='cron', hour='*', minute='*'))])
        _, grace, jobs = load_config(self.path, self.registry)
        self.assertEqual(latest_due(jobs[0]['trigger'], NOW, grace), NOW)
        self.run_tick()
        self.assertEqual(len(self.sent), 1)

    def test_report_images_and_cleanup(self):
        async def worker(date, directory):
            paths = [Path(directory) / f'{i}.png' for i in range(2)]
            for path in paths:
                path.write_bytes(b'test')
            return dict(ok=True, images=paths, report=dict(date='1405/06/25', rows=[{}]))
        delivered = []
        with patch('tools.scheduler.tasks.build_report', worker), patch('tools.scheduler.tasks.BaleSender') as sender:
            sender.return_value.photo.side_effect = lambda r, p, c: delivered.append((r, p, c, p.exists()))
            overflow('455740857', {})
        self.assertEqual(len(delivered), 2)
        self.assertTrue(delivered[0][3])
        self.assertIn('1405/06/25', delivered[0][2])
        self.assertEqual(delivered[1][2], '')
        self.assertFalse(delivered[0][1].exists())

    def test_transport_exception_hides_token(self):
        with patch.dict('os.environ', {'BALE_BOT_TOKEN': 'private-token'}):
            sender = BaleSender()
        self.addCleanup(sender.close)
        self.assertFalse(sender.session.trust_env)
        path = Path(self.temp.name) / 'image.png'
        path.write_bytes(b'test')
        with patch.object(sender.session, 'post', side_effect=RuntimeError('private-token')):
            with self.assertRaises(RuntimeError) as caught:
                sender.photo('455740857', path, '')
        self.assertNotIn('private-token', str(caught.exception))


if __name__ == '__main__':
    unittest.main()
