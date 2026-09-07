from pathlib import Path
import sqlite3
from datetime import datetime


DB_PATH = Path(r"E:\KomatsoAI\data\telegram_users.db")

TELEGRAM_ID = "8745155372"
DISPLAY_NAME = "Pishgaman HR"


def main():
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)

    try:
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS users (
                telegram_id TEXT PRIMARY KEY,
                telegram_display_name TEXT,
                verified_name TEXT,
                candidate_name TEXT,
                registration_status TEXT NOT NULL DEFAULT 'none',
                requested_at TEXT,
                verified_at TEXT,
                updated_at TEXT
            )
            """
        )

        now = datetime.now().astimezone().isoformat(timespec="seconds")

        conn.execute(
            """
            INSERT INTO users (
                telegram_id,
                telegram_display_name,
                registration_status,
                requested_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?)

            ON CONFLICT(telegram_id)
            DO UPDATE SET
                telegram_display_name = excluded.telegram_display_name,
                candidate_name = NULL,
                registration_status = excluded.registration_status,
                requested_at = excluded.requested_at,
                updated_at = excluded.updated_at
            """,
            (
                TELEGRAM_ID,
                DISPLAY_NAME,
                "awaiting_name",
                now,
                now,
            ),
        )

        conn.commit()

        row = conn.execute(
            """
            SELECT
                telegram_id,
                telegram_display_name,
                verified_name,
                candidate_name,
                registration_status
            FROM users
            WHERE telegram_id = ?
            """,
            (TELEGRAM_ID,),
        ).fetchone()

        print(row)

    finally:
        conn.close()


if __name__ == "__main__":
    main()