"""Preview or safely reset Bale/Telegram routes using an isolated native reset.

SQLite backup includes WAL. Only selected metadata is published atomically;
messages, registration records and unrelated routes are never deleted.
"""
from __future__ import annotations
import argparse
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
import json
import os
from pathlib import Path
import shutil
import sqlite3
import sys
import tempfile

DEFAULT_HOME = Path(r"C:\Users\win-10\AppData\Local\hermes")
DEFAULT_ROOT = DEFAULT_HOME / "hermes-agent"


def connect(path, readonly=False):
    return sqlite3.connect(Path(path).resolve().as_uri() +
                           ("?mode=ro" if readonly else "?mode=rw"), uri=True, timeout=15)


def check_db(conn):
    if conn.execute("PRAGMA quick_check").fetchall() != [("ok",)]:
        raise RuntimeError("SQLite quick_check failed; reset refused")


def check_pending(conn, before, keys):
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    for key in keys:
        sid = before[key]["session_id"]
        if "async_delegations" in tables and conn.execute(
                "SELECT 1 FROM async_delegations WHERE (parent_session_id=? OR origin_session=?) "
                "AND (state IN ('running','finalizing') OR delivery_state='pending') LIMIT 1",
                (sid, key)).fetchone():
            raise RuntimeError("Selected conversation has pending delegation work; reset refused")
        if "delivery_obligations" in tables and conn.execute(
                "SELECT 1 FROM delivery_obligations WHERE session_key=? "
                "AND state NOT IN ('delivered','abandoned') LIMIT 1", (key,)).fetchone():
            raise RuntimeError("Selected conversation has pending delivery; reset refused")


def read_routes(conn, home):
    scope = str((home / "sessions").resolve())
    routes = {}
    mirror = home / "sessions/sessions.json"
    if mirror.exists():
        routes = {k: v for k, v in json.loads(mirror.read_text(encoding="utf-8")).items()
                  if not k.startswith("_")}
    for key, data in conn.execute(
            "SELECT session_key,entry_json FROM gateway_routing WHERE scope=?", (scope,)):
        routes[key] = json.loads(data)
    for key, entry in routes.items():
        if not isinstance(entry, dict) or entry.get("session_key") != key or not entry.get("session_id"):
            raise RuntimeError("Invalid routing entry; reset refused")
    return routes


def native_setup(root, home):
    sys.dont_write_bytecode = True
    sys.path[:0] = [str(root), str(root / "venv/Lib/site-packages")]
    os.environ["HERMES_HOME"] = str(home)
    from gateway.platform_registry import PlatformEntry, platform_registry
    if not platform_registry.is_registered("bale"):
        # Metadata only: no adapter, credentials, hooks or outgoing messages.
        platform_registry.register(PlatformEntry(
            name="bale", label="Bale", adapter_factory=lambda _: None, check_fn=lambda: True))


def write_json(path, data):
    path = Path(path)
    fd, name = tempfile.mkstemp(prefix=".reset-", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(name, path)
    finally:
        if os.path.exists(name):
            os.unlink(name)


def stage_reset(stage, root, keys):
    """Separate process prevents Hermes home/path caches touching the live DB."""
    native_setup(root, stage)
    from gateway.config import GatewayConfig
    from gateway.session import SessionStore, SessionEntry
    store = SessionStore(stage / "sessions", GatewayConfig())
    if store._db is None:
        raise RuntimeError("Native SQLite store unavailable")
    try:
        with closing(connect(stage / "state.db", readonly=True)) as conn:
            raw = read_routes(conn, stage)
        entries = {key: SessionEntry.from_dict(raw[key]) for key in keys}
        # Administrative reset must also replace routes pointing to ended
        # sessions. Normal startup prunes/recoveries would discard/change them.
        # Seed only the isolated store with validated selected entries; the
        # original database is never exposed to these private in-memory fields.
        with store._lock:
            store._entries = entries.copy()
            store._loaded = True
        for key in keys:
            old = entries[key]
            new = store.reset_session(key, old.display_name)
            if new is None or new.session_id == old.session_id:
                raise RuntimeError("Native reset failed")
    finally:
        store._db.close()


def publish(conn, staged, scope, before, after, keys):
    """A failed insert/update rolls back the entire selected batch."""
    cols = [r[1] for r in conn.execute("PRAGMA table_info(sessions)")]
    if cols != [r[1] for r in staged.execute("PRAGMA table_info(sessions)")]:
        raise RuntimeError("Hermes schema changed during staging; reset refused")
    with conn:
        conn.execute("BEGIN IMMEDIATE")
        if read_routes(conn, Path(scope).parent) != before:
            raise RuntimeError("Routes changed while staging; reset refused")
        check_pending(conn, before, keys)
        selected_ids = {before[k]["session_id"] for k in keys}
        for route_scope, key, data in conn.execute("SELECT scope,session_key,entry_json FROM gateway_routing"):
            if (route_scope != scope or key not in keys) and json.loads(data).get("session_id") in selected_ids:
                raise RuntimeError("Selected session is shared with another route; reset refused")
        for key in keys:
            old_id, new_id = before[key]["session_id"], after[key]["session_id"]
            old = staged.execute("SELECT ended_at,end_reason FROM sessions WHERE id=?", (old_id,)).fetchone()
            row = staged.execute("SELECT * FROM sessions WHERE id=?", (new_id,)).fetchone()
            if (old is None or old[0] is None or old[1] in (None, "agent_close", "ws_orphan_reap")
                    or row is None or old_id == new_id):
                raise RuntimeError("Staged reset verification failed")
            fresh = dict(zip(cols, row))
            if fresh.get("message_count") or fresh.get("system_prompt") or fresh.get("system_prompt_hash"):
                raise RuntimeError("New session still has history or cached prompt")
            cur = conn.execute("UPDATE sessions SET ended_at=?,end_reason=? WHERE id=?", (*old, old_id))
            if cur.rowcount != 1:
                raise RuntimeError("Original session disappeared")
            names = ",".join('"' + c + '"' for c in cols)
            conn.execute(f"INSERT INTO sessions ({names}) VALUES ({','.join('?' for _ in cols)})", row)
            conn.execute(
                "INSERT INTO gateway_routing(scope,session_key,entry_json,updated_at) VALUES(?,?,?,?) "
                "ON CONFLICT(scope,session_key) DO UPDATE SET entry_json=excluded.entry_json,updated_at=excluded.updated_at",
                (scope, key, json.dumps(after[key]), datetime.now(timezone.utc).timestamp()))
        check_db(conn)


def apply_reset(home, root, platforms):
    import subprocess
    native_setup(root, home)
    from gateway.status import acquire_gateway_runtime_lock, release_gateway_runtime_lock
    from hermes_cli.gateway import find_gateway_pids
    if find_gateway_pids():
        raise RuntimeError("Gateway is running. Stop it before applying the reset")
    if not acquire_gateway_runtime_lock():
        raise RuntimeError("Gateway runtime lock is held; reset refused")
    try:
        with closing(connect(home / "state.db")) as conn:
            check_db(conn)
            before = read_routes(conn, home)
            keys = [k for k, e in before.items() if e.get("platform") in platforms]
            if not keys:
                print("No matching routes. Nothing changed.")
                return
            check_pending(conn, before, keys)
            for key in keys:
                if not conn.execute("SELECT 1 FROM sessions WHERE id=?", (before[key]["session_id"],)).fetchone():
                    raise RuntimeError("Selected route has no SQLite session; reset refused")
            stamp = datetime.now().strftime("%Y%m%d_%H%M%S_%f")
            backup = home / "backups/session-reset" / stamp
            backup.mkdir(parents=True)
            print(f"Backup: {backup}", flush=True)
            with closing(sqlite3.connect(backup / "state.db")) as dest:
                conn.backup(dest)
                check_db(dest)
            mirror = home / "sessions/sessions.json"
            if mirror.exists():
                shutil.copy2(mirror, backup / "sessions.json")
            report = {"status": "prepared", "platforms": sorted(platforms), "routes": len(keys)}
            write_json(backup / "report.json", report)
            with tempfile.TemporaryDirectory(prefix="stage-", dir=backup) as temp:
                stage = Path(temp)
                (stage / "sessions").mkdir()
                shutil.copy2(backup / "state.db", stage / "state.db")
                write_json(stage / "sessions/sessions.json", before)
                scope = str((home / "sessions").resolve())
                with closing(connect(stage / "state.db")) as staged:
                    with staged:
                        staged.execute("UPDATE gateway_routing SET scope=? WHERE scope=?",
                                       (str((stage / "sessions").resolve()), scope))
                env = dict(os.environ, HERMES_HOME=str(stage), PYTHONDONTWRITEBYTECODE="1")
                write_json(stage / "keys.json", keys)
                subprocess.run([sys.executable, str(Path(__file__).resolve()), "--stage", str(stage),
                                "--hermes-root", str(root)], env=env, check=True, timeout=120)
                with closing(connect(stage / "state.db", readonly=True)) as staged:
                    after = read_routes(staged, stage)
                    for key in keys:
                        e = after[key]
                        if not e.get("is_fresh_reset") or e.get("metadata") or e.get("model_override"):
                            raise RuntimeError("Fresh-session verification failed")
                        for field in ("origin", "platform", "display_name", "chat_type"):
                            if e.get(field) != before[key].get(field):
                                raise RuntimeError(f"Native reset changed {field}; reset refused")
                    if read_routes(conn, home) != before:
                        raise RuntimeError("Routes changed while staging; reset refused")
                    report["status"] = "validated_before_commit"
                    report["changes"] = [{"key": k, "old": before[k]["session_id"],
                                          "new": after[k]["session_id"]} for k in keys]
                    write_json(backup / "report.json", report)
                    publish(conn, staged, scope, before, after, keys)
                report["status"] = "database_committed"
                write_json(backup / "report.json", report)
                # DB is authoritative. Never overwrite it to roll back a failed mirror write.
                merged = dict(before)
                merged.update({k: after[k] for k in keys})
                write_json(mirror, merged)
            report["status"] = "complete"
            write_json(backup / "report.json", report)
            print(f"SUCCESS: {len(keys)} routes reset. History retained. No messages sent.")
    finally:
        release_gateway_runtime_lock()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--home", type=Path, default=DEFAULT_HOME)
    parser.add_argument("--hermes-root", type=Path, default=DEFAULT_ROOT)
    parser.add_argument("--platform", choices=("bale", "telegram", "both"), default="bale")
    parser.add_argument("--apply", action="store_true", help="Apply only while gateway is stopped")
    parser.add_argument("--stage", type=Path, help=argparse.SUPPRESS)
    args = parser.parse_args()
    if args.stage:
        stage_reset(args.stage, args.hermes_root, json.loads((args.stage / "keys.json").read_text()))
        return
    home = args.home.resolve()
    platforms = {"bale", "telegram"} if args.platform == "both" else {args.platform}
    if args.apply:
        apply_reset(home, args.hermes_root.resolve(), platforms)
    else:
        with closing(connect(home / "state.db", readonly=True)) as conn:
            routes = read_routes(conn, home)
        counts = Counter(e.get("platform") for e in routes.values())
        print("PREVIEW ONLY - no writes, no gateway stop, no messages")
        print("Available routes:", dict(counts))
        print("Selected platforms:", ", ".join(sorted(platforms)))
        print("Routes to reset:", sum(counts[p] for p in platforms))


if __name__ == "__main__":
    try:
        main()
    except Exception as exc:
        print(f"RESET FAILED: {exc}", file=sys.stderr)
        sys.exit(1)
