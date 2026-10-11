"""Notify existing delivery-alert subscribers when a missing-day notice reaches a recipient."""
from datetime import timezone
import logging
import sqlite3

from .delivery_alert import (
    STALE_CLAIM_SECONDS, _default_sender, _iso, _label, _message_id,
    _operators, _parse, _report_tasks, _target, _title,
)

logger = logging.getLogger(__name__)
ACTIVATION_KEY = 'source_notice_alert_activated_at'


def ensure_activation(conn, now):
    """Baseline already-sent notices once; new notices are observed after this tick."""
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS source_notice_baseline (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            PRIMARY KEY(schedule_id,due,recipient_id))''')
        conn.execute('''CREATE TABLE IF NOT EXISTS source_notice_alert_receipt (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            operator_id TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT,
            error TEXT, updated TEXT NOT NULL,
            PRIMARY KEY(schedule_id,due,recipient_id,operator_id))''')
        if conn.execute('SELECT 1 FROM notifier_meta WHERE key=?', (ACTIVATION_KEY,)).fetchone():
            return
        conn.execute('''INSERT OR IGNORE INTO source_notice_baseline
            SELECT schedule_id,due,recipient_id FROM receipts
            WHERE delivery_kind='notice' AND status='sent' ''')
        conn.execute('INSERT INTO notifier_meta VALUES (?,?)', (ACTIVATION_KEY, _iso(now)))


def notice_text(title, target, label):
    return (f'⚠️ {title} — {target}\n'
            f'به دلیل به‌روز نبودن اطلاعات، برای {label} ارسال نشد. '
            'هشدار به گیرنده ارسال شد؛ لطفاً فایل گزارش را بررسی کنید.')


def _mark(conn, key, status, now, message_id=None, error=None):
    with conn:
        conn.execute('''UPDATE source_notice_alert_receipt
            SET status=?,message_id=?,error=?,updated=?
            WHERE schedule_id=? AND due=? AND recipient_id=? AND operator_id=?''',
            (status, message_id, error, _iso(now), *key))


def _send(conn, key, text, now, sender_factory):
    from .tasks import TransportRejected, TransportUnavailable
    with conn:
        # Serialize claims before releasing the ledger lock for network I/O.
        conn.execute('BEGIN IMMEDIATE')
        row = conn.execute('''SELECT status,updated FROM source_notice_alert_receipt
            WHERE schedule_id=? AND due=? AND recipient_id=? AND operator_id=?''', key).fetchone()
        if row and row['status'] in {'sent', 'uncertain'}:
            return
        if row and row['status'] == 'sending':
            if (now.astimezone(timezone.utc) - _parse(row['updated'])).total_seconds() >= STALE_CLAIM_SECONDS:
                conn.execute('''UPDATE source_notice_alert_receipt
                    SET status='uncertain',error='InterruptedSend',updated=?
                    WHERE schedule_id=? AND due=? AND recipient_id=? AND operator_id=?''', (_iso(now), *key))
            return
        conn.execute('''INSERT INTO source_notice_alert_receipt
            VALUES (?,?,?,?,'sending',NULL,NULL,?)
            ON CONFLICT(schedule_id,due,recipient_id,operator_id) DO UPDATE SET
                status='sending',message_id=NULL,error=NULL,updated=excluded.updated''',
            (*key, _iso(now)))
    sender = None
    attempted = False
    try:
        sender = sender_factory()
        attempted = True
        mid = sender.message(key[3], text)
    except (TransportUnavailable, TransportRejected) as exc:
        _mark(conn, key, 'failed', now, error=type(exc).__name__)
    except Exception as exc:
        _mark(conn, key, 'uncertain' if attempted else 'failed', now, error=type(exc).__name__)
    else:
        _mark(conn, key, 'sent', now, message_id=_message_id(mid))
        logger.info('source notice alert sent schedule=%s due=%s recipient=%s operator=%s', *key)
    finally:
        if sender is not None:
            try:
                sender.close()
            except Exception:
                logger.error('Source notice alert sender close failed')


def scan_source_notices(conn, jobs, now, store, *, sender_factory=None):
    """One short alert per report/day/recipient/operator, independent of report retries."""
    conn.row_factory = sqlite3.Row
    ensure_activation(conn, now)
    operators = _operators(store)
    if not operators:
        return
    from .no_data import current_recipients
    factory = sender_factory or _default_sender
    for job in jobs:
        if not job.get('enabled', True) or job.get('task') not in _report_tasks():
            continue
        try:
            expected = set(current_recipients(job['task'], store))
            notices = conn.execute('''SELECT notice.due,notice.recipient_id
                FROM receipts AS notice
                WHERE notice.schedule_id=? AND notice.delivery_kind='notice' AND notice.status='sent'
                  AND NOT EXISTS (SELECT 1 FROM source_notice_baseline AS baseline
                    WHERE baseline.schedule_id=notice.schedule_id AND baseline.due=notice.due
                      AND baseline.recipient_id=notice.recipient_id)
                  AND NOT EXISTS (SELECT 1 FROM receipts AS report
                    WHERE report.schedule_id=notice.schedule_id AND report.due=notice.due
                      AND report.recipient_id=notice.recipient_id AND report.delivery_kind='report'
                      AND report.status IN ('sent','uncertain','revoked'))''', (job['id'],)).fetchall()
            for notice in notices:
                recipient = notice['recipient_id']
                if recipient not in expected:
                    continue
                text = notice_text(_title(job), _target(notice['due']), _label(store, recipient))
                for operator in operators:
                    _send(conn, (job['id'], notice['due'], recipient, operator), text, now, factory)
        except Exception as exc:
            logger.error('Source notice alert scan failed schedule=%s error=%s', job['id'], type(exc).__name__)
