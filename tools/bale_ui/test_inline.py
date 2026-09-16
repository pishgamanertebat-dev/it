import asyncio
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from tools.bale_ui import Action, InlineKeyboardBuilder, Router, StateStore
from tools.fleet.work_orders.channels.bale.message_handler import FormSession, WorkOrderMenuHandler
from tools.fleet.work_orders.dev.test_permissions import PermissionDatabaseTestCase, test_database


class BuilderTests(unittest.TestCase):
    def test_other_domains_and_roles_and_execution_permissions(self):
        builder = InlineKeyboardBuilder('repairs', ((
            Action('close', 'بستن', 'repair.close', frozenset({'OPEN'}), frozenset({'TECH'})),
        ),))
        context = dict(stage='OPEN', role='TECH', permits=lambda p: p == 'repair.close')
        markup = builder.build('revision', **context)
        data = markup['inline_keyboard'][0][0]['callback_data']
        self.assertEqual(builder.resolve(data, 'revision', **context), 'close')
        for change in ({'role':'MANAGER'}, {'stage':'CLOSED'}, {'permits':lambda p: False}):
            denied = {**context, **change}
            self.assertEqual(builder.build('revision', **denied)['inline_keyboard'], [])
            with self.assertRaises(PermissionError):
                builder.resolve(data, 'revision', **denied)
        with self.assertRaises(ValueError):
            builder.resolve(data, 'new_revision', **context)
        with self.assertRaises(ValueError):
            builder.build('x' * 64, **context)

    def test_unknown_callbacks_are_consumed_but_normal_text_is_not(self):
        router = Router()
        event = SimpleNamespace(raw_message={'bale_inline_callback':True,'data':'ik:missing:x:y'},
                                source=SimpleNamespace(chat_id='1'))
        replies = []
        self.assertEqual(router.dispatch(event, None, send=lambda *a: replies.append(a))['action'], 'skip')
        event.raw_message = None
        self.assertIsNone(router.dispatch(event, None, send=lambda *a: None))
        self.assertEqual(len(replies), 1)


class InlineFlowTests(PermissionDatabaseTestCase, unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        super().setUp()
        self.now = 1000
        self.store = StateStore(self.db_path.parent / 'ui.json')
        self.handler = self.new_handler()
        self.key = ('bale', '455740857', '455740857')
        self.session = FormSession(expires=1600, stage='PROPOSAL', work_order_type='GREASING',
            jalali_date='1405/06/16', proposal={'items':[{'machine_code':'HD715','action_code':'GREASING_FULL'}],
                'cutoff':'1405/06/15','plan_date':'1405/06/16','source_sha256':'test'})
        self.handler.pending[self.key] = self.session
        self.replies = []

    def new_handler(self):
        return WorkOrderMenuHandler(db_path=self.db_path, clock=lambda:self.now, state_store=self.store)

    def data(self, action):
        markup = self.handler._keyboard(self.key, self.session)
        self.handler._persist()
        return next(b['callback_data'] for row in markup['inline_keyboard'] for b in row
                    if b['callback_data'].endswith(':' + action))

    def click(self, data, user='455740857', chat='455740857'):
        self.callback_count = getattr(self, 'callback_count', 0) + 1
        event = SimpleNamespace(text=data, message_id=f'callback:test:{self.callback_count}',
            raw_message={'bale_inline_callback':True, 'data':data},
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id=user, chat_id=chat))
        return self.handler.handle(event, None, send=lambda *args:self.replies.append(args[-1]))

    async def test_add_and_remove_use_existing_stages(self):
        for action, stage in [('add','ADD_CODES'), ('remove','REMOVE')]:
            self.session.stage = 'PROPOSAL'
            self.handler.processed.clear()
            self.click(self.data(action))
            self.assertEqual(self.session.stage, stage)
            restored = self.new_handler()
            self.assertEqual(restored.pending[self.key].stage, stage)

    async def test_confirm_duplicate_creates_only_one_job(self):
        calls = []
        gate = asyncio.Event()
        async def worker(request):
            calls.append(request)
            await gate.wait()
            return {'ok':False, 'message':'test result'}
        self.handler.worker = worker
        data = self.data('confirm')
        self.click(data)
        self.assertEqual(self.session.stage, 'BUSY')
        self.assertEqual(self.click(data)['reason'], 'inline-rejected')
        await asyncio.sleep(0)
        self.assertEqual(len(calls), 1)
        self.assertEqual(calls[0]['machine_codes'], ['HD715'])
        restored = self.new_handler()
        self.assertEqual(restored.pending[self.key].stage, 'RESULT')
        self.assertFalse(restored.pending[self.key].keyboard_revision)
        gate.set()
        await asyncio.gather(*list(self.handler.tasks))

    async def test_air_filter_confirmation_keeps_shift_step(self):
        self.session.work_order_type = 'AIR_FILTER'
        self.click(self.data('confirm'))
        self.assertEqual(self.session.stage, 'SHIFT')
        self.assertFalse(self.handler.tasks)

    async def test_cancel_persists_and_invalidates_keyboard(self):
        data = self.data('cancel')
        self.click(data)
        self.assertFalse(self.handler.pending)
        self.assertFalse(self.new_handler().pending)
        self.assertEqual(self.click(data)['reason'], 'inline-rejected')

    async def test_expired_wrong_user_chat_and_revoked_are_rejected(self):
        data = self.data('confirm')
        self.assertEqual(self.click(data, user='1004')['reason'], 'inline-rejected')
        self.assertEqual(self.click(data, chat='other')['reason'], 'inline-rejected')
        with test_database(self.db_path) as con:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='455740857'")
        self.assertEqual(self.click(data)['reason'], 'inline-rejected')
        self.assertEqual(self.session.stage, 'PROPOSAL')
        self.now = 1601
        self.assertFalse(self.new_handler().pending)

    async def test_restored_form_accepts_current_button_but_not_old_revision(self):
        old = self.data('add')
        current = self.data('remove')
        self.handler = self.new_handler()
        self.assertEqual(self.click(old)['reason'], 'inline-rejected')
        self.click(current)
        self.assertEqual(self.handler.pending[self.key].stage, 'REMOVE')

    async def test_interrupted_job_recovery_previews_instead_of_creating(self):
        self.session.stage = 'BUSY'
        self.handler._persist()
        self.handler = self.new_handler()
        requests = []
        async def worker(request):
            requests.append(request)
            return {'ok':False, 'message':'No previous order'}
        self.handler.worker = worker
        event = SimpleNamespace(text='ارسال مجدد', source=SimpleNamespace(
            platform='bale', chat_type='dm', user_id=self.key[1], chat_id=self.key[2]))
        result = self.handler.handle(event, None, send=lambda *args:None)
        self.assertEqual(result['reason'], 'work-order-review')
        await asyncio.gather(*list(self.handler.tasks))
        self.assertEqual([r['action'] for r in requests], ['preview_latest'])

    async def test_empty_proposal_does_not_create_and_offers_new_keyboard(self):
        self.session.proposal['items'] = []
        data = self.data('confirm')
        result = self.click(data)
        self.assertEqual(result['reason'], 'work-order-input-rejected')
        self.assertEqual(self.session.stage, 'PROPOSAL')
        self.assertTrue(self.session.keyboard_revision)
        self.assertFalse(self.handler.tasks)

    async def test_expired_and_corrupt_state_fail_closed(self):
        data = self.data('add')
        self.now = 1601
        self.assertEqual(self.click(data)['reason'], 'inline-rejected')
        self.store.path.write_text('{broken', encoding='utf-8')
        with self.assertLogs('tools.fleet.work_orders.channels.bale.message_handler', level='ERROR'):
            restored = self.new_handler()
        self.assertFalse(restored.pending)

    async def test_markup_is_attached_only_to_last_chunk(self):
        delivered = []
        async def send_message(**kwargs):
            delivered.append(kwargs)
        gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=SimpleNamespace(send_message=send_message))})
        # The project test environment need not install Hermes' Telegram SDK.
        markup_type = SimpleNamespace(de_json=lambda markup, bot:markup)
        with patch.dict('sys.modules', {'telegram':SimpleNamespace(InlineKeyboardMarkup=markup_type)}):
            task = self.handler._send_reply(gateway, self.key[2], ('دستگاه\n' * 1000), lambda *a:None, key=self.key)
            await task
        self.assertGreater(len(delivered), 1)
        self.assertTrue(all('reply_markup' not in m for m in delivered[:-1]))
        buttons = delivered[-1]['reply_markup']['inline_keyboard']
        self.assertEqual(sum(map(len, buttons)), 4)
        self.assertEqual(buttons[0][0]['text'], '➕ افزودن دستگاه')


if __name__ == '__main__':
    unittest.main()
