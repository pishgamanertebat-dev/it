"""Deterministic, capability-gated private Bale overflow report."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from tools.authorization import AuthorizationStore, MECH_OVERFLOW_RECEIVE
from .report import DEFAULT_SOURCE, HELP, ReportError, parse_command

ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)
AUTHORIZATION_VERSION = 1
MECHANICAL_ROLES_VERSION = 1


async def build_report(date, output_dir):
    command = [str(ROOT / '.venv/Scripts/python.exe'), '-E', '-s', '-B', '-X', 'utf8',
               '-m', 'tools.fleet.overflow.report', '--source', str(DEFAULT_SOURCE),
               '--output-dir', str(output_dir)]
    if date:
        command += ['--date', date]
    process = await asyncio.create_subprocess_exec(
        *command, cwd=str(ROOT), stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == 'nt' else 0)
    try:
        output, stderr = await asyncio.wait_for(process.communicate(), timeout=90)
        if process.returncode:
            logger.error('Overflow worker failed: %s', stderr.decode('utf-8', errors='replace')[-2000:])
            raise RuntimeError('Overflow worker failed')
        return json.loads(output.decode('utf-8'))
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class OverflowHandler:
    def __init__(self, worker=build_report, authorization=None):
        self.worker = worker
        self.authorization = authorization if authorization is not None else AuthorizationStore()
        self.tasks = set()
        self.busy = set()
        self.processed = {}
        self.limit = asyncio.Semaphore(2)

    def handle(self, event, gateway, *, send):
        source = event.source
        platform = str(getattr(source.platform, 'value', source.platform)).lower()
        if platform != 'bale' or getattr(source, 'chat_type', None) != 'dm':
            return None
        chat_id = str(getattr(source, 'chat_id', '') or '')
        if not chat_id:
            return None
        parse_error = None
        try:
            matched, date = parse_command(event.text or '')
        except ReportError as exc:
            matched, date, parse_error = True, None, str(exc)
        if not matched:
            return None
        user_id = str(getattr(source, 'user_id', '') or '')
        # This backend gate also protects direct calls, regardless of menu visibility
        # or dispatcher/admin shortcuts. Approval comes from persisted registration.
        if not self.authorization.can_read_overflow(user_id, chat_id, platform,
                                                     getattr(source, 'chat_type', None)):
            send(gateway, chat_id, 'دسترسی به گزارش سرریز مجاز نیست؛ ثبت و تأیید هویت و مجوز لازم است.')
            return {'action': 'skip', 'reason': 'overflow-denied'}
        if parse_error is not None:
            send(gateway, chat_id, parse_error)
            return {'action': 'skip', 'reason': 'overflow-invalid-date'}
        if date is None and self.authorization.has_capability(user_id, MECH_OVERFLOW_RECEIVE):
            from tools.scheduler.tasks import overflow_report_date
            date = overflow_report_date()
        now = time.monotonic()
        self.processed = {k: expiry for k, expiry in self.processed.items() if expiry > now}
        message_id = getattr(event, 'message_id', None)
        key = (chat_id, str(message_id)) if message_id is not None else None
        if key and key in self.processed:
            return {'action': 'skip', 'reason': 'overflow-duplicate'}
        if chat_id in self.busy or len(self.tasks) >= 20:
            send(gateway, chat_id, 'گزارش در حال آماده شدن است؛ کمی بعد دوباره تلاش کنید.')
            return {'action': 'skip', 'reason': 'overflow-busy'}
        if key:
            self.processed[key] = now + 600
        self.busy.add(chat_id)
        task = asyncio.get_running_loop().create_task(self.deliver(gateway, chat_id, date, user_id))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {'action': 'skip', 'reason': 'overflow-report'}

    async def deliver(self, gateway, chat_id, date, user_id):
        adapter = None
        try:
            adapter = next(a for p, a in gateway.adapters.items()
                           if str(getattr(p, 'value', p)).lower() == 'bale')
            async with self.limit:
                if not self.authorization.can_read_overflow(user_id, chat_id):
                    logger.warning('Overflow request denied before generation')
                    return
                runtime = ROOT / 'runtime/overflow'
                runtime.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='request-', dir=runtime) as directory:
                    assert Path(directory).resolve().parent == runtime.resolve()
                    result = await self.worker(date, directory)
                    if not self.authorization.can_read_overflow(user_id, chat_id):
                        logger.warning('Overflow permission revoked before delivery')
                        return
                    if not result.get('ok'):
                        await adapter.send(chat_id, result['message'])
                        return
                    report = result['report']
                    caption = f"سرریز روزانه {report['date']}\nتعداد ردیف: {len(report['rows'])}\n" + HELP
                    for index, path in enumerate(result['images']):
                        if not self.authorization.can_read_overflow(user_id, chat_id):
                            logger.warning('Overflow permission revoked during delivery')
                            return
                        with open(path, 'rb') as photo:
                            await adapter._bot.send_photo(chat_id=chat_id, photo=photo,
                                                          caption=caption if index == 0 else None)
        except Exception:
            logger.exception('Overflow report delivery failed')
            if adapter is not None:
                try:
                    await adapter.send(chat_id, 'دریافت یا ارسال گزارش سرریز ناموفق بود؛ لطفاً دوباره «سرریز» را بفرستید.')
                except Exception:
                    logger.exception('Overflow error reply failed')
        finally:
            self.busy.discard(chat_id)


_handler = OverflowHandler()


def handle_overflow_message(event, gateway, *, send):
    return _handler.handle(event, gateway, send=send)
