"""Create work-order authorization storage, without granting any user access.

Preview: python -m tools.fleet.work_orders.migrations.002_create_work_order_permissions
Apply:   append --apply (backs up the database before changing its schema).
"""

from __future__ import annotations

import argparse
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

from tools.fleet.work_orders.core.paths import DB_PATH


def create_schema(con: sqlite3.Connection) -> None:
    con.execute(
        """
        CREATE TABLE IF NOT EXISTS service_work_order_users (
            bale_id TEXT PRIMARY KEY NOT NULL,
            role TEXT NOT NULL,
            active INTEGER NOT NULL DEFAULT 0 CHECK (active IN (0, 1)),
            created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DB_PATH)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    if not args.apply:
        print("Preview only: create service_work_order_users; grant no access.")
        print("Use --apply to back up the database and apply the schema.")
        return

    con = sqlite3.connect(args.db.resolve().as_uri() + "?mode=rw", uri=True)
    try:
        exists = con.execute(
            "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
            ("service_work_order_users",),
        ).fetchone()
        if exists:
            print("Permission table already exists; no changes made.")
            return
        backup_dir = args.db.parent / "backups"
        backup_dir.mkdir(parents=True, exist_ok=True)
        stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S_%f")
        backup_path = backup_dir / f"fleet_ops_before_permissions_{stamp}.db"
        backup = sqlite3.connect(backup_path)
        try:
            con.backup(backup)
        finally:
            backup.close()
        with con:
            create_schema(con)
        print(f"Backup: {backup_path}")
        print("Permission table created. No users added and no access granted.")
    finally:
        con.close()


if __name__ == "__main__":
    main()
