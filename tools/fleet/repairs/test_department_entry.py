"""Department boundaries on the existing writer and lifecycle, with isolated files."""
import asyncio
from contextlib import closing
import json
import sqlite3
import unittest
import uuid
from unittest.mock import patch

import openpyxl
from tools.authorization import AuthorizationStore
from tools.authorization.net import repairs_sections
from . import test_entry_service as service_tests, test_entry_bale as ui_tests
from .entry_service import preview, commit, EntryError, EntryAuthorizationDenied
from .entry_bale import RepairsEntryHandler, sections_for

DEPARTMENTS = {'654806764': 'mechanical', '1732374823': 'mechanical', '387679249': 'metalwork'}


class DepartmentWriterTests(unittest.TestCase):
    book = service_tests.EntryServiceTests.book

    def setUp(self):
        service_tests.EntryServiceTests.setUp(self)
        # Copy authorization to a temporary fixture; never mutate live roles.
        live = AuthorizationStore()
        self.auth = AuthorizationStore(self.root/'auth.sqlite3')
        with closing(sqlite3.connect(live.path.resolve().as_uri()+'?mode=ro', uri=True)) as src, closing(sqlite3.connect(self.auth.path)) as dst:
            src.backup(dst)
        p = patch('tools.authorization.net.AuthorizationStore', return_value=self.auth)
        p.start(); self.addCleanup(p.stop)
        settings = json.loads(self.config.read_text(encoding='utf8'))
        settings['department_sections'] = DEPARTMENTS
        self.config.write_text(json.dumps(settings), encoding='utf8')

    def request(self, actor, section, text='fault'):
        return dict(preview(actor, '332', section, **self.kw), description=text, operation=uuid.uuid4().hex)

    def save(self, actor, request):
        return commit(actor, request, runtime=self.root/'state', **self.kw)

    def test_five_users_matrix_and_forged_worker_requests(self):
        for actor, allowed in DEPARTMENTS.items():
            other = 'metalwork' if allowed == 'mechanical' else 'mechanical'
            self.save(actor, self.request(actor, allowed, actor))
            self.save(actor, self.request(actor, allowed, actor+' edited'))
            with self.assertRaises(EntryAuthorizationDenied):
                preview(actor, '332', other, **self.kw)
            forged = self.request(actor, allowed)
            forged['section'] = other
            before = self.source.read_bytes()
            with self.assertRaises(EntryAuthorizationDenied):
                self.save(actor, forged)
            self.assertEqual(before, self.source.read_bytes())
            with self.assertRaises(EntryAuthorizationDenied):
                self.save(actor, self.request(actor, allowed, ''))
        for actor in ['641220453', '1294822197']:
            for section in ['mechanical', 'metalwork']:
                self.save(actor, self.request(actor, section, 'NET '+actor))

    def test_two_pending_sections_share_row_conflict_retry_audit_backup(self):
        mechanical = self.request('654806764', 'mechanical', 'engine')
        metalwork = self.request('387679249', 'metalwork', 'welding')
        stale = self.request('1732374823', 'mechanical', 'stale')
        self.save('654806764', mechanical)
        result = self.save('387679249', metalwork)
        with self.assertRaises(EntryError):
            self.save('1732374823', stale)
        before = self.source.read_bytes()
        self.assertEqual(result, self.save('387679249', metalwork))
        self.assertEqual(before, self.source.read_bytes())
        book = openpyxl.load_workbook(self.source)
        try:
            sheet = book.worksheets[0]
            self.assertEqual(sheet.max_row, 3)
            self.assertEqual([sheet['D3'].value, sheet['E3'].value], ['engine', 'welding'])
        finally:
            book.close()
        with closing(sqlite3.connect(self.root/'state/audit.sqlite3')) as con:
            rows = con.execute('SELECT actor,request,status,backup FROM edits').fetchall()
        self.assertEqual(len(rows), 2)
        for actor, request, status, backup in rows:
            from pathlib import Path
            self.assertEqual(status, 'succeeded')
            self.assertTrue(Path(backup).is_file())
            self.assertIn('expected', json.loads(request))

    def test_revoke_role_after_preview_and_at_replace(self):
        req = self.request('654806764', 'mechanical')
        import tools.fleet.repairs.entry_service as service
        original = service.permission
        calls = 0
        def check(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 3:
                with closing(sqlite3.connect(self.auth.path)) as con, con:
                    con.execute("UPDATE auth_user_roles SET active=0 WHERE user_id='654806764'")
            return original(*args, **kwargs)
        before = self.source.read_bytes()
        with patch.object(service, 'permission', side_effect=check):
            with self.assertRaises(EntryAuthorizationDenied):
                self.save('654806764', req)
        self.assertEqual(calls, 3)
        self.assertEqual(before, self.source.read_bytes())


class DepartmentUITests(ui_tests.EntryBaleTests):
    async def test_department_direct_entry_edit_exit_and_forged_callbacks(self):
        for actor, section in DEPARTMENTS.items():
            self.handler.authorize = lambda u: u in DEPARTMENTS
            self.handler.sections = lambda u: (DEPARTMENTS[u],) if u in DEPARTMENTS else ()
            self.key = (actor, actor)
            await self.deliver(self.event('شرح خرابی', actor=actor))
            self.assertEqual(self.handler.sessions[self.key]['stage'], 'CODE')
            self.assertEqual(self.handler.sessions[self.key]['section'], section)
            self.assertEqual(self.messages[-1]['text'], 'کد دستگاه را وارد کنید.')
            labels = [b['text'] for row in self.messages[-1]['reply_markup']['inline_keyboard'] for b in row]
            self.assertEqual(labels, ['🚪 خروج'])
            other = 'metalwork' if section == 'mechanical' else 'mechanical'
            await self.deliver(self.action_event(other, actor=actor))
            await self.deliver(self.event('شرح معایب آهنگری' if other == 'metalwork' else 'شرح معایب مکانیکی', actor=actor))
            self.assertEqual(self.handler.sessions[self.key]['section'], section)
            await self.deliver(self.event('469', actor=actor))
            await self.deliver(self.action_event('clear', actor=actor))
            self.assertEqual(self.handler.sessions[self.key]['stage'], 'DESCRIPTION')
            await self.deliver(self.event('fault', actor=actor))
            await self.deliver(self.action_event('edit', actor=actor))
            self.assertEqual(self.handler.sessions[self.key]['section'], section)
            await self.deliver(self.event('edited', actor=actor))
            confirm = self.action_event('confirm', actor=actor)
            await self.deliver(confirm)
            self.assertEqual(self.handler.sessions[self.key]['stage'], 'CODE')
            calls = len(self.calls)
            confirm.message_id += '-replay'
            await self.deliver(confirm)
            self.assertEqual(calls, len(self.calls))
            await self.deliver(self.event('🚪 خروج', actor=actor))
            self.assertNotIn(self.key, self.handler.sessions)

    async def test_live_menu_and_scopes_for_five_users(self):
        from tools.bale_ui.runtime import _main_menu
        for actor, section in DEPARTMENTS.items():
            self.assertEqual(sections_for(actor), (section,))
            menu = _main_menu(actor, bale_approved=True)
            self.assertIn('🛠 شرح خرابی', [b.text for row in menu.rows for b in row])
        for actor in ['641220453', '1294822197']:
            self.assertEqual(sections_for(actor), ('mechanical', 'metalwork'))
            menu = _main_menu(actor, bale_approved=True)
            self.assertNotIn('🛠 شرح خرابی', [b.text for row in menu.rows for b in row])


if __name__ == '__main__':
    unittest.main()
