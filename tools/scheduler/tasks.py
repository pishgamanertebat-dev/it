"""Explicit task registry. No arbitrary commands/imports from YAML."""
from __future__ import annotations

import asyncio
import os
import tempfile
from pathlib import Path

from dotenv import dotenv_values
import requests

from tools.fleet.overflow.bale import build_report
from tools.fleet.overflow.report import HELP, validate_date

ROOT = Path(__file__).resolve().parents[2]


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

    def close(self):
        self.session.close()

    def check_connection(self):
        try:
            response = self.session.get(f'https://tapi.bale.ai/bot{self.token}/getMe', timeout=(15, 30))
            if response.status_code != 200 or not response.json().get('ok'):
                raise RuntimeError('Bale connection failed')
        except Exception:
            raise RuntimeError('Bale connection check failed') from None


def validate_overflow(params):
    if set(params) - {'date'}:
        raise ValueError('overflow only accepts params.date')
    if params.get('date') is not None:
        if not isinstance(params['date'], str):
            raise ValueError('params.date must be a quoted Jalali date')
        validate_date(params['date'])


def overflow(recipient, params):
    runtime = ROOT / 'runtime/scheduler'
    runtime.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix='overflow-', dir=runtime) as directory:
        result = asyncio.run(build_report(params.get('date'), directory))
        if not result.get('ok'):
            raise RuntimeError(result.get('message', 'Overflow report unavailable'))
        if not result.get('images'):
            raise RuntimeError('Overflow report contains no images')
        report = result['report']
        caption = f"سرریز روزانه {report['date']}\nتعداد ردیف: {len(report['rows'])}\n" + HELP
        sender = BaleSender()
        try:
            for index, path in enumerate(result['images']):
                sender.photo(recipient, path, caption if index == 0 else '')
        finally:
            sender.close()


TASKS = {'overflow': (validate_overflow, overflow)}
