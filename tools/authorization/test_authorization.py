"""Hermetic authorization + overflow + scheduled delivery acceptance tests.

Synthetic identities, temporary SQLite and fake generator/transport only.
"""
from contextlib import closing
from datetime import datetime, timedelta
import asyncio
import ast
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch
from zoneinfo import ZoneInfo

import yaml

from tools.authorization.store import (
    AuthorizationStore, DAILY_RECEIVE, OFFICE_SUPERVISOR, OVERFLOW_READ, ROOT,
)
from tools.fleet.overflow.bale import OverflowHandler, build_report
from tools.fleet.overflow.report import DEFAULT_SOURCE
from tools.scheduler.runner import load_config, tick
from tools.scheduler.tasks import overflow, overflow_report_date, overflow_report_caption


class Fixture:
    def setUp(self):
        directory = ROOT / 'runtime/authorization-tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.path = self.directory / 'identity.sqlite3'
        with closing(sqlite3.connect(self.path)) as conn, conn:
            conn.execute('''CREATE TABLE channel_users (
                platform TEXT,user_id TEXT,chat_id TEXT,registration_status TEXT,
                PRIMARY KEY(platform,user_id))''')
            conn.executemany('INSERT INTO channel_users VALUES (?,?,?,?)', [
                ('bale', '101', '101', 'approved'),
                ('bale', '202', '202', 'approved'),
                ('bale', '303', '303', 'pending_approval'),
            ])
        self.auth = AuthorizationStore(self.path)
        self.auth.migrate(self.directory / 'backups')

    def sql(self, statement, params=()):
        with closing(sqlite3.connect(self.path)) as conn, conn:
            return conn.execute(statement, params).fetchall()

    def assign(self, user='101', replace=False):
        self.auth.assign_role(user, OFFICE_SUPERVISOR, actor='test-operator', replace=replace)

    def event(self, user='101', text='سرریز', **source):
        return SimpleNamespace(text=text, message_id='test-message',
            source=SimpleNamespace(**dict(dict(platform='bale', chat_type='dm',
                user_id=user, chat_id=user), **source)))

    def ambiguous(self):
        self.assign()
        self.sql('INSERT INTO auth_user_roles VALUES (?,?,?,?,?,?,?)',
                 ('bale', '202', OFFICE_SUPERVISOR, 1, 'test', 'test', 'test'))


class StoreTests(Fixture, unittest.TestCase):
    def test_registered_supervisor_and_scoped_capabilities(self):
        self.assign()
        self.assertEqual(self.auth.roles('101'), (OFFICE_SUPERVISOR,))
        self.assertEqual(set(self.auth.capabilities_for_role(OFFICE_SUPERVISOR)), {OVERFLOW_READ, DAILY_RECEIVE})
        self.assertTrue(self.auth.has_capability('101', OVERFLOW_READ))
        self.assertTrue(self.auth.has_capability('101', DAILY_RECEIVE))
        self.assertFalse(self.auth.has_capability('101', 'files.read'))
        self.assertFalse(self.auth.has_capability('101', 'reports.*'))
        self.assertEqual(self.sql('SELECT DISTINCT resource FROM auth_capabilities'), [('reports.overflow',)])
        self.assertEqual(self.sql('SELECT display_name FROM auth_roles'), [('سرپرست دفتر',)])

    def test_registered_without_capability_denied(self):
        self.assertFalse(self.auth.can_read_overflow('202', '202'))
        self.assertEqual(self.auth.roles('202'), ())

    def test_unregistered_and_revoked_fail_even_with_assignment(self):
        self.assign()
        for status in ('pending_approval', 'none', 'rejected', 'revoked'):
            self.sql('UPDATE channel_users SET registration_status=? WHERE user_id=?', (status, '101'))
            self.assertFalse(self.auth.can_read_overflow('101', '101'))
            self.assertEqual(self.auth.resolve_daily_recipient().status, 'recipient_not_approved')
        self.assertFalse(self.auth.has_capability('404', OVERFLOW_READ))
        with self.assertRaises(ValueError):
            self.assign('303')

    def test_private_identity_required_not_chat_or_text_claim(self):
        self.assign()
        for args in [('101', '202'), ('101', '101', 'telegram'), ('101', '101', 'bale', 'group')]:
            self.assertFalse(self.auth.can_read_overflow(*args))

    def test_unique_recipient(self):
        self.assign()
        result = self.auth.resolve_daily_recipient()
        self.assertEqual((result.status, result.recipient, result.holder_count), ('ready', '101', 1))

    def test_zero_and_multiple_never_select_first(self):
        self.assertEqual(self.auth.resolve_daily_recipient().status, 'no_active_holder')
        self.ambiguous()
        result = self.auth.resolve_daily_recipient()
        self.assertEqual((result.status, result.recipient, result.holder_count), ('ambiguous_holders', None, 2))
        # A revoked second holder must not silently cause selection of the first.
        self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='202'")
        self.assertEqual(self.auth.resolve_daily_recipient().status, 'ambiguous_holders')

    def test_replace_is_atomic_and_explicit(self):
        self.assign()
        with self.assertRaises(ValueError):
            self.assign('202')
        self.assertEqual(self.auth.resolve_daily_recipient().recipient, '101')
        self.assign('202', replace=True)
        self.assertEqual(self.auth.resolve_daily_recipient().recipient, '202')
        self.assertFalse(self.auth.has_capability('101', OVERFLOW_READ))
        self.assertEqual(self.sql("SELECT COUNT(*) FROM auth_events WHERE event_type='deactivated'"), [(1,)])

    def test_daily_receive_independent_of_read(self):
        self.assign()
        self.sql('DELETE FROM auth_role_capabilities WHERE capability=?', (DAILY_RECEIVE,))
        self.assertTrue(self.auth.has_capability('101', OVERFLOW_READ))
        self.assertEqual(self.auth.resolve_daily_recipient().status, 'capability_missing')

    def test_unavailable_corrupt_missing_schema_fail_closed(self):
        missing = self.directory / 'absent.sqlite3'
        invalid = self.directory / 'corrupt.sqlite3'
        invalid.write_bytes(b'not a SQLite database')
        no_schema = self.directory / 'no-schema.sqlite3'
        sqlite3.connect(no_schema).close()
        for path in (missing, invalid, no_schema):
            auth = AuthorizationStore(path)
            self.assertFalse(auth.has_capability('101', OVERFLOW_READ))
            self.assertEqual(auth.roles('101'), ())
            self.assertEqual(auth.capabilities_for_role(OFFICE_SUPERVISOR), ())
            self.assertEqual(auth.resolve_daily_recipient().status, 'store_unavailable')
        self.assertFalse(missing.exists())

    def test_migration_repeatable_backed_up_identity_unchanged(self):
        before = self.sql('SELECT * FROM channel_users ORDER BY user_id')
        backup = self.auth.migrate(self.directory / 'backups')
        self.assertTrue(backup.exists())
        with closing(sqlite3.connect(backup)) as conn:
            self.assertEqual(conn.execute('PRAGMA integrity_check').fetchone()[0], 'ok')
        self.assertEqual(self.sql('SELECT * FROM channel_users ORDER BY user_id'), before)
        self.assertEqual(self.sql('SELECT COUNT(*) FROM auth_migrations'), [(1,)])
        self.assertEqual(self.sql('SELECT COUNT(*) FROM auth_capabilities'), [(2,)])

    def test_replacement_rollback_on_unknown_role(self):
        self.assign()
        with self.assertRaises(sqlite3.IntegrityError):
            self.auth.assign_role('202', 'unknown-role', actor='test')
        self.assertEqual(self.auth.resolve_daily_recipient().recipient, '101')


class BackendTests(Fixture, unittest.IsolatedAsyncioTestCase):
    async def test_authorized_backend_invokes_existing_worker(self):
        self.assign()
        worker = AsyncMock(return_value={'ok': False, 'message': 'fixture report missing'})
        handler = OverflowHandler(worker, self.auth)
        adapter = SimpleNamespace(send=AsyncMock())
        result = handler.handle(self.event(), SimpleNamespace(adapters={'bale': adapter}), send=Mock())
        self.assertEqual(result['reason'], 'overflow-report')
        await asyncio.gather(*handler.tasks)
        worker.assert_awaited_once()
        adapter.send.assert_awaited_once_with('101', 'fixture report missing')

    async def test_unregistered_and_no_capability_without_menu_are_denied(self):
        for user in ('202', '303', '404'):
            worker, send = AsyncMock(), Mock()
            handler = OverflowHandler(worker, self.auth)
            result = handler.handle(self.event(user), SimpleNamespace(), send=send)
            self.assertEqual(result['reason'], 'overflow-denied')
            self.assertFalse(handler.tasks)
            worker.assert_not_called()
            send.assert_called_once()

    async def test_menu_visibility_never_grants_and_approval_rechecked(self):
        self.assign()
        worker = AsyncMock()
        handler = OverflowHandler(worker, self.auth)
        event = self.event('202')
        event.menu_visible = True
        self.assertEqual(handler.handle(event, SimpleNamespace(), send=Mock())['reason'], 'overflow-denied')
        worker.assert_not_called()

    async def test_revocation_while_worker_runs_no_operational_response(self):
        self.assign()
        async def worker(date, directory):
            self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='101'")
            return {'ok': True, 'images': [Path(directory)/'image.png'], 'report': {'date': '1405/07/12', 'rows': []}}
        adapter = SimpleNamespace(send=AsyncMock(), _bot=SimpleNamespace(send_photo=AsyncMock()))
        handler = OverflowHandler(worker, self.auth)
        handler.handle(self.event(), SimpleNamespace(adapters={'bale': adapter}), send=Mock())
        await asyncio.gather(*handler.tasks)
        adapter.send.assert_not_called()
        adapter._bot.send_photo.assert_not_called()

    async def test_direct_delivery_rechecks_before_generator(self):
        worker = AsyncMock()
        adapter = SimpleNamespace(send=AsyncMock())
        handler = OverflowHandler(worker, self.auth)
        await handler.deliver(SimpleNamespace(adapters={'bale': adapter}), '202', None, '202')
        worker.assert_not_called()
        adapter.send.assert_not_called()

    async def test_real_registry_intercepts_before_admin_or_agent_routing(self):
        path = ROOT/'integrations/hermes/plugins/komatso-bale-registry/__init__.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name in {'_handle_bale', '_handle_overflow_report', '_platform_name'}]
        send = Mock()
        scope = {'_send': send, '_apply_developer_conversation_policy': lambda event: None,
                 '_admin_ids': Mock(side_effect=AssertionError('Denied report reached admin routing'))}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<registry-fixture>', 'exec'), scope)
        from tools.fleet.overflow import bale
        handler = OverflowHandler(AsyncMock(), self.auth)
        with patch.object(bale, '_handler', handler):
            result = scope['_handle_bale'](self.event('202'), SimpleNamespace())
        self.assertEqual(result['reason'], 'overflow-denied')
        handler.worker.assert_not_called()
        scope['_admin_ids'].assert_not_called()

    async def test_registry_reloads_legacy_cached_public_backend(self):
        path = ROOT/'integrations/hermes/plugins/komatso-bale-registry/__init__.py'
        tree = ast.parse(path.read_text(encoding='utf-8'))
        functions = [node for node in tree.body if isinstance(node, ast.FunctionDef)
                     and node.name == '_handle_overflow_report']
        scope = {'_send': Mock()}
        exec(compile(ast.Module(body=functions, type_ignores=[]), '<bridge-fixture>', 'exec'), scope)
        from tools.fleet.overflow import bale
        authorized_backend = SimpleNamespace(AUTHORIZATION_VERSION=1,
            handle_overflow_message=Mock(return_value={'action':'skip', 'reason':'overflow-denied'}))
        with patch.object(bale, 'AUTHORIZATION_VERSION', 0), patch('importlib.reload', return_value=authorized_backend) as reload:
            result = scope['_handle_overflow_report'](self.event('202'), SimpleNamespace())
        reload.assert_called_once_with(bale)
        authorized_backend.handle_overflow_message.assert_called_once()
        self.assertEqual(result['reason'], 'overflow-denied')

    async def test_generator_source_fixed_ignores_environment(self):
        process = Mock(returncode=0)
        process.communicate = AsyncMock(return_value=(b'{"ok":false}', b''))
        with patch('tools.fleet.overflow.bale.asyncio.create_subprocess_exec', AsyncMock(return_value=process)) as spawn:
            with patch.dict('os.environ', {'FLEET_OVERFLOW_SOURCE': 'E:/Function/other.xlsx'}):
                await build_report(None, self.directory)
        command = spawn.call_args.args
        self.assertEqual(command[command.index('--source')+1], str(DEFAULT_SOURCE))
        self.assertEqual(DEFAULT_SOURCE, Path(r'E:\Function\سرریز روزانه.xlsx'))
        self.assertIn('tools.fleet.overflow.report', command)


class DailyTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.config = self.directory/'schedules.yaml'
        self.state = self.directory/'runs.sqlite3'
        self.schedule = dict(id='overflow_daily_test', enabled=True, task='overflow',
            recipient_role=OFFICE_SUPERVISOR, timezone='Asia/Tehran',
            trigger=dict(type='cron', hour=9, minute=0), params={})
        self.config.write_text(yaml.safe_dump(dict(version=1, timezone='UTC',
            schedules=[self.schedule])), encoding='utf-8')
        self.now = datetime(2026, 10, 4, 9, 0, tzinfo=ZoneInfo('Asia/Tehran'))
        self.sent = []
        self.registry = {'overflow': (lambda p: None, lambda r,p: self.sent.append(r))}

    def run_tick(self, now=None):
        return tick(self.config, self.state, now or self.now, self.registry, self.auth)

    def test_tehran_nine_dedup_and_holder_change_no_code_or_cron_edit(self):
        self.assign()
        original = self.config.read_bytes()
        self.run_tick(self.now-timedelta(seconds=1))
        self.assertFalse(self.sent)
        self.run_tick()
        self.run_tick(self.now+timedelta(minutes=1))
        self.assertEqual(self.sent, ['101'])
        self.assign('202', replace=True)
        self.run_tick(self.now+timedelta(days=1))
        self.assertEqual(self.sent, ['101', '202'])
        self.assertEqual(self.config.read_bytes(), original)
        self.assertEqual(str(load_config(self.config, self.registry)[2][0]['trigger'].timezone), 'Asia/Tehran')

    def test_zero_multiple_unavailable_skip_logged_no_send_not_job_failure(self):
        cases = [('no_active_holder', lambda: None), ('ambiguous_holders', self.ambiguous),
                 ('store_unavailable', lambda: self.path.rename(self.directory/'offline.sqlite3'))]
        for index, (reason, setup) in enumerate(cases):
            setup()
            with self.assertLogs('tools.scheduler.runner', level='WARNING') as logs:
                self.assertEqual(self.run_tick(self.now+timedelta(days=index)), 0)
            self.assertTrue(any(reason in line for line in logs.output))
            self.assertFalse(self.sent)
            with closing(sqlite3.connect(self.state)) as conn:
                self.assertEqual(conn.execute('SELECT status,error FROM runs ORDER BY due DESC LIMIT 1').fetchone(),
                                 ('skipped', reason))

    def test_schedule_passes_previous_day_at_0900_and_during_catch_up(self):
        self.assign()
        calls = []
        self.registry['overflow'] = (lambda p: None, lambda r,p: calls.append((r,dict(p))))
        self.run_tick(self.now + timedelta(minutes=14))
        self.assertEqual(calls, [('101', {'date': '1405/07/11'})])
        self.run_tick(self.now + timedelta(days=1))
        self.assertEqual(calls[-1], ('101', {'date': '1405/07/12'}))

    def test_schedule_forbids_fixed_report_date(self):
        raw = dict(self.schedule, params={'date': '1405/07/13'})
        self.config.write_text(yaml.safe_dump(dict(version=1, timezone='UTC', schedules=[raw])), encoding='utf-8')
        with self.assertRaisesRegex(ValueError, 'previous day'):
            load_config(self.config, self.registry)

    def test_previous_day_timezone_month_year_and_leap_boundaries(self):
        import jdatetime
        tehran = ZoneInfo('Asia/Tehran')
        cases = [('1405/07/13', '1405/07/12'), ('1405/07/01', '1405/06/31'),
                 ('1405/01/01', '1404/12/29'), ('1404/01/01', '1403/12/30')]
        for day, expected in cases:
            gregorian = jdatetime.date(*map(int, day.split('/'))).togregorian()
            due = datetime(gregorian.year,gregorian.month,gregorian.day,9,0,tzinfo=tehran)
            self.assertEqual(overflow_report_date(due), expected)
            self.assertEqual(overflow_report_date(due.astimezone(ZoneInfo('UTC'))), expected)
        # UTC is still the preceding Gregorian day, but Tehran is already tomorrow.
        self.assertEqual(overflow_report_date(datetime(2026,10,4,21,0,tzinfo=ZoneInfo('UTC'))), '1405/07/12')
        with self.assertRaises(ValueError):
            overflow_report_date(datetime(2026,10,5,9,0))

    def test_previous_day_caption_preserves_title_without_false_stale_warning(self):
        caption = overflow_report_caption('1405/07/12', '1405/07/12')
        self.assertEqual(caption, 'سرریز روزانه 1405/07/12')
        with self.assertRaises(ValueError):
            overflow_report_caption('1405/07/13', '1405/07/12')

    def test_missing_previous_day_never_falls_back_and_sends_only_notice(self):
        self.assign()
        worker = AsyncMock(return_value={'ok': False, 'message': 'missing exact date'})
        with patch('tools.scheduler.tasks.overflow_report_date', return_value='1405/07/11'), \
             patch('tools.scheduler.tasks.build_report', worker), patch('tools.scheduler.tasks.BaleSender') as sender:
            result = overflow('101', {}, authorization=self.auth)
            self.assertEqual(result['status'], 'waiting_for_data')
            self.assertEqual(result['reason'], 'date_missing')
            self.assertEqual(worker.call_args.args[0], '1405/07/11')
            sender.return_value.message.assert_called_once()
            sender.return_value.photo.assert_not_called()
            sender.return_value.document.assert_not_called()

    def test_wrong_day_worker_output_cannot_be_delivered(self):
        self.assign()
        worker = AsyncMock(return_value={'ok': True, 'images': ['unused'], 'report': {'date': '1405/07/13'}})
        with patch('tools.scheduler.tasks.build_report', worker), patch('tools.scheduler.tasks.BaleSender') as sender:
            with self.assertRaises(ValueError):
                overflow('101', {'date': '1405/07/12'}, authorization=self.auth)
            sender.assert_not_called()

    def test_fixed_recipient_overflow_rejected(self):
        for values in [dict(recipient='101'), dict(timezone='local'), dict(recipient_role='unknown')]:
            raw = dict(self.schedule, **values)
            self.config.write_text(yaml.safe_dump(dict(version=1, timezone='UTC', schedules=[raw])), encoding='utf-8')
            with self.assertRaises(ValueError):
                load_config(self.config, self.registry)

    def test_delivery_backend_denied_before_generator_and_sender(self):
        with patch('tools.scheduler.tasks.build_report') as worker, patch('tools.scheduler.tasks.BaleSender') as sender:
            for user in ('101', '202'):
                self.assertEqual(overflow(user, {}, authorization=self.auth)['status'], 'skipped')
            self.ambiguous()
            self.assertEqual(overflow('101', {}, authorization=self.auth)['reason'], 'ambiguous_holders')
            worker.assert_not_called()
            sender.assert_not_called()

    def test_authorized_daily_delivery_existing_generator_fake_transport(self):
        self.assign()
        async def worker(date, directory):
            path = Path(directory)/'fixture.png'
            path.write_bytes(b'fixture-image')
            return {'ok': True, 'images': [path], 'report': {'date': '1405/07/12'}}
        with patch('tools.scheduler.tasks.build_report', worker), patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertIsNone(overflow('101', {'date': '1405/07/12'}, authorization=self.auth))
            self.assertEqual(sender.return_value.photo.call_args.args[0], '101')
            sender.return_value.close.assert_called_once()

    def test_delivery_backend_holder_changed_during_generation_no_send(self):
        self.assign()
        async def worker(date, directory):
            self.assign('202', replace=True)
            return {'ok': True, 'images': ['unused'], 'report': {'date': '1405/07/12'}}
        with patch('tools.scheduler.tasks.build_report', worker), patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertEqual(overflow('101', {}, authorization=self.auth)['reason'], 'recipient_changed')
            sender.assert_not_called()

    def test_production_schedule_single_scoped_overflow_without_id(self):
        raw = yaml.safe_load((ROOT/'settings/schedules.yaml').read_text(encoding='utf-8'))
        jobs = [j for j in raw['schedules'] if j['task']=='overflow']
        self.assertEqual(len(jobs), 1)
        self.assertNotIn('recipient', jobs[0])
        self.assertEqual(jobs[0]['recipient_capability'], DAILY_RECEIVE)
        self.assertNotIn('recipient_role', jobs[0])
        self.assertEqual(jobs[0]['timezone'], 'Asia/Tehran')
        self.assertEqual(jobs[0]['trigger'], {'type': 'cron', 'hour': 9, 'minute': 0})

    def test_no_numeric_identity_in_business_source_or_generic_resource(self):
        paths = list((ROOT/'tools/authorization').glob('*.py')) + [
            ROOT/'tools/fleet/overflow/bale.py', ROOT/'tools/scheduler/tasks.py', ROOT/'tools/scheduler/runner.py']
        for path in paths:
            if path.name.startswith('test_'):
                continue
            text = path.read_text(encoding='utf-8-sig')
            tree = ast.parse(text)
            for node in ast.walk(tree):
                if isinstance(node, ast.Constant):
                    if isinstance(node.value, int):
                        self.assertLess(node.value, 10_000_000)
                    if isinstance(node.value, str):
                        self.assertFalse(node.value.isdigit() and len(node.value) > 6, path)
            self.assertNotIn('E:\\Function\\*', text)
            self.assertNotIn('execute_code', text)
            self.assertNotIn('register_tool(', text)


if __name__ == '__main__':
    unittest.main()
