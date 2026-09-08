"""Deterministic Bale report handler; public access to this report only."""
from __future__ import annotations

import asyncio
import json
import logging
import os
import subprocess
import tempfile
import time
from pathlib import Path

from .report import HELP, ReportError, parse_command

ROOT = Path(__file__).resolve().parents[3]
logger = logging.getLogger(__name__)


async def build_report(date, output_dir):
    command = [str(ROOT / '.venv/Scripts/python.exe'), '-E', '-s', '-B', '-X', 'utf8',
               '-m', 'tools.fleet.overflow.report', '--output-dir', str(output_dir)]
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
    def __init__(self, worker=build_report):
        self.worker = worker
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
        try:
            matched, date = parse_command(event.text or '')
        except ReportError as exc:
            send(gateway, chat_id, str(exc))
            return {'action': 'skip', 'reason': 'overflow-invalid-date'}
        if not matched:
            return None
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
        task = asyncio.get_running_loop().create_task(self.deliver(gateway, chat_id, date))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {'action': 'skip', 'reason': 'overflow-report'}

    async def deliver(self, gateway, chat_id, date):
        adapter = None
        try:
            adapter = next(a for p, a in gateway.adapters.items()
                           if str(getattr(p, 'value', p)).lower() == 'bale')
            async with self.limit:
                runtime = ROOT / 'runtime/overflow'
                runtime.mkdir(parents=True, exist_ok=True)
                with tempfile.TemporaryDirectory(prefix='request-', dir=runtime) as directory:
                    assert Path(directory).resolve().parent == runtime.resolve()
                    result = await self.worker(date, directory)
                    if not result.get('ok'):
                        await adapter.send(chat_id, result['message'])
                        return
                    report = result['report']
                    caption = f"سرریز روزانه {report['date']}\nتعداد ردیف: {len(report['rows'])}\n" + HELP
                    for index, path in enumerate(result['images']):
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
