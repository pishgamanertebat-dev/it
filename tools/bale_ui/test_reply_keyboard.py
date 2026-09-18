"""Reply-menu layer: audience, label-to-command routing, delivery and fallback.

No live gateway, Bale API, fleet database or operational file is touched here.
"""
import asyncio
import importlib
import json
import sqlite3
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, patch

from tools.bale_ui import REMOVE_MARKUP, ReplyButton, ReplyMenu, ReplyMenuPresenter, ReplyMenuRegistry, StateStore
from tools.bale_ui import reply_keyboard
from tools.bale_ui.reply_keyboard import load_registry, to_reply_markup
from tools.bale_ui import runtime
from tools.fleet.work_orders.core.permissions import MAINTENANCE_MANAGER, check_work_order_permission

create_schema = importlib.import_module(
    'tools.fleet.work_orders.migrations.002_create_work_order_permissions'
).create_schema

MANAGER = '641220453'
ADMIN = '455740857'
OUTSIDER = '111222333'
WORK_ORDER_LABEL = '📋 حکم کار'
REPAIRS_LABEL = '🛠 شرح خرابی'


def event(text, user_id=MANAGER, *, chat_id=None, raw=None, chat_type='dm', platform='bale'):
    return SimpleNamespace(text=text, message_id='1', raw_message=raw,
        source=SimpleNamespace(platform=platform, chat_type=chat_type,
                               user_id=user_id, chat_id=chat_id or user_id))


class ConfiguredMenuTests(unittest.TestCase):
    def test_both_configured_users_get_the_same_two_buttons(self):
        for user in (MANAGER, ADMIN):
            menu = runtime.reply_menus.menu_for(user)
            self.assertIsNotNone(menu, user)
            self.assertEqual([[button['text'] for button in row] for row in menu.to_markup()['keyboard']],
                             [[WORK_ORDER_LABEL, REPAIRS_LABEL]])

    def test_unconfigured_user_has_no_operational_menu(self):
        self.assertIsNone(runtime.reply_menus.menu_for(OUTSIDER))
        self.assertIsNone(runtime.reply_menus.menu_for(''))

    def test_markup_is_mobile_sized_persistent_and_free_of_callback_data(self):
        markup = runtime.reply_menus.menu_for(MANAGER).to_markup()
        self.assertTrue(markup['resize_keyboard'])
        self.assertTrue(markup['is_persistent'])
        self.assertFalse(markup['one_time_keyboard'])
        self.assertNotIn('callback_data', json.dumps(markup, ensure_ascii=False))
        self.assertNotIn('inline_keyboard', markup)

    def test_audience_is_configuration_not_code(self):
        source = Path(runtime.__file__).with_name('reply_keyboard.py').read_text(encoding='utf-8')
        for user in (MANAGER, ADMIN):
            self.assertNotIn(user, source)
        self.assertNotIn('حکم کار', source)

    def test_roles_and_extra_menus_need_no_core_change(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'menus.json'
            path.write_text(json.dumps({'menus': [
                {'menu_id': 'oil_reporter', 'roles': ['OIL_ANALYST'],
                 'rows': [[{'text': '🛢 گزارش روغن', 'command': 'گزارش روغن'}]]}]}), encoding='utf-8')
            registry = load_registry(path)
            self.assertIsNone(registry.menu_for('42'))
            menu = registry.menu_for('42', 'OIL_ANALYST')
            self.assertEqual(menu.command_for('🛢 گزارش روغن'), 'گزارش روغن')

    def test_invalid_definitions_are_ignored_without_breaking_the_layer(self):
        with TemporaryDirectory() as folder:
            path = Path(folder) / 'menus.json'
            path.write_text(json.dumps({'menus': [{'menu_id': 'Bad Id', 'rows': []},
                {'menu_id': 'good', 'users': ['7'], 'rows': [[{'text': 'الف', 'command': 'الف'}]]}]}),
                encoding='utf-8')
            with patch.object(reply_keyboard.logger, 'exception'):
                self.assertEqual(load_registry(path).menu_for('7').menu_id, 'good')
                self.assertEqual(load_registry(Path(folder) / 'missing.json').menus, {})


class LabelRoutingTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(runtime, '_reply_menu_role', return_value=None)
        self.addCleanup(patcher.stop)
        patcher.start()

    def step(self, message, gateway=None):
        with patch.object(runtime.reply_presenter, 'present', return_value=None) as present:
            result = runtime.reply_menu_step(message, gateway, send=lambda *a: None)
        return result, present

    def test_tapped_labels_become_the_existing_commands(self):
        for label, command in ((WORK_ORDER_LABEL, 'حکم کار'), (REPAIRS_LABEL, 'شرح خرابی')):
            message = event(label)
            self.assertIsNone(self.step(message)[0])
            self.assertEqual(message.text, command)

    def test_typed_commands_are_left_untouched(self):
        for command in ('حکم کار', 'شرح خرابی', 'تعمیرات', 'سلام'):
            message = event(command)
            self.assertIsNone(self.step(message)[0])
            self.assertEqual(message.text, command)

    def test_unauthorized_user_gets_neither_menu_nor_translation(self):
        message = event(WORK_ORDER_LABEL, OUTSIDER)
        result, present = self.step(message)
        self.assertIsNone(result)
        self.assertEqual(message.text, WORK_ORDER_LABEL)
        present.assert_not_called()

    def test_inline_callbacks_and_other_surfaces_are_never_intercepted(self):
        callback = event('ik:repairs_entry:rev:finish',
                         raw={'bale_inline_callback': True, 'data': 'ik:repairs_entry:rev:finish'})
        result, present = self.step(callback)
        self.assertIsNone(result)
        self.assertEqual(callback.text, 'ik:repairs_entry:rev:finish')
        present.assert_not_called()
        for other in (event(WORK_ORDER_LABEL, chat_type='group'),
                      event(WORK_ORDER_LABEL, platform='telegram')):
            self.assertIsNone(self.step(other)[0])
            self.assertEqual(other.text, WORK_ORDER_LABEL)
            self.step(other)[1].assert_not_called()

    def test_menu_trigger_reshows_the_keyboard_without_entering_a_flow(self):
        message = event('منو')
        result, present = self.step(message)
        self.assertEqual(result, {'action': 'skip', 'reason': 'reply-menu-shown'})
        self.assertTrue(present.call_args.kwargs['force'])


class DispatchIntegrationTests(unittest.TestCase):
    def setUp(self):
        patcher = patch.object(runtime, '_reply_menu_role', return_value=None)
        self.addCleanup(patcher.stop)
        patcher.start()

    def dispatch(self, message):
        calls = []

        def handler(name, command):
            def record(event_, gateway, *, send):
                calls.append((name, event_.text))
                # Mirror the real handlers: unrelated text falls through.
                return {'action': 'skip', 'reason': name} if event_.text == command else None
            return record

        from tools.fleet.repairs import entry_bale, maintenance_bale
        from tools.fleet.work_orders.channels.bale import message_handler
        with patch.object(runtime.reply_presenter, 'present', return_value=None), \
             patch.object(message_handler._handler, 'handle', handler('work_order', 'حکم کار')), \
             patch.object(entry_bale._handler, 'handle', handler('repairs_entry', 'شرح خرابی')), \
             patch.object(maintenance_bale._handler, 'handle', handler('maintenance_entry', 'تعمیرات')):
            result = runtime.dispatch(message, None, send=lambda *a: None)
            if result is None:
                # Mirror the installed bridge: unrouted text continues to the
                # work-order handler and only then to the assistant.
                result = message_handler.handle_work_order_message(message, None, send=lambda *a: None)
        return result, calls

    def test_tap_and_typing_reach_the_same_existing_handlers(self):
        for text in (WORK_ORDER_LABEL, 'حکم کار'):
            result, calls = self.dispatch(event(text))
            self.assertEqual(result['reason'], 'work_order')
            self.assertEqual(calls[-1], ('work_order', 'حکم کار'))
        for text in (REPAIRS_LABEL, 'شرح خرابی'):
            result, calls = self.dispatch(event(text))
            self.assertEqual(result['reason'], 'repairs_entry')
            self.assertIn(('repairs_entry', 'شرح خرابی'), calls)

    def test_ordinary_chat_still_reaches_the_assistant(self):
        message = event('روغن موتور HD714 چه زمانی عوض شده؟')
        self.assertIsNone(self.dispatch(message)[0])


class FakeBot:
    def __init__(self):
        self.send_message = AsyncMock(return_value=SimpleNamespace(message_id=5))


class DeliveryTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.store = StateStore(Path(self.folder.name) / 'reply_menu.json')
        self.bot = FakeBot()
        # The client object is covered by ClientFallbackTests; keep the raw markup here.
        markup = patch('tools.bale_ui.reply_keyboard.to_reply_markup', lambda markup, bot=None: markup)
        markup.start()
        self.addCleanup(markup.stop)
        self.menu = ReplyMenu('ops', ((ReplyButton(WORK_ORDER_LABEL, 'حکم کار'),),), users=frozenset({MANAGER}))

    def presenter(self):
        return ReplyMenuPresenter(runtime.reply_menus, state_store=self.store,
                                  bot_for=lambda gateway: self.bot, text='منو')

    async def test_menu_is_delivered_once_and_survives_a_gateway_restart(self):
        presenter = self.presenter()
        await presenter.present(None, MANAGER, MANAGER, self.menu)
        self.bot.send_message.assert_awaited_once()
        markup = self.bot.send_message.await_args.kwargs['reply_markup']
        self.assertEqual([[button['text'] for button in row] for row in markup['keyboard']],
                         [[WORK_ORDER_LABEL]])
        self.assertIsNone(presenter.present(None, MANAGER, MANAGER, self.menu))
        restarted = self.presenter()
        self.assertIsNone(restarted.present(None, MANAGER, MANAGER, self.menu))
        self.bot.send_message.assert_awaited_once()
        forced = restarted.present(None, MANAGER, MANAGER, self.menu, force=True)
        await forced
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_server_rejecting_persistent_still_gets_a_plain_keyboard(self):
        self.bot.send_message.side_effect = [RuntimeError("Bad Request: unknown field 'is_persistent'"),
                                             SimpleNamespace(message_id=5)]
        presenter = self.presenter()
        with patch.object(reply_keyboard.logger, 'warning'):
            await presenter.present(None, MANAGER, MANAGER, self.menu)
        markups = [call.kwargs['reply_markup'] for call in self.bot.send_message.await_args_list]
        self.assertTrue(markups[0]['is_persistent'])
        self.assertNotIn('is_persistent', markups[1])
        self.assertTrue(markups[1]['resize_keyboard'])
        self.assertEqual(presenter.delivered, {(MANAGER, MANAGER): self.menu.fingerprint()})

    async def test_changed_menu_definition_is_redelivered(self):
        presenter = self.presenter()
        await presenter.present(None, MANAGER, MANAGER, self.menu)
        extended = ReplyMenu('ops', (self.menu.rows[0] + (ReplyButton(REPAIRS_LABEL, 'شرح خرابی'),),),
                             users=frozenset({MANAGER}))
        await presenter.present(None, MANAGER, MANAGER, extended)
        self.assertEqual(self.bot.send_message.await_count, 2)

    async def test_missing_transport_keeps_state_clean_and_falls_back_to_text(self):
        replies = []
        presenter = ReplyMenuPresenter(runtime.reply_menus, state_store=self.store,
                                       bot_for=lambda gateway: None, text='منو')
        self.assertIsNone(presenter.present(None, MANAGER, MANAGER, self.menu,
                                            send=lambda g, c, t: replies.append((c, t)), force=True))
        self.assertEqual(replies, [(MANAGER, 'منو')])
        self.assertEqual(presenter.delivered, {})

    async def test_transient_or_generic_errors_do_not_fallback_without_persistent(self):
        presenter = self.presenter()
        errors = (
            TimeoutError('timed out'),
            type('NetworkError', (Exception,), {})('connection reset'),
            RuntimeError('Too Many Requests: retry after 30'),
            RuntimeError('Bad Request: unknown field'),
        )
        for error in errors:
            with self.subTest(error=error):
                self.bot.send_message.reset_mock()
                self.bot.send_message.side_effect = error
                with patch.object(reply_keyboard.logger, 'exception'):
                    await presenter.present(None, MANAGER, MANAGER, self.menu)
                self.bot.send_message.assert_awaited_once()
                self.assertTrue(self.bot.send_message.await_args.kwargs['reply_markup']['is_persistent'])
                self.assertEqual(presenter.delivered, {})

    async def test_removal_is_available_for_replacing_or_revoking_a_menu(self):
        presenter = self.presenter()
        await presenter.present(None, MANAGER, MANAGER, self.menu)
        await presenter.remove(None, MANAGER, MANAGER, text='منو حذف شد')
        self.assertEqual(self.bot.send_message.await_args.kwargs['reply_markup'], REMOVE_MARKUP)
        self.assertEqual(presenter.delivered, {})

    async def test_failed_removal_keeps_delivered_for_retry_after_restart(self):
        presenter = self.presenter()
        await presenter.present(None, MANAGER, MANAGER, self.menu)
        fingerprint = self.menu.fingerprint()
        self.bot.send_message.side_effect = RuntimeError('network down')
        with patch.object(reply_keyboard.logger, 'exception'):
            await presenter.remove(None, MANAGER, MANAGER, text='منو حذف شد')
        self.assertEqual(presenter.delivered, {(MANAGER, MANAGER): fingerprint})
        restarted = self.presenter()
        self.assertEqual(restarted.delivered, {(MANAGER, MANAGER): fingerprint})
        self.bot.send_message.side_effect = None
        await restarted.remove(None, MANAGER, MANAGER, text='منو حذف شد')
        self.assertEqual(restarted.delivered, {})
        self.assertEqual(self.bot.send_message.await_args.kwargs['reply_markup'], REMOVE_MARKUP)


class ClientFallbackTests(unittest.TestCase):
    def client(self, supported):
        class KeyboardButton:
            def __init__(self, text):
                self.text = text

        class ReplyKeyboardMarkup:
            def __init__(self, keyboard, resize_keyboard=None, one_time_keyboard=None, **extra):
                unexpected = set(extra) - set(supported)
                if unexpected:
                    raise TypeError(f'unexpected keyword argument {unexpected.pop()!r}')
                self.keyboard, self.flags = keyboard, extra

        class ReplyKeyboardRemove:
            pass

        return SimpleNamespace(KeyboardButton=KeyboardButton, ReplyKeyboardMarkup=ReplyKeyboardMarkup,
                               ReplyKeyboardRemove=ReplyKeyboardRemove)

    def test_persistent_flag_degrades_gracefully_on_an_older_client(self):
        markup = runtime.reply_menus.menu_for(MANAGER).to_markup()
        with patch.dict('sys.modules', {'telegram': self.client({'is_persistent'})}):
            self.assertEqual(to_reply_markup(markup).flags, {'is_persistent': True})
        with patch.dict('sys.modules', {'telegram': self.client(set())}):
            built = to_reply_markup(markup)
        self.assertEqual(built.flags, {})
        self.assertEqual([[button.text for button in row] for row in built.keyboard],
                         [[WORK_ORDER_LABEL, REPAIRS_LABEL]])

    def test_removal_markup_uses_the_client_remove_object(self):
        with patch.dict('sys.modules', {'telegram': self.client(set())}):
            self.assertEqual(type(to_reply_markup(REMOVE_MARKUP)).__name__, 'ReplyKeyboardRemove')


class ExistingRoleAndRevokeTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self):
        self.folder = TemporaryDirectory()
        self.addCleanup(self.folder.cleanup)
        self.db_path = Path(self.folder.name) / 'permissions.db'
        con = sqlite3.connect(self.db_path)
        try:
            create_schema(con)
            con.executemany(
                'INSERT INTO service_work_order_users (bale_id, role, active) VALUES (?, ?, ?)',
                [
                    ('42', MAINTENANCE_MANAGER, 1),
                    ('43', MAINTENANCE_MANAGER, 0),
                    ('44', 'AIR_FILTER', 1),
                ],
            )
            con.commit()
        finally:
            con.close()
        self.store = StateStore(Path(self.folder.name) / 'reply_menu.json')
        self.bot = FakeBot()
        markup = patch('tools.bale_ui.reply_keyboard.to_reply_markup', lambda markup, bot=None: markup)
        markup.start()
        self.addCleanup(markup.stop)
        self.role_menu = ReplyMenu('by_role', ((ReplyButton(WORK_ORDER_LABEL, 'حکم کار'),),),
                                   roles=frozenset({MAINTENANCE_MANAGER}))
        self.registry = ReplyMenuRegistry([self.role_menu])
        self.db_patch = patch('tools.fleet.work_orders.core.permissions.DB_PATH', self.db_path)
        self.db_patch.start()
        self.addCleanup(self.db_patch.stop)

    def presenter(self, registry=None):
        return ReplyMenuPresenter(registry or self.registry, state_store=self.store,
                                  bot_for=lambda gateway: self.bot, text='منو')

    def test_runtime_reuses_work_order_permission_role_without_inventing_one(self):
        result = check_work_order_permission('42', db_path=self.db_path)
        self.assertTrue(result.allowed)
        self.assertEqual(result.role, MAINTENANCE_MANAGER)
        self.assertEqual(runtime._reply_menu_role('42'), result.role)
        self.assertIsNone(runtime._reply_menu_role('43'))
        self.assertIsNone(runtime._reply_menu_role('44'))
        presenter = self.presenter()
        with patch.object(runtime, 'reply_menus', self.registry), \
             patch.object(runtime, 'reply_presenter', presenter), \
             patch.object(presenter, 'present', return_value=None) as present:
            runtime.reply_menu_step(event('سلام', '42'), None, send=lambda *a: None)
            present.assert_called_once()
            self.assertIs(present.call_args.args[3], self.role_menu)
            present.reset_mock()
            runtime.reply_menu_step(event('سلام', '43'), None, send=lambda *a: None)
            present.assert_not_called()
            runtime.reply_menu_step(event('سلام', '44'), None, send=lambda *a: None)
            present.assert_not_called()

    async def test_revoked_role_sends_keyboard_remove_and_retries_after_failure(self):
        presenter = self.presenter()
        await presenter.present(None, '42', '42', self.role_menu)
        fingerprint = self.role_menu.fingerprint()
        con = sqlite3.connect(self.db_path)
        try:
            con.execute("UPDATE service_work_order_users SET active=0 WHERE bale_id='42'")
            con.commit()
        finally:
            con.close()
        self.bot.send_message.reset_mock()
        self.bot.send_message.side_effect = RuntimeError('network down')
        with patch.object(runtime, 'reply_menus', self.registry), \
             patch.object(runtime, 'reply_presenter', presenter), \
             patch.object(reply_keyboard.logger, 'exception'):
            self.assertIsNone(runtime.reply_menu_step(event('سلام', '42'), None, send=lambda *a: None))
            await asyncio.gather(*list(presenter.tasks))
            self.assertEqual(self.bot.send_message.await_args.kwargs['reply_markup'], REMOVE_MARKUP)
            self.assertEqual(presenter.delivered, {('42', '42'): fingerprint})
            self.bot.send_message.side_effect = None
            self.assertIsNone(runtime.reply_menu_step(event('سلام', '42'), None, send=lambda *a: None))
            await asyncio.gather(*list(presenter.tasks))
        self.assertEqual(self.bot.send_message.await_args.kwargs['reply_markup'], REMOVE_MARKUP)
        self.assertEqual(presenter.delivered, {})

    async def test_users_list_revoke_also_sends_keyboard_remove(self):
        menu = ReplyMenu('ops', ((ReplyButton(WORK_ORDER_LABEL, 'حکم کار'),),), users=frozenset({MANAGER}))
        presenter = self.presenter(ReplyMenuRegistry([menu]))
        await presenter.present(None, MANAGER, MANAGER, menu)
        self.bot.send_message.reset_mock()
        with patch.object(runtime, 'reply_menus', ReplyMenuRegistry()), \
             patch.object(runtime, 'reply_presenter', presenter), \
             patch.object(runtime, '_reply_menu_role', return_value=None):
            runtime.reply_menu_step(event('سلام'), None, send=lambda *a: None)
            await asyncio.gather(*list(presenter.tasks))
        self.assertEqual(self.bot.send_message.await_args.kwargs['reply_markup'], REMOVE_MARKUP)
        self.assertEqual(presenter.delivered, {})


if __name__ == '__main__':
    unittest.main()
