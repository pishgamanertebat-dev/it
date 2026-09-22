from __future__ import annotations

import asyncio
import os
import secrets
import sqlite3
from gateway.pairing import PairingStore
from datetime import datetime
from pathlib import Path


DB_PATH = Path(r"E:\KomatsoAI\reports\telegram_usage\telegram_users.db")

REQUEST_ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"

# A public reply-keyboard button every approved Bale user gets. Its label is
# Persian (Bale reply keyboards submit the label text, never a payload), so it
# must be translated to the literal Hermes command BEFORE the gateway's
# active-session/busy guard runs. We do this exact-match rewrite here, inside
# the pre_gateway_dispatch hook (which fires before auth, session setup and busy
# routing), and then let dispatch continue so Hermes executes its own native
# /new. No /new / session-reset logic is duplicated here.
NEW_CHAT_LABEL = "🔄 شروع گفتگوی جدید"
NEW_CHAT_COMMAND = "/new"


def _normalize_label(text) -> str:
    """Collapse whitespace and unify Arabic ی/ک so the exact match is robust."""
    return " ".join(
        str(text or "")
        .translate(str.maketrans("كي", "کی"))
        .replace("\u200c", " ")
        .split()
    )


def _is_new_chat_button(text) -> bool:
    """True only for an exact tap of the reply-keyboard label, nothing similar.

    A question like «شروع گفتگوی جدید یعنی چی؟» is NOT an exact match and is
    therefore left untouched for normal LLM handling.
    """
    return _normalize_label(text) == _normalize_label(NEW_CHAT_LABEL)


def _now_dt() -> datetime:
    return datetime.now().astimezone()


def _now() -> str:
    return _now_dt().isoformat(timespec="seconds")


def _platform_name(platform) -> str:
    value = getattr(platform, "value", None)

    if value:
        return str(value).lower()

    text = str(platform)
    return text.split(".")[-1].lower() if "." in text else text.lower()


def _admin_ids() -> set[str]:
    raw = os.getenv("BALE_ADMIN_IDS", "")

    return {
        item.strip()
        for item in raw.split(",")
        if item.strip()
    }


def _connect() -> sqlite3.Connection:
    conn = sqlite3.connect(DB_PATH)

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS channel_users (
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            chat_id TEXT,
            display_name TEXT,
            verified_name TEXT,
            registration_status TEXT NOT NULL DEFAULT 'none',
            first_seen_at TEXT NOT NULL,
            verified_at TEXT,
            updated_at TEXT NOT NULL,
            PRIMARY KEY (platform, user_id)
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_requests (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_code TEXT NOT NULL UNIQUE,
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            chat_id TEXT NOT NULL,
            candidate_name TEXT,
            status TEXT NOT NULL DEFAULT 'awaiting_name',
            requested_at TEXT NOT NULL,
            expires_at TEXT,
            approved_at TEXT,
            approved_by TEXT,
            rejected_at TEXT,
            rejected_by TEXT,
            updated_at TEXT NOT NULL
        )
        """
    )

    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS access_events (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            request_id INTEGER,
            request_code TEXT,
            platform TEXT NOT NULL,
            user_id TEXT NOT NULL,
            actor_type TEXT NOT NULL,
            actor_id TEXT,
            event_type TEXT NOT NULL,
            value TEXT,
            created_at TEXT NOT NULL
        )
        """
    )

    conn.commit()
    return conn


def _get_bale_adapter(gateway):
    adapter = gateway.adapters.get("bale")

    if adapter is not None:
        return adapter

    for key, candidate in gateway.adapters.items():

        if _platform_name(key) == "bale":
            return candidate

        candidate_platform = getattr(candidate, "platform", None)

        if candidate_platform is not None:
            if _platform_name(candidate_platform) == "bale":
                return candidate

    raise RuntimeError(
        f"Bale gateway adapter not found; "
        f"available={list(gateway.adapters.keys())}"
    )


def _send(gateway, chat_id: str, text: str) -> None:
    adapter = _get_bale_adapter(gateway)

    asyncio.get_running_loop().create_task(
        adapter.send(str(chat_id), text)
    )


def _send_to_admins(gateway, text: str) -> None:
    for admin_id in _admin_ids():
        _send(gateway, admin_id, text)


_REVOKED_NOTICE = (
    "شما از این ربات خارج شدید. برای احراز هویت مجدد کلمه «درخواست» را ارسال کنید."
)


def _revoke_reply_keyboard(gateway, chat_id: str, user_id: str, text: str = _REVOKED_NOTICE) -> None:
    """Remove a reply keyboard at the same time access is revoked; never wait for a later user message."""
    try:
        import tools as tools_package
        project_tools = r"E:\KomatsoAI\tools"
        if project_tools not in tools_package.__path__:
            tools_package.__path__.append(project_tools)
        from tools.bale_ui.runtime import revoke_reply_menu
        revoke_reply_menu(gateway, chat_id, user_id, send=_send, text=text)
    except Exception:
        _send(gateway, chat_id, text)


def _new_request_code(conn: sqlite3.Connection) -> str:
    while True:
        code = "".join(
            secrets.choice(REQUEST_ALPHABET)
            for _ in range(8)
        )

        exists = conn.execute(
            """
            SELECT 1
            FROM access_requests
            WHERE request_code = ?
            """,
            (code,),
        ).fetchone()

        if not exists:
            return code


def _observe_user(
    conn: sqlite3.Connection,
    user_id: str,
    chat_id: str,
    display_name: str,
) -> None:

    now = _now()

    conn.execute(
        """
        INSERT INTO channel_users (
            platform,
            user_id,
            chat_id,
            display_name,
            registration_status,
            first_seen_at,
            updated_at
        )
        VALUES (
            'bale',
            ?,
            ?,
            ?,
            'none',
            ?,
            ?
        )
        ON CONFLICT(platform, user_id)
        DO UPDATE SET
            chat_id = excluded.chat_id,
            display_name =
                CASE
                    WHEN excluded.display_name <> ''
                    THEN excluded.display_name
                    ELSE channel_users.display_name
                END,
            updated_at = excluded.updated_at
        """,
        (
            user_id,
            chat_id,
            display_name,
            now,
            now,
        ),
    )


def _user_status(
    conn: sqlite3.Connection,
    user_id: str,
) -> str:

    row = conn.execute(
        """
        SELECT registration_status
        FROM channel_users
        WHERE platform = 'bale'
          AND user_id = ?
        """,
        (user_id,),
    ).fetchone()

    if not row:
        return "none"

    return row[0] or "none"


def _active_request(
    conn: sqlite3.Connection,
    user_id: str,
):

    return conn.execute(
        """
        SELECT
            id,
            request_code,
            candidate_name,
            status,
            requested_at,
            expires_at
        FROM access_requests
        WHERE platform = 'bale'
          AND user_id = ?
          AND status IN (
              'awaiting_name',
              'pending_approval',
              'needs_correction',
              'expired'
          )
        ORDER BY id DESC
        LIMIT 1
        """,
        (user_id,),
    ).fetchone()


def _log_event(
    conn: sqlite3.Connection,
    request_id: int,
    request_code: str,
    user_id: str,
    event_type: str,
    value: str = "",
    actor_type: str = "user",
    actor_id: str = "",
) -> None:

    conn.execute(
        """
        INSERT INTO access_events (
            request_id,
            request_code,
            platform,
            user_id,
            actor_type,
            actor_id,
            event_type,
            value,
            created_at
        )
        VALUES (?, ?, 'bale', ?, ?, ?, ?, ?, ?)
        """,
        (
            request_id,
            request_code,
            user_id,
            actor_type,
            actor_id,
            event_type,
            value,
            _now(),
        ),
    )


def _expire_if_needed(
    conn: sqlite3.Connection,
    row,
    user_id: str,
):
    # Bale requests remain available until an administrator decides.
    return row


def _create_request(
    conn: sqlite3.Connection,
    user_id: str,
    chat_id: str,
) -> tuple[int, str]:

    code = _new_request_code(conn)

    now = _now()

    cursor = conn.execute(
        """
        INSERT INTO access_requests (
            request_code,
            platform,
            user_id,
            chat_id,
            status,
            requested_at,
            expires_at,
            updated_at
        )
        VALUES (
            ?,
            'bale',
            ?,
            ?,
            'awaiting_name',
            ?,
            NULL,
            ?
        )
        """,
        (
            code,
            user_id,
            chat_id,
            now,
            now,
        ),
    )

    request_id = cursor.lastrowid

    conn.execute(
        """
        UPDATE channel_users
        SET
            registration_status = 'awaiting_name',
            updated_at = ?
        WHERE platform = 'bale'
          AND user_id = ?
        """,
        (now, user_id),
    )

    _log_event(
        conn,
        request_id,
        code,
        user_id,
        "request_started",
        actor_type="system",
    )

    conn.commit()

    return request_id, code

def _approve_bale_user_in_hermes(
    user_id: str,
    user_name: str,
) -> bool:

    store = PairingStore()

    # اگر قبلاً در Hermes تأیید شده باشد، کار تمام است.
    if store.is_approved("bale", user_id):
        return True

    # اگر از تست‌های قبلی Pairing pending داشته باشد،
    # همان درخواست Hermes را تأیید می‌کنیم.
    for pending in store.list_pending("bale"):

        pending_user_id = str(
            pending.get("user_id", "")
        ).strip()

        if pending_user_id != user_id:
            continue

        request_id = str(
            pending.get("request_id", "")
        ).strip()

        if not request_id:
            continue

        result = store.approve_request(
            "bale",
            request_id,
        )

        if result:
            return True

    # اگر هیچ pending قدیمی وجود نداشت،
    # یک Pairing داخلی می‌سازیم و همان لحظه تأییدش می‌کنیم.
    code = store.generate_code(
        "bale",
        user_id,
        user_name,
    )

    if not code:
        return False

    result = store.approve_code(
        "bale",
        code,
    )

    return result is not None


def _handle_admin_command(
    gateway,
    admin_id: str,
    admin_chat_id: str,
    text: str,
):
    normalized = (
        (text or "")
        .strip()
        .replace("ي", "ی")
        .replace("ك", "ک")
    )

    # نمایش فهرست درخواست‌های احراز هویت
    requests_command = normalized.replace("\u200c", "")

    if (
        requests_command == "درخواستها"
        or requests_command.startswith("درخواستها ")
    ):
        parts = requests_command.split()

        page = 1

        if len(parts) >= 2:
            try:
                page = int(parts[1])
            except ValueError:
                page = 1

        if page < 1:
            page = 1

        per_page = 8

        conn = _connect()

        try:
            total = conn.execute(
                """
                SELECT COUNT(*)
                FROM access_requests
                WHERE platform = 'bale'
                """
            ).fetchone()[0]

            if total == 0:
                _send(
                    gateway,
                    admin_chat_id,
                    "📋 هیچ درخواست احراز هویتی ثبت نشده است."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-requests-empty",
                }

            total_pages = (total + per_page - 1) // per_page

            if page > total_pages:
                page = total_pages

            offset = (page - 1) * per_page

            rows = conn.execute(
                """
                SELECT
                    request_code,
                    candidate_name,
                    status,
                    user_id,
                    requested_at
                FROM access_requests
                WHERE platform = 'bale'
                ORDER BY id DESC
                LIMIT ? OFFSET ?
                """,
                (
                    per_page,
                    offset,
                ),
            ).fetchall()

            status_labels = {
                "awaiting_name": "🟡 منتظر نام",
                "pending_approval": "🟠 منتظر تأیید",
                "needs_correction": "📝 نیاز به اصلاح",
                "approved": "✅ تأیید شده",
                "rejected": "❌ رد شده",
                "expired": "⌛ منقضی",
                "revoked": "⛔ حذف شده",
            }

            lines = [
                f"📋 درخواست‌های احراز هویت",
                f"صفحه {page} از {total_pages}",
                f"تعداد کل: {total}",
                "",
            ]

            for row in rows:
                request_code = row[0]
                candidate_name = row[1] or "نام ثبت نشده"
                status = row[2] or "unknown"
                user_id = row[3]
                requested_at = row[4] or "-"

                status_text = status_labels.get(
                    status,
                    status,
                )

                if len(requested_at) > 19:
                    requested_at = requested_at[:19]

                lines.extend(
                    [
                        f"{status_text}",
                        f"👤 {candidate_name}",
                        f"🔑 {request_code}",
                        f"🆔 {user_id}",
                        f"🕒 {requested_at}",
                        "────────────",
                    ]
                )

            if page < total_pages:
                lines.extend(
                    [
                        "",
                        f"صفحه بعد: درخواستها {page + 1}",
                    ]
                )

            if page > 1:
                lines.extend(
                    [
                        f"صفحه قبل: درخواستها {page - 1}",
                    ]
                )

            _send(
                gateway,
                admin_chat_id,
                "\n".join(lines),
            )

            return {
                "action": "skip",
                "reason": "bale-admin-requests-listed",
            }

        finally:
            conn.close()

    # پیام مستقیم مدیر به کاربر بر اساس کد درخواست
    if normalized.startswith("پیام "):

        message_parts = normalized.split(maxsplit=2)

        if len(message_parts) < 3:
            _send(
                gateway,
                admin_chat_id,
                "فرمت صحیح:\n"
                "پیام CODE متن پیام"
            )

            return {
                "action": "skip",
                "reason": "bale-admin-message-invalid-format",
            }

        request_code = message_parts[1].strip().upper()
        message_text = message_parts[2].strip()

        if not message_text:
            _send(
                gateway,
                admin_chat_id,
                "❌ متن پیام خالی است."
            )

            return {
                "action": "skip",
                "reason": "bale-admin-message-empty",
            }

        conn = _connect()

        try:
            row = conn.execute(
                """
                SELECT
                    id,
                    user_id,
                    chat_id,
                    candidate_name,
                    status
                FROM access_requests
                WHERE platform = 'bale'
                  AND request_code = ?
                ORDER BY id DESC
                LIMIT 1
                """,
                (request_code,),
            ).fetchone()

            if row is None:
                _send(
                    gateway,
                    admin_chat_id,
                    f"❌ درخواست با کد {request_code} پیدا نشد."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-message-not-found",
                }

            request_id = row[0]
            target_user_id = str(row[1])
            target_chat_id = str(row[2])
            candidate_name = row[3] or ""
            status = row[4] or ""

            _send(
                gateway,
                target_chat_id,
                "📩 پیام مسئول سامانه:\n\n"
                + message_text
            )

            _log_event(
                conn,
                request_id,
                request_code,
                target_user_id,
                "admin_message_sent",
                value=message_text,
                actor_type="admin",
                actor_id=admin_id,
            )

            conn.commit()

            _send(
                gateway,
                admin_chat_id,
                f"✅ پیام برای «{candidate_name or target_user_id}» ارسال شد.\n"
                f"کد درخواست: {request_code}\n"
                f"وضعیت کاربر: {status}"
            )

            return {
                "action": "skip",
                "reason": "bale-admin-message-sent",
            }

        finally:
            conn.close()

    parts = normalized.split(maxsplit=1)

    if len(parts) != 2:
        return None

    command = parts[0]

    if command == "حذف":
        raw_target = parts[1].strip()
        target_user_id = raw_target.translate(
            str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
        ).strip()

        conn = _connect()
        try:
            row = conn.execute(
                """
                SELECT
                    user_id,
                    chat_id,
                    display_name,
                    verified_name,
                    registration_status
                FROM channel_users
                WHERE platform = 'bale'
                  AND user_id = ?
                """,
                (target_user_id,),
            ).fetchone()

            if row is None:
                req_row = conn.execute(
                    """
                    SELECT user_id
                    FROM access_requests
                    WHERE platform = 'bale'
                      AND request_code = ?
                    ORDER BY id DESC
                    LIMIT 1
                    """,
                    (target_user_id.upper(),),
                ).fetchone()
                if req_row is not None:
                    target_user_id = str(req_row[0])
                    row = conn.execute(
                        """
                        SELECT
                            user_id,
                            chat_id,
                            display_name,
                            verified_name,
                            registration_status
                        FROM channel_users
                        WHERE platform = 'bale'
                          AND user_id = ?
                        """,
                        (target_user_id,),
                    ).fetchone()

            if row is None:
                _send(
                    gateway,
                    admin_chat_id,
                    f"❌ کاربری با شناسه {target_user_id} پیدا نشد.",
                )
                return {
                    "action": "skip",
                    "reason": "bale-admin-delete-user-not-found",
                }

            user_id, target_chat_id, display_name, verified_name, reg_status = row
            target_chat_id = str(target_chat_id or user_id)
            user_name = verified_name or display_name or user_id

            if reg_status == "revoked":
                _send(
                    gateway,
                    admin_chat_id,
                    f"ℹ️ کاربر «{user_name}» با شناسه {user_id} قبلاً حذف شده است.",
                )
                return {
                    "action": "skip",
                    "reason": "bale-admin-delete-already-revoked",
                }

            if reg_status != "approved":
                status_labels = {
                    "awaiting_name": "منتظر نام",
                    "pending_approval": "منتظر تأیید",
                    "needs_correction": "نیاز به اصلاح",
                    "rejected": "رد شده",
                    "expired": "منقضی",
                    "none": "ثبت‌نشده",
                }
                status_desc = status_labels.get(reg_status, reg_status)
                _send(
                    gateway,
                    admin_chat_id,
                    f"❌ کاربر «{user_name}» با شناسه {user_id} در وضعیت «{status_desc}» است و تأییدشده (Approved) نیست.",
                )
                return {
                    "action": "skip",
                    "reason": "bale-admin-delete-not-approved",
                }

            try:
                store = PairingStore()
                store.revoke("bale", user_id)
            except Exception:
                pass

            now = _now()
            conn.execute(
                """
                UPDATE channel_users
                SET
                    registration_status = 'revoked',
                    updated_at = ?
                WHERE platform = 'bale'
                  AND user_id = ?
                """,
                (now, user_id),
            )

            conn.execute(
                """
                UPDATE access_requests
                SET
                    status = 'revoked',
                    updated_at = ?
                WHERE platform = 'bale'
                  AND user_id = ?
                  AND status = 'approved'
                """,
                (now, user_id),
            )

            latest_req = conn.execute(
                """
                SELECT id, request_code
                FROM access_requests
                WHERE platform = 'bale' AND user_id = ?
                ORDER BY id DESC LIMIT 1
                """,
                (user_id,),
            ).fetchone()

            _log_event(
                conn,
                request_id=latest_req[0] if latest_req else 0,
                request_code=latest_req[1] if latest_req else "",
                user_id=user_id,
                event_type="user_revoked",
                value=user_name,
                actor_type="admin",
                actor_id=admin_id,
            )

            conn.commit()

            _revoke_reply_keyboard(gateway, target_chat_id, user_id)

            _send(
                gateway,
                admin_chat_id,
                f"کاربر {user_name} با شناسه {user_id} با موفقیت حذف شد.",
            )

            return {
                "action": "skip",
                "reason": "bale-admin-user-deleted",
            }
        finally:
            conn.close()

    raw_target = parts[1].strip()
    target_clean = raw_target.translate(
        str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789")
    ).strip()
    target_upper = target_clean.upper()

    if command not in {"تایید", "تأیید", "رد", "اصلاح"}:
        return None

    conn = _connect()

    try:
        row = conn.execute(
            """
            SELECT
                id,
                user_id,
                chat_id,
                candidate_name,
                status,
                expires_at,
                request_code
            FROM access_requests
            WHERE platform = 'bale'
              AND (request_code = ? OR user_id = ?)
            ORDER BY id DESC
            LIMIT 1
            """,
            (target_upper, target_clean),
        ).fetchone()

        if row is None:
            _send(
                gateway,
                admin_chat_id,
                f"❌ درخواست با مشخصه «{raw_target}» پیدا نشد."
            )

            return {
                "action": "skip",
                "reason": "bale-admin-approve-not-found",
            }

        request_id = row[0]
        target_user_id = str(row[1])
        target_chat_id = str(row[2])
        candidate_name = row[3] or ""
        status = row[4] or ""
        request_code = row[6]

        if command == "اصلاح":

            if status == "approved":
                _send(
                    gateway,
                    admin_chat_id,
                    f"❌ درخواست {request_code} قبلاً تأیید شده است.\n"
                    "برای کاربر تأییدشده فعلاً از «اصلاح» استفاده نمی‌کنیم."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-correction-approved-user",
                }

            if status == "needs_correction":
                _send(
                    gateway,
                    admin_chat_id,
                    f"ℹ️ درخواست {request_code} از قبل در انتظار اصلاح کاربر است."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-already-needs-correction",
                }

            if status not in {"pending_approval", "rejected", "expired"}:
                _send(
                    gateway,
                    admin_chat_id,
                    f"❌ درخواست {request_code} در وضعیت "
                    f"«{status}» است و قابل اصلاح نیست."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-correction-invalid-status",
                }

            old_name = candidate_name

            now = _now()

            conn.execute(
                """
                UPDATE access_requests
                SET
                    candidate_name = NULL,
                    status = 'needs_correction',
                    expires_at = NULL,
                    rejected_at = NULL,
                    rejected_by = NULL,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    request_id,
                ),
            )

            conn.execute(
                """
                UPDATE channel_users
                SET
                    registration_status = 'needs_correction',
                    updated_at = ?
                WHERE platform = 'bale'
                  AND user_id = ?
                """,
                (
                    now,
                    target_user_id,
                ),
            )

            _log_event(
                conn,
                request_id,
                request_code,
                target_user_id,
                "correction_requested",
                value=old_name,
                actor_type="admin",
                actor_id=admin_id,
            )

            conn.commit()

            _send(
                gateway,
                target_chat_id,
                "📝 اطلاعات واردشده نیاز به اصلاح دارد.\n\n"
                "لطفاً نام و نام خانوادگی صحیح خود را "
                "دوباره ارسال کنید."
            )

            _send(
                gateway,
                admin_chat_id,
                f"✅ درخواست اصلاح برای «{old_name}» ارسال شد.\n"
                f"کد درخواست: {request_code}\n"
                f"شناسه بله: {target_user_id}"
            )

            return {
                "action": "skip",
                "reason": "bale-admin-correction-requested",
            }

        if command == "رد":

            if status == "rejected":
                _send(
                    gateway,
                    admin_chat_id,
                    f"ℹ️ درخواست {request_code} قبلاً رد شده است."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-already-rejected",
                }

            if status not in {"pending_approval", "expired"}:
                _send(
                    gateway,
                    admin_chat_id,
                    f"❌ درخواست {request_code} در وضعیت "
                    f"«{status}» است و قابل رد کردن نیست."
                )

                return {
                    "action": "skip",
                    "reason": "bale-admin-reject-invalid-status",
                }

            now = _now()

            conn.execute(
                """
                UPDATE access_requests
                SET
                    status = 'rejected',
                    rejected_at = ?,
                    rejected_by = ?,
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    now,
                    admin_id,
                    now,
                    request_id,
                ),
            )

            conn.execute(
                """
                UPDATE channel_users
                SET
                    registration_status = 'rejected',
                    updated_at = ?
                WHERE platform = 'bale'
                  AND user_id = ?
                """,
                (
                    now,
                    target_user_id,
                ),
            )

            _log_event(
                conn,
                request_id,
                request_code,
                target_user_id,
                "request_rejected",
                value=candidate_name,
                actor_type="admin",
                actor_id=admin_id,
            )

            conn.commit()

            _send(
                gateway,
                target_chat_id,
                "❌ درخواست دسترسی شما تأیید نشد.\n\n"
                "در صورت نیاز، با مسئول سامانه شرکت تماس بگیرید."
            )

            _send(
                gateway,
                admin_chat_id,
                f"❌ درخواست «{candidate_name}» رد شد.\n"
                f"کد درخواست: {request_code}\n"
                f"شناسه بله: {target_user_id}"
            )

            return {
                "action": "skip",
                "reason": "bale-admin-request-rejected",
            }

        if status == "approved":
            _send(
                gateway,
                admin_chat_id,
                f"✅ درخواست {request_code} قبلاً تأیید شده است."
            )

            return {
                "action": "skip",
                "reason": "bale-admin-already-approved",
            }

        if status not in {"pending_approval", "expired"}:
            _send(
                gateway,
                admin_chat_id,
                f"❌ درخواست {request_code} در وضعیت "
                f"«{status}» است و قابل تأیید نیست."
            )

            return {
                "action": "skip",
                "reason": "bale-admin-invalid-status",
            }

        if not candidate_name.strip():
            _send(gateway, admin_chat_id, "❌ نام کاربر ثبت نشده است؛ ابتدا از دستور اصلاح استفاده کنید.")
            return {"action": "skip", "reason": "bale-admin-name-required"}

        hermes_ok = _approve_bale_user_in_hermes(
            target_user_id,
            candidate_name,
        )

        if not hermes_ok:
            _send(
                gateway,
                admin_chat_id,
                "❌ تأیید در سیستم Hermes انجام نشد.\n"
                "دیتابیس KomatsoAI تغییری نکرد."
            )

            return {
                "action": "skip",
                "reason": "bale-admin-hermes-approval-failed",
            }

        now = _now()

        conn.execute(
            """
            UPDATE access_requests
            SET
                status = 'approved',
                approved_at = ?,
                approved_by = ?,
                updated_at = ?
            WHERE id = ?
            """,
            (
                now,
                admin_id,
                now,
                request_id,
            ),
        )

        conn.execute(
            """
            UPDATE channel_users
            SET
                verified_name = ?,
                registration_status = 'approved',
                verified_at = ?,
                updated_at = ?
            WHERE platform = 'bale'
              AND user_id = ?
            """,
            (
                candidate_name,
                now,
                now,
                target_user_id,
            ),
        )

        _log_event(
            conn,
            request_id,
            request_code,
            target_user_id,
            "request_approved",
            value=candidate_name,
            actor_type="admin",
            actor_id=admin_id,
        )

        conn.commit()

        _send(
            gateway,
            target_chat_id,
            f"✅ احراز هویت شما تأیید شد.\n\n"
            f"«{candidate_name}» عزیز، "
            "اکنون می‌توانید از دستیار هشت بهشت استفاده کنید.\n"
            "سؤال یا مشکل فنی خود را ارسال کنید."
        )

        _send(
            gateway,
            admin_chat_id,
            f"✅ دسترسی «{candidate_name}» تأیید شد.\n"
            f"کد درخواست: {request_code}\n"
            f"شناسه بله: {target_user_id}"
        )

        return {
            "action": "skip",
            "reason": "bale-admin-request-approved",
        }

    finally:
        conn.close()


def _handle_work_order_menu(event, gateway, receipt_only=False):
    # Extend the existing tools namespace: Hermes also owns a tools package.
    # Never replace Hermes' tools module or its existing search paths.
    try:
        import tools as tools_package
        project_tools = r"E:\KomatsoAI\tools"
        if project_tools not in tools_package.__path__:
            tools_package.__path__.append(project_tools)
        if not receipt_only:
            from tools.bale_ui.runtime import dispatch
            # Non-receipt calls reach here only through the existing admin or
            # approved-registration gate. Operational permissions stay separate.
            result = dispatch(event, gateway, send=_send, bale_approved=True)
            if result is not None:
                return result
        if receipt_only:
            from tools.fleet.work_orders.channels.bale.staff_flow import handle_staff_receipt
            return handle_staff_receipt(event, gateway, send=_send)
        from tools.fleet.work_orders.channels.bale.message_handler import handle_work_order_message
        return handle_work_order_message(event, gateway, send=_send)
    except Exception:
        import logging
        logging.getLogger(__name__).exception("Work-order bridge unavailable")
        text = " ".join((event.text or "").translate(str.maketrans("كي", "کی")).replace("\u200c", " ").split())
        raw = getattr(event, 'raw_message', None)
        is_inline = isinstance(raw, dict) and raw.get('bale_inline_callback') is True
        if is_inline or text == "حکم کار" or text.isdecimal() or text in {"انصراف", "لغو", "/cancel"}:
            chat_id = getattr(event.source, "chat_id", None)
            if chat_id:
                try:
                    _send(gateway, str(chat_id), "منوی حکم کار موقتاً در دسترس نیست. لطفاً دوباره تلاش کنید.")
                except Exception:
                    logging.getLogger(__name__).exception("Could not schedule menu error reply")
            return {"action": "skip", "reason": "work-order-bridge-error"}
        return None


def _handle_overflow_report(event, gateway):
    try:
        import tools as tools_package
        project_tools = r"E:\KomatsoAI\tools"
        if project_tools not in tools_package.__path__:
            tools_package.__path__.append(project_tools)
        from tools.fleet.overflow.bale import handle_overflow_message
        return handle_overflow_message(event, gateway, send=_send)
    except Exception:
        import logging
        import re
        logging.getLogger(__name__).exception("Overflow report bridge unavailable")
        text = " ".join((event.text or "").replace("ي", "ی").replace("\u200c", " ").split())
        if re.match(r"^سر\s*ریز(?:\s|$)", text):
            _send(gateway, str(event.source.chat_id), "گزارش سرریز موقتاً در دسترس نیست؛ لطفاً دوباره تلاش کنید.")
            return {"action": "skip", "reason": "overflow-bridge-error"}
        return None


DEVELOPER_CONVERSATION_POLICY = "KOMATSO_DEVELOPER_ACCESS"


def _apply_developer_conversation_policy(event):
    """Request-local policy; no role changes, approval writes or cached grants.

    BALE_DEVELOPER_IDS is a comma-separated local environment allowlist.
    Existing admins without an onboarding row are already approved by the
    registration dispatcher. Explicit non-approved rows always deny bypass.
    """
    prompt = getattr(event, "channel_prompt", None)
    if prompt and DEVELOPER_CONVERSATION_POLICY in prompt:
        prompt = prompt.replace(DEVELOPER_CONVERSATION_POLICY, "").strip() or None
        event.channel_prompt = prompt
    source = event.source
    if (_platform_name(source.platform) != "bale"
            or str(getattr(source, "chat_type", "")) != "dm"):
        return
    user_id = str(getattr(source, "user_id", "") or "").strip()
    allowed = {value.strip() for value in os.getenv("BALE_DEVELOPER_IDS", "").split(",") if value.strip()}
    if not user_id or user_id not in allowed:
        return
    try:
        # Read-only: missing DB/schema or lookup errors must never grant bypass.
        conn = sqlite3.connect(DB_PATH.resolve().as_uri() + "?mode=ro", uri=True)
        try:
            row = conn.execute(
                "SELECT registration_status FROM channel_users "
                "WHERE platform = 'bale' AND user_id = ?", (user_id,),
            ).fetchone()
            approved = (row[0] == "approved") if row is not None else user_id in _admin_ids()
        finally:
            conn.close()
    except sqlite3.Error:
        return
    if approved:
        event.channel_prompt = "\n\n".join(
            part for part in (prompt, DEVELOPER_CONVERSATION_POLICY) if part
        )


def _handle_bale(event, gateway, **kwargs):
    _apply_developer_conversation_policy(event)
    source = event.source

    if _platform_name(source.platform) != "bale":
        return None

    if str(getattr(source, "chat_type", "")) != "dm":
        return None

    user_id = str(
        getattr(source, "user_id", "")
        or getattr(source, "chat_id", "")
    ).strip()

    chat_id = str(
        getattr(source, "chat_id", "")
        or user_id
    ).strip()

    if not user_id or not chat_id:
        return None

    text = (event.text or "").strip()

    # Public daily overflow lookup, before registration and work-order menus.
    result = _handle_overflow_report(event, gateway)
    if result is not None:
        return result

    # Only a persisted assigned recipient can acknowledge here; this does not
    # grant registration approval or access to the manager's work-order menu.
    receipt_text = " ".join(text.translate(str.maketrans("كي", "کی")).replace("\u200c", " ").split())
    if receipt_text.isdecimal() or receipt_text in {"حکم کار", "تایید", "تأیید"} or receipt_text.startswith(("تایید AF-", "تأیید AF-", "تایید GR-", "تأیید GR-", "تایید OC-", "تأیید OC-")):
        result = _handle_work_order_menu(event, gateway, receipt_only=True)
        if result is not None:
            return result

    if user_id in _admin_ids():

        # The public «new chat» button maps to the native /new before any
        # busy/menu routing; admins are approved by definition.
        if _is_new_chat_button(text):
            event.text = NEW_CHAT_COMMAND
            return None

        result = _handle_admin_command(
            gateway,
            user_id,
            chat_id,
            text,
        )

        if result is not None:
            return result

        # Work-order requests use their own permission check.
        return _handle_work_order_menu(event, gateway)

    display_name = str(
        getattr(source, "user_name", "")
        or getattr(source, "chat_name", "")
        or ""
    ).strip()

    conn = _connect()

    try:
        # Callback payloads are not registration answers. Apply the same
        # approved-user gate without feeding opaque tokens into the name form.
        raw = getattr(event, 'raw_message', None)
        if isinstance(raw, dict) and raw.get('bale_inline_callback') is True:
            if _user_status(conn, user_id) != 'approved':
                _send(gateway, chat_id, 'برای استفاده از این دکمه باید دسترسی تاییدشده داشته باشید.')
                return {'action': 'skip', 'reason': 'bale-inline-registration-required'}

        _observe_user(
            conn,
            user_id,
            chat_id,
            display_name,
        )

        user_status = _user_status(conn, user_id)

        # Already approved users continue to Hermes normally.
        if user_status == "approved":
            conn.commit()
            # The public «new chat» button becomes the native /new command
            # here — inside pre_gateway_dispatch, i.e. before the gateway's
            # active-session/busy guard. Returning None lets dispatch proceed
            # with the rewritten text so Hermes runs its own /new; the Persian
            # label is never queued, steered or fed to the LLM.
            if _is_new_chat_button(text):
                event.text = NEW_CHAT_COMMAND
                return None
            return _handle_work_order_menu(event, gateway)

        # Rejected users remain blocked until an admin changes their status.
        if user_status == "rejected":
            conn.commit()

            _send(
                gateway,
                chat_id,
                "❌ درخواست دسترسی شما تأیید نشده است.\n\n"
                "در صورت نیاز، با مسئول شرکت تماس بگیرید."
            )

            return {
                "action": "skip",
                "reason": "bale-registration-rejected",
            }

        # Revoked users remain blocked until they send "درخواست"
        if user_status == "revoked":
            clean_text = (
                (text or "")
                .strip()
                .replace("ي", "ی")
                .replace("ك", "ک")
                .strip("«»\"' ")
            )
            if clean_text not in {"درخواست", "/درخواست"}:
                conn.commit()
                _revoke_reply_keyboard(gateway, chat_id, user_id)
                return {
                    "action": "skip",
                    "reason": "bale-registration-revoked-blocked",
                }

        row = _active_request(conn, user_id)
        row = _expire_if_needed(conn, row, user_id)

        if row is None:
            request_id, request_code = _create_request(
                conn,
                user_id,
                chat_id,
            )

            _send(
                gateway,
                chat_id,
                "سلام 👋\n\n"
                "نام و نام خانوادگی خود را "
                "وارد کنید."
            )

            return {
                "action": "skip",
                "reason": "bale-registration-started",
            }

        request_id = row[0]
        request_code = row[1]
        candidate_name = row[2] or ""
        status = row[3]
        if status == "expired":
            status = "pending_approval" if candidate_name.strip() else "awaiting_name"

        if status in {"awaiting_name", "needs_correction"}:

            if not text or text.startswith("/"):
                _send(
                    gateway,
                    chat_id,
                    "نام و نام خانوادگی خود را "
                    "وارد کنید."
                )

                return {
                    "action": "skip",
                    "reason": "bale-registration-awaiting-name",
                }

            now = _now()

            conn.execute(
                """
                UPDATE access_requests
                SET
                    candidate_name = ?,
                    status = 'pending_approval',
                    updated_at = ?
                WHERE id = ?
                """,
                (
                    text,
                    now,
                    request_id,
                ),
            )

            conn.execute(
                """
                UPDATE channel_users
                SET
                    registration_status = 'pending_approval',
                    updated_at = ?
                WHERE platform = 'bale'
                  AND user_id = ?
                """,
                (
                    now,
                    user_id,
                ),
            )

            _log_event(
                conn,
                request_id,
                request_code,
                user_id,
                "name_submitted",
                value=text,
            )

            conn.commit()

            _send(
                gateway,
                chat_id,
                "✅ درخواست احراز هویت ثبت شد.\n\n"
                "لطفاً تا بررسی و تأیید "
                "صبرکنید\n"
            )

            admin_message = (
                "🔐 درخواست دسترسی جدید\n\n"
                f"نام و نام خانوادگی: {text}\n"
                f"شناسه بله: {user_id}\n"
                f"کد درخواست: {request_code}\n"
                f"زمان درخواست: {_now()}\n\n"
                f"برای تأیید:\n"
                f"تایید {request_code}\n\n"
                f"برای اصلاح:\n"
                f"اصلاح {request_code}\n\n"
                f"برای رد:\n"
                f"رد {request_code}"
            )

            _send_to_admins(
                gateway,
                admin_message,
            )

            return {
                "action": "skip",
                "reason": "bale-registration-pending-approval",
            }

        if status == "pending_approval":
            _send(
                gateway,
                chat_id,
                "⏳ درخواست احراز هویت شما "
                "در حال بررسی است.\n\n"
                "پس از تأیید، "
                "به شما اطلاع داده خواهد شد."
            )

            return {
                "action": "skip",
                "reason": "bale-registration-still-pending",
            }

        return {
            "action": "skip",
            "reason": "bale-registration-blocked",
        }

    finally:
        conn.close()


def register(ctx):
    ctx.register_hook(
        "pre_gateway_dispatch",
        _handle_bale,
    )
