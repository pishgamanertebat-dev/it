"""Bale repair log: mechanic -> machine -> work description -> parts -> confirm."""
import json
import re
import uuid

from tools.bale_ui import Action, InlineKeyboardBuilder, StateStore
from .entry_bale import RepairsEntryHandler, ROOT, worker

CONFIG = ROOT / 'settings/maintenance_entry.json'


def permitted(actor):
    try:
        return bool(actor) and str(actor) in json.loads(CONFIG.read_text(encoding='utf-8'))['allowed_users']
    except (OSError, ValueError, KeyError):
        return False


def keyboard(stage):
    def button(action, label):
        return Action(action, label, 'repairs.edit', frozenset({stage}))
    rows = []
    if stage == 'CONFIRM':
        rows.append((button('confirm', '✅ تأیید و ذخیره'), button('edit', '✏️ اصلاح اطلاعات')))
    if stage == 'PARTS':
        rows.append((button('no_parts', 'قطعه مصرف نشده'),))
    rows.append((button('finish', 'پایان'),))
    return InlineKeyboardBuilder('maintenance_entry', rows)


async def run_worker(payload):
    return await worker(payload, module='tools.fleet.repairs.maintenance_service')


class MaintenanceEntryHandler(RepairsEntryHandler):
    entry_command = 'تعمیرات'
    namespace = 'maintenance_entry'
    initial_stage = 'MECHANIC'
    keyboard = staticmethod(keyboard)
    commands = {'تعمیرات': 'entry', 'پایان': 'finish', 'انصراف': 'finish', 'لغو': 'finish',
                '/cancel': 'finish', 'تایید': 'confirm', 'تأیید': 'confirm', 'ویرایش': 'edit',
                'قطعه مصرف نشده': 'no_parts'}
    exit_commands = {'حکم کار', 'شرح خرابی'}

    def __init__(self, *, run=run_worker, authorize=permitted, **kwargs):
        super().__init__(run=run, authorize=authorize, **kwargs)

    async def advance(self, key, command, text, gateway, send):
        if not self.authorize(key[0]):
            self.sessions.pop(key, None)
            self.persist()
            await self.reply(key, gateway, send, 'اجازهٔ ثبت تعمیرات را ندارید.')
            return
        if command == 'finish':
            self.sessions.pop(key, None)
            self.persist()
            await self.reply(key, gateway, send, 'ثبت تعمیرات پایان یافت. فقط موارد تأییدشده ذخیره شده‌اند.')
            return
        if command == 'entry':
            self.sessions[key] = {'stage': 'MECHANIC', 'expires': self.clock()+1800, 'revision': ''}
            self.persist()
            await self.reply(key, gateway, send, 'نام تعمیرکار یا تعمیرکاران را وارد کنید.')
            return
        session = self.sessions.get(key)
        if not session:
            await self.reply(key, gateway, send, 'فرم منقضی شده است؛ «تعمیرات» را دوباره بنویسید.')
            return
        stage = session['stage']
        if stage == 'CODE':
            session['stage'] = 'BUSY'
            self.persist()
            result = await self.run({'action': 'preview', 'actor': key[0], 'code': text})
            session['stage'] = 'CODE'
            if result['ok']:
                session.update(selection=result['result'], stage='DESCRIPTION')
                selected = session['selection']
                message = f"{selected['name']} — {selected['canonical']}\nتاریخ: {selected['date']}\nنوع خرابی و شرح کار انجام‌شده را وارد کنید."
            else:
                message = result['message']
        elif stage == 'CONFIRM':
            if command == 'edit':
                session.pop('request', None)
                session['stage'] = 'MECHANIC'
                message = 'اطلاعات اصلاح‌شده را وارد کنید؛ ابتدا نام تعمیرکار یا تعمیرکاران.'
            elif command == 'confirm':
                session['stage'] = 'BUSY'
                self.persist()
                result = await self.run({'action': 'commit', 'actor': key[0], 'request': session['request']})
                if result['ok']:
                    saved = result['result']
                    session.clear()
                    session.update(stage='MECHANIC', expires=self.clock()+1800, revision='')
                    message = f"✅ تعمیرات {saved['name']} در تاریخ {saved['date']}، ردیف {saved['row']} ذخیره شد.\nبرای ثبت بعدی نام تعمیرکار را وارد کنید یا «پایان» را بزنید."
                else:
                    session['stage'] = 'CONFIRM'
                    message = result['message']
            else:
                message = 'برای ذخیره، «تأیید و ذخیره» را بزنید؛ یا اطلاعات را اصلاح کنید.'
        elif stage in {'MECHANIC', 'DESCRIPTION', 'PARTS'}:
            value = ('مصرف نشده' if stage == 'PARTS' and command == 'no_parts' else text).strip()
            limit = 200 if stage == 'MECHANIC' else 1800
            if not value or len(value) > limit or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]', value):
                await self.reply(key, gateway, send, f'متن را در یک پیام، بین ۱ تا {limit} نویسه وارد کنید.')
                return
            if stage == 'MECHANIC':
                session.update(mechanic=value, stage='CODE')
                message = 'کد دستگاه را وارد کنید؛ برای ۶۰۱، EX601 (بیل) یا WA601 (لودر) را بنویسید.'
            elif stage == 'DESCRIPTION':
                session.update(description=value, stage='PARTS')
                message = 'قطعات مصرفی و تعدادشان را وارد کنید؛ اگر قطعه‌ای مصرف نشده، دکمهٔ «قطعه مصرف نشده» را بزنید.'
            else:
                request = {**session['selection'], 'mechanic': session['mechanic'],
                           'description': session['description'], 'parts': value, 'operation': uuid.uuid4().hex}
                session.update(request=request, stage='CONFIRM')
                message = (f"تاریخ: {request['date']}\nدستگاه: {request['name']} ({request['canonical']})\n"
                           f"تعمیرکار: {request['mechanic']}\nشرح کار / نوع خرابی:\n{request['description']}\n"
                           f"قطعات مصرفی:\n{value}\n\nدر ردیف جدید ثبت شود؟")
        else:
            message = '«تعمیرات» را دوباره بنویسید.'
        self.persist()
        await self.reply(key, gateway, send, message)


_handler = MaintenanceEntryHandler(state_store=StateStore(ROOT / 'runtime/bale_ui/maintenance_entry.json'))
