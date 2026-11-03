"""FIFO missing-day queue: retain indefinitely unless a newer day is ready after 36h."""
from datetime import datetime, timedelta, timezone

from tools.scheduler.misfire import due_storage_key, latest_due
from tools.scheduler.tasks import overflow_report_date
from .delivery import pinned
from .report import source_snapshot, scan

TERMINAL = {'succeeded', 'uncertain', 'no_data_final'}


class Readiness:
    """One immutable source read for all candidate days in a scheduler tick."""
    def __init__(self):
        self.data = None
        self.cache = {}

    def __call__(self, target):
        if target not in self.cache:
            if pinned(target):
                self.cache[target] = True
            else:
                if self.data is None:
                    with source_snapshot() as (data, _):
                        self.data = data
                report = scan(self.data, target)
                self.cache[target] = bool(report['rows'] and not report['missing_codes'])
        return self.cache[target]


def candidates(job, now, rows):
    # Queued dates do not expire on the generic seven-day replay horizon.
    pending = [datetime.fromisoformat(key) for key, row in rows.items()
               if row['status'] not in TERMINAL and not (row['status'] == 'skipped' and row.get('error') == 'recipient_revoked')]
    if not rows:
        latest = latest_due(job['trigger'], now, 2*86400)
        return [latest] if latest else []
    watermark = max(datetime.fromisoformat(key) for key in rows)
    due = job['trigger'].get_next_fire_time(None, watermark + timedelta(seconds=1))
    while due is not None and due <= now:
        if due.date() not in {datetime.fromisoformat(k).astimezone(job['trigger'].timezone).date() for k in rows}:
            pending.append(due)
        nxt = job['trigger'].get_next_fire_time(due, due+timedelta(seconds=1))
        if nxt is not None and nxt <= due:
            raise ValueError('Nonadvancing performance schedule')
        due = nxt
        if len(pending) > 370:
            raise ValueError('Performance queue requires operator review after a year of downtime')
    return sorted(set(pending))


def plan(conn, job, now, rows, *, checker=None):
    dues = candidates(job, now, rows)
    if not dues:
        return []
    oldest = dues[0]
    row = rows.get(due_storage_key(oldest))
    if row and (row['status'] == 'running' or (row.get('next_attempt_at') and datetime.fromisoformat(row['next_attempt_at']) > now)):
        # Keep the original backoff; never advance around an in-flight delivery.
        if row['status'] != 'running' or now - datetime.fromisoformat(row['started']) < timedelta(minutes=15):
            return []
    checker = checker or Readiness()
    try:
        if checker(overflow_report_date(oldest)):
            return [oldest]
        if now < oldest + timedelta(hours=36):
            return [oldest]
        newer = next((d for d in dues[1:] if checker(overflow_report_date(d))), None)
        if newer is None:
            return [oldest]
        for skipped in dues:
            if skipped >= newer:
                break
            if now < skipped + timedelta(hours=36) or checker(overflow_report_date(skipped)):
                return [skipped]
            # Never erase a sent/uncertain/in-flight recipient or a prepared artifact.
            if conn.execute("SELECT 1 FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind='report' AND status IN ('sent','sending','uncertain')",
                            (job['id'], due_storage_key(skipped))).fetchone():
                return [skipped]
            with conn:
                conn.execute('''INSERT INTO runs(schedule_id,due,recipient,task,status,started,finished,error,data_state)
                    VALUES (?,?,?,?,'no_data_final',?,?,'superseded_missing_date','missing')
                    ON CONFLICT(schedule_id,due) DO UPDATE SET status='no_data_final',finished=excluded.finished,
                    error='superseded_missing_date',next_attempt_at=NULL WHERE runs.status NOT IN ('succeeded','uncertain')''',
                    (job['id'],due_storage_key(skipped),job['recipient'],job['task'],
                     now.astimezone(timezone.utc).isoformat(),now.astimezone(timezone.utc).isoformat()))
        return [newer]
    except Exception:
        # A locked/corrupt source is never proof of an incomplete date. Let normal
        # execution record the precise failure, without superseding anything.
        return [oldest]
