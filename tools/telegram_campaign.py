from __future__ import annotations

import csv
import json
import sqlite3
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path


DB_PATH = Path(r"E:\KomatsoAI\reports\telegram_usage\telegram_users.db")
LOG_DIR = Path(r"E:\KomatsoAI\reports\telegram_usage\campaign_logs")

REGISTRATION_MESSAGE = (
    "لطفا نام و نام خانوادگی خود را وارد کنید"
)

VERIFIED_CHECK_MESSAGE = (
    "اطلاعات شما قبلاً ثبت شده است.\n"
    "، لطفاً فقط بنویسید: «دریافت شد»."
)

CONFIRMATION_REMINDER = (
    " فرآیند ثبت مشخصات شما هنوز در مرحله تأیید نام دارد.\n"
    "لطفاً به پیام قبلی با «بله»، «خیر» یا «لغو» پاسخ دهید."
)


def now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

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
    return conn


def log_event(
    conn: sqlite3.Connection,
    telegram_id: str,
    event_type: str,
    value: str = "",
) -> None:
    conn.execute(
        """
        INSERT INTO registration_events (
            telegram_id, event_type, value, created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (telegram_id, event_type, value, now()),
    )


def get_user(conn: sqlite3.Connection, telegram_id: str):
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


def snapshot(row):
    if row is None:
        return None
    return {key: row[key] for key in row.keys()}


def restore_snapshot(
    conn: sqlite3.Connection,
    telegram_id: str,
    previous,
) -> None:
    if previous is None:
        conn.execute(
            "DELETE FROM users WHERE telegram_id = ?",
            (telegram_id,),
        )
    else:
        conn.execute(
            """
            INSERT INTO users (
                telegram_id,
                telegram_display_name,
                verified_name,
                candidate_name,
                registration_status,
                requested_at,
                verified_at,
                updated_at
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(telegram_id)
            DO UPDATE SET
                telegram_display_name = excluded.telegram_display_name,
                verified_name = excluded.verified_name,
                candidate_name = excluded.candidate_name,
                registration_status = excluded.registration_status,
                requested_at = excluded.requested_at,
                verified_at = excluded.verified_at,
                updated_at = excluded.updated_at
            """,
            (
                previous["telegram_id"],
                previous["telegram_display_name"],
                previous["verified_name"],
                previous["candidate_name"],
                previous["registration_status"],
                previous["requested_at"],
                previous["verified_at"],
                previous["updated_at"],
            ),
        )

    log_event(
        conn,
        telegram_id,
        "campaign_send_failed_rolled_back",
    )
    conn.commit()


def arm_registration(
    conn: sqlite3.Connection,
    telegram_id: str,
    display_name: str,
) -> None:
    timestamp = now()

    conn.execute(
        """
        INSERT INTO users (
            telegram_id,
            telegram_display_name,
            registration_status,
            requested_at,
            updated_at
        )
        VALUES (?, ?, 'awaiting_name', ?, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            telegram_display_name =
                CASE
                    WHEN excluded.telegram_display_name <> ''
                    THEN excluded.telegram_display_name
                    ELSE users.telegram_display_name
                END,
            candidate_name = NULL,
            registration_status = 'awaiting_name',
            requested_at = excluded.requested_at,
            updated_at = excluded.updated_at
        """,
        (telegram_id, display_name, timestamp, timestamp),
    )

    log_event(
        conn,
        telegram_id,
        "campaign_registration_requested",
    )
    conn.commit()


def read_recipients(csv_path: Path):
    recipients = []
    seen = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        required = {"telegram_id", "display_name"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise SystemExit(
                "CSV must contain columns: telegram_id,display_name"
            )

        for line_number, row in enumerate(reader, start=2):
            telegram_id = (row.get("telegram_id") or "").strip()
            display_name = (row.get("display_name") or "").strip()

            if not telegram_id:
                continue

            if not telegram_id.isdigit():
                raise SystemExit(
                    f"Invalid Telegram ID at line {line_number}: {telegram_id}"
                )

            if telegram_id in seen:
                raise SystemExit(
                    f"Duplicate Telegram ID in CSV: {telegram_id}"
                )

            seen.add(telegram_id)
            recipients.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                }
            )

    if not recipients:
        raise SystemExit("Recipient CSV is empty.")

    return recipients


def send_telegram(telegram_id: str, message: str):
    completed = subprocess.run(
        [
            "hermes",
            "send",
            "--to",
            f"telegram:{telegram_id}",
            "--json",
            message,
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
    )

    stdout = (completed.stdout or "").strip()
    stderr = (completed.stderr or "").strip()

    payload = None
    if stdout:
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            payload = None

    success = (
        completed.returncode == 0
        and isinstance(payload, dict)
        and payload.get("success") is True
    )

    message_id = ""
    if isinstance(payload, dict):
        message_id = str(payload.get("message_id", ""))

    error = stderr or stdout or f"hermes exit code {completed.returncode}"

    return {
        "success": success,
        "message_id": message_id,
        "error": "" if success else error,
        "returncode": completed.returncode,
    }


def choose_mode(row):
    if row is None:
        return "REGISTRATION"

    status = row["registration_status"] or "none"

    if status == "verified":
        return "VERIFIED_CHECK"

    if status == "awaiting_confirmation":
        return "CONFIRMATION_REMINDER"

    if status == "awaiting_name":
        return "REGISTRATION_REMINDER"

    return "REGISTRATION"


def write_logs(results):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_log = LOG_DIR / f"all13_campaign_{stamp}.csv"
    txt_log = LOG_DIR / f"all13_campaign_{stamp}.txt"

    fields = [
        "telegram_id",
        "display_name",
        "mode",
        "result",
        "message_id",
        "previous_status",
        "final_status",
        "error",
        "time",
    ]

    with csv_log.open("w", encoding="utf-8-sig", newline="") as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    with txt_log.open("w", encoding="utf-8") as file:
        for row in results:
            file.write(
                f"{row['telegram_id']} | "
                f"{row['display_name']} | "
                f"{row['mode']} | "
                f"{row['result']} | "
                f"message_id={row['message_id']} | "
                f"previous={row['previous_status']} | "
                f"final={row['final_status']}\n"
            )
            if row["error"]:
                file.write(f"ERROR: {row['error']}\n")

    return csv_log, txt_log


def campaign(conn: sqlite3.Connection, csv_path: Path) -> None:
    recipients = read_recipients(csv_path)

    preview = []

    print()
    print("=" * 84)
    print("TELEGRAM USER CAMPAIGN - PREVIEW")
    print("=" * 84)

    for index, recipient in enumerate(recipients, start=1):
        telegram_id = recipient["telegram_id"]
        display_name = recipient["display_name"]

        row = get_user(conn, telegram_id)
        status = (
            (row["registration_status"] or "none")
            if row
            else "not-in-registry"
        )
        verified_name = (
            (row["verified_name"] or "")
            if row
            else ""
        )
        mode = choose_mode(row)

        preview.append(
            {
                "telegram_id": telegram_id,
                "display_name": display_name,
                "status": status,
                "verified_name": verified_name,
                "mode": mode,
            }
        )

        extra = f" | verified={verified_name}" if verified_name else ""

        print(
            f"{index:>2}. "
            f"{display_name:<24} "
            f"{telegram_id} | "
            f"status={status} | "
            f"mode={mode}"
            f"{extra}"
        )

    print()
    print("Behavior:")
    print("  REGISTRATION          -> arm user + send name request")
    print("  REGISTRATION_REMINDER -> keep awaiting_name + resend name request")
    print("  CONFIRMATION_REMINDER -> keep current candidate + send confirmation reminder")
    print("  VERIFIED_CHECK        -> do NOT change DB state; send delivery-check message only")
    print()
    print("Nothing has been sent yet.")
    print()

    confirmation = input(
        "Type SEND exactly to continue, or press Enter to cancel: "
    ).strip()

    if confirmation != "SEND":
        print("Cancelled. Nothing was sent.")
        return

    results = []

    for item in preview:
        telegram_id = item["telegram_id"]
        display_name = item["display_name"]
        previous_status = item["status"]
        mode = item["mode"]

        row_before = get_user(conn, telegram_id)
        previous = snapshot(row_before)
        changed_state = False

        if mode == "REGISTRATION":
            arm_registration(
                conn,
                telegram_id,
                display_name,
            )
            changed_state = True
            message = REGISTRATION_MESSAGE

        elif mode == "REGISTRATION_REMINDER":
            message = REGISTRATION_MESSAGE

        elif mode == "CONFIRMATION_REMINDER":
            message = CONFIRMATION_REMINDER

        else:
            message = VERIFIED_CHECK_MESSAGE

        sent = send_telegram(
            telegram_id,
            message,
        )

        if sent["success"]:
            event_type = {
                "REGISTRATION": "campaign_message_sent",
                "REGISTRATION_REMINDER": "campaign_registration_reminder_sent",
                "CONFIRMATION_REMINDER": "campaign_confirmation_reminder_sent",
                "VERIFIED_CHECK": "campaign_verified_delivery_check_sent",
            }[mode]

            log_event(
                conn,
                telegram_id,
                event_type,
                sent["message_id"],
            )
            conn.commit()

            row_after = get_user(conn, telegram_id)
            final_status = (
                (row_after["registration_status"] or "none")
                if row_after
                else "not-in-registry"
            )

            results.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                    "mode": mode,
                    "result": "SENT",
                    "message_id": sent["message_id"],
                    "previous_status": previous_status,
                    "final_status": final_status,
                    "error": "",
                    "time": now(),
                }
            )

            print(
                f"SENT: {display_name} ({telegram_id}) "
                f"mode={mode} message_id={sent['message_id']}"
            )

        else:
            if changed_state:
                restore_snapshot(
                    conn,
                    telegram_id,
                    previous,
                )

            row_after = get_user(conn, telegram_id)
            final_status = (
                (row_after["registration_status"] or "none")
                if row_after
                else "not-in-registry"
            )

            results.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                    "mode": mode,
                    "result": "FAILED",
                    "message_id": "",
                    "previous_status": previous_status,
                    "final_status": final_status,
                    "error": sent["error"],
                    "time": now(),
                }
            )

            print(
                f"FAILED: {display_name} ({telegram_id}) "
                f"mode={mode}"
            )

        time.sleep(0.5)

    csv_log, txt_log = write_logs(results)

    sent_count = sum(row["result"] == "SENT" for row in results)
    failed_count = len(results) - sent_count

    print()
    print("=" * 84)
    print("CAMPAIGN COMPLETE")
    print("=" * 84)
    print("Sent   :", sent_count)
    print("Failed :", failed_count)
    print("CSV log:", csv_log)
    print("TXT log:", txt_log)
    print()


def usage() -> None:
    print(
        "Usage:\n"
        "  telegram_admin_campaign_all13.py campaign RECIPIENTS.csv"
    )


def main() -> None:
    if len(sys.argv) != 3 or sys.argv[1].lower() != "campaign":
        usage()
        raise SystemExit(2)

    csv_path = Path(sys.argv[2])

    if not csv_path.exists():
        raise SystemExit(f"Recipients file not found: {csv_path}")

    conn = connect()

    try:
        campaign(conn, csv_path)
    finally:
        conn.close()


if __name__ == "__main__":
    main()
