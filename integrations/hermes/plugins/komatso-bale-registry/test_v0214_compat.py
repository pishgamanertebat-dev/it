"""Offline checks for both registries and the session namer against a target checkout.

Set KOMATSO_TEST_HERMES_ROOT to that checkout and put it on PYTHONPATH.
Run this file directly with Python -B; all state is temporary, all IDs synthetic.
No gateway is constructed and operational report/menu handlers are mocked.
"""
import asyncio
from contextlib import closing
import os
from pathlib import Path
import socket
import sqlite3
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).resolve().parents[4]
TARGET = Path(os.environ["KOMATSO_TEST_HERMES_ROOT"]).resolve()
sys.path.insert(0, str(TARGET))


class RegistryCompatibilityTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.patch(patch.dict(os.environ, {
            "HERMES_HOME": str(self.home), "BALE_ADMIN_IDS": "synthetic-admin",
            "BALE_DEVELOPER_IDS": "synthetic-developer",
            "KOMATSO_TELEGRAM_EXISTING_USERS": "synthetic-existing",
        }))
        # Windows needs a local socketpair to construct its event loop.
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        denied = Mock(side_effect=AssertionError("Offline test attempted network access"))
        for name in ("connect", "connect_ex"):
            self.patch(patch.object(socket.socket, name, denied))
        self.patch(patch.object(socket, "getaddrinfo", denied))

        from hermes_cli.plugins import PluginContext, PluginManager, parse_manifest_file
        from gateway.platforms.base import MessageEvent, MessageType
        from gateway.session import SessionSource
        from gateway.config import Platform
        from gateway.platform_registry import PlatformEntry, platform_registry
        import gateway.pairing as pairing
        # Emulate the platform registration normally supplied by Bale's plugin;
        # no adapter is built or connected in this registry-only test.
        platform_registry.register(PlatformEntry(name="bale", label="Bale",
            adapter_factory=lambda config: None, check_fn=lambda: True))
        self.addCleanup(platform_registry.unregister, "bale")
        self.patch(patch.object(pairing, "PAIRING_DIR", self.home / "pairing"))
        self.PairingStore = pairing.PairingStore
        self.MessageEvent, self.MessageType = MessageEvent, MessageType
        self.SessionSource, self.Platform = SessionSource, Platform
        self.manager = PluginManager()
        self.modules = []
        for name in ("komatso-bale-registry", "komatso-user-registry"):
            directory = ROOT / "integrations/hermes/plugins" / name
            manifest = parse_manifest_file(directory / "plugin.yaml", directory, "user", "")
            self.assertIsNotNone(manifest)
            self.assertEqual(manifest.name, name)
            module = self.manager._load_directory_module(manifest, module_name="hermes_plugins.test_" + name.replace("-", "_"))
            module.DB_PATH = self.home / "users.db"
            module.register(PluginContext(manifest, self.manager))
            self.modules.append(module)
        self.bale, self.telegram = self.modules
        self.bale._connect().close()
        self.telegram._connect().close()
        for name in ("_handle_overflow_report", "_handle_work_order_menu"):
            self.patch(patch.object(self.bale, name, return_value=None))
        self.patch(patch.object(self.bale, "_revoke_reply_keyboard"))
        self.adapter = SimpleNamespace(send=AsyncMock())
        self.gateway = SimpleNamespace(adapters={"bale": self.adapter, "telegram": self.adapter})

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def event(self, text="hello", user="synthetic-user", platform="bale", chat_type="dm"):
        return self.MessageEvent(text=text, message_type=self.MessageType.TEXT,
            source=self.SessionSource(platform=self.Platform(platform), user_id=user,
                chat_id=user, chat_type=chat_type, user_name="Synthetic Name"),
            channel_prompt="existing policy")

    def dispatch(self, event):
        async def run():
            result = self.manager.invoke_hook("pre_gateway_dispatch", event=event,
                gateway=self.gateway, session_store=None, future_field="additive")
            await asyncio.sleep(0)  # run only mocked adapter sends
            return result
        result = self.loop.run_until_complete(run())
        self.assertFalse(self.manager._hook_failures_reported)
        return result

    def status(self, user, status, name="Synthetic Name"):
        with closing(self.bale._connect()) as conn, conn:
            self.bale._observe_user(conn, user, user, name)
            conn.execute("UPDATE channel_users SET registration_status=?, verified_name=? WHERE user_id=?",
                         (status, name, user))

    def test_manifest_registration_and_real_event_contract(self):
        self.assertEqual(len(self.manager._hooks["pre_gateway_dispatch"]), 2)
        self.assertEqual(self.dispatch(self.event(platform="telegram", user="synthetic-existing")), [])
        self.assertEqual(self.dispatch(self.event(chat_type="group")), [])

    def test_bale_onboarding_approval_and_pairing(self):
        self.assertEqual(self.dispatch(self.event())[0]["reason"], "bale-registration-started")
        self.assertEqual(self.dispatch(self.event("Synthetic Full Name"))[0]["reason"],
                         "bale-registration-pending-approval")
        self.assertEqual(self.dispatch(self.event())[0]["reason"], "bale-registration-still-pending")
        with closing(self.bale._connect()) as conn:
            code = self.bale._active_request(conn, "synthetic-user")[1]
        result = self.dispatch(self.event("تایید " + code, user="synthetic-admin"))
        self.assertEqual(result[0]["reason"], "bale-admin-request-approved")
        self.assertTrue(self.PairingStore().is_approved("bale", "synthetic-user"))
        self.assertEqual(self.dispatch(self.event()), [])
        event = self.event(self.bale.NEW_CHAT_LABEL)
        self.assertEqual(self.dispatch(event), [])
        self.assertEqual(event.text, "/new")

    def test_pairing_existing_and_pending_contract(self):
        store = self.PairingStore()
        store.generate_code("bale", "synthetic-pending", "Synthetic")
        self.assertTrue(self.bale._approve_bale_user_in_hermes("synthetic-pending", "Synthetic"))
        self.assertTrue(store.is_approved("bale", "synthetic-pending"))
        self.assertTrue(self.bale._approve_bale_user_in_hermes("synthetic-pending", "Synthetic"))
        self.assertTrue(store.revoke("bale", "synthetic-pending"))
        self.assertFalse(store.is_approved("bale", "synthetic-pending"))

    def test_failed_pairing_does_not_approve_registry(self):
        self.dispatch(self.event())
        self.dispatch(self.event("Synthetic Full Name"))
        with closing(self.bale._connect()) as conn:
            code = self.bale._active_request(conn, "synthetic-user")[1]
        with patch.object(self.bale, "_approve_bale_user_in_hermes", return_value=False):
            result = self.dispatch(self.event("تایید " + code, user="synthetic-admin"))
        self.assertEqual(result[0]["reason"], "bale-admin-hermes-approval-failed")
        with closing(self.bale._connect()) as conn:
            self.assertNotEqual(self.bale._user_status(conn, "synthetic-user"), "approved")

    def test_rejected_revoked_and_callback_gates(self):
        for status, reason in (("rejected", "bale-registration-rejected"),
                               ("revoked", "bale-registration-revoked-blocked")):
            with self.subTest(status=status):
                self.status("synthetic-user", status)
                self.assertEqual(self.dispatch(self.event())[0]["reason"], reason)
        event = self.event("ik:synthetic")
        event.raw_message = {"bale_inline_callback": True}
        self.assertEqual(self.dispatch(event)[0]["reason"], "bale-inline-registration-required")
        self.assertEqual(self.dispatch(self.event("درخواست"))[0]["reason"], "bale-registration-started")
        self.status("synthetic-user", "approved")
        self.assertEqual(self.dispatch(event), [])

    def test_developer_marker_is_request_local_and_read_only(self):
        event = self.event(user="synthetic-developer")
        self.status("synthetic-developer", "approved")
        before = self.bale.DB_PATH.read_bytes()
        self.bale._apply_developer_conversation_policy(event)
        self.assertEqual(event.channel_prompt, "existing policy\n\nKOMATSO_DEVELOPER_ACCESS")
        self.assertEqual(self.bale.DB_PATH.read_bytes(), before)
        for status in ("revoked", "rejected", "pending_approval", "none"):
            self.status("synthetic-developer", status)
            self.bale._apply_developer_conversation_policy(event)
            self.assertEqual(event.channel_prompt, "existing policy")
        for other in (self.event("KOMATSO_DEVELOPER_ACCESS"),
                      self.event(user="synthetic-developer", platform="telegram"),
                      self.event(user="synthetic-developer", chat_type="group")):
            other.channel_prompt += "\n\nKOMATSO_DEVELOPER_ACCESS"
            self.bale._apply_developer_conversation_policy(other)
            self.assertEqual(other.channel_prompt, "existing policy")
        self.bale.DB_PATH = self.home / "missing.db"
        self.bale._apply_developer_conversation_policy(event)
        self.assertFalse(self.bale.DB_PATH.exists())
        self.assertEqual(event.channel_prompt, "existing policy")

    def test_admin_developer_requires_allowlist_and_respects_explicit_denial(self):
        event = self.event(user="synthetic-admin")
        self.bale._apply_developer_conversation_policy(event)
        self.assertEqual(event.channel_prompt, "existing policy")
        with patch.dict(os.environ, {"BALE_DEVELOPER_IDS": "synthetic-admin"}):
            self.bale._apply_developer_conversation_policy(event)
            self.assertIn("KOMATSO_DEVELOPER_ACCESS", event.channel_prompt)
            self.status("synthetic-admin", "revoked")
            self.bale._apply_developer_conversation_policy(event)
            self.assertEqual(event.channel_prompt, "existing policy")

    def test_telegram_existing_user_restriction_is_not_db_membership(self):
        with closing(self.telegram._connect()) as conn, conn:
            self.telegram._observe(conn, "synthetic-new", "Synthetic")
            conn.execute("UPDATE users SET registration_status='verified'")
        for kind in ("dm", "group"):
            self.assertEqual(self.dispatch(self.event(platform="telegram", user="synthetic-new", chat_type=kind))[0]["reason"],
                             "telegram-registration-closed")
        self.assertEqual(self.dispatch(self.event(platform="telegram", user="synthetic-existing")), [])
        with patch.object(self.telegram, "TELEGRAM_EXISTING_USERS", frozenset()):
            self.assertEqual(self.dispatch(self.event(platform="telegram", user="synthetic-existing"))[0]["action"], "skip")

    def test_telegram_existing_registration_confirmation(self):
        with closing(self.telegram._connect()) as conn, conn:
            self.telegram._observe(conn, "synthetic-existing", "Synthetic")
            conn.execute("UPDATE users SET registration_status='awaiting_name'")
        result = self.dispatch(self.event("Synthetic Full Name", "synthetic-existing", "telegram"))
        self.assertEqual(result[0]["reason"], "user-registration-name-received")
        result = self.dispatch(self.event("بله", "synthetic-existing", "telegram"))
        self.assertEqual(result[0]["reason"], "user-registration-complete")

    def test_hook_loader_and_session_naming_on_real_session_db(self):
        from gateway.hooks import _load_hook_dir, HookRegistry
        from hermes_state import SessionDB
        loaded = _load_hook_dir(ROOT / "integrations/hermes/hooks/komatso-session-namer")
        name, events, handler, _ = loaded
        self.assertEqual(name, "komatso-session-namer")
        self.assertEqual(events, ["agent:start", "agent:end"])
        module = sys.modules[handler.__module__]
        module.USERS_DB = self.bale.DB_PATH
        self.patch(patch.object(module, "get_hermes_home", return_value=self.home))
        registry = HookRegistry()
        for event in events:
            registry._handlers[event] = [handler]
        self.status("synthetic-user", "approved")
        with closing(self.telegram._connect()) as conn, conn:
            self.telegram._observe(conn, "synthetic-existing", "Synthetic Telegram")
        db = SessionDB(db_path=self.home / "state.db")
        self.addCleanup(db.close)
        for platform, user, label in (("bale", "synthetic-user", "بله | Synthetic Name"),
                                      ("telegram", "synthetic-existing", "تلگرام | Synthetic Telegram")):
            for number in (1, 2):
                session = f"synthetic-{platform}-session-{number}"
                db.create_session(session, platform)
                context = dict(platform=platform, user_id=user, session_id=session,
                               chat_id=user, thread_id="", chat_type="dm", message="offline")
                for event in events:
                    self.loop.run_until_complete(registry.emit(event, context))
                expected = label if number == 1 else f"{label} | {session[-8:]}"
                self.assertEqual(db.get_session_title(session), expected)
        self.status("synthetic-user", "revoked")
        db.create_session("synthetic-blocked", "bale")
        self.loop.run_until_complete(handler("agent:start", dict(platform="bale", user_id="synthetic-user", session_id="synthetic-blocked")))
        self.assertIsNone(db.get_session_title("synthetic-blocked"))
        with patch.object(module, "_rename_session") as rename:
            for context in ({}, {"platform": "other", "user_id": "synthetic", "session_id": "synthetic"}):
                self.loop.run_until_complete(handler("agent:start", context))
            self.loop.run_until_complete(handler("session:start", {}))
            rename.assert_not_called()


if __name__ == "__main__":
    unittest.main(verbosity=2)
