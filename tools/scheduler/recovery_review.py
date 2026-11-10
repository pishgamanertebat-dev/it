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
    # Recovery receipts supplement historical occurrences. Retain the original
    # gate even after independent delivery, so the old occurrence cannot resend.
    return conn.execute("SELECT 1 FROM report_recovery_hold WHERE schedule_id=? AND due=? AND status IN ('held','recovered')",key).fetchone() is not None


def delivered_recoveries(conn,key):
    """Supplemental delivery proof; original receipt history stays unchanged."""
    if not conn.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='report_recovery_operation'").fetchone():
        return {}
    return {r['recipient_id']:dict(r) for r in conn.execute('''SELECT op.* FROM report_recovery_operation op
        JOIN receipts r ON r.schedule_id=op.receipt_schedule AND r.due=op.original_due
            AND r.recipient_id=op.recipient_id AND r.delivery_kind='report'
        JOIN office_artifact_receipts a ON a.schedule_id=r.schedule_id AND a.due=r.due
            AND a.recipient_id=r.recipient_id AND a.artifact_id=op.operation_id
        WHERE op.original_schedule=? AND op.original_due=? AND op.status='sent'
            AND op.message_id IS NOT NULL AND op.approval_ref<>''
            AND r.status='sent' AND r.message_id=op.message_id
            AND a.status='sent' AND a.message_id=op.message_id AND a.sha256=op.sha256''',key)}


def hold_backlog(conn,jobs,now,store):
    """Deployment operation only. Owner review is required for historical sends.

    Existing terminal/missing policy is evaluated before calling this function.
    Ordinary future occurrences are never added here by the scheduler tick.
    """
    from .delivery_alert import _delivery_summary,occurrence_recipients
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
            for due,run in rows.items():
                original=datetime.fromisoformat(due)
                if original>now:continue
                # A past clock time is not historical backfill. Preserve the full
                # 36-hour data window and independent technical replay horizon.
                if now<original+timedelta(hours=36):continue
                if (now<original+timedelta(days=job.get('misfire',{}).get('horizon_days') or 7)
                    and run.get('status') in {'running','retry_wait','failed','partial','waiting_for_data','skipped'}
                    and run.get('error') not in {'date_missing','no_target_date_records','missing_date_expired'}):
                    continue
                expected=occurrence_recipients(conn,job,run,store,now)
                if expected is None:raise ValueError('Backlog recipients unavailable')
                receipts=_delivery_summary(conn,job,run,expected,store)
                if expected and set(expected)<={r['recipient'] for r in receipts if r['status'] in {'sent','revoked'}}:continue
                evidence=json.dumps(dict(previous_run=run,delivery=receipts,
                    release_condition='explicit scoped owner authorization plus artifact, identity and receipt validation',
                    next_action='use independent recovery operations; never rewrite historical runs or receipts'),sort_keys=True,ensure_ascii=False)
                inserted=conn.execute('''INSERT OR IGNORE INTO report_recovery_hold
                    VALUES (?,?,'held','owner_review_required_for_historical_delivery',?,?,?,NULL)''',
                    (job['id'],due,now.astimezone(timezone.utc).isoformat(),
                     (original+timedelta(hours=36)).astimezone(timezone.utc).isoformat(),evidence)).rowcount
                if inserted:added.append(dict(schedule_id=job['id'],due=due))
    return added
