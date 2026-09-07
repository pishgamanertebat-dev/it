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
    "سلام. جهت تکمیل اطلاعات کاربران سامانه پشتیبانی فنی، لطفاً نام و نام "
    "خانوادگی خود را به‌صورت کامل ارسال فرمایید.\n\n"
    "پس از ارسال نام، برای تأیید دوباره از شما سؤال می‌شود.\n"
    "اگر تمایلی به ثبت مشخصات ندارید یا می‌خواهید مستقیماً سؤال فنی بپرسید، "
    "فقط کلمه «لغو» را ارسال کنید؛ پس از آن ربات مانند قبل در دسترس شماست."
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


def validate_telegram_id(telegram_id: str) -> str:
    telegram_id = telegram_id.strip()

    if not telegram_id or not telegram_id.isdigit():
        raise SystemExit(f"Invalid Telegram ID: {telegram_id!r}")

    return telegram_id


def ensure_user(conn: sqlite3.Connection, telegram_id: str) -> None:
    telegram_id = validate_telegram_id(telegram_id)

    conn.execute(
        """
        INSERT INTO users (
            telegram_id, registration_status, updated_at
        )
        VALUES (?, 'none', ?)
        ON CONFLICT(telegram_id) DO NOTHING
        """,
        (telegram_id, now()),
    )
    conn.commit()


def add_user(
    conn: sqlite3.Connection,
    telegram_id: str,
    display_name: str,
) -> None:
    telegram_id = validate_telegram_id(telegram_id)
    display_name = display_name.strip()

    if not display_name:
        raise SystemExit("Display name cannot be empty.")

    existing = get_user(conn, telegram_id)
    timestamp = now()

    if existing is None:
        conn.execute(
            """
            INSERT INTO users (
                telegram_id,
                telegram_display_name,
                registration_status,
                updated_at
            )
            VALUES (?, ?, 'none', ?)
            """,
            (telegram_id, display_name, timestamp),
        )
        log_event(
            conn,
            telegram_id,
            "admin_user_added",
            display_name,
        )
        result = "USER ADDED TO MASTER REGISTRY."
    else:
        old_display_name = existing["telegram_display_name"] or ""

        conn.execute(
            """
            UPDATE users
            SET
                telegram_display_name = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (display_name, timestamp, telegram_id),
        )

        log_event(
            conn,
            telegram_id,
            "admin_user_display_name_updated",
            json.dumps(
                {
                    "old": old_display_name,
                    "new": display_name,
                },
                ensure_ascii=False,
            ),
        )
        result = "USER ALREADY EXISTS. DISPLAY NAME UPDATED."

    conn.commit()

    print(result)
    show_user(get_user(conn, telegram_id))


def show_user(row) -> None:
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


def arm(conn: sqlite3.Connection, telegram_id: str) -> None:
    ensure_user(conn, telegram_id)
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
        (timestamp, timestamp, telegram_id),
    )
    log_event(conn, telegram_id, "admin_registration_requested")
    conn.commit()

    print("Registration flow ARMED.")
    show_user(get_user(conn, telegram_id))


def release(conn: sqlite3.Connection, telegram_id: str) -> None:
    ensure_user(conn, telegram_id)

    conn.execute(
        """
        UPDATE users
        SET
            candidate_name = NULL,
            registration_status = 'declined',
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (now(), telegram_id),
    )
    log_event(conn, telegram_id, "admin_released")
    conn.commit()

    print("User RELEASED. Future messages go to the normal agent.")
    show_user(get_user(conn, telegram_id))


def set_name(
    conn: sqlite3.Connection,
    telegram_id: str,
    full_name: str,
) -> None:
    ensure_user(conn, telegram_id)

    full_name = full_name.strip()
    if not full_name:
        raise SystemExit("Verified name cannot be empty.")

    timestamp = now()

    conn.execute(
        """
        UPDATE users
        SET
            verified_name = ?,
            candidate_name = NULL,
            registration_status = 'verified',
            verified_at = ?,
            updated_at = ?
        WHERE telegram_id = ?
        """,
        (full_name, timestamp, timestamp, telegram_id),
    )
    log_event(conn, telegram_id, "admin_name_set", full_name)
    conn.commit()

    print("Name saved by administrator.")
    show_user(get_user(conn, telegram_id))


def list_users(conn: sqlite3.Connection) -> None:
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


def read_recipients(csv_path: Path):
    recipients = []
    seen = set()

    with csv_path.open("r", encoding="utf-8-sig", newline="") as file:
        reader = csv.DictReader(file)

        required = {"telegram_id", "display_name"}
        if not reader.fieldnames or not required.issubset(reader.fieldnames):
            raise SystemExit(
                "CSV must contain: telegram_id,display_name"
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
                    f"Duplicate Telegram ID: {telegram_id}"
                )

            seen.add(telegram_id)
            recipients.append((telegram_id, display_name))

    if not recipients:
        raise SystemExit("Recipient list is empty.")

    return recipients


def snapshot(row):
    if row is None:
        return None

    return {key: row[key] for key in row.keys()}


def arm_campaign_user(
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


def restore_snapshot(
    conn: sqlite3.Connection,
    telegram_id: str,
    previous,
) -> None:
    if previous is None:
        # A campaign recipient is already a known contact even if Telegram
        # delivery fails. Keep the user in the canonical registry instead
        # of deleting them and leaving their identity only inside CSV/logs.
        conn.execute(
            """
            UPDATE users
            SET
                candidate_name = NULL,
                registration_status = 'none',
                requested_at = NULL,
                verified_at = NULL,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (now(), telegram_id),
        )
        log_event(
            conn,
            telegram_id,
            "campaign_send_failed_user_preserved",
        )
    else:
        conn.execute(
            """
            UPDATE users
            SET
                telegram_display_name = ?,
                verified_name = ?,
                candidate_name = ?,
                registration_status = ?,
                requested_at = ?,
                verified_at = ?,
                updated_at = ?
            WHERE telegram_id = ?
            """,
            (
                previous["telegram_display_name"],
                previous["verified_name"],
                previous["candidate_name"],
                previous["registration_status"],
                previous["requested_at"],
                previous["verified_at"],
                previous["updated_at"],
                telegram_id,
            ),
        )
        log_event(
            conn,
            telegram_id,
            "campaign_send_failed_rolled_back",
        )

    conn.commit()


def send_telegram_message(
    telegram_id: str,
    message: str,
):
    telegram_id = validate_telegram_id(telegram_id)
    message = message.strip()

    if not message:
        raise SystemExit("Message text cannot be empty.")

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
            pass

    success = (
        completed.returncode == 0
        and isinstance(payload, dict)
        and payload.get("success") is True
    )

    message_id = (
        str(payload.get("message_id", ""))
        if isinstance(payload, dict)
        else ""
    )

    error = stderr or stdout

    return success, message_id, error, completed.returncode


def send_registration_message(telegram_id: str):
    return send_telegram_message(
        telegram_id,
        REGISTRATION_MESSAGE,
    )


def message_user(
    conn: sqlite3.Connection,
    telegram_id: str,
    message: str,
) -> None:
    telegram_id = validate_telegram_id(telegram_id)
    message = message.strip()

    if not message:
        raise SystemExit("Message text cannot be empty.")

    row = get_user(conn, telegram_id)
    if row is None:
        raise SystemExit(
            'USER NOT FOUND IN MASTER REGISTRY. '
            'Use: telegram_admin.py add TELEGRAM_ID "DISPLAY NAME"'
        )

    status_before = row["registration_status"] or "none"

    success, message_id, error, exit_code = send_telegram_message(
        telegram_id,
        message,
    )

    if success:
        log_event(
            conn,
            telegram_id,
            "admin_message_sent",
            json.dumps(
                {
                    "message_id": message_id,
                    "text": message,
                },
                ensure_ascii=False,
            ),
        )
        conn.commit()

        print("MESSAGE SENT.")
        print("Telegram ID :", telegram_id)
        print("Message ID  :", message_id)
        print("Status      :", status_before, "(unchanged)")
        return

    if not error:
        error = f"hermes exit code {exit_code}"

    log_event(
        conn,
        telegram_id,
        "admin_message_failed",
        json.dumps(
            {
                "exit_code": exit_code,
                "error": error[:2000],
                "text": message,
            },
            ensure_ascii=False,
        ),
    )
    conn.commit()

    print("MESSAGE FAILED.")
    print("Telegram ID :", telegram_id)
    print("Status      :", status_before, "(unchanged)")
    print("Exit code   :", exit_code)
    print("Error       :", error)
    raise SystemExit(2)


def write_campaign_logs(results):
    LOG_DIR.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")

    csv_path = LOG_DIR / f"registration_campaign_{stamp}.csv"
    txt_path = LOG_DIR / f"registration_campaign_{stamp}.txt"

    fields = [
        "telegram_id",
        "display_name",
        "result",
        "message_id",
        "previous_status",
        "final_status",
        "error",
        "time",
    ]

    with csv_path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as file:
        writer = csv.DictWriter(file, fieldnames=fields)
        writer.writeheader()
        writer.writerows(results)

    sent = sum(row["result"] == "SENT" for row in results)
    failed = sum(row["result"] == "FAILED" for row in results)
    skipped = len(results) - sent - failed

    with txt_path.open("w", encoding="utf-8") as file:
        file.write(f"Sent: {sent}\n")
        file.write(f"Failed: {failed}\n")
        file.write(f"Skipped: {skipped}\n\n")

        for row in results:
            file.write(
                f"{row['telegram_id']} | "
                f"{row['display_name']} | "
                f"{row['result']} | "
                f"message_id={row['message_id']} | "
                f"previous={row['previous_status']} | "
                f"final={row['final_status']}\n"
            )

            if row["error"]:
                file.write(f"ERROR: {row['error']}\n")

    return csv_path, txt_path


def campaign(
    conn: sqlite3.Connection,
    csv_path: Path,
) -> None:
    recipients = read_recipients(csv_path)

    print("=" * 72)
    print("REGISTRATION CAMPAIGN PREVIEW")
    print("=" * 72)

    preview = []

    for index, (telegram_id, display_name) in enumerate(
        recipients,
        start=1,
    ):
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

        preview.append(
            (
                telegram_id,
                display_name,
                status,
                verified_name,
            )
        )

        extra = (
            f" | verified={verified_name}"
            if verified_name
            else ""
        )

        print(
            f"{index:>2}. "
            f"{display_name:<24} "
            f"{telegram_id} | "
            f"status={status}"
            f"{extra}"
        )

    print()
    print("MESSAGE")
    print("-" * 72)
    print(REGISTRATION_MESSAGE)
    print("-" * 72)
    print()
    print(
        "Verified users and users already inside registration "
        "are skipped."
    )
    print(
        "If delivery fails, that user's previous registry state "
        "is restored."
    )
    print()

    confirmation = input(
        "Type SEND exactly to continue, or press Enter to cancel: "
    ).strip()

    if confirmation != "SEND":
        print("Cancelled. Nothing was sent.")
        return

    results = []

    for telegram_id, display_name, previous_status, _ in preview:
        row = get_user(conn, telegram_id)

        if row and row["registration_status"] == "verified":
            results.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                    "result": "SKIPPED_VERIFIED",
                    "message_id": "",
                    "previous_status": previous_status,
                    "final_status": "verified",
                    "error": "",
                    "time": now(),
                }
            )
            print(
                f"SKIP verified: {display_name} ({telegram_id})"
            )
            continue

        if row and row["registration_status"] in {
            "awaiting_name",
            "awaiting_confirmation",
        }:
            current_status = row["registration_status"]

            results.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                    "result": "SKIPPED_PENDING",
                    "message_id": "",
                    "previous_status": previous_status,
                    "final_status": current_status,
                    "error": "",
                    "time": now(),
                }
            )
            print(
                f"SKIP pending:  {display_name} ({telegram_id})"
            )
            continue

        previous = snapshot(row)

        arm_campaign_user(
            conn,
            telegram_id,
            display_name,
        )

        success, message_id, error, exit_code = (
            send_registration_message(telegram_id)
        )

        if success:
            log_event(
                conn,
                telegram_id,
                "campaign_message_sent",
                message_id,
            )
            conn.commit()

            results.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                    "result": "SENT",
                    "message_id": message_id,
                    "previous_status": previous_status,
                    "final_status": "awaiting_name",
                    "error": "",
                    "time": now(),
                }
            )

            print(
                f"SENT:          "
                f"{display_name} "
                f"({telegram_id}) "
                f"message_id={message_id}"
            )

        else:
            restore_snapshot(
                conn,
                telegram_id,
                previous,
            )

            restored = get_user(conn, telegram_id)

            final_status = (
                (restored["registration_status"] or "none")
                if restored
                else "not-in-registry"
            )

            if not error:
                error = f"hermes exit code {exit_code}"

            results.append(
                {
                    "telegram_id": telegram_id,
                    "display_name": display_name,
                    "result": "FAILED",
                    "message_id": "",
                    "previous_status": previous_status,
                    "final_status": final_status,
                    "error": error,
                    "time": now(),
                }
            )

            print(
                f"FAILED:        "
                f"{display_name} "
                f"({telegram_id})"
            )

        time.sleep(0.5)

    csv_log, txt_log = write_campaign_logs(results)

    sent = sum(
        row["result"] == "SENT"
        for row in results
    )
    failed = sum(
        row["result"] == "FAILED"
        for row in results
    )
    skipped = len(results) - sent - failed

    print()
    print("=" * 72)
    print("CAMPAIGN COMPLETE")
    print("=" * 72)
    print("Sent   :", sent)
    print("Failed :", failed)
    print("Skipped:", skipped)
    print("CSV log:", csv_log)
    print("TXT log:", txt_log)


def usage() -> None:
    print(
        "Usage:\n"
        "  telegram_admin.py list\n"
        "  telegram_admin.py status TELEGRAM_ID\n"
        "  telegram_admin.py add TELEGRAM_ID \"DISPLAY NAME\"\n"
        "  telegram_admin.py message TELEGRAM_ID \"TEXT\"\n"
        "  telegram_admin.py arm TELEGRAM_ID\n"
        "  telegram_admin.py release TELEGRAM_ID\n"
        "  telegram_admin.py set-name TELEGRAM_ID \"FULL NAME\"\n"
        "  telegram_admin.py campaign RECIPIENTS.csv"
    )


def main() -> None:
    if len(sys.argv) < 2:
        usage()
        raise SystemExit(1)

    action = sys.argv[1].lower()
    conn = connect()

    try:
        if action == "list":
            list_users(conn)
            return

        if action == "campaign":
            if len(sys.argv) != 3:
                usage()
                raise SystemExit(1)

            campaign(conn, Path(sys.argv[2]))
            return

        if action == "set-name":
            if len(sys.argv) < 4:
                usage()
                raise SystemExit(1)

            set_name(
                conn,
                sys.argv[2].strip(),
                " ".join(sys.argv[3:]),
            )
            return

        if action == "add":
            if len(sys.argv) < 4:
                usage()
                raise SystemExit(1)

            add_user(
                conn,
                sys.argv[2].strip(),
                " ".join(sys.argv[3:]),
            )
            return

        if action == "message":
            if len(sys.argv) < 4:
                usage()
                raise SystemExit(1)

            message_user(
                conn,
                sys.argv[2].strip(),
                " ".join(sys.argv[3:]),
            )
            return

        if len(sys.argv) != 3:
            usage()
            raise SystemExit(1)

        telegram_id = sys.argv[2].strip()

        if action == "status":
            show_user(
                get_user(conn, telegram_id)
            )
        elif action == "arm":
            arm(conn, telegram_id)
        elif action == "release":
            release(conn, telegram_id)
        else:
            usage()
            raise SystemExit(
                f"Unknown action: {action}"
            )

    finally:
        conn.close()


if __name__ == "__main__":
    main()
