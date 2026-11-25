"""Authorize the scoped Bale request in Gateway and use the project Python for Excel."""
import asyncio
from contextlib import suppress
from datetime import datetime, timezone
import json
import re
import subprocess

from . import maintenance_history_scope as scope


PROJECT_PYTHON = scope.ROOT / '.venv/Scripts/python.exe'
WORKER = 'tools.fleet.repairs.maintenance_owner_recovery'


def approval_for_event(event, owner_allowed):
    peer = event.source
    platform = getattr(peer.platform, 'value', peer.platform)
    raw = getattr(event, 'raw_message', None)
    ignored = ('forward_date', 'forward_origin', 'forward_from', 'edit_date', 'bale_inline_callback')
    modified = any(raw.get(key) if isinstance(raw, dict) else getattr(raw, key, None) for key in ignored)
    message = str(event.message_id or '')
    pin = scope.LIVE_SCOPE if event.text == scope.command(scope.LIVE_SCOPE) else scope.SCOPE_SHA256
    if (platform != 'bale' or peer.chat_type != 'dm' or str(peer.user_id) != scope.OWNER
            or str(peer.chat_id) != scope.OWNER or event.text != scope.command(pin) or modified
            or not re.fullmatch(r'[1-9][0-9]*', message) or not owner_allowed()):
        raise PermissionError('HISTORICAL_OWNER_APPROVAL_REQUIRED')
    return dict(platform='bale', owner=scope.OWNER, chat=scope.OWNER, message_id=message,
                received_at=datetime.now(timezone.utc).isoformat(), scope_sha256=pin, text=event.text)


async def run_worker(approval, *, owner_allowed):
    """Answer the Writer's in-flight permission checks over its inherited pipes."""
    if not owner_allowed():
        raise PermissionError('HISTORICAL_OWNER_APPROVAL_REQUIRED')
    process = await asyncio.create_subprocess_exec(str(PROJECT_PYTHON), '-E', '-s', '-B', '-X', 'utf8', '-m', WORKER,
        cwd=str(scope.ROOT), stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE, creationflags=subprocess.CREATE_NO_WINDOW)
    errors = asyncio.create_task(process.stderr.read())

    async def send(message):
        process.stdin.write((json.dumps(message) + '\n').encode('utf-8'))
        await process.stdin.drain()

    async def exchange():
        await send({'approval': approval})
        while line := await process.stdout.readline():
            message = json.loads(line)
            if message == {'authorize': True}:
                await send({'allowed': bool(owner_allowed())})
            elif isinstance(message, dict) and set(message) == {'receipts'}:
                if await process.wait() != 0:
                    break
                return message['receipts']
            else:
                break
        await process.wait()
        raise RuntimeError('Historical maintenance worker failed: ' + (await errors).decode('utf8', 'replace')[-1500:])

    try:
        return await asyncio.wait_for(exchange(), timeout=360 if approval['scope_sha256'] == scope.LIVE_SCOPE else 120)
    finally:
        process.stdin.close()
        if process.returncode is None:
            with suppress(ProcessLookupError):
                process.kill()
            await process.wait()
        if not errors.done():
            errors.cancel()
