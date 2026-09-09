"""Integration tests against installed Hermes, using synthetic databases only."""
from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest.mock import patch

sys.dont_write_bytecode = True
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))
import reset_user_sessions as reset

RUNTIME = Path(__file__).resolve().parents[1] / "runtime"
RUNTIME.mkdir(exist_ok=True)
# Set an isolated home BEFORE importing any Hermes module.
BOOT = tempfile.TemporaryDirectory(prefix="reset-tests-", dir=RUNTIME)
reset.native_setup(reset.DEFAULT_ROOT, Path(BOOT.name))
from gateway.session import SessionEntry, SessionSource
from gateway.config import Platform
from hermes_state import SessionDB
from datetime import datetime


class ResetTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=BOOT.name)
        self.home = Path(self.temp.name)
        (self.home / "sessions").mkdir()
        self.db = self.home / "state.db"
        native = SessionDB(self.db)
        self.before = {}
        for i, platform in enumerate(("bale", "bale", "telegram", "local")):
            key = f"agent:main:{platform}:dm:test{i}"
            sid = f"old_test_{i}"
            origin = SessionSource(platform=Platform(platform), chat_id=f"test{i}",
                                   user_id=f"user{i}", chat_type="dm")
            entry = SessionEntry(session_key=key, session_id=sid,
                                 created_at=datetime.now(), updated_at=datetime.now(),
                                 platform=Platform(platform), chat_type="dm", origin=origin,
                                 display_name=f"test{i}", metadata={"old": True})
            self.before[key] = entry.to_dict()
            native.create_session(sid, platform, user_id=f"user{i}", session_key=key,
                                  chat_id=f"test{i}", chat_type="dm")
            native.append_message(sid, "user", "synthetic conversation")
        native.replace_gateway_routing_entries(
            {k: json.dumps(v) for k, v in self.before.items()}, scope=str(self.home / "sessions"))
        native.close()
        reset.write_json(self.home / "sessions/sessions.json", self.before)
        with closing(sqlite3.connect(self.db)) as c, c:
            self.messages = c.execute("SELECT * FROM messages ORDER BY id").fetchall()
            self.original_rows = c.execute("SELECT * FROM sessions ORDER BY id").fetchall()

    def tearDown(self):
        self.temp.cleanup()

    def apply(self, platforms={"bale"}):
        # Other live gateways are deliberately irrelevant to this synthetic home.
        # Only process discovery is mocked; staging, reset, SQL and locks are real.
        with patch("hermes_cli.gateway.find_gateway_pids", return_value=[]):
            reset.apply_reset(self.home, reset.DEFAULT_ROOT, platforms)

    def test_bale_reset_preserves_history_and_other_platforms(self):
        self.apply()
        with closing(sqlite3.connect(self.db)) as c, c:
            after = reset.read_routes(c, self.home)
            self.assertEqual(self.messages, c.execute("SELECT * FROM messages ORDER BY id").fetchall())
            reset.check_db(c)
            for key, old in self.before.items():
                if old["platform"] == "bale":
                    self.assertNotEqual(old["session_id"], after[key]["session_id"])
                    self.assertTrue(after[key]["is_fresh_reset"])
                    self.assertEqual({}, after[key]["metadata"])
                    self.assertEqual(old["origin"], after[key]["origin"])
                    self.assertEqual(("session_reset",), c.execute(
                        "SELECT end_reason FROM sessions WHERE id=?", (old["session_id"],)).fetchone())
                else:
                    self.assertEqual(old, after[key])
            self.assertEqual(6, c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
        backup = next((self.home / "backups/session-reset").iterdir())
        with closing(sqlite3.connect(backup / "state.db")) as c, c:
            self.assertEqual(self.original_rows, c.execute("SELECT * FROM sessions ORDER BY id").fetchall())
        self.assertEqual("complete", json.loads((backup / "report.json").read_text())["status"])
        # A fresh gateway process must route the next message to the NEW ID
        # and load an empty conversation, rather than recover the old session.
        code = '''
import sys,json
from pathlib import Path
sys.path.insert(0,sys.argv[2])
import reset_user_sessions as r
h=Path(sys.argv[1])
r.native_setup(r.DEFAULT_ROOT,h)
from gateway.session import SessionStore,SessionSource
from gateway.config import GatewayConfig
s=SessionStore(h/'sessions',GatewayConfig())
try:
 for k,e in json.loads((h/'sessions/sessions.json').read_text()).items():
  if e['platform']=='bale':
   routed=s.get_or_create_session(SessionSource.from_dict(e['origin']))
   assert routed.session_id==e['session_id']
   assert s.load_transcript(routed.session_id)==[]
finally:
 s._db.close()
'''
        subprocess.run([sys.executable, "-B", "-c", code, str(self.home),
                        str(Path(reset.__file__).parent)], check=True, timeout=60,
                       env=dict(os.environ, HERMES_HOME=str(self.home)))

    def test_failed_second_insert_rolls_back_all_routes(self):
        original_publish = reset.publish
        def fail_on_live(conn, *args):
            conn.execute("CREATE TEMP TRIGGER reject_test BEFORE INSERT ON sessions "
                         "WHEN NEW.chat_id='test1' BEGIN SELECT RAISE(ABORT,'injected failure'); END")
            return original_publish(conn, *args)
        with patch.object(reset, "publish", side_effect=fail_on_live):
            with self.assertRaisesRegex(sqlite3.IntegrityError, "injected failure"):
                self.apply()
        with closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(self.original_rows, c.execute("SELECT * FROM sessions ORDER BY id").fetchall())
            self.assertEqual(self.before, reset.read_routes(c, self.home))

    def test_mirror_failure_leaves_valid_committed_database_and_report(self):
        original_write = reset.write_json
        def fail_mirror(path, data):
            if Path(path) == self.home / "sessions/sessions.json":
                raise OSError("injected mirror failure")
            return original_write(path, data)
        with patch.object(reset, "write_json", side_effect=fail_mirror):
            with self.assertRaisesRegex(OSError, "mirror failure"):
                self.apply()
        with closing(sqlite3.connect(self.db)) as c:
            reset.check_db(c)
            self.assertEqual(6, c.execute("SELECT COUNT(*) FROM sessions").fetchone()[0])
            self.assertEqual(self.messages, c.execute("SELECT * FROM messages ORDER BY id").fetchall())
            after = reset.read_routes(c, self.home)
            self.assertTrue(all(after[k]["is_fresh_reset"] for k in self.before if self.before[k]["platform"] == "bale"))
        backup = next((self.home / "backups/session-reset").iterdir())
        self.assertEqual("database_committed", json.loads((backup / "report.json").read_text())["status"])

    def test_running_gateway_refused_without_backup(self):
        with patch("hermes_cli.gateway.find_gateway_pids", return_value=[123]):
            with self.assertRaisesRegex(RuntimeError, "running"):
                reset.apply_reset(self.home, reset.DEFAULT_ROOT, {"bale"})
        self.assertFalse((self.home / "backups").exists())

    def test_busy_runtime_lock_refused_without_backup(self):
        with patch("gateway.status.acquire_gateway_runtime_lock", return_value=False):
            with self.assertRaisesRegex(RuntimeError, "lock"):
                self.apply()
        self.assertFalse((self.home / "backups").exists())

    def test_pending_delivery_refused_without_backup(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("CREATE TABLE delivery_obligations(session_key TEXT, state TEXT)")
            c.execute("INSERT INTO delivery_obligations VALUES(?, 'pending')", (next(iter(self.before)),))
        with self.assertRaisesRegex(RuntimeError, "pending delivery"):
            self.apply()
        self.assertFalse((self.home / "backups").exists())

    def test_both_platforms(self):
        self.apply({"bale", "telegram"})
        with closing(sqlite3.connect(self.db)) as c, c:
            after = reset.read_routes(c, self.home)
        for key, old in self.before.items():
            self.assertEqual(old["session_id"] == after[key]["session_id"], old["platform"] == "local")

    def test_database_overrides_stale_mirror(self):
        stale = json.loads(json.dumps(self.before))
        next(iter(stale.values()))["session_id"] = "stale"
        reset.write_json(self.home / "sessions/sessions.json", stale)
        with closing(sqlite3.connect(self.db)) as c, c:
            self.assertEqual(self.before, reset.read_routes(c, self.home))

    def test_routes_pointing_to_ended_sessions_are_reset(self):
        with closing(sqlite3.connect(self.db)) as c, c:
            c.execute("UPDATE sessions SET ended_at=1,end_reason='session_reset' WHERE source='bale'")
        self.apply()
        with closing(sqlite3.connect(self.db)) as c:
            after = reset.read_routes(c, self.home)
            for key, old in self.before.items():
                if old['platform'] == 'bale':
                    self.assertNotEqual(old['session_id'], after[key]['session_id'])
                    self.assertEqual((None,), c.execute('SELECT ended_at FROM sessions WHERE id=?',
                                                       (after[key]['session_id'],)).fetchone())


if __name__ == "__main__":
    try:
        unittest.main(verbosity=2)
    finally:
        BOOT.cleanup()
