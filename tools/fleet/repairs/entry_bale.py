"""Deterministic daily-defect form using the shared Bale inline UI."""
from __future__ import annotations

import asyncio
import json
import logging
from pathlib import Path
import secrets
import subprocess
import time
import uuid

from tools.bale_ui import Action, InlineKeyboardBuilder, KeyboardLifecycle, StateStore

ROOT = Path(__file__).resolve().parents[3]
CONFIG = ROOT / 'settings/repairs_entry.json'
logger = logging.getLogger(__name__)
LABELS = {'mechanical': 'شرح معایب مکانیکی', 'metalwork': 'شرح معایب آهنگری'}


def normalized(text):
    return ' '.join(str(text or '').translate(str.maketrans('كي', 'کی')).replace('\u200c', ' ').split())


def permitted(actor):
    try:
        return bool(actor) and str(actor) in json.loads(CONFIG.read_text(encoding='utf-8'))['allowed_users']
    except (OSError, ValueError, KeyError):
        return False


def keyboard(stage):
    def button(name, label):
        return Action(name, label, 'repairs.edit', frozenset({stage}))
    if stage == 'SECTION':
        rows = [(button(s, label),) for s, label in LABELS.items()]
    elif stage == 'CONFIRM':
        rows = [(button('confirm', '✅ تأیید و ذخیره'), button('edit', '✏️ ویرایش متن'))]
    elif stage == 'DESCRIPTION':
        rows = [(button('clear', 'پاک‌کردن شرح این بخش'),)]
    else:
        rows = [(button('sections', 'انتخاب بخش دیگر'),)]
    rows.append((button('finish', 'انصراف'),))
    return InlineKeyboardBuilder('repairs_entry', rows)


async def worker(payload, module='tools.fleet.repairs.entry_service'):
    process = await asyncio.create_subprocess_exec(str(ROOT / '.venv/Scripts/python.exe'),
        '-E', '-s', '-B', '-X', 'utf8', '-m', module, cwd=str(ROOT),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW)
    try:
        output, error = await asyncio.wait_for(process.communicate(json.dumps(payload).encode()), 120)
        if process.returncode:
            logger.error('Repairs entry worker failed: %s', error.decode('utf-8', errors='replace')[-2000:])
            raise RuntimeError('Entry worker failed')
        return json.loads(output.decode('utf-8'))
    finally:
        if process.returncode is None:
            process.kill()
            await process.wait()


class RepairsEntryHandler:
    entry_command = 'شرح خرابی'
    namespace = 'repairs_entry'
    initial_stage = 'CODE'
    keyboard = staticmethod(keyboard)
    commands = {'شرح خرابی': 'entry', 'پایان': 'finish', 'انصراف': 'finish', 'لغو': 'finish',
                '/cancel': 'finish', 'تایید': 'confirm', 'تأیید': 'confirm', 'ویرایش': 'edit',
                'شرح معایب مکانیکی': 'mechanical', 'شرح معایب آهنگری': 'metalwork', 'شرح معایب اهنگری': 'metalwork'}
    exit_commands = {'حکم کار', 'تعمیرات'}

    def __init__(self, *, state_store=None, run=worker, authorize=permitted, clock=time.time):
        self.store = state_store
        self.run = run
        self.authorize = authorize
        self.clock = clock
        self.sessions = {}
        self.busy = set()
        self.tasks = set()
        self.lifecycle = KeyboardLifecycle()
        self._lifecycle_restored = False
        self._expired_keyboards = []
        self.seen = {}
        if state_store:
            try:
                saved = state_store.load()
            except (ValueError, OSError):
                logger.exception('Could not restore repairs entry forms')
                saved = []
            for key, session in saved:
                if session.get('stage') == 'BUSY':
                    session['stage'] = 'CONFIRM' if session.get('request') else self.initial_stage
                    session['revision'] = ''
                    session['expires'] = self.clock() + 1800
                if session.get('expires', 0) > self.clock():
                    self.sessions[tuple(key)] = session
                elif session.get('message_id'):
                    self._expired_keyboards.append((tuple(key), session['message_id']))

    def restore_keyboards(self, gateway):
        # The transport and running event loop are available on first dispatch,
        # not during construction. Restore all saved forms, including idle users.
        if self._lifecycle_restored or self.lifecycle.bot(gateway) is None:
            return
        for key, message_id in self._expired_keyboards:
            self.lifecycle.bind(key, gateway, key[1], message_id, ttl=0)
        self._expired_keyboards.clear()
        for key, session in self.sessions.items():
            ttl = max(0, session['expires'] - self.clock()) if session.get('revision') else 0
            self.lifecycle.bind(key, gateway, key[1], session.get('message_id'), ttl=ttl)
        self._lifecycle_restored = True

    def persist(self):
        if self.store:
            self.store.save([[list(k), v] for k, v in self.sessions.items()])

    def active_for(self, actor, chat):
        key = (str(actor), str(chat))
        session = self.sessions.get(key)
        return key in self.busy or bool(session and session.get('expires', 0) > self.clock())

    async def reply(self, key, gateway, send, text, *, replace=False):
        session = self.sessions.get(key)
        bot = self.lifecycle.bot(gateway)
        previous = session.get('message_id') if session else None
        if replace and previous:
            try:
                await self.lifecycle.delete(gateway, key[1], previous)
            except Exception:
                logger.warning('Could not delete repairs prompt', exc_info=True)
                await self.lifecycle.retire(key, gateway)
            self.lifecycle.forget(key, (str(key[1]), str(previous)))
            if session.get('message_id') == previous:
                session['message_id'] = ''
        else:
            await self.lifecycle.retire(key, gateway)
        if bot is None:
            send(gateway, key[1], text)
            return
        markup = None
        if session and session['stage'] != 'BUSY':
            session['revision'] = secrets.token_hex(8)
            session['expires'] = self.clock() + 1800
            markup = self.keyboard(session['stage']).build(session['revision'], stage=session['stage'],
                        role='', permits=lambda p: p == 'repairs.edit' and self.authorize(key[0]))
        self.persist()
        kwargs = {}
        if markup:
            from telegram import InlineKeyboardMarkup
            kwargs['reply_markup'] = InlineKeyboardMarkup.de_json(markup, bot)
        message = await bot.send_message(chat_id=key[1], text=text, parse_mode=None, **kwargs)
        if markup:
            session['message_id'] = str(message.message_id)
            self.lifecycle.bind(key, gateway, key[1], message.message_id, ttl=1800)
            self.persist()

    def start_task(self, key, coroutine, gateway, send):
        self.busy.add(key)
        async def execute():
            try:
                await coroutine
            except Exception:
                logger.exception('Repairs entry interaction failed')
                session = self.sessions.get(key)
                if session and session['stage'] == 'BUSY':
                    session['stage'] = 'CONFIRM' if session.get('request') else self.initial_stage
                self.persist()
                send(gateway, key[1], f'نتیجهٔ عملیات روشن نیست؛ برای پیگیری همان ثبت «تأیید» را دوباره بفرستید. برای شروع تازه «{self.entry_command}» را بنویسید.')
            finally:
                self.busy.discard(key)
        task = asyncio.get_running_loop().create_task(execute())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    async def advance(self, key, command, text, gateway, send):
        if not self.authorize(key[0]):
            self.sessions.pop(key, None)
            self.persist()
            await self.reply(key, gateway, send, f'اجازهٔ ثبت {self.entry_command} را ندارید.')
            return
        session = self.sessions.get(key)
        if command in {'entry', 'sections'}:
            self.sessions[key] = {'stage': 'SECTION', 'expires': self.clock()+1800, 'revision': ''}
            self.persist()
            await self.reply(key, gateway, send, 'شرح خرابی را برای کدام بخش ثبت می‌کنید؟\nپس از انتخاب بخش، کد دستگاه را وارد کنید.')
            return
        if command == 'finish':
            self.sessions.pop(key, None)
            self.persist()
            await self.reply(key, gateway, send, 'ثبت شرح خرابی پایان یافت. فقط موارد تأییدشده ذخیره شده‌اند.\nبرای ادامه یا ویرایش، «شرح خرابی» را بنویسید.')
            return
        if not session:
            send(gateway, key[1], 'فرم منقضی شده است؛ «شرح خرابی» را دوباره بنویسید.')
            return
        if session['stage'] == 'SECTION':
            if command not in LABELS:
                await self.reply(key, gateway, send, 'شرح معایب مکانیکی یا شرح معایب آهنگری را انتخاب کنید.')
                return
            session.update(stage='CODE', section=command)
            self.persist()
            await self.reply(key, gateway, send, LABELS[command] + '\nکد دستگاه را وارد کنید.\nبرای ویرایش، کد همان دستگاه را دوباره وارد کنید.', replace=True)
        elif session['stage'] == 'CODE':
            session['stage'] = 'BUSY'
            self.persist()
            result = await self.run({'action': 'preview', 'actor': key[0], 'code': text, 'section': session['section']})
            session['stage'] = 'CODE'
            if result['ok']:
                session['selection'] = result['result']
                session['stage'] = 'DESCRIPTION'
                selected = session['selection']
                message = f"{selected['name']} — {selected['code']}\nتاریخ: {selected['date']}\n{LABELS[session['section']]}\n"
                if selected['expected']:
                    message += 'شرح فعلی:\n' + selected['expected'] + '\n\nمتن کامل جایگزین را وارد کنید.'
                else:
                    message += 'شرح خرابی را وارد کنید.'
            else:
                message = result['message']
            self.persist()
            await self.reply(key, gateway, send, message, replace=True)
        elif session['stage'] == 'DESCRIPTION':
            description = '' if command == 'clear' else text.strip()
            if (not description and command != 'clear') or len(description) > 1800 or '\x00' in description:
                await self.reply(key, gateway, send, 'شرح را در یک پیام، حداکثر ۱۸۰۰ نویسه وارد کنید.')
                return
            selected = session['selection']
            session['request'] = {**selected, 'description': description, 'operation': uuid.uuid4().hex}
            session['stage'] = 'CONFIRM'
            self.persist()
            await self.reply(key, gateway, send,
                f"تأیید ثبت برای {selected['name']} — {selected['code']}\nتاریخ: {selected['date']}\n{LABELS[session['section']]}\n\n"
                + (description or 'شرح این بخش پاک شود.') + '\n\nاین متن جایگزین شرح همین بخش می‌شود. تأیید می‌کنید؟')
        elif session['stage'] == 'CONFIRM':
            if command == 'edit':
                session['stage'] = 'DESCRIPTION'
                session.pop('request', None)
                self.persist()
                await self.reply(key, gateway, send, 'متن کامل اصلاح‌شده را وارد کنید.')
            elif command == 'confirm':
                session['stage'] = 'BUSY'
                self.persist()
                result = await self.run({'action': 'commit', 'actor': key[0], 'request': session['request']})
                if result['ok']:
                    saved = result['result']
                    session.update(stage='CODE')
                    session.pop('request', None)
                    session.pop('selection', None)
                    message = f"✅ شرح {saved['code']} در تاریخ {saved['date']} ذخیره شد.\nکد دستگاه بعدی را وارد کنید؛ برای ویرایش نیز کد همان دستگاه را بفرستید."
                else:
                    session['stage'] = 'CONFIRM'
                    message = result['message']
                self.persist()
                await self.reply(key, gateway, send, message)
            else:
                await self.reply(key, gateway, send, 'برای ذخیره، تأیید را بزنید؛ یا ویرایش متن / انصراف را انتخاب کنید.')

    def handle(self, event, gateway, *, send):
        source = event.source
        if str(getattr(source.platform, 'value', source.platform)).lower() != 'bale' or source.chat_type != 'dm':
            return None
        self.restore_keyboards(gateway)
        actor = str(getattr(source, 'user_id', '') or '')
        chat = str(getattr(source, 'chat_id', '') or '')
        key = (actor, chat)
        text = event.text or ''
        clean = normalized(text)
        raw = getattr(event, 'raw_message', None)
        callback = isinstance(raw, dict) and raw.get('bale_inline_callback') is True
        if callback and not str(raw.get('data', '')).startswith(f'ik:{self.namespace}:'):
            return None
        session = self.sessions.get(key)
        if clean != self.entry_command and not callback and session is None:
            return None
        if not actor or not chat or not self.authorize(actor):
            send(gateway, chat, f'اجازهٔ ثبت {self.entry_command} را ندارید.')
            return {'action': 'skip', 'reason': 'repairs-entry-denied'}
        if key in self.busy:
            send(gateway, chat, 'در حال ثبت درخواست قبلی هستم؛ چند لحظه صبر کنید.')
            return {'action': 'skip', 'reason': 'repairs-entry-busy'}
        if session and session['expires'] <= self.clock():
            self.sessions.pop(key, None)
            self.persist()
            session = None
        message_id = getattr(event, 'message_id', None)
        token = (key, str(message_id)) if message_id is not None else None
        self.seen = {k: expiry for k, expiry in self.seen.items() if expiry > self.clock()}
        if token and token in self.seen:
            return {'action': 'skip', 'reason': 'repairs-entry-duplicate'}
        if callback:
            try:
                if not session:
                    raise ValueError('No active form')
                command = self.keyboard(session['stage']).resolve(raw.get('data', ''), session.get('revision', ''),
                    stage=session['stage'], role='', permits=lambda p: p == 'repairs.edit' and self.authorize(actor))
                if session.get('message_id') and str(raw.get('origin_message_id')) != session['message_id']:
                    raise ValueError('Wrong message')
                session['revision'] = ''
                self.persist()
            except (ValueError, PermissionError):
                send(gateway, chat, f'این دکمه قدیمی یا نامعتبر است؛ «{self.entry_command}» را دوباره بنویسید.')
                return {'action': 'skip', 'reason': 'repairs-entry-stale'}
        else:
            command = self.commands.get(clean, '')
            if clean in self.exit_commands:
                self.sessions.pop(key, None)
                self.persist()
                self.lifecycle.spawn(self.lifecycle.retire(key, gateway))
                return None
        if token:
            self.seen[token] = self.clock() + 1800
        async def execute():
            await self.lifecycle.retire(key, gateway)
            await self.advance(key, command, text, gateway, send)
        self.start_task(key, execute(), gateway, send)
        return {'action': 'skip', 'reason': 'repairs-entry'}


_handler = RepairsEntryHandler(state_store=StateStore(ROOT / 'runtime/bale_ui/repairs_entry.json'))
