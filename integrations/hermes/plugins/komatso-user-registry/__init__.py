from __future__ import annotations

import asyncio
import logging
import os
import sqlite3
from datetime import datetime
from pathlib import Path

DB_PATH = Path(r"E:\KomatsoAI\reports\telegram_usage\telegram_users.db")

# Frozen on 2026-09-14: only existing Telegram users retain access.
# Do not rebuild from the live users table: observing a new user must not grant access.
# IDs are read from KOMATSO_TELEGRAM_EXISTING_USERS (comma-separated).
def _parse_telegram_existing_users(raw: str | None) -> frozenset[str]:
    if raw is None or not raw.strip():
        return frozenset()
    return frozenset(part.strip() for part in raw.split(",") if part.strip())


TELEGRAM_EXISTING_USERS = _parse_telegram_existing_users(
    os.environ.get("KOMATSO_TELEGRAM_EXISTING_USERS")
)


YES_WORDS = {
    "بله", "بلی", "آره", "اره", "تایید", "تأیید",
    "تایید میکنم", "تأیید میکنم", "تایید می کنم", "تأیید می کنم",
    "درسته", "درست است", "اوکی", "ok", "yes",
}

NO_WORDS = {
    "خیر", "نه", "نخیر", "اشتباهه", "اشتباه است", "درست نیست", "no",
}

CANCEL_WORDS = {
    "لغو", "انصراف", "نمیخوام", "نمی‌خوام", "نمیخواهم", "نمی‌خواهم",
    "تمایل ندارم", "بعدا", "بعداً",
}


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _connect() -> sqlite3.Connection:
    DB_PATH.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)

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


def _log(conn: sqlite3.Connection, telegram_id: str, event_type: str, value: str = "") -> None:
    conn.execute(
        """
        INSERT INTO registration_events (
            telegram_id, event_type, value, created_at
        )
        VALUES (?, ?, ?, ?)
        """,
        (telegram_id, event_type, value, _now()),
    )


def _observe(conn: sqlite3.Connection, telegram_id: str, display_name: str) -> None:
    conn.execute(
        """
        INSERT INTO users (
            telegram_id, telegram_display_name, updated_at
        )
        VALUES (?, ?, ?)
        ON CONFLICT(telegram_id)
        DO UPDATE SET
            telegram_display_name =
                CASE
                    WHEN excluded.telegram_display_name <> ''
                    THEN excluded.telegram_display_name
                    ELSE users.telegram_display_name
                END,
            updated_at = excluded.updated_at
        """,
        (telegram_id, display_name, _now()),
    )


def _get(conn: sqlite3.Connection, telegram_id: str):
    return conn.execute(
        """
        SELECT
            telegram_id,
            telegram_display_name,
            verified_name,
            candidate_name,
            registration_status,
            requested_at,
            verified_at
        FROM users
        WHERE telegram_id = ?
        """,
        (telegram_id,),
    ).fetchone()


def _normalize(text: str) -> str:
    return " ".join(
        (text or "")
        .strip()
        .replace("ي", "ی")
        .replace("ك", "ک")
        .split()
    ).lower()


def _is_yes(text: str) -> bool:
    return _normalize(text) in YES_WORDS


def _is_no(text: str) -> bool:
    return _normalize(text) in NO_WORDS


def _is_cancel(text: str) -> bool:
    return _normalize(text) in CANCEL_WORDS


def _platform_name(platform) -> str:
    value = getattr(platform, "value", None)
    if value:
        return str(value)

    text = str(platform)
    return text.split(".")[-1].lower() if "." in text else text.lower()


def _send(gateway, source, text: str) -> None:
    name = _platform_name(source.platform)
    adapter = gateway.adapters.get(name) or gateway.adapters.get(source.platform)

    if adapter is None:
        raise RuntimeError(f"No gateway adapter found for {name}")

    asyncio.get_running_loop().create_task(
        adapter.send(str(source.chat_id), text)
    )


def _handle_registration(event, gateway, **kwargs):
    source = event.source

    if _platform_name(source.platform) != "telegram":
        return None

    telegram_id = str(getattr(source, "user_id", "") or "").strip()
    if not telegram_id and str(getattr(source, "chat_type", "")) == "dm":
        telegram_id = str(getattr(source, "chat_id", "") or "").strip()

    if telegram_id not in TELEGRAM_EXISTING_USERS:
        try:
            _send(
                gateway,
                source,
                "ثبت‌نام کاربران جدید در ربات تلگرام بسته شده است.\n"
                "برای ثبت‌نام و استفاده از خدمات، لطفاً از ربات بله استفاده کنید.",
            )
        except Exception:
            logging.getLogger(__name__).exception("Could not send Telegram registration-closed notice")
        return {"action": "skip", "reason": "telegram-registration-closed"}

    if str(getattr(source, "chat_type", "")) != "dm":
        return None

    telegram_id = str(
        getattr(source, "user_id", "")
        or getattr(source, "chat_id", "")
    ).strip()

    if not telegram_id:
        return None

    text = (event.text or "").strip()

    display_name = str(
        getattr(source, "user_name", "")
        or getattr(source, "chat_name", "")
        or ""
    ).strip()

    conn = _connect()

    try:
        _observe(conn, telegram_id, display_name)
        row = _get(conn, telegram_id)

        if row is None:
            conn.commit()
            return None

        candidate_name = row[3] or ""
        status = row[4] or "none"

        # Only active registration states are intercepted.
        # All other users continue to the normal Hermes agent.
        if status not in {"awaiting_name", "awaiting_confirmation"}:
            conn.commit()
            return None

        if _is_cancel(text):
            conn.execute(
                """
                UPDATE users
                SET
                    candidate_name = NULL,
                    registration_status = 'declined',
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (_now(), telegram_id),
            )
            _log(conn, telegram_id, "registration_cancelled")
            conn.commit()

            _send(
                gateway,
                source,
                "ثبت مشخصات لغو شد.\n"
                "می‌توانید مانند قبل سؤال فنی خود را ارسال کنید.",
            )

            return {
                "action": "skip",
                "reason": "user-registration-cancelled",
            }

        if status == "awaiting_name":
            if not text:
                _send(
                    gateway,
                    source,
                    "لطفاً نام و نام خانوادگی خود را به‌صورت کامل ارسال کنید.\n"
                    "در صورت عدم تمایل، فقط «لغو» را ارسال کنید.",
                )
                return {
                    "action": "skip",
                    "reason": "user-registration-awaiting-name",
                }

            conn.execute(
                """
                UPDATE users
                SET
                    candidate_name = ?,
                    registration_status = 'awaiting_confirmation',
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (text, _now(), telegram_id),
            )
            _log(conn, telegram_id, "candidate_name_received", text)
            conn.commit()

            _send(
                gateway,
                source,
                f"نام و نام خانوادگی شما «{text}» ثبت شود؟\n\n"
                "اگر صحیح است «بله»، اگر اشتباه است «خیر» و "
                "اگر تمایلی به ثبت مشخصات ندارید «لغو» را ارسال کنید.",
            )

            return {
                "action": "skip",
                "reason": "user-registration-name-received",
            }

        if _is_yes(text):
            verified_name = candidate_name.strip()

            if not verified_name:
                conn.execute(
                    """
                    UPDATE users
                    SET
                        candidate_name = NULL,
                        registration_status = 'awaiting_name',
                        updated_at = ?
                    WHERE telegram_id = ?
                    """,
                    (_now(), telegram_id),
                )
                conn.commit()

                _send(
                    gateway,
                    source,
                    "لطفاً نام و نام خانوادگی خود را دوباره ارسال کنید.\n"
                    "در صورت عدم تمایل، فقط «لغو» را ارسال کنید.",
                )

                return {
                    "action": "skip",
                    "reason": "user-registration-missing-candidate",
                }

            timestamp = _now()

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
                (verified_name, timestamp, timestamp, telegram_id),
            )
            _log(conn, telegram_id, "name_verified", verified_name)
            conn.commit()

            _send(
                gateway,
                source,
                f"متشکرم، «{verified_name}» عزیز.\n"
                "نام و نام خانوادگی شما با موفقیت ثبت شد ✅\n\n"
                "اگر سؤال یا مشکل فنی دارید بفرمایید، در خدمت شما هستم.",
            )

            return {
                "action": "skip",
                "reason": "user-registration-complete",
            }

        if _is_no(text):
            conn.execute(
                """
                UPDATE users
                SET
                    candidate_name = NULL,
                    registration_status = 'awaiting_name',
                    updated_at = ?
                WHERE telegram_id = ?
                """,
                (_now(), telegram_id),
            )
            _log(conn, telegram_id, "name_rejected", candidate_name)
            conn.commit()

            _send(
                gateway,
                source,
                "بسیار خوب. لطفاً نام و نام خانوادگی صحیح خود را ارسال کنید.\n"
                "در صورت عدم تمایل، فقط «لغو» را ارسال کنید.",
            )

            return {
                "action": "skip",
                "reason": "user-registration-name-rejected",
            }

        _send(
            gateway,
            source,
            f"نام ثبت‌شده «{candidate_name}» است.\n\n"
            "اگر صحیح است «بله»، اگر اشتباه است «خیر» و "
            "برای خروج از ثبت مشخصات «لغو» را ارسال کنید.",
        )

        return {
            "action": "skip",
            "reason": "user-registration-confirmation-required",
        }

    finally:
        conn.close()


def register(ctx):
    ctx.register_hook("pre_gateway_dispatch", _handle_registration)
