from __future__ import annotations

import csv
import json
import os
import sqlite3
import urllib.parse
import urllib.request
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


# ============================================================
# CONFIG
# ============================================================

HERMES_HOME = Path(r"C:\Users\win-10\AppData\Local\hermes")
DB_PATH = HERMES_HOME / "state.db"
ENV_PATH = HERMES_HOME / ".env"

OUTPUT_ROOT = Path(r"E:\KomatsoAI\reports\telegram_usage")

TELEGRAM_API_TIMEOUT = 15


# ============================================================
# HELPERS
# ============================================================

def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d_%H%M%S")


def safe_json(value: Any) -> dict:
    if not value:
        return {}

    if isinstance(value, dict):
        return value

    try:
        obj = json.loads(value)
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def format_timestamp(value: Any) -> str:
    if value is None or value == "":
        return ""

    try:
        if isinstance(value, (int, float)):
            return (
                datetime.fromtimestamp(float(value), timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds")
            )

        text = str(value).strip()

        try:
            number = float(text)
            return (
                datetime.fromtimestamp(number, timezone.utc)
                .astimezone()
                .isoformat(timespec="seconds")
            )
        except Exception:
            return text

    except Exception:
        return str(value)


def load_env_value(path: Path, key: str) -> str | None:
    if not path.exists():
        return None

    for raw_line in path.read_text(
        encoding="utf-8",
        errors="replace",
    ).splitlines():

        line = raw_line.strip()

        if not line or line.startswith("#"):
            continue

        if line.startswith("export "):
            line = line[7:].strip()

        if "=" not in line:
            continue

        k, value = line.split("=", 1)

        if k.strip() != key:
            continue

        value = value.strip()

        if (
            len(value) >= 2
            and value[0] == value[-1]
            and value[0] in {"'", '"'}
        ):
            value = value[1:-1]

        return value

    return None


def table_columns(
    conn: sqlite3.Connection,
    table: str,
) -> list[str]:

    rows = conn.execute(
        f'PRAGMA table_info("{table}")'
    ).fetchall()

    return [row[1] for row in rows]


def table_exists(
    conn: sqlite3.Connection,
    table: str,
) -> bool:

    row = conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type='table' AND name=?
        LIMIT 1
        """,
        (table,),
    ).fetchone()

    return row is not None


def write_csv(
    path: Path,
    rows: list[dict],
    fieldnames: list[str] | None = None,
) -> None:

    path.parent.mkdir(parents=True, exist_ok=True)

    if fieldnames is None:
        ordered = []

        for row in rows:
            for key in row.keys():
                if key not in ordered:
                    ordered.append(key)

        fieldnames = ordered

    with path.open(
        "w",
        encoding="utf-8-sig",
        newline="",
    ) as f:

        writer = csv.DictWriter(
            f,
            fieldnames=fieldnames,
            extrasaction="ignore",
        )

        writer.writeheader()

        for row in rows:
            writer.writerow(row)


# ============================================================
# TELEGRAM API
# ============================================================

def telegram_get_chat(
    bot_token: str,
    chat_id: str,
) -> tuple[dict, str]:

    if not bot_token:
        return {}, "TELEGRAM_BOT_TOKEN not found"

    url = (
        "https://api.telegram.org/bot"
        + bot_token
        + "/getChat"
    )

    body = urllib.parse.urlencode(
        {"chat_id": chat_id}
    ).encode("utf-8")

    req = urllib.request.Request(
        url,
        data=body,
        method="POST",
    )

    try:
        with urllib.request.urlopen(
            req,
            timeout=TELEGRAM_API_TIMEOUT,
        ) as response:

            payload = json.loads(
                response.read().decode("utf-8")
            )

    except Exception as exc:
        return {}, f"{type(exc).__name__}: {exc}"

    if not payload.get("ok"):
        return {}, str(
            payload.get("description")
            or "Telegram API returned ok=false"
        )

    result = payload.get("result") or {}

    if not isinstance(result, dict):
        return {}, "Unexpected Telegram API response"

    return result, ""


def extract_telegram_profile(
    data: dict,
) -> dict:

    first_name = str(
        data.get("first_name") or ""
    ).strip()

    last_name = str(
        data.get("last_name") or ""
    ).strip()

    username = str(
        data.get("username") or ""
    ).strip()

    full_name = " ".join(
        x for x in [first_name, last_name] if x
    )

    birthdate = data.get("birthdate")

    if isinstance(birthdate, dict):
        birthdate_text = "-".join(
            str(x)
            for x in [
                birthdate.get("year") or "",
                birthdate.get("month") or "",
                birthdate.get("day") or "",
            ]
            if x != ""
        )
    else:
        birthdate_text = ""

    active_usernames = data.get("active_usernames")

    if isinstance(active_usernames, list):
        active_usernames_text = ", ".join(
            str(x) for x in active_usernames
        )
    else:
        active_usernames_text = ""

    return {
        "telegram_first_name": first_name,
        "telegram_last_name": last_name,
        "telegram_full_name": full_name,
        "telegram_username": username,
        "telegram_bio": str(data.get("bio") or ""),
        "telegram_birthdate": birthdate_text,
        "telegram_active_usernames": active_usernames_text,
        "telegram_chat_type": str(data.get("type") or ""),
    }


# ============================================================
# DATABASE
# ============================================================

def create_database_backup(
    source: sqlite3.Connection,
    destination: Path,
) -> None:

    backup_conn = sqlite3.connect(destination)

    try:
        source.backup(backup_conn)
    finally:
        backup_conn.close()


def get_telegram_sessions(
    conn: sqlite3.Connection,
) -> list[dict]:

    if not table_exists(conn, "sessions"):
        raise RuntimeError(
            "sessions table not found in state.db"
        )

    conn.row_factory = sqlite3.Row

    columns = table_columns(conn, "sessions")

    if "source" not in columns:
        raise RuntimeError(
            "sessions.source column not found"
        )

    rows = conn.execute(
        """
        SELECT *
        FROM sessions
        WHERE source = 'telegram'
        ORDER BY started_at
        """
    ).fetchall()

    result = []

    for raw in rows:
        row = dict(raw)

        origin = safe_json(
            row.get("origin_json")
        )

        user_id = (
            row.get("user_id")
            or origin.get("user_id")
            or ""
        )

        chat_id = (
            row.get("chat_id")
            or origin.get("chat_id")
            or user_id
            or ""
        )

        display_name = (
            row.get("display_name")
            or origin.get("user_name")
            or origin.get("chat_name")
            or ""
        )

        result.append(
            {
                **row,

                "resolved_user_id": str(user_id),
                "resolved_chat_id": str(chat_id),
                "resolved_display_name": str(display_name),

                "origin_user_name": str(
                    origin.get("user_name") or ""
                ),

                "origin_chat_name": str(
                    origin.get("chat_name") or ""
                ),

                "started_at_readable": format_timestamp(
                    row.get("started_at")
                ),

                "ended_at_readable": format_timestamp(
                    row.get("ended_at")
                ),
            }
        )

    return result


def get_user_messages(
    conn: sqlite3.Connection,
    sessions: list[dict],
) -> list[dict]:

    if not table_exists(conn, "messages"):
        return []

    columns = table_columns(conn, "messages")

    if "session_id" not in columns:
        return []

    session_map = {
        str(row.get("id")): row
        for row in sessions
        if row.get("id") is not None
    }

    if not session_map:
        return []

    conn.row_factory = sqlite3.Row

    rows = conn.execute(
        """
        SELECT *
        FROM messages
        WHERE role = 'user'
        ORDER BY id
        """
    ).fetchall()

    timestamp_candidates = [
        "created_at",
        "timestamp",
        "time",
        "ts",
    ]

    timestamp_column = next(
        (
            name
            for name in timestamp_candidates
            if name in columns
        ),
        None,
    )

    result = []

    for raw in rows:
        message = dict(raw)

        session_id = str(
            message.get("session_id") or ""
        )

        session = session_map.get(session_id)

        if session is None:
            continue

        timestamp_value = (
            message.get(timestamp_column)
            if timestamp_column
            else ""
        )

        result.append(
            {
                "message_db_id": message.get("id", ""),
                "session_id": session_id,

                "telegram_user_id":
                    session.get("resolved_user_id", ""),

                "telegram_chat_id":
                    session.get("resolved_chat_id", ""),

                "display_name":
                    session.get(
                        "resolved_display_name",
                        ""
                    ),

                "session_title":
                    session.get("title", ""),

                "message_time":
                    format_timestamp(timestamp_value),

                "content":
                    message.get("content", ""),
            }
        )

    return result


# ============================================================
# USER SUMMARY
# ============================================================

def build_user_summary(
    sessions: list[dict],
    user_messages: list[dict],
    telegram_profiles: dict[str, dict],
    telegram_errors: dict[str, str],
) -> list[dict]:

    grouped = defaultdict(list)

    for session in sessions:
        key = (
            session.get("resolved_user_id")
            or session.get("resolved_chat_id")
            or "UNKNOWN"
        )

        grouped[str(key)].append(session)

    question_counts = defaultdict(int)

    for message in user_messages:
        key = str(
            message.get("telegram_user_id")
            or message.get("telegram_chat_id")
            or "UNKNOWN"
        )

        question_counts[key] += 1

    output = []

    for user_id, items in grouped.items():

        names = sorted(
            {
                str(
                    x.get(
                        "resolved_display_name",
                        ""
                    )
                ).strip()
                for x in items
                if str(
                    x.get(
                        "resolved_display_name",
                        ""
                    )
                ).strip()
            }
        )

        chat_ids = sorted(
            {
                str(
                    x.get(
                        "resolved_chat_id",
                        ""
                    )
                ).strip()
                for x in items
                if str(
                    x.get(
                        "resolved_chat_id",
                        ""
                    )
                ).strip()
            }
        )

        titles = [
            str(x.get("title") or "").strip()
            for x in items
            if str(x.get("title") or "").strip()
        ]

        session_ids = [
            str(x.get("id") or "")
            for x in items
            if x.get("id")
        ]

        started_values = [
            x.get("started_at")
            for x in items
            if x.get("started_at") is not None
        ]

        ended_values = [
            x.get("ended_at")
            for x in items
            if x.get("ended_at") is not None
        ]

        try:
            first_seen = (
                format_timestamp(
                    min(float(x) for x in started_values)
                )
                if started_values
                else ""
            )
        except Exception:
            first_seen = ""

        try:
            last_closed_session = (
                format_timestamp(
                    max(float(x) for x in ended_values)
                )
                if ended_values
                else ""
            )
        except Exception:
            last_closed_session = ""

        db_message_count = 0

        for x in items:
            try:
                db_message_count += int(
                    x.get("message_count") or 0
                )
            except Exception:
                pass

        profile = telegram_profiles.get(
            user_id,
            {},
        )

        row = {
            "telegram_user_id": user_id,

            "telegram_chat_ids":
                ", ".join(chat_ids),

            "hermes_display_names":
                " | ".join(names),

            **profile,

            "session_count": len(items),

            "user_message_count":
                question_counts.get(user_id, 0),

            "hermes_total_message_count":
                db_message_count,

            "first_seen":
                first_seen,

            "last_closed_session":
                last_closed_session,

            "session_titles":
                " | ".join(titles),

            "session_ids":
                " | ".join(session_ids),

            "telegram_lookup_error":
                telegram_errors.get(
                    user_id,
                    "",
                ),
        }

        output.append(row)

    output.sort(
        key=lambda x: (
            -int(x.get("user_message_count") or 0),
            str(x.get("telegram_user_id") or ""),
        )
    )

    return output


# ============================================================
# HUMAN READABLE REPORT
# ============================================================

def write_text_report(
    path: Path,
    users: list[dict],
) -> None:

    lines = []

    lines.append("TELEGRAM USERS REPORT")
    lines.append("=" * 80)
    lines.append("")
    lines.append(
        f"Unique Telegram users: {len(users)}"
    )
    lines.append("")

    for index, user in enumerate(
        users,
        start=1,
    ):

        best_name = (
            user.get("telegram_full_name")
            or user.get("telegram_username")
            or user.get("hermes_display_names")
            or "UNKNOWN"
        )

        lines.append(
            f"{index}. {best_name}"
        )

        lines.append(
            f"   Telegram ID: "
            f"{user.get('telegram_user_id', '')}"
        )

        lines.append(
            f"   Hermes name: "
            f"{user.get('hermes_display_names', '')}"
        )

        lines.append(
            f"   First name: "
            f"{user.get('telegram_first_name', '')}"
        )

        lines.append(
            f"   Last name: "
            f"{user.get('telegram_last_name', '')}"
        )

        username = user.get(
            "telegram_username",
            "",
        )

        lines.append(
            f"   Username: "
            f"{('@' + username) if username else ''}"
        )

        lines.append(
            f"   Bio: "
            f"{user.get('telegram_bio', '')}"
        )

        lines.append(
            f"   Sessions: "
            f"{user.get('session_count', 0)}"
        )

        lines.append(
            f"   User messages: "
            f"{user.get('user_message_count', 0)}"
        )

        lines.append(
            f"   First seen: "
            f"{user.get('first_seen', '')}"
        )

        lines.append(
            f"   Session titles: "
            f"{user.get('session_titles', '')}"
        )

        error = user.get(
            "telegram_lookup_error",
            "",
        )

        if error:
            lines.append(
                f"   Telegram lookup error: {error}"
            )

        lines.append("")

    path.write_text(
        "\n".join(lines),
        encoding="utf-8-sig",
    )


# ============================================================
# MAIN
# ============================================================

def main() -> None:

    if not DB_PATH.exists():
        raise FileNotFoundError(
            f"state.db not found: {DB_PATH}"
        )

    stamp = now_stamp()

    output_dir = (
        OUTPUT_ROOT
        / stamp
    )

    output_dir.mkdir(
        parents=True,
        exist_ok=True,
    )

    print("Reading:")
    print(DB_PATH)
    print()

    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row

    try:
        # ----------------------------------------------------
        # Full safe SQLite backup
        # ----------------------------------------------------

        backup_path = (
            output_dir
            / f"state_backup_{stamp}.db"
        )

        create_database_backup(
            conn,
            backup_path,
        )

        # ----------------------------------------------------
        # Telegram sessions
        # ----------------------------------------------------

        sessions = get_telegram_sessions(
            conn
        )

        # ----------------------------------------------------
        # User messages
        # ----------------------------------------------------

        user_messages = get_user_messages(
            conn,
            sessions,
        )

        # ----------------------------------------------------
        # Telegram API enrichment
        # ----------------------------------------------------

        bot_token = load_env_value(
            ENV_PATH,
            "TELEGRAM_BOT_TOKEN",
        )

        telegram_profiles: dict[str, dict] = {}
        telegram_errors: dict[str, str] = {}

        unique_users = sorted(
            {
                str(
                    row.get(
                        "resolved_user_id",
                        ""
                    )
                ).strip()
                for row in sessions
                if str(
                    row.get(
                        "resolved_user_id",
                        ""
                    )
                ).strip()
            }
        )

        print(
            f"Telegram users found in DB: "
            f"{len(unique_users)}"
        )

        if bot_token:
            print(
                "Telegram Bot API enrichment: ENABLED"
            )
        else:
            print(
                "Telegram Bot API enrichment: DISABLED "
                "(token not found)"
            )

        print()

        for index, user_id in enumerate(
            unique_users,
            start=1,
        ):

            print(
                f"[{index}/{len(unique_users)}] "
                f"Telegram ID {user_id}"
            )

            if not bot_token:
                telegram_errors[user_id] = (
                    "TELEGRAM_BOT_TOKEN not found"
                )
                continue

            data, error = telegram_get_chat(
                bot_token,
                user_id,
            )

            if error:
                telegram_errors[user_id] = error
                continue

            telegram_profiles[user_id] = (
                extract_telegram_profile(
                    data
                )
            )

        # ----------------------------------------------------
        # Build reports
        # ----------------------------------------------------

        users = build_user_summary(
            sessions,
            user_messages,
            telegram_profiles,
            telegram_errors,
        )

        # Raw Telegram session view
        write_csv(
            output_dir
            / "telegram_sessions.csv",
            sessions,
        )

        # Every actual user message + owner
        write_csv(
            output_dir
            / "telegram_user_messages.csv",
            user_messages,
        )

        # Unique user summary
        write_csv(
            output_dir
            / "telegram_users.csv",
            users,
        )

        # Readable report
        write_text_report(
            output_dir
            / "telegram_users_report.txt",
            users,
        )

        # ----------------------------------------------------
        # Schema snapshot
        # ----------------------------------------------------

        schema_lines = []

        tables = conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
            ORDER BY name
            """
        ).fetchall()

        for table_row in tables:
            table = table_row[0]

            schema_lines.append(
                f"[{table}]"
            )

            for col in table_columns(
                conn,
                table,
            ):
                schema_lines.append(
                    f"  {col}"
                )

            schema_lines.append("")

        (
            output_dir
            / "database_schema.txt"
        ).write_text(
            "\n".join(schema_lines),
            encoding="utf-8-sig",
        )

    finally:
        conn.close()

    print()
    print("=" * 70)
    print("DONE")
    print("=" * 70)
    print()
    print("Output directory:")
    print(output_dir)
    print()
    print("Main files:")
    print("  telegram_users.csv")
    print("  telegram_sessions.csv")
    print("  telegram_user_messages.csv")
    print("  telegram_users_report.txt")
    print("  database_schema.txt")
    print(f"  state_backup_{stamp}.db")
    print()
    print(
        "Bot token was never printed or "
        "written to the reports."
    )


if __name__ == "__main__":
    main()