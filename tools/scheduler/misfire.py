"""Per-job catch-up selection. High-frequency schedules never replay every missed slot."""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

BACKOFF_SECONDS = (60, 120, 300, 600, 900)
SKIP_MISSED_SLACK_SECONDS = 90
STALE_RUNNING_SECONDS = 900
RETRYABLE_SKIP = frozenset({
    'date_missing', 'no_target_date_records', 'source_unavailable', 'store_unavailable'})
TERMINAL_FAILURE = frozenset({'invalid_source', 'ValueError'})
RETRYABLE_FAILURE = frozenset({
    'transport_unavailable', 'delivery_failed', 'TimeoutError', 'OSError', 'PermissionError',
    'RuntimeError', 'TransportUnavailable', 'TransportRejected', 'ConnectionError', 'ConnectTimeout'})


def backoff_delay(attempt):
    """Bounded 1m, 2m, 5m, 10m, 15m. Attempt is the count of finished tries."""
    index = min(max(int(attempt), 1) - 1, len(BACKOFF_SECONDS) - 1)
    return BACKOFF_SECONDS[index]


def due_storage_key(due):
    """UTC key shared with the existing ledger. Exact minutes stay without microseconds."""
    return due.astimezone(timezone.utc).isoformat()


def classify_source_message(message):
    """Separate a missing exact day or a locked workbook from a corrupt source."""
    text = message or ''
    lowered = text.lower()
    if 'ثبت نشده' in text or 'ردیف ثبت' in text or 'missing' in lowered:
        return 'date_missing'
    if ('در دسترس نیست' in text or 'قابل خواندن' in text or 'lock' in lowered
            or 'unavailable' in lowered):
        return 'source_unavailable'
    return 'invalid_source'


def latest_due(trigger, now, grace):
    """Coalesce to the latest occurrence within the bounded catch-up window."""
    floor = (now.astimezone(timezone.utc) - timedelta(seconds=grace)).astimezone(now.tzinfo)
    candidate = trigger.get_next_fire_time(None, floor)
    latest = None
    while candidate is not None and candidate.timestamp() <= now.timestamp():
        if candidate.timestamp() >= floor.timestamp():
            latest = candidate
        nxt = trigger.get_next_fire_time(candidate, candidate + timedelta(microseconds=1))
        if nxt is None or nxt <= candidate:
            break
        candidate = nxt
    return latest


def _occurrences(trigger, floor, now, cap):
    candidate = trigger.get_next_fire_time(None, floor - timedelta(seconds=1))
    found = []
    while candidate is not None and candidate.timestamp() <= now.timestamp():
        if candidate.timestamp() >= floor.timestamp():
            found.append(candidate)
            if len(found) > cap:
                return found, True
        nxt = trigger.get_next_fire_time(candidate, candidate + timedelta(microseconds=1))
        if nxt is None or nxt <= candidate:
            break
        candidate = nxt
    return found, False


def row_actionable(row, now, stale_seconds=STALE_RUNNING_SECONDS):
    """True when this ledger row still needs a replay attempt."""
    status = row.get('status')
    error = row.get('error') or ''
    nxt = row.get('next_attempt_at')
    if nxt and datetime.fromisoformat(nxt) > now.astimezone(timezone.utc):
        return False
    if status in {'succeeded', 'uncertain', 'no_data_final'}:
        return False
    if status == 'running':
        started = datetime.fromisoformat(row['started'])
        age = (now.astimezone(timezone.utc) - started.astimezone(timezone.utc)).total_seconds()
        return age >= stale_seconds
    if status == 'skipped':
        return error in RETRYABLE_SKIP
    if status == 'failed':
        return error in RETRYABLE_FAILURE
    if status in {'retry_wait', 'partial', 'waiting_for_data'}:
        return True
    return False


def replay_plan(trigger, now, horizon_days, rows, stale_seconds=STALE_RUNNING_SECONDS):
    """Return ``(dues, flooded, historical_gaps)``.

    A schedule that has never run catches up only its latest due, so enabling replay cannot
    dump a week of first-time history. After a watermark exists, only dues newer than that
    watermark are created; older holes stay for operator review. Non-terminal rows inside the
    horizon are retried in place. More dues than the horizon cap means a high-frequency
    expression and is refused entirely.
    """
    floor = now - timedelta(days=horizon_days)
    found, flooded = _occurrences(trigger, floor, now, horizon_days + 2)
    if flooded:
        return [], True, 0
    known = set(rows)
    watermark = max(known) if known else None
    selected = []
    gaps = 0
    for item in found:
        key = due_storage_key(item)
        row = rows.get(key)
        if row is not None:
            if row_actionable(row, now, stale_seconds):
                selected.append(item)
            continue
        if watermark is None:
            continue
        if key > watermark:
            selected.append(item)
        else:
            gaps += 1
    if watermark is None and found:
        latest = found[-1]
        if due_storage_key(latest) not in rows:
            selected.append(latest)
    return selected, False, gaps


def outcome_from_receipts(states):
    """Ledger outcome from per-recipient receipts. Empty means the handler outcome stands."""
    states = set(states)
    if not states:
        return None
    if states & {'failed', 'pending'}:
        return 'partial' if states & {'sent', 'uncertain', 'revoked'} else 'retry_wait'
    if 'uncertain' in states or 'sending' in states:
        return 'uncertain'
    if 'sent' in states:
        return 'succeeded'
    if states <= {'revoked'}:
        return 'skipped'
    return None
