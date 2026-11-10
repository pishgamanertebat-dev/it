"""Explicit historical delivery holds, separate from runs and immutable receipts."""
from datetime import datetime,timedelta,timezone
import json


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS report_recovery_hold (
        schedule_id TEXT NOT NULL, due TEXT NOT NULL, status TEXT NOT NULL,
        reason TEXT NOT NULL, created TEXT NOT NULL, deadline TEXT NOT NULL,
        evidence TEXT NOT NULL, approval_ref TEXT,
        PRIMARY KEY(schedule_id,due))''')


def held(conn,key):
    return conn.execute("SELECT 1 FROM report_recovery_hold WHERE schedule_id=? AND due=? AND status='held'",key).fetchone() is not None


def hold_backlog(conn,jobs,now,store):
    """Deployment operation only. Owner review is required for historical sends.

    Existing terminal/missing policy is evaluated before calling this function.
    Ordinary future occurrences are never added here by the scheduler tick.
    """
    from .delivery_alert import _delivery_summary,_expected
    from .misfire import due_storage_key,_occurrences
    ensure_schema(conn)
    added=[]
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        for job in jobs:
            if not job.get('enabled',True):continue
            rows={r['due']:dict(r) for r in conn.execute('SELECT * FROM runs WHERE schedule_id=?',(job['id'],))}
            dues,flooded=_occurrences(job['trigger'],now-timedelta(days=7),now,10)
            if flooded:raise ValueError('Backlog hold requires a daily schedule')
            by_day={datetime.fromisoformat(key).astimezone(job['trigger'].timezone).date():key for key in sorted(rows,reverse=True)}
            for due in dues:
                key=by_day.get(due.date(),due_storage_key(due))
                rows.setdefault(key,dict(schedule_id=job['id'],due=key,status='not_recorded',error='occurrence_not_recorded'))
            expected=_expected(job,store)
            if expected is None:raise ValueError('Backlog recipients unavailable')
            for due,run in rows.items():
                original=datetime.fromisoformat(due)
                if original>now:continue
                receipts=_delivery_summary(conn,job,run,expected,store)
                if expected and set(expected)<={r['recipient'] for r in receipts if r['status'] in {'sent','revoked'}}:continue
                evidence=json.dumps(dict(previous_run=run,delivery=receipts),sort_keys=True,ensure_ascii=False)
                inserted=conn.execute('''INSERT OR IGNORE INTO report_recovery_hold
                    VALUES (?,?,'held','owner_review_required_for_historical_delivery',?,?,?,NULL)''',
                    (job['id'],due,now.astimezone(timezone.utc).isoformat(),
                     (original+timedelta(hours=36)).astimezone(timezone.utc).isoformat(),evidence)).rowcount
                if inserted:added.append(dict(schedule_id=job['id'],due=due))
    return added
