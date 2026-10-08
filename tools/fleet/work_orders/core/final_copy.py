"""Future SENT-only NET final copies. This outbox never sends to service staff."""
from contextlib import closing
import hashlib
import logging
from pathlib import Path
import sqlite3
import time
from uuid import uuid4

from tools.authorization.store import AuthorizationStore
from tools.authorization.office_delivery import COPY_CAPABILITY
from tools.authorization.net import NET_MANAGER, NET_DEPUTY
from .db import connect_db
from .paths import PROJECT_ROOT

logger = logging.getLogger(__name__)
KIND = 'final_approved_copy'
ARTIFACT_ROOT = PROJECT_ROOT / 'runtime/work_order_copies/artifacts'


def activate(c, *, timestamp=None):
    """Operator-only activation. No historical SENT scan or backfill."""
    c.execute("""CREATE TABLE IF NOT EXISTS final_copy_meta (
        key TEXT PRIMARY KEY, value TEXT NOT NULL)""")
    c.execute("""CREATE TABLE IF NOT EXISTS work_order_final_copy (
        work_order_id INTEGER NOT NULL REFERENCES service_work_orders(id),
        artifact_sha256 TEXT NOT NULL, recipient_id TEXT NOT NULL,
        delivery_kind TEXT NOT NULL CHECK(delivery_kind='final_approved_copy'),
        artifact_path TEXT, file_name TEXT NOT NULL, caption TEXT NOT NULL,
        status TEXT NOT NULL, attempts INTEGER NOT NULL DEFAULT 0,
        next_attempt REAL, claimed_at REAL, message_id TEXT, last_error TEXT,
        created_at REAL NOT NULL, updated_at REAL NOT NULL,
        PRIMARY KEY(work_order_id,artifact_sha256,recipient_id,delivery_kind))""")
    c.execute("INSERT OR IGNORE INTO final_copy_meta VALUES ('activated_at',?)", (str(time.time() if timestamp is None else timestamp),))


def enabled(c):
    if not c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='final_copy_meta'").fetchone():
        return False
    return c.execute("SELECT 1 FROM final_copy_meta WHERE key='activated_at'").fetchone() is not None


def _eligible_issuer(order, store):
    owner = str(order['created_by'] or '')
    approver = str(order['approved_by'] or '')
    if not owner.startswith('bale:') or approver != owner:
        return False
    user = owner.split(':', 1)[1]
    return (bool(set(store.roles(user)) & {NET_MANAGER, NET_DEPUTY})
            and store.has_capability(user, 'work_orders.send')
            and store.has_capability(user, 'work_orders.approve'))


def prepare(c, order, pdf_path, *, authorization=None):
    """Pin bytes before main transport. Failure remains a copy-only blocked obligation."""
    if not enabled(c) or order['status'] != 'APPROVED' or order['work_order_type'] not in {'AIR_FILTER','OIL_CHANGE','GREASING'}:
        return None
    store = authorization or AuthorizationStore()
    if not _eligible_issuer(order, store):
        return None
    resolution = store.resolve_active_recipients(COPY_CAPABILITY)
    if resolution.status != 'ready':
        return None
    from .registry import get_work_order_spec
    machines = [str(r[0]) for r in c.execute(
        """SELECT COALESCE(m.canonical_code,i.machine_code) FROM service_work_order_items i
           LEFT JOIN machines m ON m.id=i.machine_id WHERE i.work_order_id=? ORDER BY i.id""", (order['id'],))]
    owner = str(order['created_by']).split(':', 1)[1]
    staff_name = order['display_name']
    if c.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='service_work_order_dispatch'").fetchone():
        dispatch = c.execute('SELECT staff_name FROM service_work_order_dispatch WHERE work_order_no=?', (order['work_order_no'],)).fetchone()
        if dispatch and dispatch[0]:
            staff_name = dispatch[0]
    caption = (
        'رونوشت حکم کار تأییدشده\n'
        f"نوع: {get_work_order_spec(order['work_order_type']).label_fa}\n"
        f"شماره حکم: {order['work_order_no']}\n"
        f"دستگاه: {', '.join(machines) or 'ثبت نشده'}\n"
        f"صادرکننده: {store.identity_label(owner)} ({owner})\n"
        f"سرویسکار مسئول: {staff_name} ({order['bale_id']})\n"
        f"تاریخ: {order['jalali_date']}\n\nنسخه جهت اطلاع مدیریت ارسال شد."
    )
    artifact = {'recipients': tuple(resolution.recipients), 'caption': caption[:1024],
                'file_name': Path(pdf_path).name, 'path': None, 'sha256': '', 'error': None}
    try:
        data = Path(pdf_path).read_bytes()
        artifact['sha256'] = hashlib.sha256(data).hexdigest()
        if not data.startswith(b'%PDF-'):
            raise ValueError('Invalid final PDF')
        ARTIFACT_ROOT.mkdir(parents=True, exist_ok=True)
        path = ARTIFACT_ROOT / (artifact['sha256'] + '.pdf')
        if not path.exists():
            temporary = path.with_suffix('.' + uuid4().hex + '.tmp')
            try:
                temporary.write_bytes(data)
                temporary.replace(path)
            finally:
                temporary.unlink(missing_ok=True)
        if hashlib.sha256(path.read_bytes()).hexdigest() != artifact['sha256']:
            raise ValueError('Pinned artifact mismatch')
        artifact['path'] = str(path)
    except Exception as exc:
        artifact['error'] = type(exc).__name__
    return artifact


def confirmed(result):
    """Copy requires a successful transport outcome, not an approval or callback."""
    if isinstance(result, dict):
        return result.get('ok') is True
    mid = getattr(result, 'message_id', None)
    return not isinstance(mid, bool) and isinstance(mid, (str, int))


def enqueue(c, order, artifact, result):
    """Called in the SENT transaction only. Repeated callbacks cannot create copies."""
    if not artifact or not confirmed(result):
        return
    if c.execute('SELECT status FROM service_work_orders WHERE id=?', (order['id'],)).fetchone()[0] != 'SENT':
        return
    now = time.time()
    for user in artifact['recipients']:
        c.execute("""INSERT OR IGNORE INTO work_order_final_copy(
            work_order_id,artifact_sha256,recipient_id,delivery_kind,artifact_path,file_name,caption,
            status,last_error,created_at,updated_at) VALUES (?,?,?,?,?,?,?,?,?,?,?)""",
            (order['id'], artifact['sha256'], user, KIND, artifact['path'], artifact['file_name'], artifact['caption'],
             'blocked' if artifact['error'] else 'pending', artifact['error'], now, now))


def drain_pending(*, authorization=None, sender_factory=None, clock=None):
    """One-minute scheduler drain; safe retries only, stale sending freezes uncertain."""
    from tools.scheduler.tasks import BaleSender, TransportUnavailable
    from tools.scheduler.misfire import backoff_delay
    now = time.time() if clock is None else clock
    store = authorization or AuthorizationStore()
    sender_factory = sender_factory or BaleSender
    def authorized(user):
        result = store.resolve_active_recipients(COPY_CAPABILITY)
        if result.status == 'store_unavailable':
            raise TransportUnavailable('Copy authorization temporarily unavailable')
        return user in result.recipients
    with closing(connect_db()) as c:
        if not enabled(c):
            return
        with c:
            c.execute("""UPDATE work_order_final_copy SET status='uncertain',last_error='stale_sending',updated_at=?
                WHERE status='sending' AND claimed_at<?""", (now, now - 900))
        rows = c.execute("""SELECT d.* FROM work_order_final_copy d JOIN service_work_orders w ON w.id=d.work_order_id
            WHERE w.status='SENT' AND d.status IN ('pending','failed') AND (d.next_attempt IS NULL OR d.next_attempt<=?)
            ORDER BY d.created_at LIMIT 50""", (now,)).fetchall()
        for row in rows:
            key = (row['work_order_id'], row['artifact_sha256'], row['recipient_id'], row['delivery_kind'])
            where = 'work_order_id=? AND artifact_sha256=? AND recipient_id=? AND delivery_kind=?'
            with c:
                if c.execute("UPDATE work_order_final_copy SET status='sending',claimed_at=?,updated_at=?,attempts=attempts+1 WHERE " + where +
                             " AND status IN ('pending','failed') AND (next_attempt IS NULL OR next_attempt<=?)", (now, now, *key, now)).rowcount != 1:
                    continue
            sender = None
            attempted = False
            state, error, message_id = 'failed', None, None
            try:
                if not authorized(row['recipient_id']):
                    state = 'revoked'
                    continue
                path = Path(row['artifact_path'])
                data = path.read_bytes()
                if not data.startswith(b'%PDF-') or hashlib.sha256(data).hexdigest() != row['artifact_sha256']:
                    state, error = 'blocked', 'artifact_mismatch'
                    continue
                sender = sender_factory()
                sender.check_connection()
                if not authorized(row['recipient_id']):
                    state = 'revoked'
                    continue
                attempted = True
                # Original document name and exact pinned PDF; no renderer or workbook access.
                message_id = sender.document(row['recipient_id'], path, row['caption'], file_name=row['file_name'])
                state = 'sent'
            except TransportUnavailable as exc:
                state, error = 'failed', type(exc).__name__
            except (FileNotFoundError, ValueError, TypeError) as exc:
                state, error = ('uncertain' if attempted else 'blocked'), type(exc).__name__
            except Exception as exc:
                state, error = ('uncertain' if attempted else 'failed'), type(exc).__name__
            finally:
                with c:
                    c.execute("UPDATE work_order_final_copy SET status=?,last_error=?,message_id=?,next_attempt=?,updated_at=? WHERE " + where,
                        (state, error, None if message_id is None else str(message_id),
                         now + backoff_delay(row['attempts'] + 1) if state == 'failed' else None, now, *key))
                if sender is not None:
                    try:
                        sender.close()
                    except Exception:
                        logger.warning('Final-copy transport close failed')
