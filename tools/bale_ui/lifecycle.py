"""Shared keyboard consumption, retirement and transition ordering."""
from __future__ import annotations

import asyncio
import inspect
import logging
import time
from enum import Enum

logger = logging.getLogger(__name__)


class KeyboardPolicy(Enum):
    ONE_SHOT = 'one_shot'
    REUSABLE = 'reusable'


class KeyboardLifecycle:
    def __init__(self):
        self.tasks = set()
        self.pending = set()
        self.messages = {}
        self.timers = {}
        self.bindings = {}
        self.accepted = {}

    @staticmethod
    def bot(gateway):
        for platform, adapter in getattr(gateway, 'adapters', {}).items():
            if str(getattr(platform, 'value', platform)).lower() == 'bale':
                return getattr(adapter, '_bot', None)
        return None

    def spawn(self, coroutine):
        task = asyncio.get_running_loop().create_task(coroutine)
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    async def remove(self, gateway, chat_id, message_id):
        if not message_id:
            return
        bot = self.bot(gateway)
        if bot is None:
            raise RuntimeError('Bale keyboard transport unavailable')
        for attempt in range(3):
            try:
                await bot.edit_message_reply_markup(chat_id=str(chat_id),
                    message_id=int(message_id), reply_markup=None)
                return
            except Exception as exc:
                # These outcomes already satisfy retirement (including a retry
                # after the first request succeeded but its response was lost).
                message = str(exc).lower()
                if 'message is not modified' in message or 'message to edit not found' in message:
                    return
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))

    async def retire(self, scope, gateway):
        reference = self.messages.get(scope)
        if reference is None:
            return
        await self.remove(gateway, *reference)
        if self.messages.get(scope) == reference:
            self.messages.pop(scope, None)
            self.bindings.pop(scope, None)
            timer = self.timers.pop(scope, None)
            if timer:
                timer.cancel()

    async def update_message(self, gateway, chat_id, message_id, text, markup):
        """Refresh a stateful selection in place; caller persists the revision first."""
        from telegram import InlineKeyboardMarkup
        bot = self.bot(gateway)
        if bot is None:
            raise RuntimeError('Bale keyboard transport unavailable')
        for attempt in range(3):
            try:
                await bot.edit_message_text(chat_id=str(chat_id), message_id=int(message_id),
                    text=text, parse_mode=None, reply_markup=InlineKeyboardMarkup.de_json(markup, bot))
                return
            except Exception as exc:
                if 'message is not modified' in str(exc).lower():
                    return
                if attempt == 2:
                    raise
                await asyncio.sleep(0.2 * (attempt + 1))

    def bind(self, scope, gateway, chat_id, message_id, *, ttl, policy=KeyboardPolicy.ONE_SHOT):
        """Call after sending markup; scope includes domain, user and chat.

        Call retire before replacing a keyboard. Reusable menus retain their
        markup and revision until the owning flow explicitly retires them.
        """
        if not message_id:
            return
        self.messages[scope] = (str(chat_id), str(message_id))
        binding = self.bindings[scope] = object()
        old = self.timers.pop(scope, None)
        if old:
            old.cancel()
        if policy is KeyboardPolicy.ONE_SHOT:
            reference = self.messages[scope]
            async def expire():
                try:
                    if self.bindings.get(scope) is not binding:
                        return
                    if scope in self.pending:
                        self.timers[scope] = asyncio.get_running_loop().call_later(
                            0.25, lambda: self.spawn(expire()))
                        return
                    if self.messages.get(scope) == reference:
                        await self.retire(scope, gateway)
                except Exception:
                    logger.exception('Could not retire expired keyboard')
            self.timers[scope] = asyncio.get_running_loop().call_later(
                max(0, ttl), lambda: self.spawn(expire()))

    def accept(self, builder, data, revision, *, stage, role, permits,
               consume, persist, scope, event, gateway, execute, failed):
        """Validate and reserve synchronously; remove markup before execution.

        consume clears the domain's revision; persist checkpoints it. Neither
        UI removal nor domain execution is attempted for rejected callbacks.
        """
        if scope in self.pending:
            return {'action': 'skip', 'reason': 'inline-transition-pending'}
        now = time.monotonic()
        self.accepted = {key: expiry for key, expiry in self.accepted.items() if expiry > now}
        callback_id = getattr(event, 'message_id', None)
        delivery = (scope, str(callback_id)) if callback_id is not None else None
        if delivery in self.accepted:
            return {'action': 'skip', 'reason': 'inline-duplicate-callback'}
        action = builder.resolve(data, revision, stage=stage, role=role, permits=permits)
        if builder.policy is KeyboardPolicy.ONE_SHOT:
            consume()
            persist()
        if delivery:
            self.accepted[delivery] = now + 600
        raw = event.raw_message
        origin = raw.get('origin_message_id')
        # Direct, transport-free domain invocations have no message to retire.
        if not origin:
            return execute(action)
        self.pending.add(scope)

        async def transition():
            try:
                if builder.policy is KeyboardPolicy.ONE_SHOT and not builder.actions[action].refresh:
                    await self.remove(gateway, event.source.chat_id, origin)
                    if self.messages.get(scope) == (str(event.source.chat_id), str(origin)):
                        self.messages.pop(scope, None)
                        self.bindings.pop(scope, None)
                        timer = self.timers.pop(scope, None)
                        if timer:
                            timer.cancel()
                result = execute(action)
                if inspect.isawaitable(result):
                    await result
            except Exception:
                logger.exception('Inline keyboard transition failed')
                failed()
            finally:
                self.pending.discard(scope)

        self.spawn(transition())
        return {'action': 'skip', 'reason': 'inline-transition'}
