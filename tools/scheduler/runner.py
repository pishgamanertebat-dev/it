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

from .tasks import ROOT, TASKS

CONFIG = ROOT / 'settings/schedules.yaml'
STATE = ROOT / 'runtime/scheduler/runs.sqlite3'
logger = logging.getLogger(__name__)


def only_keys(value, allowed, label):
    if not isinstance(value, dict) or set(value) - set(allowed):
        raise ValueError(f'Invalid fields in {label}; allowed: {", ".join(allowed)}')


def load_config(path=CONFIG, registry=TASKS):
    config = yaml.safe_load(Path(path).read_text(encoding='utf-8'))
    only_keys(config, ['version', 'timezone', 'misfire_grace_seconds', 'schedules'], 'config')
    if type(config.get('version')) is not int or config['version'] != 1:
        raise ValueError('version must be 1')
    tz = config.get('timezone', 'local')
    tz = get_localzone() if tz == 'local' else ZoneInfo(tz)
    grace = config.get('misfire_grace_seconds', 900)
    if type(grace) is not int or not 1 <= grace <= 86400:
        raise ValueError('misfire_grace_seconds must be between 1 and 86400')
    if not isinstance(config.get('schedules'), list):
        raise ValueError('schedules must be a list')
    jobs, ids = [], set()
    for raw in config['schedules']:
        only_keys(raw, ['id', 'enabled', 'task', 'recipient', 'trigger', 'params'], 'schedule')
        job = dict(raw)
        name = job.get('id')
        if not isinstance(name, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,80}', name) or name in ids:
            raise ValueError('Every schedule needs a unique, stable ASCII id')
        ids.add(name)
        if type(job.get('enabled', True)) is not bool:
            raise ValueError(f'{name}: enabled must be true/false')
        if job.get('task') not in registry:
            raise ValueError(f'{name}: unknown task')
        recipient = str(job.get('recipient', ''))
        if not re.fullmatch(r'[1-9][0-9]*', recipient):
            raise ValueError(f'{name}: recipient must be a positive Bale user ID')
        job['recipient'] = recipient
        params = job.get('params', {})
        if not isinstance(params, dict):
            raise ValueError(f'{name}: params must be a mapping')
        registry[job['task']][0](params)
        job['params'] = params
        spec = job.get('trigger')
        if not isinstance(spec, dict):
            raise ValueError(f'{name}: trigger is required')
        if spec.get('type') == 'cron':
            only_keys(spec, ['type', 'hour', 'minute', 'day_of_week', 'day', 'month', 'year', 'start_date', 'end_date'], name)
            if 'hour' not in spec or 'minute' not in spec:
                raise ValueError(f'{name}: cron requires hour and minute')
            job['trigger'] = CronTrigger(timezone=tz, second=0, **{k: v for k, v in spec.items() if k != 'type'})
        elif spec.get('type') == 'date':
            only_keys(spec, ['type', 'run_at'], name)
            if not isinstance(spec.get('run_at'), str):
                raise ValueError(f'{name}: run_at must be a quoted ISO Gregorian datetime')
            job['trigger'] = DateTrigger(run_date=spec['run_at'], timezone=tz)
        else:
            raise ValueError(f'{name}: trigger type must be cron or date')
        jobs.append(job)
    return tz, grace, jobs


def latest_due(trigger, now, grace):
    """Coalesce to the latest occurrence within the bounded catch-up window."""
    floor = (now.astimezone(timezone.utc) - timedelta(seconds=grace)).astimezone(now.tzinfo)
    candidate = trigger.get_next_fire_time(None, floor)
    latest = None
    while candidate is not None and candidate.timestamp() <= now.timestamp():
        if candidate.timestamp() >= floor.timestamp():
            latest = candidate
        candidate = trigger.get_next_fire_time(candidate, candidate + timedelta(microseconds=1))
    return latest


def connect(path):
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path, timeout=15)
    conn.execute('''CREATE TABLE IF NOT EXISTS runs (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, recipient TEXT NOT NULL,
        task TEXT NOT NULL, status TEXT NOT NULL, started TEXT NOT NULL,
        finished TEXT, error TEXT, PRIMARY KEY(schedule_id, due))''')
    conn.commit()
    return conn


def tick(config=CONFIG, state=STATE, now=None, registry=TASKS):
    tz, grace, jobs = load_config(config, registry)
    now = now or datetime.now(tz)
    failed = False
    conn = connect(state)
    try:
        for job in jobs:
            if not job.get('enabled', True):
                continue
            due = latest_due(job['trigger'], now, grace)
            if due is None:
                continue
            key = (job['id'], due.astimezone(timezone.utc).isoformat())
            with conn:
                inserted = conn.execute(
                    'INSERT OR IGNORE INTO runs(schedule_id,due,recipient,task,status,started) VALUES (?,?,?,?,?,?)',
                    (*key, job['recipient'], job['task'], 'running', datetime.now(timezone.utc).isoformat())).rowcount
            if not inserted:
                continue
            logger.info('Starting %s due=%s recipient=%s', *key, job['recipient'])
            status, error = 'succeeded', None
            try:
                registry[job['task']][1](job['recipient'], job['params'])
            except Exception as exc:
                # Persist the exception class only: third-party errors can contain secrets.
                status, error, failed = 'failed', type(exc).__name__, True
            with conn:
                conn.execute('UPDATE runs SET status=?,finished=?,error=? WHERE schedule_id=? AND due=?',
                             (status, datetime.now(timezone.utc).isoformat(), error, *key))
            logger.info('Completed %s status=%s error=%s', job['id'], status, error)
    finally:
        conn.close()
    return 1 if failed else 0


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
            print(f"{job['id']} task={job['task']} recipient={job['recipient']} next={upcoming}")
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
