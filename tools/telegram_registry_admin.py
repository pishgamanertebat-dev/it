from __future__ import annotations

import sqlite3
import sys
from datetime import datetime
from pathlib import Path


DB_PATH = Path(r"E:\KomatsoAI\data\telegram_users.db")


def now():
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect():
    if not DB_PATH.exists():
        raise SystemExit(f"Database not found: {DB_PATH}")

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    return conn


def ensure_events_table(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS registration_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            telegram_id TEXT NOT NULL,
            event_type TEXT NOT NULL,
            value TEXT,
            created_at TEXT NOT NULL
        )
        """
    )
    conn.commit()


def log_event(conn, telegram_id, event_type, value=""):
    conn.execute(
        """
        INSERT INTO registration_events (
            telegram_id,
            event_type,
            value,
            created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (
            telegram_id,
            event_type,
            value,
            now(),
        ),
    )


def get_user(conn, telegram_id):
    return conn.execute(
        """
        SELECT
            telegram_id,
            telegram_display_name,
            verified_name,
            candidate_name,
            registration_status,
            requested_at,
            verified_at,
            updated_at
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()


def print_user(row):
    if not row:
        print("USER NOT FOUND")
        return

    print()
    print("Telegram ID :", row["telegram_id"])
    print("Telegram    :", row["telegram_display_name"] or "")
    print("Verified    :", row["verified_name"] or "")
    print("Candidate   :", row["candidate_name"] or "")
    print("Status      :", row["registration_status"] or "")
    print("Requested   :", row["requested_at"] or "")
    print("Verified At :", row["verified_at"] or "")
    print("Updated     :", row["updated_at"] or "")
    print()


def arm(conn, telegram_id):
    row = get_user(conn, telegram_id)

    if not row:
        raise SystemExit("User not found in registry.")

    # verified_name را عمداً پاک نمی‌کنیم.
    # تا زمانی که اسم جدید تأیید نشده،
    # آخرین اسم تأییدشده قبلی را از دست نمی‌دهیم.

    timestamp = now()

    conn.execute(
        """
        UPDATE users
        SET
            candidate_name = NULL,
            registration_status = 'awaiting_name',
            requested_at = ?,
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (
            timestamp,
            timestamp,
            telegram_id,
        ),
    )

    log_event(
        conn,
        telegram_id,
        "admin_registration_requested",
    )

    conn.commit()

    print("Registration flow ARMED.")
    print_user(get_user(conn, telegram_id))


def release(conn, telegram_id):
    row = get_user(conn, telegram_id)

    if not row:
        raise SystemExit("User not found in registry.")

    timestamp = now()

    conn.execute(
        """
        UPDATE users
        SET
            candidate_name = NULL,
            registration_status = 'declined',
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (
            timestamp,
            telegram_id,
        ),
    )

    log_event(
        conn,
        telegram_id,
        "admin_released",
    )

    conn.commit()

    print("User RELEASED from registration flow.")
    print("Future messages will go to the normal agent.")
    print_user(get_user(conn, telegram_id))


def status(conn, telegram_id):
    print_user(get_user(conn, telegram_id))


def list_users(conn):
    rows = conn.execute(
        """
        SELECT
            telegram_id,
            telegram_display_name,
            verified_name,
            registration_status
        FROM users
        ORDER BY updated_at DESC
        """
    ).fetchall()

    print()

    for row in rows:
        print(
            f"{row['telegram_id']} | "
            f"{row['telegram_display_name'] or ''} | "
            f"{row['verified_name'] or ''} | "
            f"{row['registration_status'] or ''}"
        )

    print()


def main():
    if len(sys.argv) < 2:
        print(
            "Usage:\n"
            "  telegram_registry_admin.py arm TELEGRAM_ID\n"
            "  telegram_registry_admin.py release TELEGRAM_ID\n"
            "  telegram_registry_admin.py status TELEGRAM_ID\n"
            "  telegram_registry_admin.py list"
        )
        raise SystemExit(1)

    action = sys.argv[1].lower()

    conn = connect()

    try:
        ensure_events_table(conn)

        if action == "list":
            list_users(conn)
            return

        if len(sys.argv) != 3:
            raise SystemExit("Telegram ID is required.")

        telegram_id = sys.argv[2].strip()

        if action == "arm":
            arm(conn, telegram_id)

        elif action == "release":
            release(conn, telegram_id)

        elif action == "status":
            status(conn, telegram_id)

        else:
            raise SystemExit(f"Unknown action: {action}")

    finally:
        conn.close()


if __name__ == "__main__":
    main()