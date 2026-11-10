"""Explicit, operation-scoped delivery of already validated maintenance artifacts.

Uses the official sender and additive recovery receipts. Never edits historical runs
or receipts. A crash after claiming an operation is uncertain, never an automatic retry.
"""
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path

from tools.authorization.maintenance import CAPABILITY
from tools.fleet.maintenance_daily.report import scan, render, validate_pdf
from . import office_receipts
from .runner import _save_receipt
from .tasks import BaleSender, TransportUnavailable


def timestamp():
    return datetime.now(timezone.utc).isoformat()


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def ensure_schema(conn):
    conn.execute('''CREATE TABLE IF NOT EXISTS report_recovery_operation (
        operation_id TEXT PRIMARY KEY, original_schedule TEXT NOT NULL,
        original_due TEXT NOT NULL, target_date TEXT NOT NULL, recipient_id TEXT NOT NULL,
        sha256 TEXT NOT NULL, source_sha256 TEXT NOT NULL, receipt_schedule TEXT NOT NULL,
        approval_ref TEXT NOT NULL, status TEXT NOT NULL, message_id TEXT, updated TEXT NOT NULL,
        UNIQUE(original_schedule,original_due,recipient_id,sha256))''')
    conn.execute('''CREATE TABLE IF NOT EXISTS report_recovery_event (
        id INTEGER PRIMARY KEY, operation_id TEXT NOT NULL, created TEXT NOT NULL,
        event TEXT NOT NULL, evidence TEXT NOT NULL)''')
    conn.commit()


def event(conn, op, name, evidence):
    conn.execute('INSERT INTO report_recovery_event(operation_id,created,event,evidence) VALUES (?,?,?,?)',
                 (op['operation_id'], timestamp(), name, json.dumps(evidence, ensure_ascii=False, sort_keys=True)))


def validate_candidate(op, directory):
    if digest(op['pdf']) != op['pdf_sha256'] or digest(op['source_snapshot']) != op['source_sha256']:
        raise ValueError('Pinned recovery bytes changed')
    report = scan(Path(op['source_snapshot']).read_bytes(), op['data_date'])
    if not report['rows_matched']:
        raise ValueError('Target date has no activity')
    reference = Path(directory) / (op['data_date'].replace('/', '-') + '.pdf')
    render(report, reference)  # Reconstruct exact cell/page layout for split Persian text.
    if digest(reference) != op['pdf_sha256']:
        raise ValueError('Reference rendering differs from approved artifact')
    return validate_pdf(op['pdf'], report)


def authorize(conn, proposals, authorization, store, directory):
    """The caller supplies the owner's explicit scope; no implicit global approval."""
    ensure_schema(conn)
    approved = authorization['operations']
    if len(proposals) != len(approved) or len({p['operation_id'] for p in proposals}) != len(proposals):
        raise ValueError('Authorization scope mismatch')
    reference = authorization['approval_ref']
    if not isinstance(reference, str) or not reference.strip():
        raise ValueError('Explicit approval reference required')
    validations = {}
    for op in proposals:
        scope = dict(operation_id=op['operation_id'], target_date=op['data_date'],
                     recipient_id=op['recipient_id'], sha256=op['pdf_sha256'])
        if scope not in approved or op['original_job'] != 'maintenance_daily_report':
            raise ValueError('Operation not authorized')
        resolution = store.resolve_active_recipients(CAPABILITY)
        if resolution.status != 'ready' or op['recipient_id'] not in resolution.recipients:
            raise ValueError('Recipient not currently authorized')
        validations[op['operation_id']] = validate_candidate(op, directory)
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        for op in proposals:
            existing = conn.execute('SELECT * FROM report_recovery_operation WHERE operation_id=?',
                                    (op['operation_id'],)).fetchone()
            values = (op['original_job'],op['original_due'],op['data_date'],op['recipient_id'],
                      op['pdf_sha256'],op['source_sha256'],op['proposed_receipt_schedule_id'])
            if existing:
                if tuple(existing[k] for k in ('original_schedule','original_due','target_date','recipient_id',
                                               'sha256','source_sha256','receipt_schedule')) != values:
                    raise ValueError('Operation identity changed')
                continue
            conn.execute('INSERT INTO report_recovery_operation VALUES (?,?,?,?,?,?,?,?,?,?,?,?)',
                         (op['operation_id'],*values,reference,'authorized',None,timestamp()))
            event(conn,op,'owner_authorized',dict(approval=authorization,validation=validations[op['operation_id']],
                                                 recipient_name=store.identity_label(op['recipient_id'])))
            hold = conn.execute('SELECT * FROM report_recovery_hold WHERE schedule_id=? AND due=?',
                                (op['original_job'],op['original_due'])).fetchone()
            if hold:
                if hold['reason'] != 'owner_review_required_for_historical_delivery':
                    raise ValueError('A different safety hold requires resolving its cause')
                # The historical gate is opened only for these approved operations.
                # Original occurrences remain suppressed to avoid a second delivery path.
                event(conn,op,'historical_hold_released_for_operation',dict(hold))
    return validations


def duplicate_evidence(conn, op):
    user = op['recipient_id']
    rows = [dict(r) for r in conn.execute('''SELECT * FROM receipts WHERE recipient_id=?
        AND delivery_kind='report' AND status IN ('sent','sending','uncertain')
        AND ((schedule_id=? AND due=?) OR schedule_id=?)''',
        (user,op['original_job'],op['original_due'],op['proposed_receipt_schedule_id']))]
    rows += [dict(r) for r in conn.execute('''SELECT * FROM office_artifact_receipts
        WHERE recipient_id=? AND status IN ('sent','sending','uncertain') AND sha256=?''',
        (user,op['pdf_sha256']))]
    return rows


def execute(conn, proposals, store, *, sender_factory=BaleSender):
    """Caller must own the scheduler worker lock; uncertain/sending never resend."""
    sender = None
    try:
        for op in proposals:
            key = (op['proposed_receipt_schedule_id'],op['original_due'])
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                row = conn.execute('SELECT * FROM report_recovery_operation WHERE operation_id=?',
                                   (op['operation_id'],)).fetchone()
                if not row:
                    raise ValueError('Operation has no recorded approval')
                expected = (op['original_job'],op['original_due'],op['data_date'],op['recipient_id'],
                            op['pdf_sha256'],op['source_sha256'],op['proposed_receipt_schedule_id'])
                if tuple(row[k] for k in ('original_schedule','original_due','target_date','recipient_id',
                                          'sha256','source_sha256','receipt_schedule')) != expected:
                    raise ValueError('Approved operation identity changed')
                if row['status'] != 'authorized':
                    continue
                hold = conn.execute('SELECT reason FROM report_recovery_hold WHERE schedule_id=? AND due=?',
                                    (op['original_job'],op['original_due'])).fetchone()
                if hold and hold['reason'] != 'owner_review_required_for_historical_delivery':
                    raise ValueError('Unresolved safety hold')
                evidence = duplicate_evidence(conn,op)
                if evidence:
                    status = 'already_sent' if all(r['status']=='sent' and r['message_id'] for r in evidence) else 'uncertain'
                    conn.execute('UPDATE report_recovery_operation SET status=?,updated=? WHERE operation_id=?',
                                 (status,timestamp(),op['operation_id']))
                    event(conn,op,'duplicate_prevented',evidence)
                    continue
                current = store.resolve_active_recipients(CAPABILITY)
                if current.status != 'ready' or op['recipient_id'] not in current.recipients:
                    event(conn,op,'recipient_revoked',{})
                    continue
                if digest(op['pdf']) != op['pdf_sha256'] or digest(op['source_snapshot']) != op['source_sha256']:
                    raise ValueError('Pinned recovery bytes changed before transport')
            if sender is None:
                sender = sender_factory()
                sender.check_connection()
            current = store.resolve_active_recipients(CAPABILITY)
            if current.status != 'ready' or op['recipient_id'] not in current.recipients:
                continue
            with conn:
                conn.execute('BEGIN IMMEDIATE')
                changed = conn.execute("UPDATE report_recovery_operation SET status='sending',updated=? WHERE operation_id=? AND status='authorized'",
                                       (timestamp(),op['operation_id'])).rowcount
                if not changed:
                    continue
                event(conn,op,'sending',dict(recipient_id=op['recipient_id'],sha256=op['pdf_sha256']))
            _save_receipt(conn,key,op['recipient_id'],'sending',None)
            office_receipts.save(conn,key,op['recipient_id'],op['operation_id'],'sending',digest=op['pdf_sha256'])
            message_id = None
            try:
                message_id = sender.document(op['recipient_id'],op['pdf'],op['caption'])
                status = 'sent' if message_id else 'uncertain'
            except TransportUnavailable:
                status = 'failed'  # Proven pre-send failure; further execution needs explicit handling.
            except Exception:
                status = 'uncertain'
            _save_receipt(conn,key,op['recipient_id'],status,message_id)
            office_receipts.save(conn,key,op['recipient_id'],op['operation_id'],status,message_id,op['pdf_sha256'])
            with conn:
                conn.execute('UPDATE report_recovery_operation SET status=?,message_id=?,updated=? WHERE operation_id=?',
                             (status,message_id,timestamp(),op['operation_id']))
                event(conn,op,status,dict(message_id=message_id,recipient_id=op['recipient_id'],
                                        sha256=op['pdf_sha256'],operation_id=op['operation_id']))
    finally:
        if sender is not None:
            sender.close()
    return [dict(conn.execute('SELECT * FROM report_recovery_operation WHERE operation_id=?',
                             (op['operation_id'],)).fetchone()) for op in proposals]


def reconcile_holds(conn,proposals,approval_ref):
    """After installing the recovered-occurrence guard, close only proven scopes.

    The old occurrence stays suppressed; its run/receipts are never rewritten.
    """
    from .recovery_review import delivered_recoveries
    keys={(p['original_job'],p['original_due']) for p in proposals}
    changed=[]
    with conn:
        conn.execute('BEGIN IMMEDIATE')
        for key in sorted(keys):
            group=[p for p in proposals if (p['original_job'],p['original_due'])==key]
            approved={p['recipient_id'] for p in group}
            expected={r[0] for r in conn.execute("SELECT recipient_id FROM receipts WHERE schedule_id=? AND due=? AND delivery_kind='report' AND status<>'revoked'",key)}
            delivered=delivered_recoveries(conn,key)
            if approved!=expected or not all(p['recipient_id'] in delivered
                and delivered[p['recipient_id']]['operation_id']==p['operation_id']
                and delivered[p['recipient_id']]['approval_ref']==approval_ref for p in group):
                raise ValueError('Recovery scope not fully reconciled')
            hold=conn.execute('SELECT * FROM report_recovery_hold WHERE schedule_id=? AND due=?',key).fetchone()
            if not hold or hold['status']=='recovered':continue
            if hold['status']!='held' or hold['reason']!='owner_review_required_for_historical_delivery':
                raise ValueError('Different safety hold cannot be released')
            for op in group:event(conn,op,'historical_hold_satisfied',dict(previous_hold=dict(hold),approval_ref=approval_ref,
                next_action='no further send; original occurrence suppressed by recovered guard'))
            conn.execute("UPDATE report_recovery_hold SET status='recovered',approval_ref=? WHERE schedule_id=? AND due=?",(approval_ref,*key))
            changed.append(dict(schedule_id=key[0],due=key[1],status='recovered'))
    return changed
