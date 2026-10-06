"""Explicit task registry. No arbitrary commands/imports from YAML."""
from __future__ import annotations

import asyncio
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo
import os
import logging
import tempfile
from pathlib import Path

from dotenv import dotenv_values
import requests

from tools.authorization import AuthorizationStore
from tools.fleet.overflow.bale import build_report
from tools.fleet.overflow.report import validate_date
from tools.fleet.report_caption import jalali_today, report_caption

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class BaleSender:
    def __init__(self):
        home = Path(os.environ.get('HERMES_HOME', r'C:\Users\win-10\AppData\Local\hermes'))
        self.token = os.environ.get('BALE_BOT_TOKEN') or dotenv_values(home / '.env').get('BALE_BOT_TOKEN')
        if not self.token:
            raise RuntimeError('BALE_BOT_TOKEN is not configured')
        self.session = requests.Session()
        # Match the live Bale adapter: direct transport, no inherited proxies.
        self.session.trust_env = False

    def photo(self, recipient, path, caption):
        try:
            with open(path, 'rb') as photo:
                response = self.session.post(
                    f'https://tapi.bale.ai/bot{self.token}/sendPhoto',
                    data={'chat_id': recipient, 'caption': caption},
                    files={'photo': (Path(path).name, photo, 'image/png')},
                    timeout=(15, 90))
            if response.status_code != 200 or not response.json().get('ok'):
                raise RuntimeError('Bale rejected the photo')
        except Exception:
            # Request exceptions contain the token-bearing URL; never log them.
            raise RuntimeError('Bale photo delivery failed; outcome may be uncertain') from None

    def photo_memory(self, recipient, data, filename, caption):
        """Upload an operator-requested PNG directly from memory, without persistence."""
        if not isinstance(data, bytes) or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('PNG bytes required')
        try:
            response = self.session.post(
                f'https://tapi.bale.ai/bot{self.token}/sendPhoto',
                data={'chat_id': recipient, 'caption': caption},
                files={'photo': (filename, data, 'image/png')}, timeout=(15,90))
            if response.status_code != 200 or not response.json().get('ok'):
                raise RuntimeError('Bale rejected the photo')
        except Exception:
            raise RuntimeError('Bale photo delivery failed; outcome may be uncertain') from None

    def close(self):
        self.session.close()

    def document(self, recipient, path, caption):
        try:
            with open(path, 'rb') as document:
                response = self.session.post(
                    f'https://tapi.bale.ai/bot{self.token}/sendDocument',
                    data={'chat_id': recipient, 'caption': caption},
                    files={'document': (Path(path).name, document, 'application/pdf')},
                    timeout=(15, 120))
            if response.status_code != 200 or not response.json().get('ok'):
                raise RuntimeError('Bale rejected the document')
        except Exception:
            raise RuntimeError('Bale document delivery failed; outcome may be uncertain') from None

    def check_connection(self):
        try:
            response = self.session.get(f'https://tapi.bale.ai/bot{self.token}/getMe', timeout=(15, 30))
            if response.status_code != 200 or not response.json().get('ok'):
                raise RuntimeError('Bale connection failed')
        except Exception:
            raise RuntimeError('Bale connection check failed') from None


def overflow_report_date(due=None):
    """Previous calendar day of the scheduled occurrence, always in Tehran."""
    tehran = ZoneInfo('Asia/Tehran')
    instant = due if due is not None else datetime.now(tehran)
    if instant.tzinfo is None or instant.utcoffset() is None:
        raise ValueError('Overflow occurrence must have an explicit timezone')
    previous_day = instant.astimezone(tehran).date() - timedelta(days=1)
    return jalali_today(previous_day)


def overflow_report_caption(report_date, expected_date):
    """Same report template; yesterday is the intended, current reporting day."""
    import jdatetime
    expected_date = validate_date(expected_date)
    if report_date != expected_date:
        raise ValueError('Overflow report does not match the requested previous day')
    expected_day = jdatetime.date(*map(int, expected_date.split('/'))).togregorian()
    return report_caption('سرریز روزانه', report_date, today=expected_day)


def validate_overflow(params):
    if set(params) - {'date'}:
        raise ValueError('overflow only accepts params.date')
    if params.get('date') is not None:
        if not isinstance(params['date'], str):
            raise ValueError('params.date must be a quoted Jalali date')
        validate_date(params['date'])


def overflow(recipient, params, *, authorization=None):
    validate_overflow(params)
    store = authorization if authorization is not None else AuthorizationStore()

    def authorized():
        resolution = store.resolve_daily_recipient()
        if resolution.status != 'ready' or resolution.recipient != recipient:
            reason = resolution.status if resolution.status != 'ready' else 'recipient_changed'
            logger.warning('Overflow no send: %s active_holders=%s', reason, resolution.holder_count)
            return {'status': 'skipped', 'reason': reason}
        return None

    denied = authorized()
    if denied:
        return denied
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='overflow-', dir=runtime) as directory:
        expected_date = validate_date(params['date']) if params.get('date') else overflow_report_date()
        result = asyncio.run(build_report(expected_date, directory))
        denied = authorized()
        if denied:
            return denied
        if not result.get('ok'):
            raise RuntimeError(result.get('message', 'Overflow report unavailable'))
        if not result.get('images'):
            raise RuntimeError('Overflow report contains no images')
        report = result['report']
        caption = overflow_report_caption(report['date'], expected_date)
        sender = BaleSender()
        try:
            for index, path in enumerate(result['images']):
                denied = authorized()
                if denied:
                    return denied
                sender.photo(recipient, path, caption if index == 0 else '')
        finally:
            sender.close()


def validate_repairs(params):
    if set(params) - {'source', 'section'}:
        raise ValueError('repairs only accepts params.source and params.section')
    if params.get('section', 'mechanical') not in {'mechanical', 'metalwork'}:
        raise ValueError('repairs section must be mechanical or metalwork')
    if 'source' in params and (not isinstance(params['source'], str) or not Path(params['source']).is_absolute()):
        raise ValueError('repairs source must be an absolute workbook path')


def repairs(recipient, params):
    from tools.fleet.repairs.report import build_report as build_repairs
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='repairs-', dir=runtime) as directory:
        report = build_repairs(directory, params.get('source'), section=params.get('section', 'mechanical'))
        sender = BaleSender()
        try:
            sender.document(recipient, report['pdf'], report['caption'])
        finally:
            sender.close()


def driver_daily(recipient, params, *, authorization=None):
    """Same scheduler and recipient invariant; PDF documents for the exact previous day."""
    from tools.authorization import DRIVER_RECEIVE
    from integrations.hermes.function_domain.driver_report import build_driver_pdf, SECTIONS
    validate_overflow(params)
    store = authorization if authorization is not None else AuthorizationStore()
    def authorized():
        resolution = store.resolve_daily_recipient(capability=DRIVER_RECEIVE)
        if resolution.status != 'ready' or resolution.recipient != recipient:
            reason = resolution.status if resolution.status != 'ready' else 'recipient_changed'
            logger.warning('Driver daily no send: %s active_holders=%s', reason, resolution.holder_count)
            return {'status':'skipped', 'reason':reason}
        return None
    denied = authorized()
    if denied:
        return denied
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='driver-', dir=runtime) as directory:
        expected = validate_date(params['date']) if params.get('date') else overflow_report_date()
        result = build_driver_pdf(expected, directory)
        denied = authorized()
        if denied:
            return denied
        if result['report']['date'] != expected or len(result['documents']) != 2:
            raise ValueError('Driver output must contain two PDFs for the exact requested day')
        sender = BaleSender()
        try:
            for section, path in zip(SECTIONS, result['documents']):
                denied = authorized()
                if denied:
                    return denied
                content = result['report']['sections'][section]
                sender.document(recipient, path, content['title'] + ' ' + expected)
                logger.info('Driver daily section=%s date=%s status=%s', section, expected, content['status'])
        finally:
            sender.close()


def _mechanical_push(params, *, capability, read_capability, pdf=False, authorization=None):
    """One generation, capability-based fan-out, recheck each recipient before every upload."""
    from tools.authorization import DRIVER_REPORT_READ
    from integrations.hermes.function_domain.driver_report import build_driver_pdf, SECTIONS
    validate_overflow(params)
    store = authorization if authorization is not None else AuthorizationStore()

    def recipients():
        resolution = store.resolve_active_recipients(capability)
        return tuple(user for user in resolution.recipients
                     if store.has_capability(user, read_capability))
    initial = recipients()
    if not initial:
        logger.info('Mechanical delivery skipped: no eligible recipients capability=%s', capability)
        return {'status':'skipped','reason':'no_eligible_recipient'}
    runtime=ROOT/'runtime/scheduler';runtime.mkdir(parents=True,exist_ok=True)
    sent=[];revoked=[];failed=[]
    with tempfile.TemporaryDirectory(prefix='mechanical-',dir=runtime) as directory:
        expected=validate_date(params['date']) if params.get('date') else overflow_report_date()
        if pdf:
            result=build_driver_pdf(expected,directory,sections=('mechanical',))
            if (not result.get('ok') or result['report']['date']!=expected
                    or len(result['documents'])!=1):
                raise ValueError('One mechanical PDF for the exact requested day is required')
            paths=result['documents']
            caption=SECTIONS['mechanical']+' '+expected
        else:
            result=asyncio.run(build_report(expected,directory))
            if not result.get('ok') or not result.get('images'):
                raise RuntimeError('Mechanical overflow report unavailable')
            paths=result['images']
            caption=overflow_report_caption(result['report']['date'],expected)
        sender=None
        try:
            for user in initial:
                if user not in recipients():
                    revoked.append(user);continue
                if sender is None:sender=BaleSender()
                try:
                    for index,path in enumerate(paths):
                        if user not in recipients():
                            revoked.append(user);break
                        if pdf:sender.document(user,path,caption)
                        else:sender.photo(user,path,caption if index==0 else '')
                    else:sent.append(user)
                except Exception:
                    # Continue other authorized recipients. Never retry an uncertain transport result.
                    failed.append(user)
                    logger.error('Mechanical delivery failed for one recipient capability=%s',capability)
        finally:
            if sender is not None:sender.close()
    return {'status':'failed' if failed else ('succeeded' if sent else 'skipped'),
            'reason':'delivery_failed' if failed else ('recipient_revoked' if not sent else ''),
            'sent_count':len(sent),'revoked_count':len(revoked),'failed_count':len(failed)}


def mechanical_overflow(recipient, params, *, authorization=None):
    from tools.authorization import MECH_OVERFLOW_RECEIVE, OVERFLOW_READ
    return _mechanical_push(params,capability=MECH_OVERFLOW_RECEIVE,
                            read_capability=OVERFLOW_READ,authorization=authorization)


def mechanical_driver_daily(recipient, params, *, authorization=None):
    from tools.authorization import MECH_DRIVER_RECEIVE, DRIVER_REPORT_READ
    return _mechanical_push(params,capability=MECH_DRIVER_RECEIVE,
                            read_capability=DRIVER_REPORT_READ,pdf=True,authorization=authorization)


def metalwork_driver_daily(recipient, params, *, authorization=None):
    """Scheduled metalwork PDF only; receive capability grants no on-demand reads."""
    from tools.authorization import METALWORK_DRIVER_RECEIVE
    from integrations.hermes.function_domain.driver_report import build_driver_pdf, SECTIONS
    validate_overflow(params)
    store = authorization if authorization is not None else AuthorizationStore()

    def recipients():
        return store.resolve_active_recipients(METALWORK_DRIVER_RECEIVE)

    resolution = recipients()
    if resolution.status != 'ready' or not resolution.recipients:
        logger.info('Metalwork delivery skipped: %s', resolution.status)
        return {'status': 'skipped', 'reason': resolution.status}
    initial = tuple(dict.fromkeys(resolution.recipients))
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    sent, revoked, failed = [], [], []
    with tempfile.TemporaryDirectory(prefix='metalwork-', dir=runtime) as directory:
        expected = validate_date(params['date']) if params.get('date') else overflow_report_date()
        result = build_driver_pdf(expected, directory, sections=('metalwork',))
        report = result['report']
        content = report['sections']['metalwork']
        if not result.get('ok') or report['date'] != expected or len(result['documents']) != 1:
            raise ValueError('One metalwork PDF for the exact requested day is required')
        if content['status'] == 'date_missing':
            logger.warning('Metalwork delivery skipped: date_missing target=%s', expected)
            return {'status': 'skipped', 'reason': 'date_missing'}
        if content['status'] not in {'ready', 'no_defects'}:
            raise ValueError('Invalid metalwork report status')
        path = Path(result['documents'][0])
        if path.parent.resolve() != Path(directory).resolve() or path.name != f'driver-metalwork-{expected.replace("/", "-")}.pdf':
            raise ValueError('Unexpected metalwork output path')
        sender = None
        try:
            for user in initial:
                if user not in recipients().recipients:
                    revoked.append(user)
                    continue
                if sender is None:
                    sender = BaleSender()
                # Recheck after transport initialization, immediately before upload.
                if user not in recipients().recipients:
                    revoked.append(user)
                    continue
                try:
                    sender.document(user, path, SECTIONS['metalwork'] + ' ' + expected)
                    sent.append(user)
                except Exception:
                    failed.append(user)
                    logger.error('Metalwork delivery failed for one recipient')
        finally:
            if sender is not None:
                sender.close()
    logger.info('Metalwork delivery date=%s sent=%s revoked=%s failed=%s', expected, len(sent), len(revoked), len(failed))
    return {'status': 'failed' if failed else ('succeeded' if sent else 'skipped'),
            'reason': 'delivery_failed' if failed else ('recipient_revoked' if not sent else ''),
            'sent_count': len(sent), 'revoked_count': len(revoked), 'failed_count': len(failed)}


TASKS = {'overflow': (validate_overflow, overflow), 'repairs': (validate_repairs, repairs),
         'driver_daily': (validate_overflow, driver_daily),
         'mechanical_overflow':(validate_overflow,mechanical_overflow),
         'mechanical_driver_daily':(validate_overflow,mechanical_driver_daily),
         'metalwork_driver_daily': (validate_overflow, metalwork_driver_daily)}
from tools.authorization import MECH_OVERFLOW_RECEIVE, MECH_DRIVER_RECEIVE, METALWORK_DRIVER_RECEIVE
MULTI_RECIPIENT_TASKS={'mechanical_overflow':MECH_OVERFLOW_RECEIVE,
                      'mechanical_driver_daily':MECH_DRIVER_RECEIVE,
                      'metalwork_driver_daily':METALWORK_DRIVER_RECEIVE}
