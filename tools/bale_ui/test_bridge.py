"""Exercise installed adapter/registry code without network or live databases."""
import ast
import importlib.util
import logging
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

PLUGIN_ROOT = Path('C:/Users/win-10/AppData/Local/hermes/plugins')


class BridgeTests(unittest.IsolatedAsyncioTestCase):
    async def test_adapter_preserves_sender_identity_and_existing_callbacks(self):
        path = PLUGIN_ROOT / 'bale/adapter.py'
        tree = ast.parse(path.read_text(encoding='utf-8-sig'))
        original = next(n for n in tree.body if isinstance(n, ast.ClassDef) and n.name == 'BaleAdapter')
        method = next(n for n in original.body if isinstance(n, ast.AsyncFunctionDef) and n.name == '_handle_callback_query')
        original.body = [method]
        base = type('Base', (), {'_handle_callback_query':AsyncMock()})
        namespace = {'TelegramAdapter':base, 'logger':logging.getLogger(__name__)}
        exec(compile(ast.Module(body=[original], type_ignores=[]), str(path), 'exec'), namespace)
        adapter = namespace['BaleAdapter']()
        adapter.platform = 'bale'
        adapter.handle_message = AsyncMock()
        adapter._is_callback_user_authorized = Mock(return_value=True)
        query = SimpleNamespace(id='123', data='ik:work_order:revision:add', answer=AsyncMock(),
            from_user=SimpleNamespace(id=42, first_name='test'),
            message=SimpleNamespace(message_id=7, chat=SimpleNamespace(id=99, type='private')))
        modules = {
            'gateway.platforms.base':SimpleNamespace(MessageEvent=SimpleNamespace, MessageType=SimpleNamespace(TEXT='text')),
            'gateway.session':SimpleNamespace(SessionSource=SimpleNamespace),
        }
        with patch.dict('sys.modules', modules):
            await adapter._handle_callback_query(SimpleNamespace(callback_query=query), None)
        event = adapter.handle_message.call_args.args[0]
        self.assertEqual(event.source.user_id, '42')
        self.assertEqual(event.source.chat_id, '99')
        self.assertEqual(event.message_id, 'callback:123')
        self.assertTrue(event.raw_message['bale_inline_callback'])
        query.answer.assert_awaited_once()
        adapter.handle_message.reset_mock()
        adapter._is_callback_user_authorized.return_value = False
        await adapter._handle_callback_query(SimpleNamespace(callback_query=query), None)
        adapter.handle_message.assert_not_called()
        query.data = 'cp:existing'
        await adapter._handle_callback_query(SimpleNamespace(callback_query=query), None)
        base._handle_callback_query.assert_awaited_once()

    async def test_registry_gate_and_bridge_do_not_treat_callback_as_registration_name(self):
        spec = importlib.util.spec_from_file_location('inline_registry_test', PLUGIN_ROOT / 'komatso-bale-registry/__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'gateway.pairing':SimpleNamespace(PairingStore=Mock())}):
            spec.loader.exec_module(plugin)
        event = SimpleNamespace(text='ik:work_order:x:add',
            raw_message={'bale_inline_callback':True,'data':'ik:work_order:x:add'},
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id='42', chat_id='42'))
        connection = Mock()
        with patch.object(plugin, '_handle_overflow_report', return_value=None), \
             patch.object(plugin, '_admin_ids', return_value=set()), \
             patch.object(plugin, '_connect', return_value=connection), \
             patch.object(plugin, '_send'), \
             patch.object(plugin, '_observe_user') as observe, \
             patch.object(plugin, '_user_status', return_value='revoked') as status, \
             patch('tools.bale_ui.runtime.dispatch', return_value={'action':'skip','reason':'routed'}) as dispatch:
            self.assertEqual(plugin._handle_bale(event, None)['reason'], 'bale-inline-registration-required')
            observe.assert_not_called()
            dispatch.assert_not_called()
            status.return_value = 'approved'
            self.assertEqual(plugin._handle_bale(event, None)['reason'], 'routed')
            dispatch.assert_called_once()

    def _load_registry_plugin(self):
        spec = importlib.util.spec_from_file_location(
            'reply_keyboard_registry_revoke', PLUGIN_ROOT / 'komatso-bale-registry/__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'gateway.pairing': SimpleNamespace(PairingStore=Mock())}):
            spec.loader.exec_module(plugin)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        plugin.DB_PATH = Path(folder.name) / 'users.db'
        return plugin

    def test_admin_delete_triggers_reply_keyboard_remove_without_waiting(self):
        plugin = self._load_registry_plugin()
        conn = plugin._connect()
        try:
            conn.execute(
                """INSERT INTO channel_users
                   (platform, user_id, chat_id, display_name, verified_name,
                    registration_status, first_seen_at, updated_at)
                   VALUES ('bale', '42', '99', 'n', 'n', 'approved', 't', 't')""")
            conn.commit()
        finally:
            conn.close()
        with patch.object(plugin, '_send') as send, \
             patch('tools.bale_ui.runtime.revoke_reply_menu') as revoke:
            result = plugin._handle_admin_command(None, '9', '9', 'حذف 42')
        self.assertEqual(result['reason'], 'bale-admin-user-deleted')
        revoke.assert_called_once()
        self.assertEqual(revoke.call_args.args[:3], (None, '99', '42'))
        self.assertEqual(revoke.call_args.kwargs['text'], plugin._REVOKED_NOTICE)
        send.assert_called_once()
        self.assertIn('با موفقیت حذف شد', send.call_args.args[2])

    def test_revoked_blocked_path_retries_keyboard_remove(self):
        plugin = self._load_registry_plugin()
        conn = plugin._connect()
        try:
            conn.execute(
                """INSERT INTO channel_users
                   (platform, user_id, chat_id, display_name, verified_name,
                    registration_status, first_seen_at, updated_at)
                   VALUES ('bale', '42', '42', 'n', 'n', 'revoked', 't', 't')""")
            conn.commit()
        finally:
            conn.close()
        event = SimpleNamespace(text='سلام', raw_message=None,
            source=SimpleNamespace(platform='bale', chat_type='dm', user_id='42', chat_id='42'))
        with patch.object(plugin, '_handle_overflow_report', return_value=None), \
             patch.object(plugin, '_admin_ids', return_value=set()), \
             patch.object(plugin, '_send'), \
             patch('tools.bale_ui.runtime.revoke_reply_menu') as revoke:
            result = plugin._handle_bale(event, None)
        self.assertEqual(result['reason'], 'bale-registration-revoked-blocked')
        revoke.assert_called_once()
        self.assertEqual(revoke.call_args.args[:3], (None, '42', '42'))


NEW_CHAT_LABEL = "🔄 شروع گفتگوی جدید"


class ApprovedMenuRenderingTests(unittest.TestCase):
    _load_registry_plugin = BridgeTests._load_registry_plugin

    def test_registration_gate_composes_common_button_with_existing_menu(self):
        from tools.bale_ui import runtime
        from tools.fleet.work_orders.channels.bale import message_handler
        from tools.fleet.work_orders.core.permissions import MAINTENANCE_MANAGER

        plugin = self._load_registry_plugin()
        user_id = '641220453'  # Existing configured specialized menu audience.
        conn = plugin._connect()
        conn.execute(
            """INSERT INTO channel_users
               (platform, user_id, chat_id, display_name, verified_name,
                registration_status, first_seen_at, updated_at)
               VALUES ('bale', ?, ?, 'n', 'n', 'approved', 't', 't')""",
            (user_id, user_id))
        conn.commit()
        original = runtime.reply_menus.menu_for(user_id)
        try:
            for status, role, expected in (
                ('approved', MAINTENANCE_MANAGER,
                 [['📋 حکم کار', '🛠 شرح خرابی'], [NEW_CHAT_LABEL]]),
                ('approved', None, [[NEW_CHAT_LABEL]]),
                ('revoked', MAINTENANCE_MANAGER, None),
                ('rejected', None, None),
                ('pending', None, None),
            ):
                with self.subTest(status=status, role=role):
                    conn.execute('UPDATE channel_users SET registration_status=?', (status,))
                    conn.commit()
                    event = SimpleNamespace(text='سلام', raw_message=None,
                        source=SimpleNamespace(platform='bale', chat_type='dm',
                                               user_id=user_id, chat_id=user_id))
                    with patch.object(plugin, '_handle_overflow_report', return_value=None), \
                         patch.object(plugin, '_admin_ids', return_value=set()), \
                         patch.object(plugin, '_send'), \
                         patch.object(runtime, '_reply_menu_role', return_value=role), \
                         patch.object(runtime.reply_presenter, 'present') as present, \
                         patch.object(runtime, 'revoke_reply_menu'), \
                         patch.object(runtime.router, 'dispatch', return_value=None), \
                         patch.object(message_handler, 'handle_work_order_message', return_value=None):
                        plugin._handle_bale(event, None)
                    if expected is None:
                        present.assert_not_called()
                    else:
                        present.assert_called_once()
                        menu = present.call_args.args[3]
                        self.assertEqual([[b.text for b in row] for row in menu.rows], expected)
                        self.assertEqual(menu.command_for(NEW_CHAT_LABEL), '/new')
                        if role:
                            self.assertEqual(menu.rows[:-1], original.rows)
                            self.assertEqual(menu.roles, original.roles)
                            self.assertEqual(menu.users, original.users)
                            self.assertEqual(menu.menu_id, original.menu_id)
                    self.assertIs(runtime.reply_menus.menu_for(user_id), original)
        finally:
            conn.close()


class NewChatButtonTests(unittest.TestCase):
    """The «new chat» reply button becomes the native /new in pre_gateway_dispatch.

    pre_gateway_dispatch fires before auth, session setup and the gateway's
    active-session/busy guard, so rewriting event.text here guarantees Hermes
    sees /new before any busy routing — idle or busy. No /new logic is copied.
    """

    def _load(self):
        spec = importlib.util.spec_from_file_location(
            'new_chat_button_registry', PLUGIN_ROOT / 'komatso-bale-registry/__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules', {'gateway.pairing': SimpleNamespace(PairingStore=Mock())}):
            spec.loader.exec_module(plugin)
        folder = TemporaryDirectory()
        self.addCleanup(folder.cleanup)
        plugin.DB_PATH = Path(folder.name) / 'users.db'
        return plugin

    def _approve(self, plugin, user_id='42', chat_id='42'):
        conn = plugin._connect()
        try:
            conn.execute(
                """INSERT INTO channel_users
                   (platform, user_id, chat_id, display_name, verified_name,
                    registration_status, first_seen_at, updated_at)
                   VALUES ('bale', ?, ?, 'n', 'n', 'approved', 't', 't')""",
                (user_id, chat_id))
            conn.commit()
        finally:
            conn.close()

    def _event(self, text, user_id='42', chat_id='42'):
        return SimpleNamespace(text=text, raw_message=None,
            source=SimpleNamespace(platform='bale', chat_type='dm',
                                   user_id=user_id, chat_id=chat_id))

    def test_approved_user_tap_is_rewritten_to_new_before_any_routing(self):
        plugin = self._load()
        self._approve(plugin)
        event = self._event(NEW_CHAT_LABEL)
        with patch.object(plugin, '_handle_overflow_report', return_value=None), \
             patch.object(plugin, '_admin_ids', return_value=set()), \
             patch.object(plugin, '_handle_work_order_menu') as work_order, \
             patch('tools.bale_ui.runtime.dispatch') as dispatch, \
             patch.object(plugin, '_send') as send:
            result = plugin._handle_bale(event, None)
        # None => dispatch proceeds normally with the rewritten text.
        self.assertIsNone(result)
        self.assertEqual(event.text, '/new')
        # The Persian label never enters the work-order menu, dispatch or LLM.
        work_order.assert_not_called()
        dispatch.assert_not_called()
        send.assert_not_called()

    def test_admin_tap_is_rewritten_to_new(self):
        plugin = self._load()
        event = self._event(NEW_CHAT_LABEL, user_id='9', chat_id='9')
        with patch.object(plugin, '_handle_overflow_report', return_value=None), \
             patch.object(plugin, '_admin_ids', return_value={'9'}), \
             patch.object(plugin, '_handle_admin_command') as admin, \
             patch.object(plugin, '_handle_work_order_menu') as work_order:
            result = plugin._handle_bale(event, None)
        self.assertIsNone(result)
        self.assertEqual(event.text, '/new')
        admin.assert_not_called()
        work_order.assert_not_called()

    def test_busy_or_idle_both_see_new_because_rewrite_precedes_busy_guard(self):
        # The rewrite happens in pre_gateway_dispatch, which the gateway runs
        # before its busy guard; the bridge itself never inspects busy state.
        plugin = self._load()
        self._approve(plugin)
        for _ in range(2):  # idempotent regardless of any downstream busy state
            event = self._event(NEW_CHAT_LABEL)
            with patch.object(plugin, '_handle_overflow_report', return_value=None), \
                 patch.object(plugin, '_admin_ids', return_value=set()), \
                 patch.object(plugin, '_handle_work_order_menu') as work_order, \
                 patch('tools.bale_ui.runtime.dispatch') as dispatch:
                result = plugin._handle_bale(event, None)
            self.assertIsNone(result)
            self.assertEqual(event.text, '/new')
            work_order.assert_not_called()
            dispatch.assert_not_called()

    def test_similar_question_is_left_untouched(self):
        plugin = self._load()
        self._approve(plugin)
        event = self._event('شروع گفتگوی جدید یعنی چی؟')
        with patch.object(plugin, '_handle_overflow_report', return_value=None), \
             patch.object(plugin, '_admin_ids', return_value=set()), \
             patch.object(plugin, '_handle_work_order_menu', return_value=None) as work_order:
            plugin._handle_bale(event, None)
        # Not an exact match: text unchanged and normal routing runs.
        self.assertEqual(event.text, 'شروع گفتگوی جدید یعنی چی؟')
        work_order.assert_called_once()

    def test_unauthorized_user_gets_no_new_bypass(self):
        plugin = self._load()  # user 42 has no row => status 'none'
        event = self._event(NEW_CHAT_LABEL)
        with patch.object(plugin, '_handle_overflow_report', return_value=None), \
             patch.object(plugin, '_admin_ids', return_value=set()), \
             patch.object(plugin, '_handle_work_order_menu') as work_order, \
             patch('tools.bale_ui.runtime.dispatch') as dispatch, \
             patch.object(plugin, '_send'):
            result = plugin._handle_bale(event, None)
        # Registration flow starts; text is NOT rewritten to /new.
        self.assertEqual(event.text, NEW_CHAT_LABEL)
        self.assertEqual(result['reason'], 'bale-registration-started')
        work_order.assert_not_called()
        dispatch.assert_not_called()

    def test_exact_match_helper_is_whitespace_and_arabic_tolerant(self):
        plugin = self._load()
        self.assertTrue(plugin._is_new_chat_button(NEW_CHAT_LABEL))
        self.assertTrue(plugin._is_new_chat_button("🔄  شروع  گفتگوی  جدید"))
        self.assertTrue(plugin._is_new_chat_button("🔄 شروع گفتگوي جديد"))  # Arabic ي/ي
        self.assertFalse(plugin._is_new_chat_button("شروع گفتگوی جدید"))
        self.assertFalse(plugin._is_new_chat_button("شروع گفتگوی جدید یعنی چی؟"))
