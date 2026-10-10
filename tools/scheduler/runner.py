"""Run once per minute via Windows Task Scheduler; YAML is read each time."""
from __future__ import annotations

import argparse
from datetime import datetime, timedelta, timezone
import json
import logging
from logging.handlers import RotatingFileHandler
from pathlib import Path
import re
import sqlite3
from zoneinfo import ZoneInfo

from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.date import DateTrigger
from tzlocal import get_localzone
import yaml

from tools.authorization import AuthorizationStore, OFFICE_SUPERVISOR, DRIVER_RECEIVE
from tools.authorization.office_delivery import OFFICE_TASKS
from . import office_receipts
from .misfire import (
    RETRYABLE_FAILURE, RETRYABLE_SKIP, SKIP_MISSED_SLACK_SECONDS, TERMINAL_FAILURE,
    backoff_delay, due_storage_key, latest_due, outcome_from_receipts, replay_plan)
from .tasks import (
    ROOT, TASKS, MULTI_RECIPIENT_TASKS, TransportRejected, TransportUncertain,
    TransportUnavailable, overflow_report_date)

CONFIG = ROOT / 'settings/schedules.yaml'
STATE = ROOT / 'runtime/scheduler/runs.sqlite3'
logger = logging.getLogger(__name__)
REPORT_TASKS = {'overflow', 'driver_daily', *MULTI_RECIPIENT_TASKS}


def only_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'Invalid fields in {label}; allowed: {", ".join(allowed)}')


def normalize_misfire(raw, schedule_id):
    if raw is None:
        return {'policy': 'latest_only', 'horizon_days': None}
    if not isinstance(raw, dict):
        raise ValueError(f'{schedule_id}: misfire must be a mapping')
    only_keys(raw, ['policy', 'horizon_days'], f'{schedule_id}.misfire')
    policy = raw.get('policy', 'latest_only')
    if policy not in {'replay', 'latest_only', 'skip_missed'}:
        raise ValueError(f'{schedule_id}: misfire policy must be replay, latest_only, or skip_missed')
    if policy != 'replay':
        if 'horizon_days' in raw:
            raise ValueError(f'{schedule_id}: horizon_days applies only to replay')
        return {'policy': policy, 'horizon_days': None}
    horizon = raw.get('horizon_days', 7)
    if type(horizon) is not int or not 1 <= horizon <= 30:
        raise ValueError(f'{schedule_id}: horizon_days must be an integer from 1 to 30')
    return {'policy': policy, 'horizon_days': horizon}


def validate_external_misfire(entries):
    if entries is None:
        return
    if not isinstance(entries, list):
        raise ValueError('external_misfire must be a list')
    seen = set()
    for raw in entries:
        only_keys(raw, ['id', 'name', 'policy'], 'external_misfire')
        name = raw.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name) or name in seen:
            raise ValueError('external_misfire id must be unique')
        seen.add(name)
        if not isinstance(raw.get('name'), str) or not raw['name']:
            raise ValueError('external_misfire name is required')
        if raw.get('policy') not in {'latest_only', 'skip_missed'}:
            raise ValueError('external high-frequency jobs cannot use replay')


def load_config(path=CONFIG, registry=TASKS):
    config = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    only_keys(config, ['version', 'timezone', 'misfire_grace_seconds', 'schedules', 'external_misfire'], 'config')
    if type(config.get('version')) is not int or config['version'] != 1:
        raise ValueError('version must be 1')
    tz = config.get('timezone', 'local')
    tz = get_localzone() if tz == 'local' else ZoneInfo(tz)
    grace = config.get('misfire_grace_seconds', 900)
    if type(grace) is not int or not 1 <= grace <= 86400:
        raise ValueError('misfire_grace_seconds must be between 1 and 86400')
    if not isinstance(config.get('schedules'), list):
        raise ValueError('schedules must be a list')
    validate_external_misfire(config.get('external_misfire'))
    jobs, ids = [], set()
    for raw in config['schedules']:
        only_keys(raw, ['id', 'enabled', 'task', 'recipient', 'recipient_role', 'recipient_capability',
                        'timezone', 'trigger', 'params', 'misfire'], 'schedule')
        job = dict(raw)
        name = job.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name) or name in ids:
            raise ValueError('Every schedule needs a unique, stable ASCII id')
        ids.add(name)
        if type(job.get('enabled', True)) is not bool:
            raise ValueError(f'{name}: enabled must be true/false')
        if job.get('task') not in registry:
            raise ValueError(f'{name}: unknown task')
        job['misfire'] = normalize_misfire(job.get('misfire'), name)
        if job['task'] in MULTI_RECIPIENT_TASKS:
            expected=MULTI_RECIPIENT_TASKS[job['task']]
            if ('recipient' in job or 'recipient_role' in job
                    or job.get('recipient_capability')!=expected):
                raise ValueError(f'{name}: mechanical delivery requires its fixed recipient capability')
            if job.get('timezone')!='Asia/Tehran':
                raise ValueError(f'{name}: explicit Asia/Tehran timezone required')
            job['recipient']='capability:'+expected
        elif job['task'] in {'overflow', 'driver_daily'}:
            if 'recipient_capability' in job:
                if ('recipient' in job or 'recipient_role' in job or job['recipient_capability'] != OFFICE_TASKS[job['task']]):
                    raise ValueError(f'{name}: office delivery requires its fixed receive capability')
            elif 'recipient' in job or job.get('recipient_role') != OFFICE_SUPERVISOR:
                raise ValueError(f'{name}: legacy office delivery requires office_supervisor')
            if job.get('timezone') != 'Asia/Tehran':
                raise ValueError(f'{name}: overflow requires explicit Asia/Tehran timezone')
            job['recipient'] = ('capability:' + job['recipient_capability']) if 'recipient_capability' in job else 'role:' + OFFICE_SUPERVISOR
        else:
            if 'recipient_role' in job or 'recipient_capability' in job:
                raise ValueError(f'{name}: role delivery is only defined for supported reports')
            recipient = str(job.get('recipient', ''))
            if not re.fullmatch(r'[1-9][0-9]*', recipient):
                raise ValueError(f'{name}: recipient must be a positive Bale user ID')
            job['recipient'] = recipient
        job_tz = ZoneInfo(job['timezone']) if 'timezone' in job else tz
        params = job.get('params', {})
        if not isinstance(params, dict):
            raise ValueError(f'{name}: params must be a mapping')
        if job['task'] in {'overflow', 'driver_daily', *MULTI_RECIPIENT_TASKS} and params:
            raise ValueError(f'{name}: overflow schedule computes the previous day; params must be empty')
        registry[job['task']][0](params)
        job['params'] = params
        spec = job.get('trigger')
        if not isinstance(spec, dict):
            raise ValueError(f'{name}: trigger is required')
        if spec.get('type') == 'cron':
            only_keys(spec, ['type', 'hour', 'minute', 'day_of_week', 'day', 'month', 'year', 'start_date', 'end_date'], name)
            if 'hour' not in spec or 'minute' not in spec:
                raise ValueError(f'{name}: cron requires hour and minute')
            job['trigger'] = CronTrigger(timezone=job_tz, second=0, **{k: v for k, v in spec.items() if k != 'type'})
        elif spec.get('type') == 'date':
            only_keys(spec, ['type', 'run_at'], name)
            if not isinstance(spec.get('run_at'), str):
                raise ValueError(f'{name}: run_at must be a quoted ISO Gregorian datetime')
            job['trigger'] = DateTrigger(run_date=spec['run_at'], timezone=job_tz)
        else:
            raise ValueError(f'{name}: trigger type must be cron or date')
        if job['task'] == 'maintenance_daily_report':
            if (job['id'] != 'maintenance_daily_report' or set(spec) != {'type', 'hour', 'minute'}
                    or spec['type'] != 'cron' or type(spec['hour']) is not int
                    or type(spec['minute']) is not int
                    or any(j['task']=='maintenance_daily_report' for j in jobs)):
                raise ValueError('Maintenance requires one fixed daily Tehran job with its canonical id')
        jobs.append(job)
    return tz, grace, jobs


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("BEGIN IMMEDIATE")
    conn.execute('''CREATE TABLE IF NOT EXISTS runs (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient TEXT NOT NULL,
        task TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL,
        finished TEXT, error TEXT, attempt INTEGER NOT NULL DEFAULT 0,
        next_attempt_at TEXT, PRIMARY KEY(schedule_id, due))''')
    columns = {row['name'] for row in conn.execute('PRAGMA table_info(runs)')}
    if 'attempt' not in columns:
        conn.execute('ALTER TABLE runs ADD COLUMN attempt INTEGER NOT NULL DEFAULT 0')
    if 'next_attempt_at' not in columns:
        conn.execute('ALTER TABLE runs ADD COLUMN next_attempt_at TEXT')
    for name in ('data_state', 'notice_delivery_state'):
        if name not in columns:
            conn.execute(f'ALTER TABLE runs ADD COLUMN {name} TEXT')
    # Transactional migration: legacy receipts retain their report identity. Known
    # legacy notices require evidence-backed recovery, never heuristic reclassification.
    with conn:
        existing = {r['name'] for r in conn.execute('PRAGMA table_info(receipts)')}
        if existing and 'delivery_kind' not in existing:
            conn.execute('ALTER TABLE receipts RENAME TO receipts_legacy')
        conn.execute("""CREATE TABLE IF NOT EXISTS receipts (
            schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient_id TEXT NOT NULL,
            delivery_kind TEXT NOT NULL CHECK(delivery_kind IN ('notice','report')),
            status TEXT NOT NULL, message_id TEXT, updated TEXT NOT NULL,
            PRIMARY KEY(schedule_id, due, recipient_id, delivery_kind))""")
        if existing and 'delivery_kind' not in existing:
            conn.execute("""INSERT INTO receipts
                SELECT schedule_id,due,recipient_id,'report',status,message_id,updated FROM receipts_legacy""")
            conn.execute('DROP TABLE receipts_legacy')
    office_receipts.create_schema(conn)
    conn.commit()
    return conn


def _rows_for(conn, schedule_id):
    found = {}
    for row in conn.execute(
            'SELECT due,status,error,next_attempt_at,started,attempt,data_state FROM runs WHERE schedule_id=?',
            (schedule_id,)):
        found[row['due']] = dict(row)
    return found


def _closed(conn, key, kind="report"):
    sent, uncertain = set(), set()
    for row in conn.execute(
            'SELECT recipient_id,status FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind=?', (*key, kind)):
        if row['status'] == 'sent':
            sent.add(row['recipient_id'])
        elif row['status'] == 'uncertain':
            uncertain.add(row['recipient_id'])
    result = {'sent': sent, 'uncertain': uncertain}
    if kind == 'report':
        result['notice'] = _closed(conn, key, 'notice')
        result['artifacts'] = office_receipts.states(conn, key)
    return result


def _save_receipt(conn, key, recipient_id, status, message_id, delivery_kind="report"):
    updated = datetime.now(timezone.utc).isoformat(timespec='microseconds')
    with conn:
        if delivery_kind == 'report' and status in {'sending', 'sent'}:
            conn.execute("UPDATE runs SET data_state='ready' WHERE schedule_id=? AND due=?", key)
        conn.execute('''INSERT INTO receipts(schedule_id,due,recipient_id,delivery_kind,status,message_id,updated)
            VALUES (?,?,?,?,?,?,?)
            ON CONFLICT(schedule_id,due,recipient_id,delivery_kind) DO UPDATE SET
              status=CASE
                WHEN receipts.status='sent' THEN 'sent'
                WHEN receipts.status='uncertain' THEN 'uncertain'
                ELSE excluded.status END,
              message_id=COALESCE(excluded.message_id, receipts.message_id),
              updated=excluded.updated''',
            (*key, str(recipient_id), delivery_kind, status, message_id, updated))


def _receipt_states(conn, key):
    return [row['status'] for row in conn.execute(
        "SELECT status FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind='report'", key)]


def _freeze_sending(conn, key):
    office_receipts.recover(conn, key)
    with conn:
        conn.execute('''UPDATE receipts SET status='uncertain', updated=?
            WHERE schedule_id=? AND due=? AND status='sending' ''',
            (datetime.now(timezone.utc).isoformat(timespec='microseconds'), *key))


def _claim_once(conn, job, due):
    key = (job['id'], due_storage_key(due))
    with conn:
        inserted = conn.execute(
            'INSERT OR IGNORE INTO runs(schedule_id,due,recipient,task,status,started) VALUES (?,?,?,?,?,?)',
            (*key, job['recipient'], job['task'], 'running', datetime.now(timezone.utc).isoformat())).rowcount
    return inserted == 1


def _claim_replay(conn, job, due, row, now):
    key = (job['id'], due_storage_key(due))
    started = now.astimezone(timezone.utc).isoformat(timespec='seconds')
    if row is None:
        with conn:
            return conn.execute(
                'INSERT OR IGNORE INTO runs(schedule_id,due,recipient,task,status,started) VALUES (?,?,?,?,?,?)',
                (*key, job['recipient'], job['task'], 'running', started)).rowcount == 1
    nxt = row.get('next_attempt_at')
    if nxt and datetime.fromisoformat(nxt) > now.astimezone(timezone.utc):
        return False
    if row['status'] == 'running':
        _freeze_sending(conn, key)
        states = _receipt_states(conn, key)
        if states and not set(states) & {'failed', 'pending'}:
            derived = outcome_from_receipts(states)
            with conn:
                conn.execute('UPDATE runs SET status=?,finished=?,error=? WHERE schedule_id=? AND due=?',
                             (derived or 'uncertain', started, 'uncertain' if derived == 'uncertain' else None, *key))
            return False
        with conn:
            return conn.execute(
                '''UPDATE runs SET status='running', started=?
                   WHERE schedule_id=? AND due=? AND status='running' AND started=?''',
                (started, *key, row['started'])).rowcount == 1
    with conn:
        return conn.execute(
            '''UPDATE runs SET status='running', started=?
               WHERE schedule_id=? AND due=? AND status=?''',
            (started, *key, row['status'])).rowcount == 1


def _adjust_replay(status, error, result):
    result = result if isinstance(result, dict) else {}
    uncertain = result.get('uncertain_recipients') or []
    retry_ids = result.get('retry_recipients') or []
    sent_count = result.get('sent_count') or 0
    process_failed = status == 'failed'
    if uncertain and not retry_ids:
        status, error, process_failed = 'uncertain', 'uncertain', True
    elif status == 'failed' and (retry_ids or error in RETRYABLE_FAILURE):
        status = 'partial' if sent_count else 'retry_wait'
        process_failed = False
    elif status == 'skipped' and error in RETRYABLE_SKIP:
        status, process_failed = ('waiting_for_data' if error in {'date_missing', 'no_target_date_records'} else 'retry_wait'), False
    elif status == 'failed' and error in TERMINAL_FAILURE:
        process_failed = True
    return status, error, process_failed


def _artifact(status, error):
    if error in {'date_missing', 'no_target_date_records'}:
        return 'no_data'
    if error in TERMINAL_FAILURE:
        return 'fail'
    if status == 'succeeded':
        return 'pass'
    if status == 'uncertain':
        return 'uncertain'
    if status in {'retry_wait', 'partial'}:
        return 'pending'
    return 'not_applicable'


def _execute(conn, job, due, now, registry, authorization, replay, previous):
    key = (job['id'], due_storage_key(due))
    logger.info('Starting %s due=%s recipient=%s', *key, job['recipient'])
    status, error, process_failed, result = 'succeeded', None, False, None
    try:
        recipient = job['recipient']
        params = dict(job['params'])
        if job['task'] in OFFICE_TASKS and 'recipient_capability' in job:
            params['date'] = overflow_report_date(due)
        elif job['task'] in {'overflow', 'driver_daily'}:
            store = authorization if authorization is not None else AuthorizationStore()
            resolution = (store.resolve_daily_recipient(capability=DRIVER_RECEIVE)
                          if job['task'] == 'driver_daily' else store.resolve_daily_recipient())
            if resolution.status != 'ready':
                status, error = 'skipped', resolution.status
                logger.warning(('Overflow' if job['task'] == 'overflow' else 'Driver daily') +
                               ' no send: %s active_holders=%s', resolution.status, resolution.holder_count)
            else:
                recipient = resolution.recipient
                params['date'] = overflow_report_date(due)
                logger.info(('Overflow' if job['task'] == 'overflow' else 'Driver daily') +
                            ' requested previous-day report: %s', params['date'])
        if job['task'] in MULTI_RECIPIENT_TASKS:
            params['date'] = overflow_report_date(due)
            if job['task'] == 'maintenance_daily_report':
                params['occurrence'] = due.isoformat()
        if replay and job['task'] in REPORT_TASKS and registry[job['task']][1] is TASKS[job['task']][1]:
            from .no_data import current_recipients
            store = authorization if authorization is not None else AuthorizationStore()
            if (job['task'] in OFFICE_TASKS and 'recipient_capability' in job
                    and store.resolve_active_recipients(OFFICE_TASKS[job['task']]).status == 'store_unavailable'):
                raise TransportUnavailable('Office authorization temporarily unavailable')
            current = set(current_recipients(job['task'], store))
            for user in current:
                if conn.execute("SELECT 1 FROM receipts WHERE schedule_id=? AND due=? AND recipient_id=? AND delivery_kind='report'", (*key, user)).fetchone() is None:
                    _save_receipt(conn, key, user, 'pending', None)
            for old in conn.execute("SELECT recipient_id FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind='report' AND status IN ('failed','pending')", key).fetchall():
                if old['recipient_id'] not in current:
                    _save_receipt(conn, key, old['recipient_id'], 'revoked', None)
        if status != 'skipped':
            if replay:
                def on_receipt(recipient_id, receipt_status, message_id=None, delivery_kind="report"):
                    _save_receipt(conn, key, recipient_id, receipt_status, message_id, delivery_kind)
                kwargs = {'on_receipt': on_receipt, 'closed': _closed(conn, key)}
                if job['task'] in OFFICE_TASKS and 'recipient_capability' in job:
                    kwargs['on_artifact_receipt'] = lambda user, artifact, state, mid=None, digest=None: office_receipts.save(conn, key, user, artifact, state, mid, digest)
                if job['task'] in REPORT_TASKS:
                    kwargs['authorization'] = authorization if authorization is not None else AuthorizationStore()
                result = registry[job['task']][1](recipient, params, **kwargs)
            elif job['task'] in MULTI_RECIPIENT_TASKS:
                result = registry[job['task']][1](
                    recipient, params, authorization=authorization if authorization is not None else AuthorizationStore())
            else:
                result = registry[job['task']][1](recipient, params)
            if isinstance(result, dict) and result.get('status') == 'waiting_for_data':
                status, error = 'waiting_for_data', result['reason']
            if isinstance(result, dict) and result.get('status') == 'failed':
                status, error, process_failed = 'failed', result.get('reason', 'delivery_failed'), True
            if isinstance(result, dict) and result.get('status') == 'skipped':
                status, error = 'skipped', result['reason']
            if isinstance(result, dict) and result.get('status') == 'uncertain':
                status, error, process_failed = 'uncertain', result.get('reason', 'uncertain'), True
    except TransportUncertain:
        status, error, process_failed = 'uncertain', 'TransportUncertain', True
    except (TransportUnavailable, TransportRejected, OSError) as exc:
        status, error, process_failed = 'failed', type(exc).__name__, True
    except Exception as exc:
        # Persist the exception class only: third-party errors can contain secrets.
        status, error, process_failed = 'failed', type(exc).__name__, True
    if replay:
        status, error, process_failed = _adjust_replay(status, error, result)
        states = _receipt_states(conn, key)
        if 'sending' in states:
            _freeze_sending(conn, key)
            states = _receipt_states(conn, key)
        derived = outcome_from_receipts(states)
        if derived and status != 'waiting_for_data' and error not in TERMINAL_FAILURE:
            status = derived
            if derived == 'succeeded':
                error = None
            elif derived == 'uncertain':
                error = 'uncertain'
            elif derived == 'skipped' and not error:
                error = 'recipient_revoked'
            process_failed = derived in {'uncertain', 'failed'}
        attempt = int((previous or {}).get('attempt') or 0) + 1
        next_at = None
        if status in {'retry_wait', 'partial', 'waiting_for_data'}:
            next_at = (now.astimezone(timezone.utc) + timedelta(seconds=backoff_delay(attempt))).isoformat(
                timespec='seconds')
        finished = datetime.now(timezone.utc).isoformat()
        with conn:
            conn.execute('''UPDATE runs SET status=?,finished=?,error=?,attempt=?,next_attempt_at=?,
                            data_state=COALESCE(?,data_state),notice_delivery_state=COALESCE(?,notice_delivery_state) WHERE schedule_id=? AND due=?''',
                         (status, finished, error, attempt, next_at,
                          'missing' if status == 'waiting_for_data' else ('ready' if status == 'succeeded' or error in {'TransportUnavailable','TransportRejected','transport_unavailable','delivery_failed'} else None),
                          (result or {}).get('notice_delivery_state') if isinstance(result, dict) else None, *key))
        target = overflow_report_date(due) if job['task'] in REPORT_TASKS else ''
        previous_status = (previous or {}).get('status')
        previous_error = (previous or {}).get('error')
        if previous_error in RETRYABLE_SKIP or previous_status in {None, 'skipped'}:
            reason = 'misfire'
        elif previous_status in {'failed', 'retry_wait', 'partial', 'running'}:
            reason = 'transport_retry'
        else:
            reason = 'misfire'
        receipts = list(conn.execute(
            'SELECT recipient_id,delivery_kind,status,message_id FROM receipts WHERE schedule_id=? AND due=?', key))
        logger.info(
            'recovery schedule_id=%s original_due=%s recovered_at=%s reason=%s target_date=%s attempt=%s recipient_count=%s artifact=%s outcome=%s',
            job['id'], due.isoformat(), now.isoformat(timespec='seconds'), reason, target, attempt,
            len(receipts), _artifact(status, error), status)
        for receipt in receipts:
            logger.info(
                'recovery_recipient schedule_id=%s original_due=%s recipient=%s kind=%s state=%s message_id=%s',
                job['id'], due.isoformat(), receipt['recipient_id'], receipt['delivery_kind'], receipt['status'], receipt['message_id'] or '')
    else:
        with conn:
            conn.execute('UPDATE runs SET status=?,finished=?,error=? WHERE schedule_id=? AND due=?',
                         (status, datetime.now(timezone.utc).isoformat(), error, *key))
    logger.info('Completed %s status=%s error=%s', job['id'], status, error)
    return process_failed


def tick(config=CONFIG, state=STATE, now=None, registry=TASKS, authorization=None):
    tz, grace, jobs = load_config(config, registry)
    now = now or datetime.now(tz)
    failed = False
    conn = connect(state)
    try:
        _delivery_notifier(conn, jobs, now, authorization, activate=True)
        for job in jobs:
            if not job.get('enabled', True):
                continue
            policy = job['misfire']['policy']
            if policy != 'replay':
                slack = SKIP_MISSED_SLACK_SECONDS if policy == 'skip_missed' else grace
                due = latest_due(job['trigger'], now, slack)
                if due is None or not _claim_once(conn, job, due):
                    continue
                failed = _execute(conn, job, due, now, registry, authorization, False, None) or failed
                continue
            rows = _rows_for(conn, job['id'])
            with conn:
                conn.execute("""UPDATE runs SET status='no_data_final',next_attempt_at=NULL,finished=?
                    WHERE schedule_id=? AND due<? AND status IN ('waiting_for_data','retry_wait','skipped')
                    AND (data_state='missing' OR error IN ('date_missing','no_target_date_records'))""",
                    (now.astimezone(timezone.utc).isoformat(), job['id'],
                     due_storage_key(now-timedelta(days=job['misfire']['horizon_days']))))
            rows = _rows_for(conn, job['id'])
            dues, flooded, _gaps = replay_plan(
                job['trigger'], now, job['misfire']['horizon_days'], rows,
                daily_identity=job['task'] in REPORT_TASKS)
            if flooded:
                logger.error('catch-up refused schedule_id=%s reason=frequency_flood', job['id'])
                continue
            for due in dues:
                row = rows.get(due_storage_key(due))
                if not _claim_replay(conn, job, due, row, now):
                    continue
                failed = _execute(conn, job, due, now, registry, authorization, True, row) or failed
        _delivery_notifier(conn, jobs, now, authorization, scan=True)
        # Copies retry from their own outbox, never through the staff send/archive flow.
        try:
            from tools.fleet.work_orders.core.final_copy import drain_pending
            if Path(state).resolve() == STATE.resolve() and Path(config).resolve() == CONFIG.resolve():
                drain_pending()
        except Exception as exc:
            logger.error('Final copy drain failed: %s', type(exc).__name__)
    finally:
        conn.close()
    return 1 if failed else 0


def _delivery_notifier(conn, jobs, now, authorization, *, activate=False, scan=False):
    """Failure alerts stay outside report state. A notifier error cannot fail the reports."""
    try:
        from .delivery_alert import ensure_activation, scan_delivery_exceptions
        if activate:
            ensure_activation(conn, now)
        if scan:
            scan_delivery_exceptions(conn, jobs, now, authorization)
    except Exception as exc:
        logger.error('Delivery exception notifier failed: %s', type(exc).__name__)


def plan_replay(config=CONFIG, state=STATE, now=None, registry=TASKS):
    """Read-only list of replay dues a tick would claim. Does not send."""
    tz, _grace, jobs = load_config(config, registry)
    now = now or datetime.now(tz)
    conn = connect(state)
    planned = []
    try:
        for job in jobs:
            if not job.get('enabled', True) or job['misfire']['policy'] != 'replay':
                continue
            rows = _rows_for(conn, job['id'])
            dues, flooded, gaps = replay_plan(
                job['trigger'], now, job['misfire']['horizon_days'], rows,
                daily_identity=job['task'] in REPORT_TASKS)
            for due in dues:
                key = due_storage_key(due)
                row = rows.get(key)
                planned.append({
                    'schedule_id': job['id'], 'task': job['task'], 'original_due': due.isoformat(),
                    'ledger_due': key, 'previous_status': None if row is None else row.get('status'),
                    'previous_error': None if row is None else row.get('error'),
                    'target_date': overflow_report_date(due) if job['task'] in REPORT_TASKS else '',
                    'flooded': flooded, 'historical_gaps': gaps})
            if flooded:
                planned.append({'schedule_id': job['id'], 'flooded': True, 'historical_gaps': gaps, 'original_due': None})
    finally:
        conn.close()
    return planned


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', type=Path, default=CONFIG)
    parser.add_argument('--state', type=Path, default=STATE)
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument('--check', action='store_true', help='Validate and list next runs; never send')
    mode.add_argument('--tick', action='store_true', help='Execute due schedules')
    mode.add_argument('--status', action='store_true', help='Show the latest 30 execution records')
    args = parser.parse_args()
    if args.check:
        tz, grace, jobs = load_config(args.config)
        now = datetime.now(tz)
        print(f'Timezone: {tz}; now: {now.isoformat()}; catch-up: {grace}s')
        for job in jobs:
            upcoming = job['trigger'].get_next_fire_time(None, now) if job.get('enabled', True) else None
            print(f"{job['id']} task={job['task']} recipient={job['recipient']} next={upcoming} misfire={job['misfire']['policy']}")
        return 0
    if args.status:
        if not args.state.exists():
            print('No runs yet')
            return 0
        conn = sqlite3.connect(f'{args.state.resolve().as_uri()}?mode=ro', uri=True)
        try:
            conn.row_factory = sqlite3.Row
            print(json.dumps([dict(r) for r in conn.execute('SELECT * FROM runs ORDER BY started DESC LIMIT 30')], indent=2))
        finally:
            conn.close()
        return 0
    logs = ROOT / 'runtime/scheduler'
    logs.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(logs / 'scheduler.log', maxBytes=2_000_000, backupCount=3, encoding='utf-8')
    logging.basicConfig(level=logging.INFO, handlers=[handler], format='%(asctime)s %(levelname)s %(message)s')
    try:
        return tick(args.config, args.state)
    except Exception as exc:
        logger.error('Scheduler tick failed: %s', type(exc).__name__)
        return 1


if __name__ == '__main__':
    raise SystemExit(main())
