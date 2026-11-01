"""Source-completion reminders with independent slots and explicit recipients."""
from datetime import timedelta, timezone
import logging
import re
import sqlite3
from zoneinfo import ZoneInfo

from .no_data import current_recipients

logger = logging.getLogger(__name__)
TEHRAN = ZoneInfo('Asia/Tehran')
TASK = 'mechanical_driver_daily'
MAINTENANCE_TASK = 'maintenance_daily_report'
TERMINAL = {'sent', 'uncertain', 'not_needed', 'revoked'}


def validate_config(raw, *, label='mechanical_reminders', default_hours=(8, 15)):
    if raw is None:
        return ()
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'hours'}:
        raise ValueError(f'Invalid {label} configuration')
    if type(raw.get('enabled', True)) is not bool:
        raise ValueError(f'{label}.enabled must be boolean')
    hours = raw.get('hours', list(default_hours))
    if (not isinstance(hours, list) or not hours
            or any(type(h) is not int or not 0 <= h <= 23 for h in hours)
            or len(set(hours)) != len(hours)):
        raise ValueError(f'{label}.hours must be distinct hours')
    return tuple(hours) if raw.get('enabled', True) else ()


def validate_maintenance_config(raw):
    if raw is None:
        return (), ()
    if not isinstance(raw, dict) or set(raw) - {'enabled', 'hours', 'recipients'}:
        raise ValueError('Invalid maintenance_reminders configuration')
    hours = validate_config({k: v for k, v in raw.items() if k != 'recipients'},
                            label='maintenance_reminders', default_hours=(11, 15))
    recipients = raw.get('recipients', [])
    if (not isinstance(recipients, list) or (hours and not recipients)
            or any(not isinstance(u, str) or not re.fullmatch(r'[1-9][0-9]*', u) for u in recipients)
            or len(set(recipients)) != len(recipients)):
        raise ValueError('maintenance_reminders.recipients must be distinct private Bale IDs')
    return hours, tuple(recipients)


def reminder_recipients(job, store):
    if job['task'] != MAINTENANCE_TASK:
        return current_recipients(TASK, store)
    # A text reminder grants no workbook access or PDF subscription. Resolve the
    # existing approved private mechanical/metalwork identities, then narrow to
    # the operator's explicit list. Recheck this set before every send.
    from tools.authorization import MECH_DRIVER_RECEIVE, METALWORK_DRIVER_RECEIVE
    eligible = set()
    for capability in (MECH_DRIVER_RECEIVE, METALWORK_DRIVER_RECEIVE):
        resolved = store.resolve_active_recipients(capability)
        if resolved.status == 'store_unavailable':
            return ()
        if resolved.status == 'ready':
            eligible.update(resolved.recipients)
    return tuple(u for u in job.get('maintenance_reminder_recipients', ()) if u in eligible)


def ensure_schema(conn):
    with conn:
        conn.execute('''CREATE TABLE IF NOT EXISTS completion_reminder_receipts (
            schedule_id TEXT NOT NULL, slot TEXT NOT NULL, target_date TEXT NOT NULL,
            recipient_id TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT,
            error TEXT, updated TEXT NOT NULL, next_attempt_at TEXT,
            PRIMARY KEY(schedule_id,slot,recipient_id))''')


def probe(target):
    from integrations.hermes.function_domain.driver_report import load_driver
    report = load_driver(target)
    content = report['sections']['mechanical']
    if report['date'] != target or content['status'] not in {'ready', 'no_defects', 'date_missing'}:
        raise ValueError('Unexpected mechanical source state')
    return {'status': content['status'], 'latest_date': report.get('latest_date')}


def probe_maintenance(target):
    from tools.fleet.maintenance_daily.report import source_snapshot, scan
    with source_snapshot() as (data, _):
        report = scan(data, target)
    if report['date'] != target:
        raise ValueError('Unexpected maintenance source date')
    return {'status': 'ready' if report['rows_matched'] else 'date_missing',
            'latest_date': max(report['available_dates'], default=None)}


def maintenance_reminder_text(target, result):
    text = (f'⏰ یادآوری تکمیل گزارش تعمیرات ۱۴۰۵\nتاریخ گزارش: {target}\n'
            'برای این تاریخ، فعالیت تعمیراتی در فایل ثبت نشده است.\n'
            'لطفاً به تکمیل اطلاعات تعمیرات در فایل تعمیرات ۱۴۰۵ اقدام کنید.')
    if result.get('latest_date'):
        from tools.fleet.overflow.report import validate_date
        text += '\nآخرین تاریخ دارای اطلاعات: ' + validate_date(result['latest_date'])
    return text


def reminder_text(target, result):
    reason = ('اطلاعات این تاریخ هنوز ثبت نشده است.' if result['status'] == 'date_missing'
              else 'بخش مکانیکی این تاریخ خالی است؛ وضعیت ثبت اطلاعات را بررسی کنید.')
    text = (f'⏰ یادآوری تکمیل بخش مکانیکی گزارش روزانه رانندگان\n'
            f'تاریخ گزارش: {target}\n{reason}\n'
            'لطفاً به تکمیل اطلاعات تعمیرات در بخش مکانیکی گزارش رانندگان اقدام کنید.')
    if result.get('latest_date'):
        from tools.fleet.overflow.report import validate_date
        text += '\nآخرین تاریخ موجود در فایل: ' + validate_date(result['latest_date'])
    return text


def _pending(conn, key, now):
    from .delivery_alert import STALE_CLAIM_SECONDS, _parse, _iso
    row = conn.execute('''SELECT * FROM completion_reminder_receipts
        WHERE schedule_id=? AND slot=? AND recipient_id=?''', key).fetchone()
    if row is None:
        return True
    if row['status'] in TERMINAL:
        return False
    if row['status'] == 'sending':
        if now.astimezone(timezone.utc) >= _parse(row['updated']) + timedelta(seconds=STALE_CLAIM_SECONDS):
            with conn:
                conn.execute('''UPDATE completion_reminder_receipts
                    SET status='uncertain',error='InterruptedSend',updated=?
                    WHERE schedule_id=? AND slot=? AND recipient_id=? AND status='sending' ''',
                    (_iso(now), *key))
        return False
    return not row['next_attempt_at'] or now.astimezone(timezone.utc) >= _parse(row['next_attempt_at'])


def _claim(conn, key, target, now, status='sending'):
    from .delivery_alert import _iso
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        if not _pending(conn, key, now):
            return False
        conn.execute('''INSERT INTO completion_reminder_receipts
            (schedule_id,slot,target_date,recipient_id,status,updated) VALUES (?,?,?,?,?,?)
            ON CONFLICT(schedule_id,slot,recipient_id) DO UPDATE SET
                status=excluded.status,message_id=NULL,error=NULL,updated=excluded.updated,next_attempt_at=NULL''',
            (key[0], key[1], target, key[2], status, _iso(now)))
    return True


def _mark(conn, key, status, now, message_id=None, error=None):
    from .delivery_alert import _iso, _message_id
    with conn:
        conn.execute('''UPDATE completion_reminder_receipts
            SET status=?,message_id=?,error=?,updated=?,next_attempt_at=?
            WHERE schedule_id=? AND slot=? AND recipient_id=?''',
            (status, _message_id(message_id), error, _iso(now),
             _iso(now + timedelta(minutes=5)) if status == 'failed' else None, *key))


def _send(conn, key, target, text, now, store, sender_factory, *, job):
    from .tasks import TransportUnavailable, TransportRejected
    if key[2] not in reminder_recipients(job, store) or not _claim(conn, key, target, now):
        return
    sender = None
    attempted = False
    try:
        sender = sender_factory()
        sender.check_connection()
        if key[2] not in reminder_recipients(job, store):
            _mark(conn, key, 'revoked', now)
            return
        attempted = True
        mid = sender.message(key[2], text)
    except (TransportUnavailable, TransportRejected) as exc:
        _mark(conn, key, 'failed', now, error=type(exc).__name__)
    except Exception as exc:
        _mark(conn, key, 'uncertain' if attempted else 'failed', now, error=type(exc).__name__)
    else:
        _mark(conn, key, 'sent', now, message_id=mid)
        logger.info('Completion reminder sent schedule=%s slot=%s recipient=%s target=%s',
                    *key, target)
    finally:
        if sender is not None:
            try:
                sender.close()
            except Exception:
                logger.error('Completion reminder sender close failed')


def run_reminders(conn, jobs, now, store, *, checker=None, sender_factory=None):
    """Check the exact previous day, independently of PDF delivery or its receipts.

    Blank mechanical sections get an observation, never an accusation of a defect.
    Each slot closes separately, so the afternoon rechecks the updated workbook.
    Only the current slot's hour is eligible; old reminders are never replayed.
    """
    from .delivery_alert import _default_sender
    from .misfire import due_storage_key
    from .tasks import overflow_report_date
    now = now.astimezone(TEHRAN)
    hour_fields = {TASK: 'mechanical_reminder_hours', MAINTENANCE_TASK: 'maintenance_reminder_hours'}
    selected = [j for j in jobs if j.get('enabled', True) and j['task'] in hour_fields
                and now.hour in j.get(hour_fields[j['task']], ())]
    if not selected:
        return
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    slot = due_storage_key(now.replace(minute=0, second=0, microsecond=0))
    target = overflow_report_date(now)
    for job in selected:
        try:
            pending = [u for u in reminder_recipients(job, store)
                       if _pending(conn, (job['id'], slot, u), now)]
            if not pending:
                continue
            maintenance = job['task'] == MAINTENANCE_TASK
            result = (checker or (probe_maintenance if maintenance else probe))(target)
            if result['status'] == 'ready':
                for user in pending:
                    _claim(conn, (job['id'], slot, user), target, now, 'not_needed')
                continue
            if result['status'] not in {'date_missing', 'no_defects'}:
                raise ValueError('Unexpected completion probe state')
            text = (maintenance_reminder_text if maintenance else reminder_text)(target, result)
            for user in pending:
                _send(conn, (job['id'], slot, user), target, text, now, store,
                      sender_factory or _default_sender, job=job)
        except Exception as exc:
            # Locked/invalid sources are not evidence of missing entries.
            logger.error('Completion check failed schedule=%s error=%s', job['id'], type(exc).__name__)
