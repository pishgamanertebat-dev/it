from __future__ import annotations

import asyncio
import sqlite3
from pathlib import Path

from hermes_cli.config import get_hermes_home
from hermes_state import SessionDB


USERS_DB = Path(
    r"E:\KomatsoAI\reports\telegram_usage\telegram_users.db"
)


def _normalize_platform(value) -> str:
    return str(value or "").strip().lower()


def _get_verified_name(
    platform: str,
    user_id: str,
) -> str | None:

    if not USERS_DB.exists():
        return None

    conn = sqlite3.connect(USERS_DB)

    try:
        if platform == "bale":

            row = conn.execute(
                """
                SELECT
                    verified_name,
                    display_name,
                    registration_status
                FROM channel_users
                WHERE platform = 'bale'
                  AND user_id = ?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            if not row:
                return None

            verified_name = (row[0] or "").strip()
            display_name = (row[1] or "").strip()
            status = (row[2] or "").strip()

            if status != "approved":
                return None

            return verified_name or display_name or None

        if platform == "telegram":

            row = conn.execute(
                """
                SELECT
                    verified_name,
                    telegram_display_name,
                    registration_status
                FROM users
                WHERE telegram_id = ?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            if not row:
                return None

            verified_name = (row[0] or "").strip()
            display_name = (row[1] or "").strip()

            return verified_name or display_name or None

        return None

    finally:
        conn.close()


def _platform_label(platform: str) -> str:

    if platform == "bale":
        return "بله"

    if platform == "telegram":
        return "تلگرام"

    return platform


def _rename_session(
    platform: str,
    user_id: str,
    session_id: str,
) -> None:

    name = _get_verified_name(
        platform,
        user_id,
    )

    if not name:
        return

    base_title = (
        f"{_platform_label(platform)} | {name}"
    )

    db_path = get_hermes_home() / "state.db"
    db = SessionDB(db_path=db_path)

    try:
        current = db.get_session_title(session_id)

        if current == base_title:
            return

        # اول عنوان ساده و خوانا را امتحان می‌کنیم.
        try:
            db.set_session_title(
                session_id,
                base_title,
            )
            return

        except ValueError:
            # عنوان Session باید unique باشد.
            # اگر همین کاربر Session دیگری داشته باشد،
            # یک شناسه کوتاه اضافه می‌کنیم.
            unique_title = (
                f"{base_title} | {session_id[-8:]}"
            )

            if current == unique_title:
                return

            db.set_session_title(
                session_id,
                unique_title,
            )

    finally:
        db.close()


async def handle(
    event_type: str,
    context: dict,
):

    if event_type not in {
        "agent:start",
        "agent:end",
    }:
        return

    platform = _normalize_platform(
        context.get("platform")
    )

    if platform not in {
        "bale",
        "telegram",
    }:
        return

    user_id = str(
        context.get("user_id") or ""
    ).strip()

    session_id = str(
        context.get("session_id") or ""
    ).strip()

    if not user_id or not session_id:
        return

    await asyncio.to_thread(
        _rename_session,
        platform,
        user_id,
        session_id,
    )