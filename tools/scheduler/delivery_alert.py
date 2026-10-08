"""Bale alert when a due report artifact was not actually delivered.

Runs once per existing scheduler tick, after delivery reconciliation. It never
resends a report, never treats missing source data as a transport failure, and
never alerts an occurrence that was already unresolved at activation.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone
import logging
import re
import sqlite3
from zoneinfo import ZoneInfo

from tools.authorization.delivery_alert import CAPABILITY as DELIVERY_FAILURE_RECEIVE
from tools.authorization.store import AuthorizationStore, RecipientSetResolution

logger = logging.getLogger(__name__)
GRACE = timedelta(minutes=15)
STALE_CLAIM_SECONDS = 120
TEHRAN = ZoneInfo('Asia/Tehran')
UNCERTAIN_SENTENCE = (
    'نتیجه ارسال قابل تأیید نیست و برای جلوگیری از ارسال تکراری، ارسال مجدد خودکار انجام نشده است.')
PROBLEM_RUN = frozenset({
    'retry_wait', 'partial', 'uncertain', 'failed', 'waiting_for_data', 'no_data_final', 'running'})
MISSING_ERRORS = frozenset({'date_missing', 'no_target_date_records'})
TRANSPORT_ERRORS = frozenset({
    'transport_unavailable', 'delivery_failed', 'TransportUnavailable', 'TransportRejected',
    'TransportUncertain', 'uncertain', 'TimeoutError', 'ConnectionError', 'ConnectTimeout'})
RETRYING_RUN = frozenset({'retry_wait', 'partial', 'waiting_for_data'})
REPORT_TITLES = {
    'overflow_daily_test': 'گزارش سرریز روزانه',
    'overflow_daily_mechanical': 'گزارش سرریز روزانه',
    'driver_daily_office_supervisor': 'گزارش روزانه رانندگان',
    'driver_daily_mechanical': 'گزارش روزانه معایب مکانیکی رانندگان',
    'driver_daily_metalwork': 'گزارش روزانه معایب آهنگری رانندگان',
    'maintenance_daily_report': 'گزارش روزانه تعمیرات ماشین‌آلات',
}
_SCHEMA = (
    '''CREATE TABLE IF NOT EXISTS notifier_meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)''',
    '''CREATE TABLE IF NOT EXISTS notifier_baseline_receipt (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
        delivery_kind TEXT NOT NULL, status TEXT NOT NULL,
        PRIMARY KEY (schedule_id, due, recipient_id, delivery_kind))''',
    '''CREATE TABLE IF NOT EXISTS notifier_baseline_run (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL,
        PRIMARY KEY (schedule_id, due))''',
    '''CREATE TABLE IF NOT EXISTS delivery_failure_watch (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
        delivery_kind TEXT NOT NULL, first_seen TEXT NOT NULL,
        PRIMARY KEY (schedule_id, due, recipient_id, delivery_kind))''',
    '''CREATE TABLE IF NOT EXISTS delivery_alert_case (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, episode INTEGER NOT NULL,
        fingerprint TEXT NOT NULL, exception_status TEXT NOT NULL,
        recovery_status TEXT NOT NULL, exception_text TEXT NOT NULL,
        recovery_text TEXT, created TEXT NOT NULL, updated TEXT NOT NULL,
        PRIMARY KEY (schedule_id, due, episode))''',
    '''CREATE TABLE IF NOT EXISTS delivery_alert_receipt (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, episode INTEGER NOT NULL,
        alert_kind TEXT NOT NULL, operator_id TEXT NOT NULL, status TEXT NOT NULL,
        message_id TEXT, error TEXT, updated TEXT NOT NULL,
        PRIMARY KEY (schedule_id, due, episode, alert_kind, operator_id))''',
)


def ensure_schema(conn):
    for statement in _SCHEMA:
        conn.execute(statement)


def _iso(moment):
    return _utc(moment).isoformat(timespec='seconds')


def _utc(moment):
    if moment.tzinfo is None or moment.utcoffset() is None:
        raise ValueError('Delivery alert clock must be timezone-aware')
    return moment.astimezone(timezone.utc)


def _parse(value):
    moment = datetime.fromisoformat(value)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=timezone.utc)
    return moment


def ensure_activation(conn, now):
    """Snapshot unresolved deliveries once, before this tick creates new ones."""
    ensure_schema(conn)
    if conn.execute("SELECT 1 FROM notifier_meta WHERE key='activated_at'").fetchone():
        return False
    with conn:
        conn.execute("INSERT INTO notifier_meta(key,value) VALUES ('activated_at',?)", (_iso(now),))
        # Pending rows on a missing-data occurrence are reservations, not failed sends.
        conn.execute('''INSERT INTO notifier_baseline_receipt(
            schedule_id,due,recipient_id,delivery_kind,status)
            SELECT receipt.schedule_id,receipt.due,receipt.recipient_id,receipt.delivery_kind,receipt.status
            FROM receipts AS receipt
            LEFT JOIN runs ON runs.schedule_id=receipt.schedule_id AND runs.due=receipt.due
            WHERE receipt.status IN ('uncertain','failed','sending')
               OR (receipt.status='pending' AND runs.status IN ('retry_wait','partial','failed'))''')
        conn.execute('''INSERT INTO notifier_baseline_run(schedule_id,due,status)
            SELECT schedule_id,due,status FROM runs''')
    return True


def _release_resolved(conn):
    with conn:
        conn.execute('''DELETE FROM notifier_baseline_receipt
            WHERE EXISTS (
                SELECT 1 FROM receipts AS receipt
                WHERE receipt.schedule_id=notifier_baseline_receipt.schedule_id
                  AND receipt.due=notifier_baseline_receipt.due
                  AND receipt.recipient_id=notifier_baseline_receipt.recipient_id
                  AND receipt.delivery_kind=notifier_baseline_receipt.delivery_kind
                  AND receipt.status='sent')''')
        conn.execute('''DELETE FROM notifier_baseline_run
            WHERE EXISTS (
                SELECT 1 FROM runs
                WHERE runs.schedule_id=notifier_baseline_run.schedule_id
                  AND runs.due=notifier_baseline_run.due AND runs.status='succeeded')''')


def _report_tasks():
    from .tasks import MULTI_RECIPIENT_TASKS
    return {'overflow', 'driver_daily', *MULTI_RECIPIENT_TASKS}


def _column(row, name):
    return row[name] if name in row.keys() else None


def _required_kind(run):
    status = run['status'] or ''
    error = run['error'] or ''
    if status in {'running', 'skipped'}:
        return None
    missing = (status in {'waiting_for_data', 'no_data_final'}
               or _column(run, 'data_state') == 'missing' or error in MISSING_ERRORS)
    if missing and status != 'succeeded' and error not in TRANSPORT_ERRORS and _column(run, 'data_state') != 'ready':
        return 'notice'
    if status in {'succeeded', 'uncertain', 'partial', 'retry_wait', 'failed'}:
        return 'report'
    return None


def _historical(conn, schedule_id, due, recipient, kind, receipt_exists):
    if conn.execute('''SELECT 1 FROM notifier_baseline_receipt
            WHERE schedule_id=? AND due=? AND recipient_id=? AND delivery_kind=?''',
            (schedule_id, due, recipient, kind)).fetchone():
        return True
    if receipt_exists:
        return False
    row = conn.execute(
        'SELECT status FROM notifier_baseline_run WHERE schedule_id=? AND due=?',
        (schedule_id, due)).fetchone()
    return row is not None and row['status'] in PROBLEM_RUN


def _classify(run, kind, status, any_receipt):
    """Return failed, uncertain, or None when this is not a delivery problem."""
    if status in {'sent', 'revoked'}:
        return None
    if status in {'uncertain', 'sending'}:
        return 'uncertain'
    if status in {'failed', 'pending'}:
        return 'failed'
    if status != 'missing':
        return None
    notice_state = _column(run, 'notice_delivery_state')
    if kind == 'notice':
        if notice_state == 'uncertain':
            return 'uncertain'
        if notice_state in {'retry_wait', 'failed'} or any_receipt or run['status'] == 'no_data_final':
            return 'failed'
        return None
    if run['status'] == 'succeeded':
        return None
    if run['status'] == 'uncertain':
        return 'uncertain'
    if run['status'] == 'partial':
        return 'failed'
    if run['status'] in {'retry_wait', 'failed'} and (run['error'] or '') in TRANSPORT_ERRORS:
        return 'failed'
    if any_receipt and run['status'] in {'retry_wait', 'failed'}:
        return 'failed'
    return None


def _title(job):
    return REPORT_TITLES.get(job.get('id')) or REPORT_TITLES.get(job.get('task')) or str(job.get('id') or '')


def _clock(due):
    return _parse(due).astimezone(TEHRAN).strftime('%H:%M')


def _target(due):
    from .tasks import overflow_report_date
    return overflow_report_date(_parse(due))


def _label(store, user_id):
    method = getattr(store, 'identity_label', None)
    if callable(method):
        try:
            value = method(user_id)
        except Exception:
            logger.error('Delivery alert label lookup failed')
            value = None
        if isinstance(value, str):
            label = ' '.join(value.split())
            if label:
                return label
    return str(user_id)


def _problems(conn, job, run, expected, store):
    kind = _required_kind(run)
    if kind is None:
        return []
    rows = list(conn.execute(
        'SELECT recipient_id,delivery_kind,status FROM receipts WHERE schedule_id=? AND due=?',
        (job['id'], run['due'])))
    by_user = {row['recipient_id']: row for row in rows if row['delivery_kind'] == kind}
    found = []
    for user in expected:
        user = str(user)
        row = by_user.get(user)
        status = row['status'] if row else 'missing'
        if _historical(conn, job['id'], run['due'], user, kind, row is not None):
            continue
        klass = _classify(run, kind, status, bool(by_user))
        if klass is None:
            continue
        found.append({
            'recipient': user, 'kind': kind, 'klass': klass,
            'retrying': klass != 'uncertain' and run['status'] in RETRYING_RUN,
            'label': _label(store, user)})
    found.sort(key=lambda item: (item['label'], item['recipient']))
    return found


def _sync_watches(conn, schedule_id, due, waiting, now):
    keys = {(item['recipient'], item['kind']) for item in waiting}
    existing = list(conn.execute(
        '''SELECT recipient_id,delivery_kind,first_seen FROM delivery_failure_watch
           WHERE schedule_id=? AND due=?''', (schedule_id, due)))
    with conn:
        for row in existing:
            if (row['recipient_id'], row['delivery_kind']) not in keys:
                conn.execute('''DELETE FROM delivery_failure_watch
                    WHERE schedule_id=? AND due=? AND recipient_id=? AND delivery_kind=?''',
                    (schedule_id, due, row['recipient_id'], row['delivery_kind']))
        for item in waiting:
            conn.execute('''INSERT INTO delivery_failure_watch(
                schedule_id,due,recipient_id,delivery_kind,first_seen)
                VALUES (?,?,?,?,?)
                ON CONFLICT(schedule_id,due,recipient_id,delivery_kind) DO NOTHING''',
                (schedule_id, due, item['recipient'], item['kind'], _iso(now)))
    saved = {}
    for row in conn.execute(
            '''SELECT recipient_id,delivery_kind,first_seen FROM delivery_failure_watch
               WHERE schedule_id=? AND due=?''', (schedule_id, due)):
        saved[(row['recipient_id'], row['delivery_kind'])] = row['first_seen']
    return saved


def _split(problems, run, watches, now):
    actionable, watching = [], []
    for item in problems:
        if item['klass'] == 'uncertain' or run['status'] == 'no_data_final':
            actionable.append(item)
            continue
        seen = watches.get((item['recipient'], item['kind']))
        if seen and _utc(now) >= _parse(seen) + GRACE:
            actionable.append(item)
        else:
            watching.append(item)
    return actionable, watching


def format_exception(title, target, due_clock, problems):
    kinds = {item['kind'] for item in problems}
    klasses = {item['klass'] for item in problems}
    if klasses == {'uncertain'} and kinds == {'notice'}:
        header = '⚠️ وضعیت ارسال اعلان گزارش نامشخص است'
    elif klasses == {'uncertain'}:
        header = '⚠️ وضعیت ارسال گزارش نامشخص است'
    elif kinds == {'notice'}:
        header = '⚠️ ارسال اعلان گزارش کامل نشد'
    else:
        header = '⚠️ ارسال گزارش کامل نشد'
    lines = [header, '', f'گزارش: {title}', f'تاریخ گزارش: {target}', f'موعد: {due_clock}']
    for item in problems:
        if item['klass'] == 'uncertain':
            state = UNCERTAIN_SENTENCE
            reason = 'نتیجه ارسال از بله تأیید نشد'
        elif item['retrying']:
            state = 'تلاش مجدد ادامه دارد'
            reason = 'اعلان نبود اطلاعات به گیرنده نرسید' if item['kind'] == 'notice' else 'خطای ارسال در بله'
        else:
            state = 'بررسی دستی لازم است'
            reason = 'اعلان نبود اطلاعات به گیرنده نرسید' if item['kind'] == 'notice' else 'خطای ارسال در بله'
        lines.extend((f"گیرنده ناموفق: {item['label']}", f'وضعیت: {state}', f'علت: {reason}'))
    if any(item['klass'] == 'uncertain' for item in problems):
        lines.append(UNCERTAIN_SENTENCE)
    if any(not item['retrying'] for item in problems):
        lines.append('بررسی دستی لازم است')
    return '\n'.join(lines)


def format_recovery(title, target):
    return '\n'.join((
        '✅ مشکل ارسال گزارش برطرف شد',
        f'گزارش: {title}',
        f'تاریخ: {target}',
        'تمام گیرندگان مورد انتظار اکنون دریافت کرده‌اند.',
    ))


def _fingerprint(problems):
    return '\n'.join(sorted(f"{item['recipient']}|{item['kind']}|{item['klass']}" for item in problems))


def _operators(store):
    try:
        resolution = store.resolve_active_recipients(DELIVERY_FAILURE_RECEIVE)
    except Exception:
        logger.error('Delivery alert recipient resolution failed')
        return ()
    if type(resolution) is not RecipientSetResolution or resolution.status != 'ready':
        return ()
    found = []
    for user in resolution.recipients:
        if isinstance(user, str) and re.fullmatch(r'[1-9][0-9]*', user) and user not in found:
            found.append(user)
    return tuple(found)


def _message_id(value):
    if value is None:
        return None
    text = str(value)
    if not re.fullmatch(r'[1-9][0-9]{0,30}', text):
        return None
    return text


def _receipts(conn, case, kind):
    return {row['operator_id']: row for row in conn.execute(
        '''SELECT operator_id,status,updated FROM delivery_alert_receipt
           WHERE schedule_id=? AND due=? AND episode=? AND alert_kind=?''',
        (case['schedule_id'], case['due'], case['episode'], kind))}


def _all_sent(conn, case, kind, operators):
    rows = _receipts(conn, case, kind)
    return bool(operators) and all(rows.get(user) and rows[user]['status'] == 'sent' for user in operators)


def _exception_reached(conn, case):
    return conn.execute(
        '''SELECT 1 FROM delivery_alert_receipt
           WHERE schedule_id=? AND due=? AND episode=? AND alert_kind='exception' AND status='sent'
           LIMIT 1''',
        (case['schedule_id'], case['due'], case['episode'])).fetchone() is not None


def _claim(conn, case, kind, operators, now):
    claimed = []
    stamp = _iso(now)
    with conn:
        for user in operators:
            row = conn.execute(
                '''SELECT status,updated FROM delivery_alert_receipt
                   WHERE schedule_id=? AND due=? AND episode=? AND alert_kind=? AND operator_id=?''',
                (case['schedule_id'], case['due'], case['episode'], kind, user)).fetchone()
            if row and row['status'] == 'sent':
                continue
            if row and row['status'] == 'sending':
                age = (_utc(now) - _parse(row['updated'])).total_seconds()
                if age < STALE_CLAIM_SECONDS:
                    continue
            conn.execute('''INSERT INTO delivery_alert_receipt(
                schedule_id,due,episode,alert_kind,operator_id,status,message_id,error,updated)
                VALUES (?,?,?,?,?,'sending',NULL,NULL,?)
                ON CONFLICT(schedule_id,due,episode,alert_kind,operator_id) DO UPDATE SET
                    status='sending', message_id=NULL, error=NULL, updated=excluded.updated''',
                (case['schedule_id'], case['due'], case['episode'], kind, user, stamp))
            claimed.append(user)
    return claimed


def _mark(conn, case, kind, user, status, message_id, error, now):
    with conn:
        conn.execute('''UPDATE delivery_alert_receipt
            SET status=?, message_id=?, error=?, updated=?
            WHERE schedule_id=? AND due=? AND episode=? AND alert_kind=? AND operator_id=?''',
            (status, message_id, error, _iso(now), case['schedule_id'], case['due'],
             case['episode'], kind, user))


def _set_status(conn, case, kind, value, now):
    column = 'exception_status' if kind == 'exception' else 'recovery_status'
    with conn:
        conn.execute(
            f'''UPDATE delivery_alert_case SET {column}=?, updated=?
                WHERE schedule_id=? AND due=? AND episode=?''',
            (value, _iso(now), case['schedule_id'], case['due'], case['episode']))


def _default_sender():
    from .tasks import BaleSender
    sender = BaleSender()
    try:
        sender.check_connection()
    except Exception:
        sender.close()
        raise
    return sender


def _dispatch(conn, case, kind, text, operators, now, sender_factory):
    if not operators:
        if (kind == 'exception' and case['exception_status'] != 'failed') or (
                kind == 'recovery' and case['recovery_status'] not in {'failed', 'sent', 'closed'}):
            logger.info('delivery alert withheld schedule=%s due=%s kind=%s reason=no_operator',
                        case['schedule_id'], case['due'], kind)
        _set_status(conn, case, kind, 'failed', now)
        return False
    if _all_sent(conn, case, kind, operators):
        _set_status(conn, case, kind, 'sent', now)
        return True
    claimed = _claim(conn, case, kind, operators, now)
    sender = None
    try:
        if claimed:
            sender = sender_factory()
            for user in claimed:
                try:
                    message_id = sender.message(user, text)
                except Exception as exc:
                    _mark(conn, case, kind, user, 'failed', None, type(exc).__name__, now)
                    logger.error('delivery alert send failed schedule=%s due=%s kind=%s error=%s',
                                 case['schedule_id'], case['due'], kind, type(exc).__name__)
                else:
                    _mark(conn, case, kind, user, 'sent', _message_id(message_id), None, now)
    except Exception as exc:
        for user in claimed:
            _mark(conn, case, kind, user, 'failed', None, type(exc).__name__, now)
        logger.error('delivery alert sender failed schedule=%s due=%s kind=%s error=%s',
                     case['schedule_id'], case['due'], kind, type(exc).__name__)
    finally:
        if sender is not None:
            try:
                sender.close()
            except Exception:
                logger.error('delivery alert sender close failed')
    delivered = _all_sent(conn, case, kind, operators)
    _set_status(conn, case, kind, 'sent' if delivered else 'failed', now)
    if delivered:
        logger.info('delivery alert sent schedule=%s due=%s kind=%s operators=%s',
                    case['schedule_id'], case['due'], kind, len(operators))
    return delivered


def _reload(conn, schedule_id, due, episode):
    return conn.execute(
        'SELECT * FROM delivery_alert_case WHERE schedule_id=? AND due=? AND episode=?',
        (schedule_id, due, episode)).fetchone()


def _open_case(conn, schedule_id, due):
    return conn.execute(
        '''SELECT * FROM delivery_alert_case
           WHERE schedule_id=? AND due=? AND recovery_status NOT IN ('sent','closed')
           ORDER BY episode DESC LIMIT 1''', (schedule_id, due)).fetchone()


def _insert_case(conn, schedule_id, due, fingerprint, text, now):
    episode = conn.execute(
        'SELECT COALESCE(MAX(episode),0)+1 FROM delivery_alert_case WHERE schedule_id=? AND due=?',
        (schedule_id, due)).fetchone()[0]
    stamp = _iso(now)
    with conn:
        conn.execute('''INSERT INTO delivery_alert_case(
            schedule_id,due,episode,fingerprint,exception_status,recovery_status,
            exception_text,recovery_text,created,updated)
            VALUES (?,?,?,?,'pending','none',?,NULL,?,?)''',
            (schedule_id, due, episode, fingerprint, text, stamp, stamp))
    return _reload(conn, schedule_id, due, episode)


def _raise_exception(conn, job, run, problems, operators, now, sender_factory):
    fingerprint = _fingerprint(problems)
    text = format_exception(_title(job), _target(run['due']), _clock(run['due']), problems)
    case = _open_case(conn, job['id'], run['due'])
    if case is not None and case['fingerprint'] == fingerprint:
        if not _all_sent(conn, case, 'exception', operators):
            _dispatch(conn, case, 'exception', case['exception_text'], operators, now, sender_factory)
        return
    if case is not None and not _exception_reached(conn, case):
        with conn:
            conn.execute('''UPDATE delivery_alert_case
                SET fingerprint=?, exception_text=?, updated=?
                WHERE schedule_id=? AND due=? AND episode=?''',
                (fingerprint, text, _iso(now), case['schedule_id'], case['due'], case['episode']))
            conn.execute('''DELETE FROM delivery_alert_receipt
                WHERE schedule_id=? AND due=? AND episode=? AND alert_kind='exception' AND status!='sent' ''',
                (case['schedule_id'], case['due'], case['episode']))
        case = _reload(conn, case['schedule_id'], case['due'], case['episode'])
        _dispatch(conn, case, 'exception', text, operators, now, sender_factory)
        return
    case = _insert_case(conn, job['id'], run['due'], fingerprint, text, now)
    _dispatch(conn, case, 'exception', text, operators, now, sender_factory)


def _close_undelivered(conn, schedule_id, due, now):
    with conn:
        conn.execute('''UPDATE delivery_alert_case SET recovery_status='closed', updated=?
            WHERE schedule_id=? AND due=? AND recovery_status NOT IN ('sent','closed')
              AND NOT EXISTS (
                SELECT 1 FROM delivery_alert_receipt
                WHERE delivery_alert_receipt.schedule_id=delivery_alert_case.schedule_id
                  AND delivery_alert_receipt.due=delivery_alert_case.due
                  AND delivery_alert_receipt.episode=delivery_alert_case.episode
                  AND delivery_alert_receipt.alert_kind='exception'
                  AND delivery_alert_receipt.status='sent')''',
            (_iso(now), schedule_id, due))


def _recover(conn, job, run, operators, now, sender_factory):
    schedule_id, due = job['id'], run['due']
    sent = conn.execute(
        '''SELECT case_row.episode FROM delivery_alert_case AS case_row
           WHERE case_row.schedule_id=? AND case_row.due=?
             AND case_row.recovery_status NOT IN ('sent','closed')
             AND EXISTS (
               SELECT 1 FROM delivery_alert_receipt AS receipt
               WHERE receipt.schedule_id=case_row.schedule_id AND receipt.due=case_row.due
                 AND receipt.episode=case_row.episode AND receipt.alert_kind='exception'
                 AND receipt.status='sent')
           ORDER BY case_row.episode DESC LIMIT 1''', (schedule_id, due)).fetchone()
    if not sent:
        _close_undelivered(conn, schedule_id, due, now)
        return
    case = _reload(conn, schedule_id, due, sent['episode'])
    text = case['recovery_text'] or format_recovery(_title(job), _target(due))
    if not case['recovery_text']:
        with conn:
            conn.execute('''UPDATE delivery_alert_case SET recovery_text=?, updated=?
                WHERE schedule_id=? AND due=? AND episode=?''',
                (text, _iso(now), schedule_id, due, case['episode']))
        case = _reload(conn, schedule_id, due, case['episode'])
    if _dispatch(conn, case, 'recovery', text, operators, now, sender_factory):
        with conn:
            conn.execute('''UPDATE delivery_alert_case SET recovery_status='sent', updated=?
                WHERE schedule_id=? AND due=? AND recovery_status!='closed' ''',
                (_iso(now), schedule_id, due))


def _expected(job, store):
    from .no_data import current_recipients
    try:
        users = current_recipients(job['task'], store)
    except Exception:
        logger.error('Delivery alert expected recipients unavailable schedule=%s', job.get('id'))
        return None
    if not isinstance(users, tuple):
        return None
    return tuple(str(user) for user in users)


def _process_run(conn, job, run, store, operators, now, sender_factory):
    expected = _expected(job, store)
    if expected is None:
        return
    problems = _problems(conn, job, run, expected, store)
    waiting = [item for item in problems
               if item['klass'] != 'uncertain' and run['status'] != 'no_data_final']
    watches = _sync_watches(conn, job['id'], run['due'], waiting, now)
    actionable, still_waiting = _split(problems, run, watches, now)
    if actionable:
        _raise_exception(conn, job, run, actionable, operators, now, sender_factory)
        return
    if still_waiting:
        return
    _recover(conn, job, run, operators, now, sender_factory)


def scan_delivery_exceptions(conn, jobs, now, authorization=None, *, sender_factory=None):
    """Alert current operators about new unresolved deliveries. Never raises for transport."""
    conn.row_factory = sqlite3.Row
    ensure_schema(conn)
    ensure_activation(conn, now)
    _release_resolved(conn)
    selected = [job for job in jobs
                if job.get('enabled', True) and job.get('task') in _report_tasks() and job.get('id')]
    if not selected:
        return
    store = AuthorizationStore() if authorization is None else authorization
    operators = _operators(store)
    factory = sender_factory or _default_sender
    for job in selected:
        try:
            runs = list(conn.execute('SELECT * FROM runs WHERE schedule_id=?', (job['id'],)))
            for run in runs:
                _process_run(conn, job, run, store, operators, now, factory)
        except Exception as exc:
            logger.error('Delivery exception scan failed schedule=%s error=%s', job.get('id'), type(exc).__name__)
