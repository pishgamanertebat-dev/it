"""Publication notices in the existing Journal, using the existing Bale sender.

Only the explicitly configured owner, with a current existing alert capability,
can receive a notice. No inferred recipients and no automatic uncertain resend.
"""
from datetime import datetime, timezone
from zoneinfo import ZoneInfo

from tools.scheduler import delivery_alert as alerts
from .entry_service import EntryError

TEHRAN = ZoneInfo('Asia/Tehran')


def now():
    return datetime.now(timezone.utc)


def phase(moment=None):
    if moment is not None and moment.utcoffset() is None:
        raise EntryError('PUBLICATION_TIMEZONE_REQUIRED')
    local = (moment or now()).astimezone(TEHRAN)
    minute = local.hour*60 + local.minute
    return 'before' if minute < 750 else 'open' if minute < 775 else 'closed'


def ensure(db):
    alerts.ensure_schema(db)
    with db:
        db.execute('''CREATE TABLE IF NOT EXISTS maintenance_publication_watch_v1 (
            slot TEXT PRIMARY KEY, pending INTEGER NOT NULL, reason TEXT NOT NULL,
            last_success TEXT, updated TEXT NOT NULL)''')


def observe(db, pending, reason='', *, moment=None, success=False):
    moment = moment or now()
    ensure(db)
    day = moment.astimezone(TEHRAN).date().isoformat()
    stamp = moment.isoformat()
    previous = db.execute('SELECT last_success FROM maintenance_publication_watch_v1 ORDER BY updated DESC LIMIT 1').fetchone()
    last_success = stamp if success else previous[0] if previous else None
    # Errors are codes only: paths, report text, names and exception strings
    # must never enter a Bale notice.
    import re
    code = reason if re.fullmatch('[A-Z][A-Z0-9_]{0,95}', reason) else 'PUBLICATION_REVIEW_REQUIRED' if reason else ''
    with db:
        if success:
            db.execute('UPDATE maintenance_publication_watch_v1 SET pending=0')
        db.execute('''INSERT INTO maintenance_publication_watch_v1 VALUES (?,?,?,?,?)
            ON CONFLICT(slot) DO UPDATE SET pending=excluded.pending,reason=excluded.reason,
            last_success=excluded.last_success,updated=excluded.updated''',
            (day, pending, code, last_success, stamp))
    return day


def notify(db, settings, *, moment=None, authorization=None, sender_factory=None):
    moment = moment or now()
    ensure(db)
    owner = settings.get('alert_owner')
    store = alerts.AuthorizationStore() if authorization is None else authorization
    recipients = (owner,) if isinstance(owner, str) and owner in alerts._operators(store) else ()
    for watch in db.execute('SELECT * FROM maintenance_publication_watch_v1 ORDER BY slot').fetchall():
        # A later day's tick must also close a missed day's pending window.
        due = datetime.fromisoformat(watch['slot']+'T12:55:00').replace(tzinfo=TEHRAN)
        if moment < due or not watch['pending']:
            continue
        key = ('maintenance_excel_1230', due.isoformat(), 1)
        reason = watch['reason'] or 'PUBLICATION_WINDOW_EXPIRED'
        labels = {'SOURCE_UNAVAILABLE':'ارتباط با معدن برقرار نشد',
                  'BACKOFF_SKIP':'ارتباط با معدن؛ انتظار طبق Backoff',
                  'PUBLICATION_SMB_EXCLUSION_UNVERIFIED':'کنترل انحصار نوشتن معدن تأیید نشده است',
                  'PUBLICATION_DEFERRED_EXCEL_OWNER_FILE':'فایل Excel مقصد باز یا قفل است',
                  'PUBLICATION_EXCEL_LOCKED':'فایل Excel مقصد قفل است',
                  'PUBLICATION_SHARE_PERMISSION_DENIED':'مجوز لازم برای نوشتن روی Share وجود ندارد',
                  'PUBLICATION_INTEGRITY_REVIEW_REQUIRED':'سلامت Journal یا Workbook نیازمند بررسی انسانی است',
                  'PUBLICATION_READBACK_UNCERTAIN':'نتیجه انتشار نیازمند بررسی انسانی است',
                  'PUBLICATION_WINDOW_EXPIRED':'پنجره روزانه بدون انتشار پایان یافت'}
        text = ('⚠️ انتشار گزارش‌های تعمیرات در معدن انجام نشد.\n'
                f"نوبت: {watch['slot']}، ساعت ۱۲:۵۵ تهران\n"
                f"علت: {labels.get(reason, 'خطای کنترل؛ بررسی انسانی لازم است')} ({reason})\nعملیات در انتظار: {watch['pending'] if watch['pending'] >= 0 else 'نیازمند بررسی'}\n"
                'گزارش‌های تأییدشده در Function محفوظ‌اند.\nوضعیت: در انتظار نوبت بعدی.')
        if watch['last_success']: text += '\nآخرین انتشار موفق: '+watch['last_success']
        with db:
            db.execute('''INSERT OR IGNORE INTO delivery_alert_case VALUES
                (?,?,?,'publication','pending','pending',?,NULL,?,?)''', (*key, text, moment.isoformat(), moment.isoformat()))
        case = db.execute('SELECT * FROM delivery_alert_case WHERE schedule_id=? AND due=? AND episode=?', key).fetchone()
        # Existing notifier persists claim/message-id and retries only known
        # pre-send failures. A lost acknowledgement remains uncertain.
        alerts._dispatch(db, case, 'exception', case['exception_text'], recipients,
                         moment, sender_factory or alerts._default_sender)
