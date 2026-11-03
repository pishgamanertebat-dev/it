"""Per-recipient receipts and a durable, immutable PDF for retries after edits."""
from datetime import datetime
import hashlib
import json
from pathlib import Path
import tempfile
from zoneinfo import ZoneInfo

from tools.authorization import AuthorizationStore
from tools.authorization.machinery_performance import CAPABILITY, REPORT_USERS
from tools.scheduler.report_errors import ReportFailure
from .report import build, exact_date, source_snapshot
from .render import caption, validate_pdf

ROOT = Path(__file__).resolve().parents[3]
OUTBOX = ROOT / 'runtime/scheduler/machinery-performance'


def validate_params(params):
    if not isinstance(params, dict) or set(params)-{'date', 'occurrence'}:
        raise ValueError('Invalid performance report params')
    if 'date' in params:
        exact_date(params['date'])
    if 'occurrence' in params and datetime.fromisoformat(params['occurrence']).utcoffset() is None:
        raise ValueError('Timezone-aware occurrence required')


def pinned(target, *, outbox=OUTBOX):
    target = exact_date(target)
    folder = Path(outbox) / target.replace('/', '-')
    manifest = folder / 'report.json'
    if not manifest.exists():
        return None
    try:
        record = json.loads(manifest.read_text(encoding='utf-8'))
        pdf = folder / 'report.pdf'
        if (record['report']['date'] != target or record['report']['mode'] != 'monthly'
                or record['report']['comparison'] != 'average_gt_limit'
                or hashlib.sha256(pdf.read_bytes()).hexdigest() != record['pdf_sha256']):
            raise ValueError('Pinned report identity/hash mismatch')
        validate_pdf(pdf, record['report'])
        return {'pdf': str(pdf), 'report': record['report'], 'status': 'ready'}
    except Exception as exc:
        raise ReportFailure('pdf_validation', stage='validation') from exc


def prepare(target, *, outbox=OUTBOX):
    existing = pinned(target, outbox=outbox)
    if existing:
        return existing
    outbox = Path(outbox)
    outbox.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='prepare-', dir=outbox) as tmp:
        result = build(target, tmp, mode='monthly', comparison='average_gt_limit')
        if result['status'] != 'ready':
            return result
        # Confirm the live source remained the snapshot used for this artifact.
        with source_snapshot() as (_, digest):
            if digest != result['report']['source_sha256']:
                raise ReportFailure('source_changed', stage='source')
        folder = outbox / exact_date(target).replace('/', '-')
        folder.mkdir(exist_ok=True)
        pdf = folder / 'report.pdf'
        data = Path(result['pdf']).read_bytes()
        pdf.write_bytes(data)
        record = dict(report=result['report'], pdf_sha256=hashlib.sha256(data).hexdigest(),
                      prepared_at=datetime.now(ZoneInfo('Asia/Tehran')).isoformat())
        partial = folder / 'report.partial.json'
        partial.write_text(json.dumps(record, ensure_ascii=False), encoding='utf-8')
        partial.replace(folder / 'report.json')  # Manifest is the publication boundary.
    return pinned(target, outbox=outbox)


def deliver(recipient, params, *, authorization=None, on_receipt=None, closed=None):
    from tools.scheduler.tasks import (BaleSender, TransportUnavailable, TransportRejected,
                                       closed_sets, note_receipt, overflow_report_date)
    validate_params(params)
    target = exact_date(params.get('date'))
    if recipient != 'capability:' + CAPABILITY:
        raise ValueError('Fixed performance subscription capability required')
    if params.get('occurrence') and overflow_report_date(datetime.fromisoformat(params['occurrence'])) != target:
        raise ValueError('Occurrence/date mismatch')
    store = authorization if authorization is not None else AuthorizationStore()

    def recipients():
        result = store.resolve_active_recipients(CAPABILITY)
        if result.status == 'store_unavailable':
            raise TransportUnavailable('Performance authorization unavailable')
        return tuple(u for u in result.recipients if u in REPORT_USERS) if result.status == 'ready' else ()

    initial = recipients()
    if not initial:
        return {'status': 'skipped', 'reason': 'recipient_revoked'}
    sent_closed, uncertain_closed = closed_sets(closed)
    if set(initial) <= sent_closed:
        return {'status': 'succeeded'}
    pending = [u for u in initial if u not in sent_closed and u not in uncertain_closed]
    if not pending:
        return {'status': 'uncertain', 'reason': 'uncertain'}
    result = prepare(target)
    if result['status'] == 'waiting_for_data':
        return {'status': 'waiting_for_data', 'reason': 'date_missing', 'notice_delivery_state': 'suppressed'}
    report, pdf = result['report'], Path(result['pdf'])
    sent, failed, uncertain = [], [], []
    sender = None
    try:
        for user in pending:
            if user not in recipients():
                note_receipt(on_receipt, user, 'revoked')
                continue
            if sender is None:
                sender = BaleSender()
                sender.check_connection()
            if user not in recipients():
                note_receipt(on_receipt, user, 'revoked')
                continue
            validate_pdf(pdf, report)
            note_receipt(on_receipt, user, 'sending')
            try:
                mid = sender.document(user, pdf, caption(report))
                if mid in (None, ''):
                    raise RuntimeError('Missing delivery receipt')
                note_receipt(on_receipt, user, 'sent', mid)
                sent.append(user)
            except (TransportUnavailable, TransportRejected):
                note_receipt(on_receipt, user, 'failed')
                failed.append(user)
            except Exception:
                note_receipt(on_receipt, user, 'uncertain')
                uncertain.append(user)
    finally:
        if sender is not None:
            sender.close()
    return dict(status='failed' if failed or uncertain else ('succeeded' if sent else 'skipped'),
                reason='delivery_failed' if failed or uncertain else ('recipient_revoked' if not sent else ''),
                retry_recipients=failed, uncertain_recipients=uncertain, sent_count=len(sent))
