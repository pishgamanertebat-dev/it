"""Small, synchronous menu handler called after Bale registration approval."""

from __future__ import annotations

import asyncio
import json
import logging
import re
import subprocess
import time
import secrets
from copy import copy
from dataclasses import asdict, dataclass, field
from pathlib import Path
from tools.bale_ui import StateStore, KeyboardLifecycle
from tools.fleet.work_orders.channels.bale.keyboards import proposal_keyboard, COMMANDS

from tools.fleet.work_orders.core.permissions import (
    WorkOrderPermissionDenied,
    normalize_bale_id,
    require_work_order_permission,
)
from tools.fleet.work_orders.core.paths import PROJECT_ROOT
from tools.fleet.work_orders.core.service import validate_jalali_date
from tools.fleet.work_orders.core.review import normalize_shift
from tools.fleet.work_orders.core.conversation import review_context
from tools.fleet.work_orders.channels.bale.work_order_menu import (
    InvalidWorkOrderSelection,
    WorkOrderTypeDisabled,
    build_work_order_menu,
    resolve_work_order_selection,
)


logger = logging.getLogger(__name__)


def normalize_text(text: str) -> str:
    return " ".join(text.translate(str.maketrans("كي", "کی")).replace("\u200c", " ").split())


def normalize_digits(text: str) -> str:
    return text.translate(str.maketrans("۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩", "01234567890123456789"))


async def run_create_worker(request: dict) -> dict:
    process = await asyncio.create_subprocess_exec(
        str(PROJECT_ROOT / ".venv" / "Scripts" / "python.exe"),
        "-E", "-s", "-B", "-X", "utf8", "-m",
        "tools.fleet.work_orders.channels.bale.create_worker",
        cwd=str(PROJECT_ROOT),
        stdin=asyncio.subprocess.PIPE, stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        creationflags=subprocess.CREATE_NO_WINDOW,
    )
    output, _stderr = await process.communicate(json.dumps(request).encode("utf-8"))
    if process.returncode != 0:
        raise RuntimeError(f"Work-order worker exited with code {process.returncode}")
    result = json.loads(output.decode("utf-8"))
    if _stderr and not result.get("ok"):
        logger.error("Work-order worker diagnostic: %s", _stderr.decode("utf-8", errors="replace")[-6000:])
    return result


@dataclass
class FormSession:
    expires: float
    stage: str = "MENU"
    work_order_type: str = ""
    machine_codes: list[str] = field(default_factory=list)
    jalali_date: str = ""
    result: str = ""
    order_no: str = ""
    staff_options: list = field(default_factory=list)
    proposal: dict | None = None
    additions: list = field(default_factory=list)
    keyboard_revision: str = ''
    keyboard_message_id: str = ''


async def send_manager_excel(gateway, chat_id, order):
    adapter = next((adapter for platform, adapter in gateway.adapters.items()
                    if str(getattr(platform, "value", platform)).lower() == "bale"), None)
    if adapter is None or not getattr(adapter, "_bot", None):
        raise RuntimeError("Bale document transport unavailable")
    # Use the existing configured bot. The adapter's generic send_document can
    # report success for a text fallback even when the attachment upload failed.
    with open(order["file_path"], "rb") as document:
        await adapter._bot.send_document(
            chat_id=chat_id, document=document, filename=order["file_name"],
            caption=f"فایل حکم {order['work_order_no']} برای بررسی شما؛ هنوز برای سرویسکار ارسال نشده است.",
        )


class WorkOrderMenuHandler:
    def __init__(self, *, db_path: Path | str | None = None, clock=time.time, worker=None, document_sender=None, state_store=None):
        self.db_path = db_path
        self.clock = clock
        self.worker = worker or run_create_worker
        self.document_sender = document_sender or send_manager_excel
        self.pending: dict[tuple[str, str, str], FormSession] = {}
        self.tasks: set[asyncio.Task] = set()
        self.processed: dict[tuple, float] = {}
        self.state_store = state_store
        self.lifecycle = KeyboardLifecycle()
        self._lifecycle_restored = False
        self._expired_keyboards = []
        if state_store:
            try:
                for key, value in state_store.load():
                    session = FormSession(**value)
                    if session.stage == 'BUSY':
                        session.stage = 'RESULT'
                        session.keyboard_revision = ''
                        session.result = 'اجرای قبلی قطع شده است؛ پیش از ساخت دوباره، آخرین حکم را با «ارسال مجدد» بررسی کنید.'
                        session.expires = self.clock() + 600
                    if session.expires > self.clock():
                        self.pending[tuple(key)] = session
                    elif session.keyboard_message_id:
                        self._expired_keyboards.append((tuple(key), session.keyboard_message_id))
            except (ValueError, TypeError, OSError):
                logger.exception('Could not restore work-order UI state')

    def _persist(self):
        if self.state_store:
            self.state_store.save([[list(key), asdict(value)] for key, value in self.pending.items()])

    def _keyboard(self, key, session):
        permission = require_work_order_permission(key[1], db_path=self.db_path)
        session.keyboard_revision = secrets.token_hex(8)
        return proposal_keyboard.build(session.keyboard_revision, stage=session.stage,
            role=permission.role, permits=lambda name: name == 'work_order.manage' and permission.allowed)

    def _send_reply(self, gateway, chat_id, reply, send, *, key=None):
        try:
            markup = None
            session = self.pending.get(key) if key else None
            if session and session.stage == 'PROPOSAL':
                markup = self._keyboard(key, session)
            render_revision = session.keyboard_revision if markup else None
            self._persist()
            chunks = []
            chunk = ''
            for line in reply.splitlines(keepends=True):
                if chunk and len(chunk) + len(line) > 3000:
                    chunks.append(chunk)
                    chunk = ''
                chunk += line
            if chunk:
                chunks.append(chunk)
            adapter = next((adapter for platform, adapter in gateway.adapters.items()
                            if str(getattr(platform, 'value', platform)).lower() == 'bale'), None) if gateway else None
            bot = getattr(adapter, '_bot', None) if adapter is not None else None

            # Adapter.send treats text as Markdown and escapes punctuation. Bale
            # displays those escapes literally and may expose HTML entities.
            # Work-order forms are plain text, so disable formatting entirely.
            if bot is None and len(chunks) <= 1:
                if chunks:
                    send(gateway, chat_id, chunks[0])
                return None
            if adapter is None:
                for part in chunks:
                    send(gateway, chat_id, part)
                return None

            async def send_in_order():
                try:
                    if key:
                        await self.lifecycle.retire(key, gateway)
                    for index, part in enumerate(chunks):
                        if bot is not None:
                            options = {}
                            if (markup and index == len(chunks) - 1 and
                                    session.keyboard_revision == render_revision and session.stage == 'PROPOSAL'):
                                from telegram import InlineKeyboardMarkup
                                options['reply_markup'] = InlineKeyboardMarkup.de_json(markup, bot)
                            message = await bot.send_message(chat_id=str(chat_id), text=part, parse_mode=None, **options)
                            if options:
                                message_id = getattr(message, 'message_id', None)
                                if session.keyboard_revision != render_revision or session.stage != 'PROPOSAL':
                                    await self.lifecycle.remove(gateway, chat_id, message_id)
                                    continue
                                self.lifecycle.bind(key, gateway, chat_id, message_id,
                                    ttl=max(0, session.expires - self.clock()), policy=proposal_keyboard.policy)
                                session.keyboard_message_id = str(message_id or '')
                                self._persist()
                        else:
                            await adapter.send(str(chat_id), part)
                except Exception:
                    logger.exception('Could not send ordered work-order reply')

            task = asyncio.get_running_loop().create_task(send_in_order())
            self.tasks.add(task)
            task.add_done_callback(self.tasks.discard)
            return task
        except Exception:
            logger.exception("Could not schedule work-order reply")
            return None

    async def _run_request(self, key, session, request, gateway, send):
        chat_id = key[2]
        try:
            if request['action'] == 'dispatch':
                from tools.fleet.work_orders.channels.bale.staff_flow import dispatch
                reply = await dispatch(gateway, session.order_no, key[1], chat_id, request['staff_id'], request.get('roster_id'))
                session.stage = 'STAFF'
                session.result = reply
                session.expires = self.clock() + 600
                delivery = self._send_reply(gateway, chat_id, reply, send)
                if delivery:
                    await delivery
                return
            result = await self.worker(request)
            if not result.get("ok"):
                if result.get('error') == 'INVALID_INPUT' and request['action'] == 'validate_add':
                    session.stage = 'ADD_CODES'
                    reply = result['message'] + '\nکدها را اصلاح کنید یا بنویسید: برگشت'
                elif result.get("error") == "INVALID_INPUT" and request["action"] == "validate_machines":
                    session.stage = "MACHINES"
                    reply = result["message"] + "\nکد دستگاه‌ها را اصلاح و دوباره ارسال کنید."
                else:
                    session.stage = "RESULT"
                    reply = result.get("message", "عملیات با خطا روبه‌رو شد؛ وضعیت حکم باید بررسی شود.")
                    session.result = reply
            elif request['action'] == 'propose':
                from tools.fleet.work_orders.channels.bale.proposal_form import render
                session.proposal = result['proposal']
                session.stage = 'PROPOSAL'
                session.jalali_date = session.proposal['plan_date']
                reply = render(session.proposal)
            elif request['action'] == 'validate_add':
                session.additions = result['items']
                if session.work_order_type in {'GREASING', 'OIL_CHANGE'}:
                    from tools.fleet.work_orders.channels.bale.proposal_form import add_items, render
                    add_items(session.proposal, session.additions, 'GREASING_FULL')
                    session.stage = 'PROPOSAL'
                    reply = render(session.proposal)
                else:
                    session.stage = 'ADD_ACTION'
                    reply = 'نوع تعویض دستگاه‌های اضافه‌شده را انتخاب کنید:\n1) بیرونی\n2) داخلی و بیرونی\nبرای بازگشت بنویسید: برگشت'
            elif request["action"] == "validate_machines":
                session.machine_codes = result["machine_codes"]
                session.stage = "DATE"
                reply = f"تعداد دستگاه: {len(session.machine_codes)}\nتاریخ حکم را وارد کنید؛ مانند 1405/06/15."
            elif request["action"] == "confirm_review":
                from tools.fleet.work_orders.core.staff_dispatch import staff_menu
                session.work_order_type = result.get('work_order_type', session.work_order_type)
                menu, session.staff_options = staff_menu(session.work_order_type)
                session.stage = "STAFF"
                reply = f"✅ تایید بررسی فایل حکم {session.order_no} ثبت شد.\n{menu}"
                session.result = reply
            elif request["action"] == "edit":
                from tools.fleet.work_orders.channels.bale.proposal_form import render
                session.stage = "PROPOSAL"
                session.work_order_type = result["work_order_type"]
                session.machine_codes = []
                session.jalali_date = ""
                session.order_no = ""
                session.result = ""
                review_context(key[1], chat_id, 'manager', number='', stage='EDIT', db_path=self.db_path)
                session.proposal = result['proposal']
                session.jalali_date = session.proposal['plan_date']
                reply = 'خروجی اصلاح‌شده شمارهٔ جدید می‌گیرد؛ حکم قبلی محفوظ می‌ماند.\n' + render(session.proposal)
            elif result.get('orders'):
                session.work_order_type = 'OIL_CHANGE'
                summaries = []
                for order in result['orders']:
                    number = order['work_order_no']
                    reply = f"✅ حکم مستقل {number}\n{order['item_summary']}\nحکم هنوز برای سرویسکار ارسال نشده است."
                    session.order_no = number
                    try:
                        require_work_order_permission(key[1], db_path=self.db_path)
                        await self.document_sender(gateway, chat_id, order)
                        session.stage = 'REVIEW'
                        reply += f"\nپس از بررسی همین فایل بنویسید:\nثبت تایید {number}\nبرای اصلاح:\nویرایش {number}"
                    except Exception:
                        logger.exception('Manager oil Excel delivery failed for %s', number)
                        session.stage = 'RESULT'
                        reply += f"\nارسال فایل ناموفق بود؛ حکم محفوظ است. بنویسید:\nارسال مجدد {number}"
                    review_context(key[1],chat_id,'manager',number=number,stage=session.stage,db_path=self.db_path)
                    delivery = self._send_reply(gateway,chat_id,reply,send)
                    if delivery:
                        await delivery
                    summaries.append(number + ': ' + order['item_summary'])
                reply = 'حکم‌های مستقل ساخته‌شده:\n' + '\n'.join(summaries)
                reply += '\nبرای هر حکم، تأیید و انتخاب سرویسکار جداگانه انجام می‌شود.'
                if result.get('batch_error'):
                    reply += '\n⚠️ ' + result['batch_error']
                session.result = reply
            else:
                order = result["order"]
                session.work_order_type = order.get('work_order_type', session.work_order_type)
                session.order_no = order["work_order_no"]
                session.stage = "RESULT"
                review_context(key[1], chat_id, 'manager', number=session.order_no, stage='RESULT', db_path=self.db_path)
                reply = (
                    ("✅ فایل حکم برای بررسی مجدد\n\n" if request['action'] in {'preview', 'preview_latest'} else "✅ حکم کار ساخته شد\n\n") +
                    f"شماره: {order['work_order_no']}\n"
                    f"نوع: {order['label']}\nتعداد دستگاه: {order['item_count']}\n"
                    f"فایل: {order['file_name']}\nوضعیت فایل: آماده ارسال\n\n"
                    "حکم هنوز برای سرویسکار ارسال نشده است."
                )
                if order.get('item_summary'):
                    reply += '\n' + order['item_summary']
                try:
                    require_work_order_permission(key[1], db_path=self.db_path)
                    await self.document_sender(gateway, chat_id, order)
                    session.stage = "REVIEW"
                    review_context(key[1], chat_id, 'manager', number=session.order_no, stage='REVIEW', db_path=self.db_path)
                    reply += "\n\nفایل را بررسی کنید؛ آیا تایید می‌کنید؟\nبرای تایید بنویسید:\nتایید"
                    if session.work_order_type == 'OIL_CHANGE':
                        reply += "\n\nبرای اصلاح دستگاه‌ها و دریافت نوبت سرویس از برنامه‌ریزی بنویسید:\nویرایش"
                    elif session.work_order_type == 'GREASING':
                        reply += "\n\nبرای اصلاح دستگاه‌ها بنویسید:\nویرایش"
                    else:
                        reply += "\n\nبرای اصلاح دستگاه‌ها، تاریخ یا شیفت بنویسید:\nویرایش"
                except Exception:
                    logger.exception("Manager Excel delivery failed")
                    reply += "\n\nارسال فایل ناموفق بود؛ حکم محفوظ است. برای تلاش دوباره بنویسید:\nارسال مجدد"
                session.result = reply
            session.expires = self.clock() + 600
        except Exception:
            logger.exception("Work-order request failed")
            session.stage = "RESULT"
            reply = "عملیات با خطا روبه‌رو شد. پیش از ساخت دوباره، وضعیت حکم باید بررسی شود."
            session.result = reply
        delivery = self._send_reply(gateway, chat_id, reply, send, key=key)
        if delivery:
            await delivery

    def _start_request(self, key, session, request, gateway, send):
        loop = asyncio.get_running_loop()
        session.stage = "BUSY"
        session.keyboard_revision = ''
        self._persist()
        task = loop.create_task(self._run_request(key, session, request, gateway, send))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)

    def handle(self, event, gateway, *, send, _accepted=False):
        source = event.source
        platform = getattr(source.platform, "value", source.platform)
        if str(platform).lower() != "bale" or getattr(source, "chat_type", None) != "dm":
            return None

        # Identity comes only from the authenticated event, never the message
        # body or a fallback chat ID.
        user_id = normalize_bale_id(getattr(source, "user_id", None))
        chat_id = str(getattr(source, "chat_id", "") or "").strip()
        text = normalize_text(event.text or "")
        raw = getattr(event, 'raw_message', None)
        callback = isinstance(raw, dict) and raw.get('bale_inline_callback') is True
        is_entry = text == "حکم کار"
        review_command = re.fullmatch(r"(ثبت تایید|ثبت تأیید|ارسال مجدد|اصلاح|ویرایش) ((?:AF|GR|OC)-1405-\d{2}-\d{2}-\d+)", normalize_digits(text))
        key = ("bale", user_id or "", chat_id)
        now = self.clock()
        if gateway is not None and not self._lifecycle_restored:
            for saved_key, message_id in self._expired_keyboards:
                self.lifecycle.bind(saved_key, gateway, saved_key[2], message_id, ttl=0)
            self._expired_keyboards.clear()
            for saved_key, saved in self.pending.items():
                self.lifecycle.bind(saved_key, gateway, saved_key[2], saved.keyboard_message_id,
                    ttl=max(0, saved.expires - now) if saved.keyboard_revision else 0)
            self._lifecycle_restored = True
        if key in self.lifecycle.pending and not _accepted:
            # Concurrent taps/text must not race the asynchronous markup edit.
            return {'action': 'skip', 'reason': 'inline-transition-pending'}
        self.pending = {k: session for k, session in self.pending.items() if session.expires > now or session.stage == "BUSY"}
        self.processed = {k: expires for k, expires in self.processed.items() if expires > now}
        if callback:
            try:
                permission = require_work_order_permission(user_id, db_path=self.db_path)
                session = self.pending.get(key)
                if session is None:
                    raise ValueError('Expired form')
                def execute(action):
                    forwarded = copy(event)
                    forwarded.text = COMMANDS[action]
                    forwarded.raw_message = None
                    session.keyboard_message_id = ''
                    return self.handle(forwarded, gateway, send=send, _accepted=True)

                return self.lifecycle.accept(proposal_keyboard, raw.get('data', ''), session.keyboard_revision,
                    stage=session.stage, role=permission.role,
                    permits=lambda name: name == 'work_order.manage' and permission.allowed,
                    consume=lambda: setattr(session, 'keyboard_revision', ''), persist=self._persist,
                    scope=key, event=event, gateway=gateway, execute=execute,
                    failed=lambda: self._send_reply(gateway, chat_id,
                        'به‌روزرسانی منو انجام نشد؛ عملیات اجرا نشد. منوی حکم کار را دوباره باز کنید.', send))
            except PermissionError:
                self._send_reply(gateway, chat_id, 'اجازهٔ انجام این عملیات را ندارید.', send)
                return {'action': 'skip', 'reason': 'inline-rejected'}
            except ValueError:
                self._send_reply(gateway, chat_id, 'این دکمه مربوط به منوی قدیمی یا منقضی‌شده است؛ منوی حکم کار را دوباره باز کنید.', send)
                return {'action': 'skip', 'reason': 'inline-rejected'}
        message_id = getattr(event, "message_id", None)
        message_key = (*key, str(message_id)) if message_id is not None else None
        if message_key in self.processed:
            return {"action": "skip", "reason": "work-order-duplicate-message"}
        recovery = text in {'ارسال مجدد', 'تایید', 'تأیید'}
        if not is_entry and not review_command and not recovery and key not in self.pending:
            return None
        if not chat_id:
            return {"action": "skip", "reason": "work-order-missing-chat"}

        reason = "work-order-menu"
        try:
            if recovery and key not in self.pending:
                from tools.fleet.work_orders.core.permissions import check_work_order_permission
                if not check_work_order_permission(user_id, db_path=self.db_path).allowed:
                    return None
                saved = review_context(user_id, chat_id, 'manager', db_path=self.db_path)
                if saved and saved[0]:
                    self.pending[key] = FormSession(expires=now + 600, order_no=saved[0], stage=saved[1])
                elif text != 'ارسال مجدد':
                    return None
                else:
                    session = self.pending[key] = FormSession(expires=now + 600)
                    self._start_request(key, session, {'action': 'preview_latest', 'bale_id': user_id}, gateway, send)
                    self._send_reply(gateway, chat_id, 'در حال بازیابی آخرین فایل حکم شما…', send)
                    return {'action': 'skip', 'reason': 'work-order-review'}
            require_work_order_permission(user_id, db_path=self.db_path)
            session = self.pending.get(key)
            if session and session.stage == "BUSY":
                reply = "در حال انجام درخواست قبلی هستم؛ لطفاً منتظر نتیجه بمانید."
                reason = "work-order-busy"
            elif session and session.stage == 'RESULT' and not session.order_no and text == 'ارسال مجدد':
                self._start_request(key, session, {'action': 'preview_latest', 'bale_id': user_id}, gateway, send)
                reply = 'در حال بازیابی آخرین فایل حکم شما…'
                reason = 'work-order-review'
            elif review_command or (session and session.order_no and text in {"تایید", "تأیید", "ارسال مجدد", "اصلاح", "ویرایش"}):
                command = review_command[1] if review_command else text
                number = review_command[2] if review_command else session.order_no
                if session is None:
                    session = self.pending[key] = FormSession(expires=now + 600)
                session.order_no = number
                action = {"ارسال مجدد": "preview", "اصلاح": "edit", "ویرایش": "edit"}.get(command, "confirm_review")
                if action == 'confirm_review' and session.stage == 'RESULT' and not review_command:
                    raise ValueError('ابتدا با «ارسال مجدد» فایل را دریافت و بررسی کنید؛ سپس «تایید» را بفرستید.')
                self._start_request(key, session, {"action": action, "bale_id": user_id, "work_order_no": number}, gateway, send)
                reply = {"preview": "در حال ارسال فایل…", "edit": "در حال باز کردن فرم اصلاح…", "confirm_review": "در حال ثبت تأیید بررسی فایل…"}[action]
                reason = "work-order-review"
            elif is_entry:
                reply = build_work_order_menu(bale_id=user_id, db_path=self.db_path)
                reply += "\n\nبرای خروج، «انصراف» را بفرستید."
                self.pending[key] = FormSession(expires=now + 600)
            elif text in {"انصراف", "لغو", "/cancel"}:
                self.pending.pop(key, None)
                reply = "انتخاب حکم کار لغو شد."
                reason = "work-order-menu-cancelled"
            elif session.stage == "MENU" and text.isdecimal():
                item = resolve_work_order_selection(text, bale_id=user_id, db_path=self.db_path)
                session.work_order_type = item["key"]
                self._start_request(key, session, {'action':'propose','bale_id':user_id,'work_order_type':item['key']}, gateway, send)
                labels = {'OIL_CHANGE':'تعویض روغن', 'GREASING':'گریس‌کاری', 'AIR_FILTER':'هواکش'}
                reply = 'در حال بررسی کارکردها و تهیهٔ پیشنهاد ' + labels[item['key']] + '…'
                reason = "work-order-type-selected"
            elif session.proposal is not None and session.stage in {'PROPOSAL','REMOVE','ADD_CODES','ADD_ACTION'}:
                from tools.fleet.work_orders.channels.bale.proposal_form import render, add_items
                if text == 'برگشت':
                    session.stage = 'PROPOSAL'
                    reply = render(session.proposal)
                elif session.stage == 'PROPOSAL':
                    if text == 'حذف':
                        session.stage = 'REMOVE'
                        reply = 'شمارهٔ ردیف‌های حذف را با فاصله بنویسید؛ مانند 1 3. برای بازگشت: برگشت'
                    elif text == 'اضافه':
                        session.stage = 'ADD_CODES'
                        reply = 'کد دستگاه‌ها را بنویسید؛ مانند 465 714. برای بازگشت: برگشت'
                    elif text in {'تایید','تأیید'}:
                        if not session.proposal['items']:
                            raise ValueError('فهرست خالی است؛ دستگاه اضافه کنید یا انصراف بدهید.')
                        if session.work_order_type in {'GREASING', 'OIL_CHANGE'}:
                            request = {"action": "create", "bale_id": user_id,
                                       "work_order_type": session.work_order_type,
                                       "machine_codes": [i['machine_code'] for i in session.proposal['items']],
                                       "jalali_date": session.jalali_date, "shift": "روزانه",
                                       "item_actions": {i['machine_code']:i['action_code'] for i in session.proposal['items']},
                                       "proposal": {k:session.proposal[k] for k in ('cutoff','plan_date','source_sha256')}}
                            self._start_request(key, session, request, gateway, send)
                            reply = 'در حال ساخت حکم کار و فایل اکسل…'
                            reason = 'work-order-creating'
                        else:
                            session.stage = 'SHIFT'
                            reply = 'شیفت را وارد کنید: صبح، ظهر یا شب؛ مانند صبح ظهر.'
                    else:
                        reply = 'از دکمه‌های زیر انتخاب کنید.'
                elif session.stage == 'REMOVE':
                    values = re.split(r'[\s,،]+', normalize_digits(text))
                    if not all(v.isdecimal() and 1 <= int(v) <= len(session.proposal['items']) for v in values):
                        raise ValueError('شمارهٔ ردیف معتبر وارد کنید یا بنویسید: برگشت')
                    remove = {int(v) for v in values}
                    session.proposal['items'] = [i for n,i in enumerate(session.proposal['items'],1) if n not in remove]
                    session.stage = 'PROPOSAL'
                    reply = render(session.proposal)
                elif session.stage == 'ADD_CODES':
                    codes = [c for c in re.split(r'[\s,،]+',normalize_digits(text)) if c]
                    if session.work_order_type not in {'GREASING','OIL_CHANGE'}:
                        codes = [c.upper() for c in codes]
                        codes = [c[2:] if re.fullmatch(r'HD\d+',c) else c for c in codes]
                    if not codes or len(codes) > 100:
                        raise ValueError('کد دستگاه‌های معتبر را وارد کنید.')
                    self._start_request(key,session,{'action':'validate_add','bale_id':user_id,'work_order_type':session.work_order_type,'machine_codes':codes},gateway,send)
                    reply = 'در حال بررسی دستگاه‌های اضافه‌شده…'
                else:
                    choice = normalize_digits(text)
                    if choice not in {'1','2'}:
                        raise ValueError('گزینهٔ ۱ یا ۲ را وارد کنید یا بنویسید: برگشت')
                    add_items(session.proposal,session.additions,'AIR_FILTER_OUTER' if choice == '1' else 'AIR_FILTER_INNER_OUTER')
                    session.stage = 'PROPOSAL'
                    reply = render(session.proposal)
            elif session.stage == 'STAFF' and text.isdecimal():
                choice = int(normalize_digits(text))
                if not 1 <= choice <= len(session.staff_options):
                    raise ValueError('این گزینه هنوز تعریف نشده است؛ سرویسکار فعال را انتخاب کنید.')
                staff = session.staff_options[choice - 1]
                self._start_request(key, session, {'action': 'dispatch', 'staff_id': staff['id'], 'roster_id':staff.get('roster_id')}, gateway, send)
                reply = 'در حال ارسال حکم برای سرویسکار…'
                reason = 'work-order-dispatch'
            elif session.stage == "MACHINES":
                codes = [code for code in re.split(r"[\s,،;؛]+", normalize_digits(text).upper()) if code]
                if not codes or len(codes) > 100 or any(not re.fullmatch(r"[A-Z0-9]{1,32}", code) for code in codes):
                    raise ValueError("کد دستگاه‌ها را با فاصله یا ویرگول وارد کنید؛ مانند 465 710 714.")
                self._start_request(key, session, {"action": "validate_machines", "bale_id": user_id, "work_order_type": session.work_order_type, "machine_codes": codes}, gateway, send)
                reply = "در حال بررسی کد دستگاه‌ها…"
                reason = "work-order-validating-machines"
            elif session.stage == "DATE":
                session.jalali_date = validate_jalali_date(normalize_digits(text).replace("-", "/"))
                session.stage = "SHIFT"
                reply = "شیفت را وارد کنید: صبح، ظهر یا شب. می‌توانید چند شیفت بنویسید؛ مثلاً «صبح ظهر» به صورت «صبح-ظهر» ثبت می‌شود. پس از این مرحله حکم ساخته می‌شود."
                reason = "work-order-awaiting-shift"
            elif session.stage == "SHIFT":
                text = normalize_shift(text)
                if not text or len(text) > 40 or text.startswith("/"):
                    raise ValueError("نام شیفت را وارد کنید؛ مانند «صبح».")
                request = {"action": "create", "bale_id": user_id, "work_order_type": session.work_order_type, "machine_codes": session.machine_codes, "jalali_date": session.jalali_date, "shift": text}
                if session.proposal is not None:
                    request['machine_codes'] = [i['machine_code'] for i in session.proposal['items']]
                    request['item_actions'] = {i['machine_code']:i['action_code'] for i in session.proposal['items']}
                    request['proposal'] = {k:session.proposal[k] for k in ('cutoff','plan_date','source_sha256')}
                self._start_request(key, session, request, gateway, send)
                reply = "در حال ساخت حکم کار و فایل اکسل…"
                reason = "work-order-creating"
            elif session.stage in {"RESULT", "REVIEW"} and text in {"وضعیت حکم", "نتیجه"}:
                reply = session.result
                reason = "work-order-result"
            else:
                # Ordinary chat and other commands leave this short menu flow.
                if session.stage not in {'RESULT', 'REVIEW', 'STAFF'}:
                    self.pending.pop(key, None)
                return None
        except WorkOrderPermissionDenied as exc:
            self.pending.pop(key, None)
            reply = str(exc)
            reason = "work-order-permission-denied"
        except (InvalidWorkOrderSelection, WorkOrderTypeDisabled) as exc:
            reply = str(exc) + "\nشمارهٔ دیگری انتخاب کنید یا «انصراف» را بفرستید."
            reason = "work-order-selection-rejected"
        except ValueError as exc:
            reply = str(exc)
            reason = "work-order-input-rejected"
        except Exception:
            self.pending.pop(key, None)
            logger.exception("Work-order menu failed")
            reply = "منوی حکم کار موقتاً در دسترس نیست. لطفاً دوباره تلاش کنید."
            reason = "work-order-menu-error"

        session = self.pending.get(key)
        if session:
            session.expires = now + 600
        if message_key:
            self.processed[message_key] = now + 600
        self._send_reply(gateway, chat_id, reply, send, key=key)
        # Handled requests must never fall through to the AI agent, including
        # permission denial and failures.
        return {"action": "skip", "reason": reason}


_handler = WorkOrderMenuHandler(state_store=StateStore(PROJECT_ROOT / 'runtime' / 'bale_ui' / 'work_order.json'))


def handle_work_order_message(event, gateway, *, send):
    return _handler.handle(event, gateway, send=send)
