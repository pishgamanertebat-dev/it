"""Capability fan-out for the exact scheduler-supplied day; no model or tools routing."""
from pathlib import Path
import logging,tempfile
from tools.authorization import AuthorizationStore
from tools.authorization.maintenance import CAPABILITY
from .report import build,exact_date,source_snapshot,validate_pdf
ROOT=Path(__file__).resolve().parents[3]
logger=logging.getLogger(__name__)

def validate_params(params):
    if not isinstance(params,dict) or set(params)-{'date','occurrence'}:raise ValueError('Invalid maintenance params')
    if 'date' in params:exact_date(params['date'])
    if 'occurrence' in params:
        from datetime import datetime
        v=datetime.fromisoformat(params['occurrence'])
        if v.utcoffset() is None:raise ValueError('Timezone-aware occurrence required')

def verify_source_digest(expected):
    with source_snapshot() as (_,actual):
        if actual!=expected:raise RuntimeError('Workbook version changed before upload')

def deliver(recipient, params, *, authorization=None):
    from tools.scheduler.tasks import BaleSender
    validate_params(params)
    if not params.get('date'):raise ValueError('Scheduler must provide exact previous-day date')
    expected=exact_date(params['date'])
    if params.get('occurrence'):
        from datetime import datetime
        from tools.scheduler.tasks import overflow_report_date
        if overflow_report_date(datetime.fromisoformat(params['occurrence']))!=expected:raise ValueError('Occurrence/date mismatch')
    store=authorization if authorization is not None else AuthorizationStore()
    def recipients():return store.resolve_active_recipients(CAPABILITY)
    resolution=recipients();initial=tuple(dict.fromkeys(resolution.recipients)) if resolution.status=='ready' else ()
    logger.info('Maintenance occurrence=%s target_tehran=%s recipient_count=%s recipient_ids=%s',params.get('occurrence'),expected,len(initial),initial)
    if not initial:
        logger.info('Maintenance workbook_sha256=not_read sheets_scanned=0 devices_matched=0 rows_matched=0 pdf_pages=0 pdf_bytes=0 skip_reason=%s cleanup=not_needed',resolution.status)
        return {'status':'skipped','reason':resolution.status}
    runtime=ROOT/'runtime/scheduler';runtime.mkdir(parents=True,exist_ok=True)
    sent=[];revoked=[];failed=[];directory=None
    try:
        with tempfile.TemporaryDirectory(prefix='maintenance-',dir=runtime) as directory:
            eligible=recipients()
            initial=tuple(u for u in initial if u in eligible.recipients and eligible.status=='ready')
            if not initial:return {'status':'skipped','reason':'recipient_revoked_before_generation'}
            result=build(expected,directory)
            report=result['report']
            logger.info('Maintenance workbook_sha256=%s sheets_scanned=%s devices_matched=%s rows_matched=%s',report['source_sha256'],report['sheets_scanned'],report['devices_matched'],report['rows_matched'])
            if result['status']=='skipped':
                logger.info('Maintenance pdf_pages=0 pdf_bytes=0 skip_reason=%s',result['reason']);return {'status':'skipped','reason':result['reason']}
            if report['date']!=expected or result['status']!='ready':raise ValueError('Unexpected report date/status')
            path=Path(result['pdf'])
            if path.parent.resolve()!=Path(directory).resolve():raise ValueError('Unexpected output path')
            validation=validate_pdf(path,report)
            logger.info('Maintenance pdf_pages=%s pdf_bytes=%s',validation['pages'],validation['bytes'])
            sender=None
            try:
                for user in initial:
                    current=recipients()
                    if current.status!='ready' or user not in current.recipients:
                        revoked.append(user);logger.info('Maintenance recipient=%s send=revoked',user);continue
                    if sender is None:sender=BaleSender()
                    verify_source_digest(report['source_sha256'])
                    current=recipients()
                    if current.status!='ready' or user not in current.recipients:
                        revoked.append(user);logger.info('Maintenance recipient=%s send=revoked',user);continue
                    try:
                        sender.document(user,path,'گزارش روزانه تعمیرات ماشین‌آلات\nتاریخ گزارش: '+expected)
                        sent.append(user);logger.info('Maintenance recipient=%s send=success',user)
                    except Exception:
                        failed.append(user);logger.error('Maintenance recipient=%s send=failure outcome_may_be_uncertain=true',user)
            finally:
                if sender is not None:sender.close()
        return {'status':'failed' if failed else ('succeeded' if sent else 'skipped'),
                'reason':'delivery_failed' if failed else ('recipient_revoked' if not sent else ''),
                'sent_count':len(sent),'failed_count':len(failed),'revoked_count':len(revoked)}
    finally:
        logger.info('Maintenance cleanup=%s', 'not_needed' if directory is None else ('success' if not Path(directory).exists() else 'failed'))
