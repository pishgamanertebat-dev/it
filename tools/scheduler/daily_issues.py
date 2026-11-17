"""One brief, issue-only message at 19:00 Asia/Tehran; ledger evidence stays intact."""
from collections import defaultdict
from datetime import timedelta
import hashlib
import sqlite3

from .delivery_alert import (
    TEHRAN, STALE_CLAIM_SECONDS, _default_sender, _delivery_summary, _iso,
    _message_id, _operators, _parse, _target, occurrence_recipients,
)
from .owner_history import closed_by_owner
from .report_errors import REASONS, public_reason

SEND_AT = (19, 0)
SOURCE_FILES = {
    'overflow': 'سرریز روزانه.xlsx',
    'mechanical_overflow': 'سرریز روزانه.xlsx',
    'driver_daily': 'گزارش روزانه رانندگان2.xlsx',
    'mechanical_driver_daily': 'گزارش روزانه رانندگان2.xlsx',
    'metalwork_driver_daily': 'گزارش روزانه رانندگان2.xlsx',
    'maintenance_daily_report': 'تعمیرات 1405.xlsx',
    'oil_balance_daily_report': 'موازنه روغن مصرفی.xlsx',
    'machinery_performance_daily_report': 'عملکرد ماشین آلات.xlsx',
}
SHORT_REASONS = {
    'date_missing': 'داده این تاریخ کامل نیست',
    'no_target_date_records': 'داده این تاریخ کامل نیست',
    'source_stale': 'به‌روز بودن نسخه اکسل تأیید نشده',
    'source_unavailable': 'اکسل قابل خواندن نیست',
    'source_missing': 'اکسل پیدا نشد',
    'source_locked': 'اکسل قفل است',
    'source_permission': 'مجوز خواندن اکسل موجود نیست',
    'source_changed': 'اکسل هنگام تولید گزارش تغییر کرد',
    'source_invalid': 'ساختار یا داده اکسل معتبر نیست',
    'source_section_missing': 'جدول تعویض‌ها یا موجودی روغن پیدا نشد',
    'source_formula_unverified': 'نتیجه ذخیره‌شده فرمول‌ها قابل تأیید نیست',
    'source_performance_formula_unverified': 'نتیجه ذخیره‌شده فرمول‌ها قابل تأیید نیست',
    'source_identity_mismatch': 'کد رکورد با کد شیت متفاوت است',
    'driver_pdf_content': 'کامل‌بودن محتوای PDF تأیید نشده',
    'pdf_layout': 'متن در PDF جا نمی‌شود',
    'pdf_validation': 'کامل‌بودن یا خوانایی PDF تأیید نشده',
    'pdf_export': 'تبدیل اکسل به PDF ناموفق بود',
    'report_generation': 'تولید گزارش ناموفق بود',
    'report_configuration': 'تنظیمات تولید گزارش ناقص است',
    'PermissionError': 'دسترسی به فایل ناموفق بود',
    'FileNotFoundError': 'اکسل پیدا نشد',
    'OSError': 'خواندن اکسل ناموفق بود',
    'ValueError': 'تولید گزارش ناموفق بود',
    'invalid_source': 'ساختار یا داده اکسل معتبر نیست',
    'transport_unavailable': 'ارتباط با بله برقرار نشد',
    'TransportUnavailable': 'ارتباط با بله برقرار نشد',
    'TransportRejected': 'بله فایل را نپذیرفت',
    'delivery_failed': 'ارسال گزارش ناموفق بود',
}


def ensure_schema(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS report_correction_review (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            artifact_id TEXT NOT NULL, status TEXT NOT NULL, reason TEXT NOT NULL,
            evidence TEXT NOT NULL, updated TEXT NOT NULL,
            PRIMARY KEY(schedule_id,due,recipient_id,artifact_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS manager_daily_issues (
            day TEXT PRIMARY KEY, captured TEXT NOT NULL, sha256 TEXT NOT NULL,
            message TEXT NOT NULL)''')
        conn.execute('''CREATE TABLE IF NOT EXISTS manager_daily_issue_receipt (
            day TEXT NOT NULL, operator_id TEXT NOT NULL, status TEXT NOT NULL,
            message_id TEXT, error TEXT, updated TEXT NOT NULL,
            PRIMARY KEY(day,operator_id))''')


def _reason(code, details):
    public = public_reason(code, details)
    short = SHORT_REASONS.get(code) or REASONS.get(code)
    if not short:
        return 'تولید یا ارسال گزارش کامل نشده'
    # Retain validated sheet/cell coordinates, without raw exception text.
    return short + (public[len(REASONS[code]):] if public else '')


def collect_issues(conn, jobs, now, store):
    """Only recorded open problems; no successful counts or invented past runs."""
    issues = set()
    for job in jobs:
        filename = SOURCE_FILES.get(job.get('task'))
        if not filename or not job.get('enabled', True):
            continue
        runs = {r['due']: dict(r) for r in conn.execute(
            'SELECT * FROM runs WHERE schedule_id=?', (job['id'],))}
        holds = {r['due'] for r in conn.execute(
            "SELECT due FROM report_recovery_hold WHERE schedule_id=? AND status='held'",
            (job['id'],))}
        corrections = defaultdict(list)
        for row in conn.execute('''SELECT * FROM report_correction_review
                WHERE schedule_id=? AND status NOT IN ('sent','resolved')''', (job['id'],)):
            corrections[row['due']].append(row)
        for due in runs.keys() | corrections.keys():
            if _parse(due) > now or closed_by_owner(due):
                continue
            target = _target(due)
            for correction in corrections[due]:
                section = {'metalwork': ' آهنگری', 'mechanical': ' مکانیکی'}.get(correction['artifact_id'], '')
                reason = ('محتوای PDF' + section + ' نیازمند بررسی'
                          if correction['status'] == 'needs_content_review'
                          else 'اصلاحیه PDF' + section + ' تعیین تکلیف نشده')
                issues.add((filename, target, reason))
            run = runs.get(due)
            # Owner-held history belongs to its separate review, not a daily audit.
            # Explicit pending content corrections above remain visible.
            if run is None or due in holds or run['status'] == 'no_data_final':
                continue
            expected = occurrence_recipients(conn, job, run, store, now)
            receipts = _delivery_summary(conn, job, run, expected or (), store)
            if expected and receipts and all(r['status'] in {'sent', 'revoked'} for r in receipts):
                continue
            if run['status'] == 'uncertain' or any(r['status'] in {'uncertain', 'sending'} for r in receipts):
                reason = 'نتیجه ارسال در بله نامشخص است'
            elif expected is None:
                reason = 'گیرندگان گزارش قابل بررسی نیستند'
            elif run.get('error'):
                reason = _reason(run['error'], run.get('error_detail'))
            elif run['status'] == 'succeeded' and not expected:
                # A legacy success without historical recipient evidence is not
                # a new operational failure. Never invent today's recipients.
                continue
            elif run['status'] == 'succeeded':
                reason = 'رسید کامل ارسال موجود نیست'
            elif run['status'] == 'running':
                reason = 'گزارش هنوز در حال اجراست'
            else:
                reason = 'ارسال گزارش کامل نشده'
            issues.add((filename, target, reason))
    return sorted(issues)


def _dates(values):
    import jdatetime
    dates = sorted({jdatetime.date(*map(int, value.split('/'))) for value in values})
    ranges = []
    for value in dates:
        if ranges and value - ranges[-1][1] == timedelta(days=1):
            ranges[-1][1] = value
        else:
            ranges.append([value, value])
    def label(value):
        return f'{value.year:04d}/{value.month:02d}/{value.day:02d}'
    return '، '.join(label(start) + (' تا ' + label(end) if end != start else '') for start, end in ranges)


def format_message(issues):
    """One line per Excel file, duplicate recipients/jobs collapsed, no header."""
    grouped = defaultdict(lambda: defaultdict(set))
    for filename, target, reason in issues:
        grouped[filename][reason].add(target)
    return '\n'.join(filename + ' | ' + '؛ '.join(
        _dates(dates) + ': ' + reason for reason, dates in sorted(reasons.items()))
        for filename, reasons in sorted(grouped.items()))


def _legacy_delivered(conn, day, operator):
    # Retire pending old multipart text without changing its historical receipts.
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE name='manager_daily_receipt'").fetchone():
        return False
    return bool(conn.execute('''SELECT 1 FROM manager_daily_receipt
        WHERE day=? AND operator_id=? AND status IN ('sent','uncertain','sending')''',
        (day, operator)).fetchone())


def scan_daily_issues(conn, jobs, now, store, *, sender_factory=None):
    local = now.astimezone(TEHRAN)
    if (local.hour, local.minute) < SEND_AT:
        return
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    day = local.date().isoformat()
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        saved = conn.execute('SELECT * FROM manager_daily_issues WHERE day=?', (day,)).fetchone()
        if not saved:
            message = format_message(collect_issues(conn, jobs, now, store))
            conn.execute('INSERT INTO manager_daily_issues VALUES (?,?,?,?)',
                         (day, _iso(now), hashlib.sha256(message.encode()).hexdigest(), message))
            saved = conn.execute('SELECT * FROM manager_daily_issues WHERE day=?', (day,)).fetchone()
    message = saved['message']
    if hashlib.sha256(message.encode()).hexdigest() != saved['sha256']:
        raise RuntimeError('Daily issue message integrity failure')
    if not message:
        return
    from .tasks import TransportUnavailable, TransportRejected
    for operator in _operators(store):
        if _legacy_delivered(conn, day, operator):
            continue
        key = (day, operator)
        with conn:
            conn.execute('BEGIN IMMEDIATE')
            row = conn.execute('SELECT * FROM manager_daily_issue_receipt WHERE day=? AND operator_id=?', key).fetchone()
            if row and row['status'] in {'sent', 'uncertain'}:
                continue
            if row and row['status'] == 'sending':
                if (now - _parse(row['updated'])).total_seconds() >= STALE_CLAIM_SECONDS:
                    conn.execute('''UPDATE manager_daily_issue_receipt SET status='uncertain',
                        error='InterruptedSend',updated=? WHERE day=? AND operator_id=?''', (_iso(now), *key))
                continue
            conn.execute('''INSERT INTO manager_daily_issue_receipt VALUES (?,?,'sending',NULL,NULL,?)
                ON CONFLICT(day,operator_id) DO UPDATE SET status='sending',error=NULL,updated=excluded.updated''',
                (*key, _iso(now)))
        sender = None
        attempted = False
        mid = error = None
        try:
            if operator not in _operators(store):
                state = 'revoked'
            else:
                sender = (sender_factory or _default_sender)()
                attempted = True
                mid = _message_id(sender.message(operator, message))
                state = 'sent' if mid else 'uncertain'
        except (TransportUnavailable, TransportRejected) as exc:
            state, error = 'failed', type(exc).__name__
        except Exception as exc:
            state, error = ('uncertain' if attempted else 'failed'), type(exc).__name__
        finally:
            if sender:
                try:
                    sender.close()
                except Exception:
                    pass
        with conn:
            conn.execute('''UPDATE manager_daily_issue_receipt SET status=?,message_id=?,error=?,updated=?
                WHERE day=? AND operator_id=?''', (state, mid, error, _iso(now), *key))
