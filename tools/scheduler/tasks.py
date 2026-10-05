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


TASKS = {'overflow': (validate_overflow, overflow), 'repairs': (validate_repairs, repairs),
         'driver_daily': (validate_overflow, driver_daily)}
