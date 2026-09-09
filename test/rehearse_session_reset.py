"""Reset a temporary COPY of real state; original state is opened read-only.

No gateway stop/start or network delivery. Temporary private copies are removed.
"""
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
import sys
import sqlite3
import tempfile
import hashlib

sys.dont_write_bytecode = True
PROJECT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT / "tools"))
import reset_user_sessions as reset


def digest(conn, table):
    result = hashlib.sha256()
    for row in conn.execute('SELECT * FROM "' + table + '" ORDER BY rowid'):
        result.update(repr(row).encode())
    return result.hexdigest()


def main():
    source = reset.DEFAULT_HOME
    with tempfile.TemporaryDirectory(prefix="reset-rehearsal-", dir=PROJECT / "runtime") as temp:
        home = Path(temp)
        (home / "sessions").mkdir()
        with closing(reset.connect(source / "state.db", readonly=True)) as src:
            with closing(sqlite3.connect(home / "state.db")) as dst:
                src.backup(dst)
                # Read database routes from the consistent snapshot, with the
                # live JSON only as a legacy fallback (it is never modified).
                routes = reset.read_routes(dst, source)
                with dst:
                    dst.execute("UPDATE gateway_routing SET scope=? WHERE scope=?",
                                (str(home / "sessions"), str(source / "sessions")))
        reset.write_json(home / "sessions/sessions.json", routes)
        with closing(reset.connect(home / "state.db")) as conn:
            before = digest(conn, "messages")
            count = conn.execute("SELECT COUNT(*) FROM messages").fetchone()[0]
            others = conn.execute("SELECT * FROM sessions WHERE source != 'bale' ORDER BY id").fetchall()
        reset.native_setup(reset.DEFAULT_ROOT, home)
        # Only process discovery is replaced; lock, backup, native reset,
        # transaction and validation all execute against the temporary home.
        with patch("hermes_cli.gateway.find_gateway_pids", return_value=[]):
            reset.apply_reset(home, reset.DEFAULT_ROOT, {"bale"})
        with closing(reset.connect(home / "state.db")) as conn:
            after = reset.read_routes(conn, home)
            assert digest(conn, "messages") == before
            assert conn.execute("SELECT * FROM sessions WHERE source != 'bale' ORDER BY id").fetchall() == others
            assert all(after[k] == v for k, v in routes.items() if v["platform"] != "bale")
            assert all(after[k]["session_id"] != v["session_id"] for k, v in routes.items() if v["platform"] == "bale")
            reset.check_db(conn)
        print("REHEARSAL PASSED:", sum(v["platform"] == "bale" for v in routes.values()),
              "Bale routes reset;", count, "messages unchanged; other platforms unchanged.")
        print("Original DB opened read-only. No gateway or user session changed.")


if __name__ == "__main__":
    main()
