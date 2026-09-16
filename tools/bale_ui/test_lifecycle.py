import asyncio
import unittest
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.bale_ui import Action, InlineKeyboardBuilder, KeyboardLifecycle, KeyboardPolicy
from tools.bale_ui import test_inline


class LifecycleTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.lifecycle = KeyboardLifecycle()
        self.builder = InlineKeyboardBuilder('reports', ((
            Action('next', 'بعدی', 'reports.read', frozenset({'OPEN'})),
        ),))
        self.bot = SimpleNamespace(edit_message_reply_markup=AsyncMock())
        self.gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=self.bot)})
        self.event = SimpleNamespace(source=SimpleNamespace(chat_id='42'),
            raw_message={'origin_message_id':'77'})
        self.revision = 'r1'
        self.events = []

    def accept(self, **overrides):
        def consume():
            self.revision = ''
            self.events.append('consume')
        options = dict(stage='OPEN', role='READER', permits=lambda p:True,
            consume=consume, persist=lambda:self.events.append('persist'),
            scope=('reports','42','42'), event=self.event, gateway=self.gateway,
            execute=lambda action:self.events.append('execute'), failed=lambda:self.events.append('failed'))
        options.update(overrides)
        return self.lifecycle.accept(self.builder, 'ik:reports:r1:next', self.revision, **options)

    async def settle(self):
        if self.lifecycle.tasks:
            await asyncio.gather(*list(self.lifecycle.tasks))

    async def test_consume_persist_remove_then_execute_and_suppress_concurrent_tap(self):
        async def edit(**kwargs):
            self.events.append('remove')
        self.bot.edit_message_reply_markup.side_effect = edit
        self.accept()
        self.assertEqual(self.events, ['consume','persist'])
        self.assertEqual(self.accept()['reason'], 'inline-transition-pending')
        await self.settle()
        self.assertEqual(self.events, ['consume','persist','remove','execute'])
        self.bot.edit_message_reply_markup.assert_awaited_once_with(chat_id='42', message_id=77, reply_markup=None)
        with self.assertRaises(ValueError):
            self.accept()

    async def test_invalid_revision_and_denied_permission_never_remove(self):
        with self.assertRaises(PermissionError):
            self.accept(permits=lambda p:False)
        self.revision = 'wrong'
        with self.assertRaises(ValueError):
            self.accept()
        self.bot.edit_message_reply_markup.assert_not_called()
        self.assertFalse(self.events)

    async def test_reusable_override_preserves_markup_and_revision(self):
        self.builder.policy = KeyboardPolicy.REUSABLE
        self.accept()
        await self.settle()
        self.accept()
        await self.settle()
        self.assertEqual(self.events, ['execute','execute'])
        self.assertEqual(self.revision, 'r1')
        self.bot.edit_message_reply_markup.assert_not_called()

    async def test_transient_cleanup_failure_retries_before_execution(self):
        self.bot.edit_message_reply_markup.side_effect = [TimeoutError(), None]
        self.accept()
        with patch('tools.bale_ui.lifecycle.asyncio.sleep', new=AsyncMock()):
            await self.settle()
        self.assertEqual(self.bot.edit_message_reply_markup.await_count, 2)
        self.assertEqual(self.events, ['consume','persist','execute'])

    async def test_permanent_cleanup_failure_does_not_execute_business_operation(self):
        self.bot.edit_message_reply_markup.side_effect = TimeoutError()
        self.accept()
        with patch('tools.bale_ui.lifecycle.asyncio.sleep', new=AsyncMock()), \
             self.assertLogs('tools.bale_ui.lifecycle', level='ERROR'):
            await self.settle()
        self.assertEqual(self.events, ['consume','persist','failed'])
        self.assertFalse(self.lifecycle.pending)

    async def test_already_removed_message_allows_transition(self):
        self.bot.edit_message_reply_markup.side_effect = RuntimeError('Message is not modified')
        self.accept()
        await self.settle()
        self.assertIn('execute', self.events)

    async def test_expiration_removes_markup_without_sending_expired_warning(self):
        self.lifecycle.bind('scope', self.gateway, '42', '77', ttl=0)
        await asyncio.sleep(0.01)
        await self.settle()
        self.bot.edit_message_reply_markup.assert_awaited_once()
        self.assertFalse(self.lifecycle.messages)
        self.assertFalse(self.events)

    async def test_replacement_cancels_old_timer_and_reusable_has_no_timer(self):
        self.lifecycle.bind('scope', self.gateway, '42', '77', ttl=0)
        await self.lifecycle.retire('scope', self.gateway)
        self.lifecycle.bind('scope', self.gateway, '42', '78', ttl=0, policy=KeyboardPolicy.REUSABLE)
        await asyncio.sleep(0.01)
        self.assertEqual(self.lifecycle.messages['scope'], ('42','78'))
        self.bot.edit_message_reply_markup.assert_awaited_once()


class WorkOrderLifecycleTests(test_inline.InlineFlowTests):
    async def test_callback_removes_markup_before_stage_change(self):
        data = self.data('add')
        edits = []
        async def edit(**kwargs):
            self.assertEqual(self.session.stage, 'PROPOSAL')
            self.assertFalse(self.session.keyboard_revision)
            edits.append(kwargs)
        bot = SimpleNamespace(edit_message_reply_markup=edit, send_message=AsyncMock())
        gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=bot)})
        event = SimpleNamespace(text=data, message_id='cb:1',
            raw_message={'bale_inline_callback':True,'data':data,'origin_message_id':'77'},
            source=SimpleNamespace(platform='bale',chat_type='dm',user_id=self.key[1],chat_id=self.key[2]))
        self.handler.handle(event, gateway, send=lambda *a:None)
        duplicate = self.handler.handle(event, gateway, send=lambda *a:None)
        self.assertEqual(duplicate['reason'], 'inline-transition-pending')
        await asyncio.gather(*list(self.handler.lifecycle.tasks))
        if self.handler.tasks:
            await asyncio.gather(*list(self.handler.tasks))
        self.assertEqual(len(edits), 1)
        self.assertEqual(self.session.stage, 'ADD_CODES')
        self.assertNotIn('منقضی', bot.send_message.call_args.kwargs['text'])
        self.assertEqual(self.handler.handle(event, gateway, send=lambda *a:None)['reason'], 'inline-duplicate-callback')

    async def test_permission_error_does_not_claim_expiration(self):
        self.click(self.data('add'), user='1004')
        self.assertIn('اجازه', self.replies[-1])
        self.assertNotIn('منقضی', self.replies[-1])

    async def test_text_cancel_retires_old_keyboard_before_reply(self):
        order = []
        async def edit(**kwargs):
            order.append('remove')
        async def send_message(**kwargs):
            order.append('reply')
        gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(
            _bot=SimpleNamespace(edit_message_reply_markup=edit, send_message=send_message))})
        self.handler.lifecycle.bind(self.key, gateway, self.key[2], '77', ttl=600)
        event = SimpleNamespace(text='انصراف', source=SimpleNamespace(
            platform='bale',chat_type='dm',user_id=self.key[1],chat_id=self.key[2]))
        self.handler.handle(event, gateway, send=lambda *a:None)
        await asyncio.gather(*list(self.handler.tasks))
        self.assertEqual(order, ['remove', 'reply'])
        self.assertFalse(self.handler.lifecycle.messages)

    async def test_expired_saved_keyboard_is_cleaned_on_gateway_restore(self):
        self.session.keyboard_message_id = '77'
        self.handler._persist()
        self.now = 1700
        self.handler = self.new_handler()
        bot = SimpleNamespace(edit_message_reply_markup=AsyncMock(), send_message=AsyncMock())
        gateway = SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=bot)})
        event = SimpleNamespace(text='سلام', source=SimpleNamespace(
            platform='bale',chat_type='dm',user_id=self.key[1],chat_id=self.key[2]))
        self.handler.handle(event, gateway, send=lambda *a:None)
        await asyncio.sleep(0.01)
        if self.handler.lifecycle.tasks:
            await asyncio.gather(*list(self.handler.lifecycle.tasks))
        bot.edit_message_reply_markup.assert_awaited_once_with(chat_id=self.key[2], message_id=77, reply_markup=None)
        bot.send_message.assert_not_called()
