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
