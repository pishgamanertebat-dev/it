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
        return user_id if platform == "bale" else None

    try:
        conn = sqlite3.connect(USERS_DB.resolve().as_uri() + "?mode=ro", uri=True)
    except sqlite3.Error:
        return user_id if platform == "bale" else None

    try:
        if platform == "bale":

            row = conn.execute(
                """
                SELECT
                    verified_name,
                    registration_status
                FROM channel_users
                WHERE platform = 'bale'
                  AND user_id = ?
                LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            if not row:
                return user_id  # e.g. an independently admitted admin

            verified_name = (row[0] or "").strip()
            status = (row[1] or "").strip()

            if status != "approved":
                return None

            return verified_name or user_id

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

    except sqlite3.Error:
        return user_id if platform == "bale" else None
    finally:
        conn.close()


def _platform_label(platform: str) -> str:

    if platform == "bale":
        return "بله"

    if platform == "telegram":
        return "تلگرام"

    return platform


def _short_title(text: str, limit: int) -> str:
    """Bound at whitespace boundaries so neither Persian nor an emoji is split."""
    text = " ".join(str(text or "").split())
    if len(text) <= limit:
        return text
    words = []
    for word in text.split():
        if len(" ".join(words + [word])) > limit - 1:
            break
        words.append(word)
    return " ".join(words) + "…"


def _rename_session(
    platform: str,
    user_id: str,
    session_id: str,
    message: str = "",
) -> None:

    name = _get_verified_name(
        platform,
        user_id,
    )

    if not name:
        return

    label = SessionDB.sanitize_title(_short_title(name, 40)) if platform == "bale" else name
    base_title = f"{_platform_label(platform)} | {label or user_id}"

    db_path = get_hermes_home() / "state.db"
    db = SessionDB(db_path=db_path)

    try:
        current = db.get_session_title(session_id)

        if current == base_title or (current or "").startswith(base_title + " | "):
            return
        if platform == "bale":
            if (current or "").startswith((base_title + " — ", name + " — ")):
                return
            # agent:end runs after Hermes' normal title pipeline. Keep its topic;
            # never set a name at agent:start, which would suppress auto-titling.
            opening = next((m.get("content") for m in db.get_messages(session_id, limit=20)
                            if m.get("role") == "user"), None)
            from agent.message_content import flatten_message_text
            topic = current or flatten_message_text(opening) or message
            budget = db.MAX_TITLE_LENGTH - len(base_title) - 3
            base_title += " — " + _short_title(topic, budget) if topic else ""
        if current == base_title:
            return
        try:
            db.set_session_title(session_id, base_title)
        except ValueError:
            suffix = f" | {session_id[-8:]}"
            unique_title = _short_title(base_title, db.MAX_TITLE_LENGTH - len(suffix)) + suffix
            if current != unique_title:
                db.set_session_title(session_id, unique_title)

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

    if platform == "bale":
        # Group/thread titles cannot safely identify the entire chat as one sender.
        if event_type != "agent:end" or context.get("chat_type") not in {"dm", "private"}:
            return
        if str(context.get("user_id") or "") != str(context.get("chat_id") or ""):
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
        context.get("message") or "",
    )
