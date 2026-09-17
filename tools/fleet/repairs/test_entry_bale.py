import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tools.bale_ui import StateStore
from .entry_bale import RepairsEntryHandler, ROOT


class EntryBaleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        directory = ROOT/'runtime/repairs-ui-tests'
        directory.mkdir(parents=True, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=directory)
        self.addCleanup(self.temp.cleanup)
        self.messages = []
        self.calls = []
        self.counter = 0
        self.now = 1000
        self.store = StateStore(Path(self.temp.name)/'state.json')
        self.handler = self.make_handler()
        self.key = ('641220453', '641220453')
        async def send_message(**kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(message_id=len(self.messages))
        self.bot = SimpleNamespace(send_message=AsyncMock(side_effect=send_message), edit_message_reply_markup=AsyncMock())
        self.gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=self.bot)})
        telegram = SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda value, bot: value))
        self.patcher = patch.dict('sys.modules', {'telegram': telegram})
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    async def asyncTearDown(self):
        for timer in self.handler.lifecycle.timers.values():
            timer.cancel()

    def make_handler(self):
        return RepairsEntryHandler(state_store=self.store, run=self.run_worker,
            authorize=lambda actor: actor in {'641220453', '455740857'}, clock=lambda: self.now)

    async def run_worker(self, payload):
        self.calls.append(payload)
        if payload['action'] == 'preview':
            return {'ok': True, 'result': {'code': 'HD710', 'name': 'دامپتراک', 'date': '1405/06/26',
                'section': payload['section'], 'expected': 'previous description'}}
        return {'ok': True, 'result': payload['request']}

    def event(self, text='', raw=None, actor='641220453', chat=None):
        self.counter += 1
        return SimpleNamespace(text=text, raw_message=raw, message_id=str(self.counter),
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id=actor, chat_id=chat or actor))

    def fallback(self, gateway, chat, text):
        self.messages.append({'chat_id':chat, 'text':text})

    async def deliver(self, event):
        result = self.handler.handle(event, self.gateway, send=self.fallback)
        await asyncio.gather(*list(self.handler.tasks))
        return result

    def action_event(self, action, actor='641220453', chat=None):
        session = self.handler.sessions[self.key]
        return self.event(raw={'bale_inline_callback':True,
            'data':f"ik:repairs_entry:{session['revision']}:{action}",
            'origin_message_id':session['message_id']}, actor=actor, chat=chat)

    async def start_description(self):
        await self.deliver(self.event('شرح خرابی'))
        await self.deliver(self.action_event('mechanical'))
        await self.deliver(self.event('710'))

    async def test_full_flow_requires_confirmation_then_next_device_and_other_section(self):
        await self.start_description()
        self.assertIn('previous description', self.messages[-1]['text'])
        await self.deliver(self.event('خرابی موتور'))
        self.assertEqual([p['action'] for p in self.calls], ['preview'])
        confirm = self.action_event('confirm')
        await self.deliver(confirm)
        self.assertEqual([p['action'] for p in self.calls], ['preview', 'commit'])
        self.assertEqual(self.calls[-1]['request']['description'], 'خرابی موتور')
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'CODE')
        await self.deliver(confirm)
        self.assertEqual(len(self.calls), 2)
        await self.deliver(self.event('شرح خرابی'))
        await self.deliver(self.action_event('metalwork'))
        await self.deliver(self.event('710'))
        self.assertEqual(self.calls[-1]['section'], 'metalwork')

    async def test_denied_group_and_forged_identity_never_run_worker(self):
        await self.deliver(self.event('شرح خرابی', actor='654806764'))
        await self.deliver(self.event('شرح خرابی', actor='', chat='641220453'))
        group = self.event('شرح خرابی')
        group.source.chat_type = 'group'
        self.assertIsNone(await self.deliver(group))
        self.assertEqual(self.calls, [])

    async def test_stale_wrong_origin_and_other_authorized_user_buttons_rejected(self):
        await self.deliver(self.event('شرح خرابی'))
        old = self.action_event('mechanical')
        wrong = self.action_event('mechanical')
        wrong.raw_message['origin_message_id'] = '999'
        await self.deliver(wrong)
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'SECTION')
        await self.deliver(self.action_event('mechanical', actor='455740857', chat='641220453'))
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'SECTION')
        await self.deliver(old)
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'CODE')
        old.message_id = 'old-again'
        await self.deliver(old)
        self.assertEqual(self.calls, [])

    async def test_edit_clear_and_finish_do_not_save_drafts(self):
        await self.start_description()
        await self.deliver(self.event('initial draft'))
        await self.deliver(self.action_event('edit'))
        await self.deliver(self.action_event('clear'))
        self.assertEqual(self.handler.sessions[self.key]['request']['description'], '')
        await self.deliver(self.action_event('finish'))
        self.assertNotIn(self.key, self.handler.sessions)
        self.assertEqual(len(self.calls), 1)

    async def test_restart_and_interrupted_commit_keep_operation_for_retry(self):
        await self.start_description()
        await self.deliver(self.event('new description'))
        operation = self.handler.sessions[self.key]['request']['operation']
        self.handler.sessions[self.key]['stage'] = 'BUSY'
        self.handler.persist()
        self.handler = self.make_handler()
        await self.deliver(self.event('تأیید'))
        self.assertEqual(self.calls[-1]['request']['operation'], operation)

    async def test_revoked_permission_and_expiry_prevent_commit(self):
        await self.start_description()
        await self.deliver(self.event('new description'))
        confirmation = self.action_event('confirm')
        self.handler.authorize = lambda actor: False
        await self.deliver(confirmation)
        self.assertEqual(len(self.calls), 1)
        self.handler.authorize = lambda actor: True
        self.now += 2000
        await self.deliver(self.action_event('confirm'))
        self.assertEqual(len(self.calls), 1)

    async def test_busy_preview_or_commit_tells_user_to_wait(self):
        await self.start_description()
        calls = len(self.calls)
        self.handler.busy.add(self.key)
        result = await self.deliver(self.event('HD710'))
        self.assertEqual(result['reason'], 'repairs-entry-busy')
        self.assertEqual(self.messages[-1]['text'], 'در حال ثبت درخواست قبلی هستم؛ چند لحظه صبر کنید.')
        self.assertEqual(len(self.calls), calls)
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'DESCRIPTION')

    async def test_switch_to_work_order_releases_form(self):
        await self.deliver(self.event('شرح خرابی'))
        self.assertIsNone(await self.deliver(self.event('حکم کار')))
        self.assertNotIn(self.key, self.handler.sessions)
        await asyncio.gather(*list(self.handler.lifecycle.tasks))

    def restart_handler(self):
        for timer in self.handler.lifecycle.timers.values():
            timer.cancel()
        self.handler = self.make_handler()

    async def drain_lifecycle(self):
        await asyncio.sleep(0.01)
        await asyncio.gather(*list(self.handler.lifecycle.tasks))

    async def test_restart_restores_idle_keyboard_with_remaining_expiry_once(self):
        await self.deliver(self.event('شرح خرابی'))
        saved = dict(self.handler.sessions[self.key])
        self.now += 1700
        self.restart_handler()
        # An unrelated user's message restores every saved keyboard.
        await self.deliver(self.event('hello', actor='455740857'))
        self.assertEqual(self.handler.lifecycle.messages[self.key], (self.key[1], saved['message_id']))
        timer = self.handler.lifecycle.timers[self.key]
        self.assertAlmostEqual(timer.when() - asyncio.get_running_loop().time(), 100, delta=1)
        await self.deliver(self.event('hello', actor='455740857'))
        self.assertIs(self.handler.lifecycle.timers[self.key], timer)
        self.assertEqual(self.handler.sessions[self.key], saved)
        self.bot.edit_message_reply_markup.assert_not_awaited()

    async def test_restart_removes_expired_and_interrupted_keyboards(self):
        for interrupted in (False, True):
            with self.subTest(interrupted=interrupted):
                await self.deliver(self.event('شرح خرابی'))
                message_id = self.handler.sessions[self.key]['message_id']
                if interrupted:
                    self.handler.sessions[self.key]['stage'] = 'BUSY'
                    self.handler.persist()
                else:
                    self.now += 2000
                self.restart_handler()
                await self.deliver(self.event('hello', actor='455740857'))
                await self.drain_lifecycle()
                self.bot.edit_message_reply_markup.assert_awaited_with(
                    chat_id=self.key[1], message_id=int(message_id), reply_markup=None)
                self.assertNotIn(self.key, self.handler.lifecycle.messages)
                if not interrupted:
                    self.assertNotIn(self.key, self.handler.sessions)
                self.assertEqual(self.calls, [])

    async def test_restart_then_work_order_retires_old_keyboard(self):
        await self.deliver(self.event('شرح خرابی'))
        message_id = self.handler.sessions[self.key]['message_id']
        self.restart_handler()
        self.assertIsNone(await self.deliver(self.event('حکم کار')))
        await self.drain_lifecycle()
        self.bot.edit_message_reply_markup.assert_awaited_with(
            chat_id=self.key[1], message_id=int(message_id), reply_markup=None)
        self.assertNotIn(self.key, self.handler.sessions)
        self.assertNotIn(self.key, self.handler.lifecycle.messages)


if __name__ == '__main__':
    unittest.main()
