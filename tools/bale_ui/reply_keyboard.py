"""Reusable Bale reply-keyboard (ReplyKeyboardMarkup) primitives.

This layer owns markup shape, menu lookup and delivery bookkeeping only.
Audience mapping comes from configuration, never from constants here, and no
business rule, permission write or domain flow belongs in this module.

Reply keyboards are a second, independent UI layer: they never produce
callback_data and never touch the inline-keyboard lifecycle. A button only
injects its command as ordinary user text.
"""
from __future__ import annotations

import asyncio
import hashlib
import json
import logging
import re
from dataclasses import dataclass
from pathlib import Path

from .lifecycle import KeyboardLifecycle

logger = logging.getLogger(__name__)

REMOVE_MARKUP = {'remove_keyboard': True}
# Flags newer than some Bale API / client builds; dropped one by one on TypeError.
OPTIONAL_FLAGS = ('is_persistent',)
_MENU_ID = re.compile(r'[a-z][a-z0-9_]{0,31}\Z')
_TRANSIENT_ERROR = re.compile(
    r'timed?\s*out|timeout|network|connection|retryafter|retry.after|'
    r'rate.?limit|too many requests|flood',
    re.I,
)


def _persistent_flag_unsupported(error) -> bool:
    """True only when the client or API explicitly rejects is_persistent."""
    text = f'{type(error).__name__}: {error}'
    if _TRANSIENT_ERROR.search(text):
        return False
    return 'is_persistent' in text.lower()


def normalize(text) -> str:
    return ' '.join(str(text or '').translate(str.maketrans('كي', 'کی')).replace('\u200c', ' ').split())


@dataclass(frozen=True)
class ReplyButton:
    """Display label and the command text the router must finally see."""
    text: str
    command: str

    def __post_init__(self):
        if not normalize(self.text) or not normalize(self.command):
            raise ValueError('Reply button needs both a label and a command')


@dataclass(frozen=True)
class ReplyMenu:
    menu_id: str
    rows: tuple[tuple[ReplyButton, ...], ...]
    users: frozenset[str] = frozenset()
    roles: frozenset[str] = frozenset()
    resize_keyboard: bool = True
    persistent: bool = True
    one_time_keyboard: bool = False

    def __post_init__(self):
        if not _MENU_ID.fullmatch(self.menu_id):
            raise ValueError('Invalid menu id')
        if not self.rows or not all(self.rows):
            raise ValueError('Reply menu needs at least one button')
        labels = [normalize(button.text) for button in self.buttons]
        if len(set(labels)) != len(labels):
            raise ValueError('Duplicate reply button label')

    @property
    def buttons(self) -> tuple[ReplyButton, ...]:
        return tuple(button for row in self.rows for button in row)

    def serves(self, user_id, role=None) -> bool:
        """Audience comes from configuration; an empty menu serves nobody."""
        return (bool(user_id) and str(user_id) in self.users) or (bool(role) and str(role) in self.roles)

    def command_for(self, text) -> str | None:
        """Map a tapped label — or the manually typed command — to the command."""
        clean = normalize(text)
        for button in self.buttons:
            if clean in {normalize(button.text), normalize(button.command)}:
                return normalize(button.command)
        return None

    def to_markup(self, *, persistent_supported=True) -> dict:
        markup = {
            'keyboard': [[{'text': button.text} for button in row] for row in self.rows],
            'resize_keyboard': bool(self.resize_keyboard),
            'one_time_keyboard': bool(self.one_time_keyboard),
        }
        if self.persistent and persistent_supported:
            markup['is_persistent'] = True
        return markup

    def fingerprint(self) -> str:
        payload = json.dumps([self.menu_id, self.to_markup()], ensure_ascii=False, sort_keys=True)
        return hashlib.sha256(payload.encode('utf-8')).hexdigest()[:16]


class ReplyMenuRegistry:
    def __init__(self, menus=(), *, triggers=()):
        self.menus: dict[str, ReplyMenu] = {}
        self.triggers = frozenset(normalize(item) for item in triggers if normalize(item))
        for menu in menus:
            self.add(menu)

    def add(self, menu: ReplyMenu) -> ReplyMenu:
        if menu.menu_id in self.menus:
            raise ValueError('Duplicate reply menu id')
        self.menus[menu.menu_id] = menu
        return menu

    def get(self, menu_id) -> ReplyMenu | None:
        return self.menus.get(menu_id)

    def menu_for(self, user_id, role=None) -> ReplyMenu | None:
        for menu in self.menus.values():
            if menu.serves(user_id, role):
                return menu
        return None

    def triggered(self, text) -> bool:
        return normalize(text) in self.triggers


def load_registry(path) -> ReplyMenuRegistry:
    """Read the menu-to-audience mapping; a missing or broken file yields no menu."""
    try:
        config = json.loads(Path(path).read_text(encoding='utf-8'))
    except (OSError, ValueError):
        logger.exception('Could not read reply-menu configuration')
        return ReplyMenuRegistry()
    registry = ReplyMenuRegistry(triggers=config.get('triggers', ()))
    for entry in config.get('menus', ()):
        try:
            registry.add(ReplyMenu(
                menu_id=entry['menu_id'],
                rows=tuple(tuple(ReplyButton(button['text'], button['command']) for button in row)
                           for row in entry['rows']),
                users=frozenset(str(user) for user in entry.get('users', ())),
                roles=frozenset(str(role) for role in entry.get('roles', ())),
                resize_keyboard=entry.get('resize_keyboard', True),
                persistent=entry.get('persistent', True),
                one_time_keyboard=entry.get('one_time_keyboard', False),
            ))
        except (KeyError, TypeError, ValueError):
            logger.exception('Ignoring invalid reply-menu definition')
    return registry


def to_reply_markup(markup, bot=None):
    """Build the client object, degrading to a plain keyboard when a flag is unknown."""
    from telegram import KeyboardButton, ReplyKeyboardMarkup, ReplyKeyboardRemove
    if markup.get('remove_keyboard'):
        return ReplyKeyboardRemove()
    rows = [[KeyboardButton(button['text']) for button in row] for row in markup['keyboard']]
    options = {name: value for name, value in markup.items() if name != 'keyboard'}
    while True:
        try:
            return ReplyKeyboardMarkup(rows, **options)
        except TypeError:
            dropped = next((name for name in OPTIONAL_FLAGS if name in options), None)
            if dropped is None:
                raise
            logger.warning('Reply keyboard flag %s unsupported by this client; falling back', dropped)
            options.pop(dropped)


class ReplyMenuPresenter:
    """Delivers a menu once per user and markup revision, or on explicit request.

    A reply keyboard lives in the Bale client, so one successful delivery keeps
    the menu across gateway restarts. The stored fingerprint prevents repeated
    delivery and forces a refresh only when the menu definition changes.
    """

    def __init__(self, registry: ReplyMenuRegistry, *, state_store=None, bot_for=None, text=''):
        self.registry = registry
        self.store = state_store
        self.bot_for = bot_for or KeyboardLifecycle.bot
        self.text = text
        self.delivered: dict[tuple[str, str], str] = {}
        self.pending: set[tuple[str, str]] = set()
        self.tasks: set = set()
        if state_store:
            try:
                self.delivered = {tuple(key): value for key, value in state_store.load()}
            except (OSError, TypeError, ValueError):
                logger.exception('Could not restore reply-menu delivery state')

    def persist(self):
        if self.store:
            try:
                self.store.save([[list(key), value] for key, value in self.delivered.items()])
            except OSError:
                logger.exception('Could not persist reply-menu delivery state')

    def spawn(self, coroutine):
        try:
            task = asyncio.get_running_loop().create_task(coroutine)
        except RuntimeError:
            coroutine.close()
            return None
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return task

    def present(self, gateway, chat_id, user_id, menu, *, send=None, force=False, text=None):
        key = (str(user_id), str(chat_id))
        fingerprint = menu.fingerprint()
        if key in self.pending or (not force and self.delivered.get(key) == fingerprint):
            return None
        message = text if text is not None else self.text
        bot = self.bot_for(gateway)
        if bot is None:
            if force and send:
                send(gateway, str(chat_id), message)
            return None
        self.pending.add(key)

        async def deliver():
            try:
                # A server that rejects the persistent flag still gets a plain
                # resizable keyboard rather than no menu at all.
                for supported in (True, False) if menu.persistent else (False,):
                    try:
                        await bot.send_message(chat_id=str(chat_id), text=message, parse_mode=None,
                            reply_markup=to_reply_markup(menu.to_markup(persistent_supported=supported), bot))
                    except Exception as error:
                        if supported and _persistent_flag_unsupported(error):
                            logger.warning('Persistent reply menu %s rejected; retrying without the flag',
                                           menu.menu_id, exc_info=True)
                            continue
                        raise
                    self.delivered[key] = fingerprint
                    self.persist()
                    return
            except Exception:
                logger.exception('Could not deliver Bale reply menu %s', menu.menu_id)
            finally:
                self.pending.discard(key)

        task = self.spawn(deliver())
        if task is None:
            self.pending.discard(key)
        return task

    def remove(self, gateway, chat_id, user_id, *, text, send=None):
        """Retire a reply menu so a role's menu can be replaced or revoked."""
        key = (str(user_id), str(chat_id))
        bot = self.bot_for(gateway)
        if bot is None:
            if send:
                send(gateway, str(chat_id), text)
            return None
        if key in self.pending:
            return None
        self.pending.add(key)

        async def retire():
            try:
                await bot.send_message(chat_id=str(chat_id), text=text, parse_mode=None,
                                       reply_markup=to_reply_markup(REMOVE_MARKUP, bot))
                self.delivered.pop(key, None)
                self.persist()
            except Exception:
                logger.exception('Could not remove Bale reply menu')
            finally:
                self.pending.discard(key)

        task = self.spawn(retire())
        if task is None:
            self.pending.discard(key)
        return task
