"""No business rules, authentication writes, or Telegram dependency live here."""
from __future__ import annotations

import json
import os
import re
import secrets
from dataclasses import dataclass
from pathlib import Path
from .lifecycle import KeyboardPolicy

PREFIX = 'ik:'
_NAME = re.compile(r'[a-z][a-z0-9_]{0,19}\Z')


@dataclass(frozen=True)
class Action:
    name: str
    label: str
    permission: str
    stages: frozenset[str]
    roles: frozenset[str] = frozenset()


class InlineKeyboardBuilder:
    def __init__(self, namespace, rows, *, policy=KeyboardPolicy.ONE_SHOT):
        if not _NAME.fullmatch(namespace):
            raise ValueError('Invalid namespace')
        self.namespace = namespace
        if not isinstance(policy, KeyboardPolicy):
            raise ValueError('Invalid keyboard lifecycle policy')
        self.policy = policy
        self.rows = tuple(tuple(row) for row in rows)
        self.actions = {a.name: a for row in self.rows for a in row}
        if len(self.actions) != sum(map(len, self.rows)):
            raise ValueError('Duplicate action')
        for action in self.actions.values():
            if not _NAME.fullmatch(action.name) or not action.label or not action.permission:
                raise ValueError('Invalid action')

    def allowed(self, action, *, stage, role, permits):
        return (stage in action.stages and
                (not action.roles or role in action.roles) and
                permits(action.permission))

    def build(self, revision, *, stage, role, permits):
        rows = []
        for row in self.rows:
            buttons = []
            for action in row:
                if self.allowed(action, stage=stage, role=role, permits=permits):
                    data = f'{PREFIX}{self.namespace}:{revision}:{action.name}'
                    if not 1 <= len(data.encode('utf-8')) <= 64:
                        raise ValueError('Callback data exceeds Bale limit')
                    buttons.append({'text': action.label, 'callback_data': data})
            if buttons:
                rows.append(buttons)
        return {'inline_keyboard': rows}

    def resolve(self, data, revision, *, stage, role, permits):
        parts = data.split(':')
        if len(parts) != 4 or parts[:2] != ['ik', self.namespace] or not revision or parts[2] != revision:
            raise ValueError('Expired or invalid keyboard')
        action = self.actions.get(parts[3])
        if action is None or not self.allowed(action, stage=stage, role=role, permits=permits):
            raise PermissionError('Action is unavailable')
        return action.name


class StateStore:
    """Atomic JSON snapshots; one gateway process owns each module file.

    Keep this under the application runtime directory, never in the fleet DB.
    The domain owns serialization, expiration and interrupted-job recovery.
    """
    def __init__(self, path):
        self.path = Path(path)

    def load(self):
        try:
            return json.loads(self.path.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return []

    def save(self, value):
        self.path.parent.mkdir(parents=True, exist_ok=True)
        temporary = self.path.with_name(self.path.name + '.' + secrets.token_hex(8) + '.tmp')
        try:
            with temporary.open('x', encoding='utf-8') as stream:
                json.dump(value, stream, ensure_ascii=False)
                stream.flush()
                os.fsync(stream.fileno())
            os.replace(temporary, self.path)
        finally:
            temporary.unlink(missing_ok=True)


class Router:
    def __init__(self):
        self.handlers = {}

    def register(self, namespace, handler):
        if not _NAME.fullmatch(namespace) or namespace in self.handlers:
            raise ValueError('Invalid or duplicate namespace')
        self.handlers[namespace] = handler

    def dispatch(self, event, gateway, *, send):
        raw = getattr(event, 'raw_message', None)
        if not isinstance(raw, dict) or raw.get('bale_inline_callback') is not True:
            return None
        data = raw.get('data', '')
        parts = data.split(':') if isinstance(data, str) else []
        handler = self.handlers.get(parts[1]) if len(parts) == 4 and parts[0] == 'ik' else None
        if handler:
            return handler(event, gateway, send=send)
        send(gateway, str(event.source.chat_id), 'این دکمه دیگر در دسترس نیست؛ منو را دوباره باز کنید.')
        return {'action': 'skip', 'reason': 'inline-unknown-action'}
