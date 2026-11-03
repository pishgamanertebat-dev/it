"""Receipt-protected delivery of the selected oil balance PDF."""
from datetime import datetime
import logging
from pathlib import Path
import tempfile

from tools.authorization import AuthorizationStore
from tools.authorization.oil_balance import CAPABILITY
from tools.scheduler.report_errors import ReportFailure
from .report import build, exact_date, source_snapshot
from .render import validate_pdf

ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)


def validate_params(params):
    if not isinstance(params, dict) or set(params)-{'date', 'occurrence'}:
        raise ValueError('Invalid oil balance params')
    if 'date' in params:
        exact_date(params['date'])
    if 'occurrence' in params and datetime.fromisoformat(params['occurrence']).utcoffset() is None:
        raise ValueError('Timezone-aware occurrence required')


def verify_source_digest(expected):
    with source_snapshot() as (_, actual):
        if actual != expected:
            raise ReportFailure('source_changed', stage='source')


def deliver(recipient, params, *, authorization=None, on_receipt=None, closed=None):
    from tools.scheduler.tasks import (BaleSender, TransportUnavailable, closed_sets,
                                       note_receipt, overflow_report_date)
    validate_params(params)
    expected = exact_date(params.get('date'))
    if recipient != 'capability:' + CAPABILITY:
        raise ValueError('Fixed oil balance receive capability required')
    if params.get('occurrence') and overflow_report_date(datetime.fromisoformat(params['occurrence'])) != expected:
        raise ValueError('Occurrence/date mismatch')
    store = authorization if authorization is not None else AuthorizationStore()

    def recipients():
        result = store.resolve_active_recipients(CAPABILITY)
        if result.status == 'store_unavailable':
            raise TransportUnavailable('Oil balance authorization unavailable')
        return tuple(result.recipients) if result.status == 'ready' else ()

    initial = recipients()
    if not initial:
        return {'status': 'skipped', 'reason': 'recipient_revoked'}
    sent_closed, uncertain_closed = closed_sets(closed)
    if set(initial) <= sent_closed:
        return {'status': 'succeeded'}
    pending = [u for u in initial if u not in sent_closed and u not in uncertain_closed]
    if not pending:
        return {'status': 'uncertain', 'reason': 'uncertain'}
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    sent, failed, uncertain, revoked = [], [], [], []
    with tempfile.TemporaryDirectory(prefix='oil-balance-', dir=runtime) as directory:
        result = build(expected, directory)
        report = result['report']
        if result['status'] == 'waiting_for_data':
            return {'status': 'waiting_for_data', 'reason': 'date_missing',
                    'latest_date': max(report['available_dates'], default=None), 'notice_delivery_state': 'suppressed'}
        if result['status'] != 'ready' or report['date'] != expected:
            raise ValueError('Unexpected oil balance report date/status')
        path = Path(result['pdf'])
        if path.parent.resolve() != Path(directory).resolve():
            raise ValueError('Unexpected output path')
        validation = validate_pdf(path, report)
        logger.info('Oil balance target=%s source_sha256=%s rows=%s pages=%s pdf_sha256=%s',
                    expected, report['source_sha256'], report['rows_matched'], validation['pages'], validation['pdf_sha256'])
        sender = None
        try:
            for user in pending:
                if user not in recipients():
                    revoked.append(user)
                    note_receipt(on_receipt, user, 'revoked')
                    continue
                if sender is None:
                    sender = BaleSender()
                    sender.check_connection()
                verify_source_digest(report['source_sha256'])
                if user not in recipients():
                    revoked.append(user)
                    note_receipt(on_receipt, user, 'revoked')
                    continue
                note_receipt(on_receipt, user, 'sending')
                try:
                    mid = sender.document(user, path, 'گزارش روزانه موازنه روغن\nتاریخ تعویض‌ها: ' + expected)
                    if mid in (None, ''):
                        raise RuntimeError('Delivery receipt missing')
                    sent.append(user)
                    note_receipt(on_receipt, user, 'sent', mid)
                except TransportUnavailable:
                    failed.append(user)
                    note_receipt(on_receipt, user, 'failed')
                except Exception:
                    uncertain.append(user)
                    note_receipt(on_receipt, user, 'uncertain')
        finally:
            if sender is not None:
                sender.close()
    result = {'status': 'failed' if failed or uncertain else ('succeeded' if sent else 'skipped'),
              'reason': 'delivery_failed' if failed or uncertain else ('recipient_revoked' if not sent else ''),
              'sent_count': len(sent), 'revoked_count': len(revoked)}
    if failed:
        result['retry_recipients'] = failed
    if uncertain:
        result['uncertain_recipients'] = uncertain
    return result
