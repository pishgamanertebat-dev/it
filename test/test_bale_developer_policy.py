"""Offline policy tests: synthetic registration DB, no gateway or Bale sends."""
import importlib.util
from contextlib import closing
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
PLUGIN = ROOT / "integrations/hermes/plugins/komatso-bale-registry/__init__.py"


class DeveloperPolicyTests(unittest.TestCase):
    def setUp(self):
        pairing = ModuleType("gateway.pairing")
        pairing.PairingStore = object
        spec = importlib.util.spec_from_file_location("bale_policy_test_plugin", PLUGIN)
        self.plugin = importlib.util.module_from_spec(spec)
        with patch.dict(sys.modules, {"gateway.pairing": pairing}):
            spec.loader.exec_module(self.plugin)
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.addCleanup(self.temp.cleanup)
        self.plugin.DB_PATH = Path(self.temp.name) / "users.db"
        with closing(sqlite3.connect(self.plugin.DB_PATH)) as conn, conn:
            conn.execute("CREATE TABLE channel_users (platform TEXT, user_id TEXT, registration_status TEXT)")
        self.env = patch.dict(os.environ, {"BALE_DEVELOPER_IDS": " 101, 303 "})
        self.env.start()
        self.addCleanup(self.env.stop)

    def status(self, user_id, status):
        with closing(sqlite3.connect(self.plugin.DB_PATH)) as conn, conn:
            conn.execute("DELETE FROM channel_users WHERE user_id=?", (user_id,))
            conn.execute("INSERT INTO channel_users VALUES ('bale', ?, ?)", (user_id, status))

    def event(self, user_id="101", platform="bale", chat_type="dm"):
        return SimpleNamespace(source=SimpleNamespace(
            user_id=user_id, chat_id="101", platform=platform, chat_type=chat_type
        ), text="debug this code", channel_prompt="existing channel policy")

    def test_approved_developer_unrestricted_conversation_prompt(self):
        self.status("101", "approved")
        event = self.event()
        # Exercise the actual registered dispatch handler, stopping before any
        # operational routing or network side effects.
        with patch.object(self.plugin, "_handle_overflow_report", return_value={"action": "skip"}):
            self.plugin._handle_bale(event, None)
        self.assertIn("Override only the topic restrictions", event.channel_prompt)
        self.assertIn("debugging and development are allowed", event.channel_prompt)
        self.assertIn("This grants no additional", event.channel_prompt)
        self.assertTrue(event.channel_prompt.startswith("existing channel policy\n\n"))

    def test_approved_normal_user_keeps_current_restriction(self):
        self.status("202", "approved")
        event = self.event("202")
        self.plugin._apply_developer_conversation_policy(event)
        self.assertEqual(event.channel_prompt, "existing channel policy")
        self.assertIn("You must not answer questions outside this technical scope.",
                      (ROOT / "AGENTS.md").read_text(encoding="utf-8"))

    def test_unapproved_or_revoked_developer_has_no_bypass(self):
        event = self.event()
        self.status("101", "approved")
        self.plugin._apply_developer_conversation_policy(event)
        for status in ("revoked", "pending_approval", "rejected", "none"):
            with self.subTest(status=status):
                self.status("101", status)
                self.plugin._apply_developer_conversation_policy(event)
                self.assertEqual(event.channel_prompt, "existing channel policy")
        for event in (self.event("303"), self.event(None),
                      self.event(platform="telegram"), self.event(chat_type="group")):
            with self.subTest(source=event.source):
                self.plugin._apply_developer_conversation_policy(event)
                self.assertEqual(event.channel_prompt, "existing channel policy")
        self.status("101", "approved")
        event = self.event()
        with patch.dict(os.environ, {"BALE_DEVELOPER_IDS": ""}):
            self.plugin._apply_developer_conversation_policy(event)
        self.assertEqual(event.channel_prompt, "existing channel policy")
        self.plugin.DB_PATH = Path(self.temp.name) / "missing.db"
        self.plugin._apply_developer_conversation_policy(event)
        self.assertEqual(event.channel_prompt, "existing channel policy")
        self.assertFalse(self.plugin.DB_PATH.exists())


if __name__ == "__main__":
    unittest.main()
