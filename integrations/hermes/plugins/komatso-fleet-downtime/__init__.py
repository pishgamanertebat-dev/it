"""Lightweight, pure-stdlib read-only Fleet Downtime Analytics plugin for all approved KomatsoAI profiles.

Security & Performance invariants:
- Pure Python stdlib at import time (ZERO openpyxl/PyMuPDF/PIL imports in Hermes Gateway).
- Zero SQLite or disk I/O on turns that do not invoke a downtime tool.
- Available to ALL approved KomatsoAI users across all profiles (default, admin, maintenance, net)
  without granting access to E:\\Function files, repair workbooks, shell, or admin operations.
- Fail-closed for unapproved, revoked, rejected, or non-private Bale senders.
"""
from __future__ import annotations

from contextlib import closing
import json
import os
from pathlib import Path
import sqlite3
import sys
from typing import Any

ROOT = Path(r"E:\KomatsoAI")
if str(ROOT) not in sys.path:
    sys.path.append(str(ROOT))
import tools as _tools_pkg

_project_tools = str(ROOT / "tools")
if _project_tools not in _tools_pkg.__path__:
    _tools_pkg.__path__.append(_project_tools)

from integrations.hermes.shared_fast_core import Operation, register_operations
from tools.fleet.downtime import DEFAULT_DB_PATH, DEFAULT_RUNTIME_DIR
from tools.fleet.downtime.query import DowntimeQueryService

TOOLSET = "komatso_fleet_downtime"
AUTH_DB_PATH = ROOT / "reports" / "telegram_usage" / "telegram_users.db"

PROPERTIES: dict[str, dict[str, Any]] = {
    "period": {
        "type": "string",
        "description": "دوره شمسی به فرمت YYYY-MM (مثلاً 1405-08) یا نام ماه فارسی (مثلاً آبان ۱۴۰۵). در صورت حذف، آخرین دوره فعال انتخاب می‌شود.",
    },
    "date": {
        "type": "string",
        "description": "تاریخ شمسی دقیق (مثلاً 1405/08/20 یا ۲۰ آبان ۱۴۰۵).",
    },
    "device_code": {
        "type": "string",
        "description": "کد یا نام دستگاه (مثلاً 706، ۷۰۶، بیل ۱۲۵۲، EX853، 717، گریدر).",
    },
    "device_a": {
        "type": "string",
        "description": "کد دستگاه اول برای مقایسه (مثلاً 706).",
    },
    "device_b": {
        "type": "string",
        "description": "کد دستگاه دوم برای مقایسه (مثلاً 717).",
    },
    "metric": {
        "type": "string",
        "enum": [
            "downtime_hours",
            "downtime_pct",
            "repair_hours",
            "repair_pct",
            "work_hours",
            "work_pct",
            "days_zero_work",
            "max_consecutive_zero_work_days",
        ],
        "description": "شاخص رتبه‌بندی دستگاه‌ها.",
    },
    "order": {
        "type": "string",
        "enum": ["desc", "asc"],
        "description": "ترتیب مرتب‌سازی: desc (بیشترین) یا asc (کمترین، مثلاً کمترین کارکرد).",
    },
    "limit": {
        "type": "integer",
        "minimum": 1,
        "maximum": 50,
        "description": "حداکثر تعداد نتایج برگشتی.",
    },
    "cause": {
        "type": "string",
        "description": "فیلتر علت توقف (تعمیرات، بی‌باری، تأخیر انفجار، نبود بارگیر، تأخیر راننده).",
    },
    "keyword": {
        "type": "string",
        "description": "کلیدواژه یا موضوع فارسی در ستون ملاحظات (مثلاً روغن، موتور، انفجار، جوشکاری).",
    },
}

TOOL_SPECS: tuple[tuple[str, list[str], list[str], str], ...] = (
    (
        "list_downtime_periods",
        [],
        [],
        "فهرست دوره‌های ماهانه واردشده توقفات و کارکرد ماشین‌آلات به‌همراه پوشش روزها و وضعیت کیفیت داده.",
    ),
    (
        "get_daily_downtime",
        ["date", "device_code", "limit"],
        ["date"],
        "دریافت آمار کارکرد و توقفات یک روز مشخص (با پشتیبانی از فیلتر یک دستگاه و اعلام صریح روز فاقد شیت).",
    ),
    (
        "get_monthly_downtime_summary",
        ["period"],
        [],
        "خلاصه کامل ماهانه کل ناوگان: ساعات مبنا، کارکرد خالص، توقف کل، درصدهای وزنی، علل پنج‌گانه، گروه‌های ماشین‌آلات و هشدار مغایرت‌ها.",
    ),
    (
        "get_device_downtime",
        ["device_code", "period", "date"],
        ["device_code"],
        "گزارش عملکرد و توقفات یک دستگاه خاص (ماهانه یا یک روز مشخص) شامل تفکیک علل، روزهای بدون کارکرد و ملاحظات. در کد مبهم (مثل 601) درخواست رفع ابهام می‌کند.",
    ),
    (
        "rank_devices_by_downtime",
        ["period", "metric", "order", "limit"],
        [],
        "رتبه‌بندی دستگاه‌ها بر اساس ساعات توقف، درصد توقف، توقف تعمیراتی، کمترین/بیشترین کارکرد یا تعداد روزهای بدون کارکرد.",
    ),
    (
        "get_downtime_causes",
        ["period", "device_code", "cause"],
        [],
        "تحلیل تفکیک علل پنج‌گانه توقف (تعمیرات، تأخیر انفجار، بی‌باری، تأخیر راننده، نبود بارگیر) و نسبت تعمیراتی به عملیاتی.",
    ),
    (
        "get_downtime_notes",
        ["period", "device_code", "date", "keyword", "limit"],
        [],
        "جست‌وجو و تحلیل موضوعات پرتکرار ستون ملاحظات فارسی به‌همراه متن اصلی ثبت‌شده در منبع.",
    ),
    (
        "compare_device_performance",
        ["device_a", "device_b", "period"],
        ["device_a", "device_b"],
        "مقایسه دو دستگاه از نظر ساعات مبنا، کارکرد، توقف کل، توقف تعمیراتی، درصد کارکرد و علل اصلی توقف.",
    ),
    (
        "get_monthly_downtime_report",
        ["period"],
        [],
        "دریافت خلاصه مدیریتی و مشخصات فایل PDF آماده و Cacheشده گزارش ماهانه توقفات ماشین‌آلات.",
    ),
)


def _session_identity() -> dict[str, str]:
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return {"platform": "cli", "chat_type": "dm", "chat_id": "", "user_id": ""}
    return {
        key: str(get_session_env("HERMES_SESSION_" + env, "") or "")
        for key, env in (
            ("platform", "PLATFORM"),
            ("chat_type", "CHAT_TYPE"),
            ("chat_id", "CHAT_ID"),
            ("user_id", "USER_ID"),
        )
    }


def is_approved_user(who: dict[str, str], *, auth_db: Path = AUTH_DB_PATH) -> bool:
    """Read-only downtime queries are open to ALL approved KomatsoAI users (not admin-only).

    Fail-closed on unapproved, pending, rejected, revoked, or non-private Bale chats.
    """
    platform = (who.get("platform") or "").lower()
    user_id = str(who.get("user_id") or "").strip()
    chat_id = str(who.get("chat_id") or "").strip()
    chat_type = (who.get("chat_type") or "").lower()

    if platform == "cli":
        return True
    if platform == "bale":
        if chat_type != "dm" or not user_id or user_id != chat_id:
            return False
        if not auth_db.is_file():
            return False
        try:
            with closing(sqlite3.connect(auth_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
                conn.execute("PRAGMA query_only=ON")
                row = conn.execute(
                    "SELECT registration_status, chat_id FROM channel_users "
                    "WHERE platform = 'bale' AND user_id = ?",
                    (user_id,),
                ).fetchone()
            return bool(row and row[0] == "approved" and str(row[1]) == user_id)
        except (sqlite3.Error, OSError):
            return False
    if platform == "telegram":
        if not user_id or not auth_db.is_file():
            return False
        try:
            with closing(sqlite3.connect(auth_db.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)) as conn:
                conn.execute("PRAGMA query_only=ON")
                row = conn.execute(
                    "SELECT registration_status FROM channel_users "
                    "WHERE platform = 'telegram' AND user_id = ?",
                    (user_id,),
                ).fetchone()
            return bool(row and row[0] == "approved")
        except (sqlite3.Error, OSError):
            return False
    return False


def execute_tool(
    tool_name: str,
    args: dict[str, Any] | None,
    *,
    who: dict[str, str] | None = None,
    auth_db: Path = AUTH_DB_PATH,
    db_path: Path = DEFAULT_DB_PATH,
    runtime_dir: Path = DEFAULT_RUNTIME_DIR,
) -> str:
    caller = who if who is not None else _session_identity()
    if not is_approved_user(caller, auth_db=auth_db):
        return json.dumps(
            {"ok": False, "error": "دسترسی فقط برای کاربران تأییدشده KomatsoAI مجاز است."},
            ensure_ascii=False,
        )
    spec_map = {name: (keys, req) for name, keys, req, _ in TOOL_SPECS}
    if tool_name not in spec_map:
        return json.dumps({"ok": False, "error": "ابزار نامعتبر است."}, ensure_ascii=False)
    allowed_keys, required_keys = spec_map[tool_name]
    payload = args if isinstance(args, dict) else {}
    unknown = set(payload) - set(allowed_keys)
    if unknown:
        return json.dumps({"ok": False, "error": f"پارامتر نامعتبر: {sorted(unknown)}"}, ensure_ascii=False)
    missing = [k for k in required_keys if not payload.get(k)]
    if missing:
        return json.dumps({"ok": False, "error": f"پارامتر الزامی وارد نشده: {missing}"}, ensure_ascii=False)

    svc = DowntimeQueryService(db_path=db_path, runtime_dir=runtime_dir)
    method = getattr(svc, tool_name)
    try:
        result = method(**payload)
    except Exception as exc:
        return json.dumps({"ok": False, "error": f"خطا در پرس‌وجوی توقفات: {exc}"}, ensure_ascii=False)

    # Note: Do not attach PDF path via MEDIA unless pdf_ready is True
    return json.dumps(result, ensure_ascii=False, default=str)


def downtime_prompt_context(_info: Any) -> str:
    """Ultra-compact routing hint (<450 chars); adds ZERO Excel/SQLite data to prompt."""
    return (
        "برای پرسش درباره کارکرد، توقفات روزانه یا ماهانه ماشین‌آلات، علل توقف، مقایسه دستگاه‌ها یا "
        "گزارش ماهانه توقفات، فقط در صورت مرتبط بودن سوال از ابزارهای komatso_fleet_downtime استفاده کن. "
        "در سوال‌های غیرمرتبط این ابزارها را صدا نزن. اعداد را خودت محاسبه نکن؛ دقیقاً از خروجی ابزار با ذکر دوره، "
        "تاریخ، واحد ساعت:دقیقه و منبع داده گزارش کن. در کد مبهم (مثل ۶۰۱) درخواست رفع ابهام کن و علت قطعی خرابی را صرفاً با همزمانی گزارش‌ها نتیجه نگیر."
    )


def register(ctx: Any) -> None:
    ctx.register_system_prompt_section("komatso.fleet.downtime.routing", downtime_prompt_context, max_chars=500)
    operations: list[Operation] = []
    for name, keys, required, description in TOOL_SPECS:
        schema = {
            "name": name,
            "description": description,
            "parameters": {
                "type": "object",
                "properties": {k: PROPERTIES[k] for k in keys},
                "required": required,
                "additionalProperties": False,
            },
        }

        def _handler(args: dict[str, Any], _name: str = name, **kwargs: Any) -> str:
            return execute_tool(_name, args)

        operations.append(Operation(name, TOOLSET, schema, _handler, description))
    register_operations(ctx, operations)
