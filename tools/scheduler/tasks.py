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
from .misfire import classify_source_message
from .no_data import notify_missing

ROOT = Path(__file__).resolve().parents[2]
logger = logging.getLogger(__name__)


class TransportUnavailable(RuntimeError):
    """Definite pre-send disconnect or rejection. Safe to retry."""


class TransportRejected(TransportUnavailable):
    """Bale answered and refused the upload. Nothing was delivered."""


class TransportUncertain(RuntimeError):
    """The request may have reached Bale. Do not send it again automatically."""


class BaleSender:
    def __init__(self):
        home = Path(os.environ.get('HERMES_HOME', r'C:\Users\win-10\AppData\Local\hermes'))
        self.token = os.environ.get('BALE_BOT_TOKEN') or dotenv_values(home / '.env').get('BALE_BOT_TOKEN')
        if not self.token:
            raise RuntimeError('BALE_BOT_TOKEN is not configured')
        self.session = requests.Session()
        # Match the live Bale adapter: direct transport, no inherited proxies.
        self.session.trust_env = False

    def _message_id(self, response):
        try:
            body = response.json()
        except Exception:
            raise TransportUncertain('Bale outcome uncertain') from None
        if response.status_code != 200 or not body.get('ok'):
            raise TransportRejected('Bale rejected the upload')
        message_id = (body.get('result') or {}).get('message_id')
        return None if message_id is None else str(message_id)

    def _post(self, method, data, files, timeout):
        # Connection failures happen before Bale accepts the upload. A read timeout does not.
        try:
            response = self.session.post(
                f'https://tapi.bale.ai/bot{self.token}/{method}', data=data, files=files, timeout=timeout)
        except requests.exceptions.ConnectTimeout:
            raise TransportUnavailable('Bale unavailable before send') from None
        except requests.exceptions.ConnectionError as exc:
            # Connection resets may happen after upload. Only a proven failure
            # to establish a connection is safe to retry.
            from urllib3.exceptions import NewConnectionError
            pending, seen = [exc], set()
            while pending:
                error = pending.pop()
                if id(error) in seen:
                    continue
                seen.add(id(error))
                if isinstance(error, NewConnectionError):
                    raise TransportUnavailable('Bale unavailable before send') from None
                pending.extend(v for v in (getattr(error, 'reason', None),
                                           getattr(error, '__cause__', None),
                                           *getattr(error, 'args', ())) if isinstance(v, BaseException))
            raise TransportUncertain('Bale outcome uncertain') from None
        except (requests.exceptions.Timeout, requests.exceptions.RequestException):
            raise TransportUncertain('Bale outcome uncertain') from None
        except TransportRejected:
            raise
        except Exception:
            raise TransportUncertain('Bale outcome uncertain') from None
        return self._message_id(response)

    def message(self, recipient, text):
        return self._post('sendMessage', {'chat_id': recipient, 'text': text}, None, (15, 60))

    def photo(self, recipient, path, caption):
        with open(path, 'rb') as photo:
            return self._post('sendPhoto', {'chat_id': recipient, 'caption': caption},
                              {'photo': (Path(path).name, photo, 'image/png')}, (15, 90))

    def photo_memory(self, recipient, data, filename, caption):
        """Upload an operator-requested PNG directly from memory, without persistence."""
        if not isinstance(data, bytes) or not data.startswith(b'\x89PNG\r\n\x1a\n'):
            raise ValueError('PNG bytes required')
        return self._post('sendPhoto', {'chat_id': recipient, 'caption': caption},
                          {'photo': (filename, data, 'image/png')}, (15, 90))

    def close(self):
        self.session.close()

    def document(self, recipient, path, caption):
        with open(path, 'rb') as document:
            return self._post('sendDocument', {'chat_id': recipient, 'caption': caption},
                              {'document': (Path(path).name, document, 'application/pdf')}, (15, 120))

    def check_connection(self):
        try:
            response = self.session.get(f'https://tapi.bale.ai/bot{self.token}/getMe', timeout=(15, 30))
        except (requests.exceptions.RequestException, requests.exceptions.Timeout):
            raise TransportUnavailable('Bale connection check failed') from None
        except Exception:
            raise TransportUnavailable('Bale connection check failed') from None
        try:
            ok = response.status_code == 200 and response.json().get('ok')
        except Exception:
            raise TransportUnavailable('Bale connection check failed') from None
        if not ok:
            raise TransportUnavailable('Bale connection check failed')


def note_receipt(callback, recipient, status, message_id=None):
    if callback is not None:
        callback(str(recipient), status, None if message_id in (None, '') else str(message_id))


def closed_sets(closed):
    closed = closed or {}
    return set(closed.get('sent') or ()), set(closed.get('uncertain') or ())


def _fanout_result(sent, revoked, failed, uncertain, retryable):
    result = {'status': 'failed' if failed else ('succeeded' if sent else 'skipped'),
              'reason': 'delivery_failed' if failed else ('recipient_revoked' if not sent else ''),
              'sent_count': len(sent), 'revoked_count': len(revoked), 'failed_count': len(failed)}
    if uncertain:
        result['uncertain_recipients'] = list(uncertain)
    if retryable:
        result['retry_recipients'] = list(retryable)
    return result


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


def overflow(recipient, params, *, authorization=None, on_receipt=None, closed=None):
    validate_overflow(params)
    store = authorization if authorization is not None else AuthorizationStore()
    sent_closed, uncertain_closed = closed_sets(closed)

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
    if recipient in sent_closed:
        return None
    if recipient in uncertain_closed:
        return {'status': 'uncertain', 'reason': 'uncertain', 'uncertain_recipients': [recipient]}
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='overflow-', dir=runtime) as directory:
        expected_date = validate_date(params['date']) if params.get('date') else overflow_report_date()
        result = asyncio.run(build_report(expected_date, directory))
        denied = authorized()
        if denied:
            return denied
        if not result.get('ok'):
            reason = result.get('reason') or classify_source_message(result.get('message') or '')
            logger.warning('Overflow no send: %s', reason)
            if reason == 'invalid_source':
                return {'status': 'failed', 'reason': 'invalid_source'}
            if reason == 'date_missing':
                return notify_missing('گزارش سرریز روزانه', expected_date, result.get('latest_date'),
                                      lambda: (recipient,) if authorized() is None else (),
                                      on_receipt=on_receipt, closed=closed)
            return {'status': 'skipped', 'reason': reason}
        if not result.get('images'):
            raise RuntimeError('Overflow report contains no images')
        report = result['report']
        caption = overflow_report_caption(report['date'], expected_date)
        sender = BaleSender()
        delivered = 0
        message_ids = []
        try:
            sender.check_connection()
            for index, path in enumerate(result['images']):
                denied = authorized()
                if denied:
                    if delivered:
                        note_receipt(on_receipt, recipient, 'uncertain', ','.join(message_ids))
                        return {'status': 'uncertain', 'reason': 'uncertain', 'uncertain_recipients': [recipient]}
                    return denied
                if delivered == 0:
                    note_receipt(on_receipt, recipient, 'sending')
                try:
                    message_id = sender.photo(recipient, path, caption if index == 0 else '')
                except (TransportUnavailable, TransportRejected):
                    state = 'uncertain' if delivered else 'failed'
                    note_receipt(on_receipt, recipient, state, ','.join(message_ids))
                    if state == 'failed':
                        return {'status': 'failed', 'reason': 'transport_unavailable',
                                'retry_recipients': [recipient], 'sent_count': 0, 'failed_count': 1}
                    return {'status': 'uncertain', 'reason': 'uncertain',
                            'uncertain_recipients': [recipient], 'sent_count': 0, 'failed_count': 1}
                except Exception:
                    note_receipt(on_receipt, recipient, 'uncertain', ','.join(message_ids))
                    return {'status': 'uncertain', 'reason': 'uncertain',
                            'uncertain_recipients': [recipient], 'sent_count': 0, 'failed_count': 1}
                delivered += 1
                if message_id not in (None, ''):
                    message_ids.append(str(message_id))
            note_receipt(on_receipt, recipient, 'sent', ','.join(message_ids))
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


def driver_daily(recipient, params, *, authorization=None, on_receipt=None, closed=None):
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
    sent_closed, uncertain_closed = closed_sets(closed)
    if recipient in sent_closed:
        return None
    if recipient in uncertain_closed:
        return {'status': 'uncertain', 'reason': 'uncertain', 'uncertain_recipients': [recipient]}
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='driver-', dir=runtime) as directory:
        expected = validate_date(params['date']) if params.get('date') else overflow_report_date()
        result = build_driver_pdf(expected, directory, missing_result=True)
        denied = authorized()
        if denied:
            return denied
        if result['report']['date'] != expected:
            raise ValueError('Driver output date mismatch')
        if result['report'].get('sections') and all(c['status'] == 'date_missing' for c in result['report']['sections'].values()):
            return notify_missing('گزارش روزانه رانندگان', expected, result['report'].get('latest_date'),
                                  lambda: (recipient,) if authorized() is None else (),
                                  on_receipt=on_receipt, closed=closed)
        if result['report']['date'] != expected or len(result['documents']) != 2:
            raise ValueError('Driver output must contain two PDFs for the exact requested day')
        sender = BaleSender()
        delivered = 0
        message_ids = []
        try:
            sender.check_connection()
            for section, path in zip(SECTIONS, result['documents']):
                denied = authorized()
                if denied:
                    if delivered:
                        note_receipt(on_receipt, recipient, 'uncertain', ','.join(message_ids))
                        return {'status': 'uncertain', 'reason': 'uncertain', 'uncertain_recipients': [recipient]}
                    return denied
                content = result['report']['sections'][section]
                if delivered == 0:
                    note_receipt(on_receipt, recipient, 'sending')
                try:
                    message_id = sender.document(recipient, path, content['title'] + ' ' + expected)
                except (TransportUnavailable, TransportRejected):
                    state = 'uncertain' if delivered else 'failed'
                    note_receipt(on_receipt, recipient, state, ','.join(message_ids))
                    if state == 'failed':
                        return {'status': 'failed', 'reason': 'transport_unavailable',
                                'retry_recipients': [recipient], 'sent_count': 0, 'failed_count': 1}
                    return {'status': 'uncertain', 'reason': 'uncertain',
                            'uncertain_recipients': [recipient], 'sent_count': 0, 'failed_count': 1}
                except Exception:
                    note_receipt(on_receipt, recipient, 'uncertain', ','.join(message_ids))
                    return {'status': 'uncertain', 'reason': 'uncertain',
                            'uncertain_recipients': [recipient], 'sent_count': 0, 'failed_count': 1}
                delivered += 1
                if message_id not in (None, ''):
                    message_ids.append(str(message_id))
                logger.info('Driver daily section=%s date=%s status=%s', section, expected, content['status'])
            note_receipt(on_receipt, recipient, 'sent', ','.join(message_ids))
        finally:
            sender.close()


def _mechanical_push(params, *, capability, read_capability, pdf=False, authorization=None,
                    on_receipt=None, closed=None):
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
    sent_closed, uncertain_closed = closed_sets(closed)
    runtime=ROOT/'runtime/scheduler';runtime.mkdir(parents=True,exist_ok=True)
    sent=[];revoked=[];failed=[];uncertain=[];retryable=[]
    with tempfile.TemporaryDirectory(prefix='mechanical-',dir=runtime) as directory:
        expected=validate_date(params['date']) if params.get('date') else overflow_report_date()
        if pdf:
            result=build_driver_pdf(expected,directory,sections=('mechanical',),missing_result=True)
            if result['report']['date'] != expected:
                raise ValueError('Driver output date mismatch')
            if result['report']['sections']['mechanical']['status'] == 'date_missing':
                return notify_missing('گزارش روزانه معایب مکانیکی رانندگان', expected,
                                      result['report'].get('latest_date'), recipients,
                                      on_receipt=on_receipt, closed=closed)
            if (not result.get('ok') or result['report']['date']!=expected
                    or len(result['documents'])!=1):
                raise ValueError('One mechanical PDF for the exact requested day is required')
            paths=result['documents']
            caption=SECTIONS['mechanical']+' '+expected
        else:
            result=asyncio.run(build_report(expected,directory))
            if not result.get('ok'):
                reason=result.get('reason') or classify_source_message(result.get('message') or '')
                logger.warning('Mechanical overflow no send: %s', reason)
                if reason=='invalid_source':
                    return {'status':'failed','reason':'invalid_source'}
                if reason == 'date_missing':
                    return notify_missing('گزارش سرریز روزانه', expected, result.get('latest_date'),
                                          recipients, on_receipt=on_receipt, closed=closed)
                return {'status':'skipped','reason':reason}
            if not result.get('images'):
                raise RuntimeError('Mechanical overflow report unavailable')
            paths=result['images']
            caption=overflow_report_caption(result['report']['date'],expected)
        sender=None
        try:
            for user in initial:
                if user in sent_closed or user in uncertain_closed:
                    continue
                if user not in recipients():
                    revoked.append(user);note_receipt(on_receipt, user, 'revoked');continue
                if sender is None:
                    sender=BaleSender();sender.check_connection()
                delivered=0;message_ids=[]
                try:
                    note_receipt(on_receipt, user, 'sending')
                    for index,path in enumerate(paths):
                        if user not in recipients():
                            if delivered:
                                note_receipt(on_receipt, user, 'uncertain', ','.join(message_ids))
                                uncertain.append(user);failed.append(user)
                            else:
                                revoked.append(user);note_receipt(on_receipt, user, 'revoked')
                            break
                        message_id=(sender.document(user,path,caption) if pdf
                                    else sender.photo(user,path,caption if index==0 else ''))
                        delivered+=1
                        if message_id not in (None,''):message_ids.append(str(message_id))
                    else:
                        sent.append(user)
                        note_receipt(on_receipt, user, 'sent', ','.join(message_ids))
                except (TransportUnavailable, TransportRejected):
                    state='uncertain' if delivered else 'failed'
                    note_receipt(on_receipt, user, state, ','.join(message_ids))
                    failed.append(user)
                    (uncertain if state=='uncertain' else retryable).append(user)
                    logger.error('Mechanical delivery failed for one recipient capability=%s',capability)
                except Exception:
                    note_receipt(on_receipt, user, 'uncertain', ','.join(message_ids))
                    failed.append(user);uncertain.append(user)
                    logger.error('Mechanical delivery failed for one recipient capability=%s',capability)
        finally:
            if sender is not None:sender.close()
    return _fanout_result(sent, revoked, failed, uncertain, retryable)


def mechanical_overflow(recipient, params, *, authorization=None, on_receipt=None, closed=None):
    from tools.authorization import MECH_OVERFLOW_RECEIVE, OVERFLOW_READ
    return _mechanical_push(params,capability=MECH_OVERFLOW_RECEIVE,
                            read_capability=OVERFLOW_READ,authorization=authorization,
                            on_receipt=on_receipt,closed=closed)


def mechanical_driver_daily(recipient, params, *, authorization=None, on_receipt=None, closed=None):
    from tools.authorization import MECH_DRIVER_RECEIVE, DRIVER_REPORT_READ
    return _mechanical_push(params,capability=MECH_DRIVER_RECEIVE,
                            read_capability=DRIVER_REPORT_READ,pdf=True,authorization=authorization,
                            on_receipt=on_receipt,closed=closed)


def metalwork_driver_daily(recipient, params, *, authorization=None, on_receipt=None, closed=None):
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
    sent_closed, uncertain_closed = closed_sets(closed)
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    sent, revoked, failed, uncertain, retryable = [], [], [], [], []
    with tempfile.TemporaryDirectory(prefix='metalwork-', dir=runtime) as directory:
        expected = validate_date(params['date']) if params.get('date') else overflow_report_date()
        result = build_driver_pdf(expected, directory, sections=('metalwork',), missing_result=True)
        report = result['report']
        content = report['sections']['metalwork']
        if report['date'] != expected:
            raise ValueError('Driver output date mismatch')
        if content['status'] == 'date_missing':
            return notify_missing('گزارش روزانه معایب آهنگری رانندگان', expected, report.get('latest_date'),
                                  lambda: recipients().recipients, on_receipt=on_receipt, closed=closed)
        if not result.get('ok') or report['date'] != expected or len(result['documents']) != 1:
            raise ValueError('One metalwork PDF for the exact requested day is required')
        if content['status'] not in {'ready', 'no_defects'}:
            raise ValueError('Invalid metalwork report status')
        path = Path(result['documents'][0])
        if path.parent.resolve() != Path(directory).resolve() or path.name != f'driver-metalwork-{expected.replace("/", "-")}.pdf':
            raise ValueError('Unexpected metalwork output path')
        sender = None
        try:
            for user in initial:
                if user in sent_closed or user in uncertain_closed:
                    continue
                if user not in recipients().recipients:
                    revoked.append(user)
                    note_receipt(on_receipt, user, 'revoked')
                    continue
                if sender is None:
                    sender = BaleSender()
                    sender.check_connection()
                # Recheck after transport initialization, immediately before upload.
                if user not in recipients().recipients:
                    revoked.append(user)
                    note_receipt(on_receipt, user, 'revoked')
                    continue
                note_receipt(on_receipt, user, 'sending')
                try:
                    message_id = sender.document(user, path, SECTIONS['metalwork'] + ' ' + expected)
                    sent.append(user)
                    note_receipt(on_receipt, user, 'sent', None if message_id in (None, '') else str(message_id))
                except (TransportUnavailable, TransportRejected):
                    failed.append(user)
                    retryable.append(user)
                    note_receipt(on_receipt, user, 'failed')
                    logger.error('Metalwork delivery failed for one recipient')
                except Exception:
                    failed.append(user)
                    uncertain.append(user)
                    note_receipt(on_receipt, user, 'uncertain')
                    logger.error('Metalwork delivery failed for one recipient')
        finally:
            if sender is not None:
                sender.close()
    logger.info('Metalwork delivery date=%s sent=%s revoked=%s failed=%s', expected, len(sent), len(revoked), len(failed))
    return _fanout_result(sent, revoked, failed, uncertain, retryable)


TASKS = {'overflow': (validate_overflow, overflow), 'repairs': (validate_repairs, repairs),
         'driver_daily': (validate_overflow, driver_daily),
         'mechanical_overflow':(validate_overflow,mechanical_overflow),
         'mechanical_driver_daily':(validate_overflow,mechanical_driver_daily),
         'metalwork_driver_daily': (validate_overflow, metalwork_driver_daily)}
from tools.authorization import MECH_OVERFLOW_RECEIVE, MECH_DRIVER_RECEIVE, METALWORK_DRIVER_RECEIVE
MULTI_RECIPIENT_TASKS={'mechanical_overflow':MECH_OVERFLOW_RECEIVE,
                      'mechanical_driver_daily':MECH_DRIVER_RECEIVE,
                      'metalwork_driver_daily':METALWORK_DRIVER_RECEIVE}


# Independent receive-only report; existing report handlers stay unchanged.
from tools.authorization.maintenance import CAPABILITY as MAINTENANCE_DAILY_RECEIVE
from tools.fleet.maintenance_daily.delivery import validate_params as validate_maintenance_daily

def maintenance_daily_report(recipient, params, *, authorization=None, on_receipt=None, closed=None):
    from tools.fleet.maintenance_daily.delivery import deliver
    return deliver(recipient, params, authorization=authorization, on_receipt=on_receipt, closed=closed)

TASKS['maintenance_daily_report'] = (validate_maintenance_daily, maintenance_daily_report)
MULTI_RECIPIENT_TASKS['maintenance_daily_report'] = MAINTENANCE_DAILY_RECEIVE
