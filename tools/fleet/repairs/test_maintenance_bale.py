import asyncio
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from tools.bale_ui import StateStore
from .maintenance_bale import MaintenanceEntryHandler, ROOT


class MaintenanceBaleTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        parent = ROOT/'runtime/maintenance-ui-tests'
        parent.mkdir(parents=True, exist_ok=True)
        temp = tempfile.TemporaryDirectory(dir=parent)
        self.addCleanup(temp.cleanup)
        self.store = StateStore(Path(temp.name)/'state.json')
        self.messages, self.calls = [], []
        self.now, self.counter = 1000, 0
        self.key = ('455740857', '455740857')
        self.handler = self.make_handler()
        async def send_message(**kwargs):
            self.messages.append(kwargs)
            return SimpleNamespace(message_id=len(self.messages))
        bot = SimpleNamespace(send_message=AsyncMock(side_effect=send_message), edit_message_reply_markup=AsyncMock())
        self.gateway = SimpleNamespace(adapters={'bale': SimpleNamespace(_bot=bot)})
        mock_telegram = SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda value, bot: value))
        patcher = patch.dict('sys.modules', {'telegram': mock_telegram})
        patcher.start()
        self.addCleanup(patcher.stop)

    def make_handler(self):
        return MaintenanceEntryHandler(state_store=self.store, run=self.worker, clock=lambda: self.now)

    async def asyncTearDown(self):
        for timer in self.handler.lifecycle.timers.values():
            timer.cancel()

    async def worker(self, payload):
        self.calls.append(payload)
        if payload['action'] == 'preview':
            return {'ok': True, 'result': {'sheet': '710', 'name': 'دامپ 710', 'code': '710',
                                           'canonical': 'HD710', 'date': '1405/06/26'}}
        return {'ok': True, 'result': {**payload['request'], 'row': 7}}

    def event(self, text='', actor='455740857', raw=None):
        self.counter += 1
        return SimpleNamespace(text=text, message_id=str(self.counter), raw_message=raw,
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id=actor, chat_id=actor))

    def fallback(self, gateway, chat, text):
        self.messages.append({'text': text})

    async def deliver(self, event):
        result = self.handler.handle(event, self.gateway, send=self.fallback)
        await asyncio.gather(*list(self.handler.tasks))
        return result

    def button(self, action):
        session = self.handler.sessions[self.key]
        return self.event(raw={'bale_inline_callback': True,
            'data': f"ik:maintenance_entry:{session['revision']}:{action}",
            'origin_message_id': session['message_id']})

    async def form(self):
        for text in ['تعمیرات', 'نام تعمیرکار', '710', 'شرح خط اول\nشرح خط دوم']:
            await self.deliver(self.event(text))

    async def test_full_flow_confirm_once_and_next_record(self):
        await self.form()
        await self.deliver(self.button('no_parts'))
        self.assertEqual([p['action'] for p in self.calls], ['preview'])
        confirm = self.button('confirm')
        await self.deliver(confirm)
        self.assertEqual(self.calls[-1]['request']['parts'], 'مصرف نشده')
        self.assertIn('\n', self.calls[-1]['request']['description'])
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'MECHANIC')
        await self.deliver(confirm)
        self.assertEqual(len(self.calls), 2)

    async def test_permissions_group_and_other_actor_callback(self):
        await self.deliver(self.event('تعمیرات', actor='641220453'))
        self.assertFalse(self.handler.sessions)
        await self.deliver(self.event('تعمیرات', actor='654806764'))
        self.assertIn(('654806764', '654806764'), self.handler.sessions)
        event = self.event('تعمیرات')
        event.source.chat_type = 'group'
        self.assertIsNone(await self.deliver(event))
        await self.form()
        await self.deliver(self.event('قطعه'))
        stolen = self.button('confirm')
        stolen.source.user_id = '654806764'
        await self.deliver(stolen)
        self.assertEqual([p['action'] for p in self.calls], ['preview'])

    async def test_wrong_origin_edit_cancel_and_expiry(self):
        await self.form()
        await self.deliver(self.event('قطعه'))
        wrong = self.button('confirm')
        wrong.raw_message['origin_message_id'] = '999'
        await self.deliver(wrong)
        self.assertEqual(len(self.calls), 1)
        await self.deliver(self.button('edit'))
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'MECHANIC')
        await self.deliver(self.event('انصراف'))
        self.assertFalse(self.handler.sessions)
        await self.form()
        self.now += 1801
        await self.deliver(self.event('قطعه'))
        self.assertFalse(self.handler.sessions)
        self.assertEqual([p['action'] for p in self.calls], ['preview', 'preview'])

    async def test_restore_busy_confirmation_preserves_operation(self):
        await self.form()
        await self.deliver(self.event('قطعه'))
        session = self.handler.sessions[self.key]
        operation = session['request']['operation']
        session['stage'] = 'BUSY'
        self.handler.persist()
        restored = self.make_handler()
        self.assertEqual(restored.sessions[self.key]['stage'], 'CONFIRM')
        self.assertEqual(restored.sessions[self.key]['request']['operation'], operation)

    async def test_worker_failure_retries_same_request(self):
        await self.form()
        await self.deliver(self.event('قطعه'))
        operation = self.handler.sessions[self.key]['request']['operation']
        self.handler.run = AsyncMock(return_value={'ok': False, 'message': 'file locked'})
        await self.deliver(self.button('confirm'))
        self.assertEqual(self.handler.sessions[self.key]['stage'], 'CONFIRM')
        self.handler.run = self.worker
        await self.deliver(self.button('confirm'))
        self.assertEqual(self.calls[-1]['request']['operation'], operation)

    async def test_runtime_switches_forms_and_routes_callbacks(self):
        from tools.bale_ui import runtime
        from .entry_bale import RepairsEntryHandler
        defect = RepairsEntryHandler(run=self.worker, authorize=lambda actor: True, clock=lambda: self.now)
        with patch('tools.fleet.repairs.maintenance_bale._handler', self.handler), \
             patch('tools.fleet.repairs.entry_bale._handler', defect), \
             patch.object(runtime.router, 'handlers', {}):
            for text, handler in [('شرح خرابی', defect), ('تعمیرات', self.handler), ('شرح خرابی', defect)]:
                runtime.dispatch(self.event(text), self.gateway, send=self.fallback)
                await asyncio.gather(*list(handler.tasks))
                other = defect if handler is self.handler else self.handler
                self.assertTrue(handler.active_for(*self.key))
                self.assertFalse(other.active_for(*self.key))
            for timer in defect.lifecycle.timers.values():
                timer.cancel()


if __name__ == '__main__':
    unittest.main()
