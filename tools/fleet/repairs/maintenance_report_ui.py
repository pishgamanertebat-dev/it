"""Bale presentation for the maintenance report's durable approval stages."""
import asyncio
import logging
from pathlib import Path
import re
import sqlite3

from tools.bale_ui import KeyboardLifecycle
from .entry_dates import readable
from .maintenance_report_parse import DIGITS, declared_report
from .maintenance_report_store import has_correction_prompt, operation_id, remember

LOG = logging.getLogger(__name__)
STATUS = re.compile(r'وضعیت گزارش(?: ([1-9][0-9]*))?\Z')
CALLBACK = re.compile(r'ik:mreport:([0-9a-f]{32})([0-9a-f]{16}):(a|b|s|[ac](?:[1-9]|[1-3][0-9]|40))\Z')
MESSAGE_ID = re.compile(r'[1-9][0-9]*\Z')
HANDLED = dict(action='skip',reason='maintenance-report-partial')


def status_command(text):
    return STATUS.fullmatch(' '.join(str(text or '').translate(DIGITS).split()))


def card(result):
    view = result['view']
    items = result['items']
    pending = [item for item in items if item['status']=='PENDING_CORRECTION']
    valid = [item for item in items if item['status']=='VALID']
    remaining = [item for item in items if item['status']!='COMMITTED']
    lines = [f"گزارش {readable(result['date'])} | پیام {readable(result['source_id'].split(':')[1])}"]
    rows = []

    def button(label, action):
        rows.append([dict(text=label,callback_data=f"ik:mreport:{result['id']}{view['token']}:{action}")])

    if view['phase'] == 'reply':
        item = items[view['number']-1]
        return (f"مورد {readable(str(item['number']))}\n{item['raw']}\n{item['reason']}\n"
                "در پاسخ به همین پیام، کد دقیق یا کد و شرح کامل همین مورد را در یک خط بفرستید."), [], True
    if result.get('committed'):
        lines.append(f"✅ {readable(str(result['committed']))} مورد در Excel ثبت شد.")
        lines.append(f"⚠️ {readable(str(len(remaining)))} مورد باقی مانده است." if remaining else
                     'همهٔ موارد این گزارش ثبت شده‌اند.')
        if (result.get('delivery') or {}).get('status') == 'correction_required':
            lines.append('PDF این تاریخ قبلاً ارسال شده است؛ اصلاحیه باید جداگانه بررسی شود.')
    elif not remaining:
        lines.append(f"✅ {readable(str(len(items)))} مورد در Excel ثبت شده است.")

    if view['phase'] == 'text':
        lines.append('متن و شیت مقصد را بررسی کنید. با «تأیید متن» همین موارد در Excel ثبت می‌شوند.')
        valid = [items[n-1] for n in view['numbers']]
        button('✅ تأیید متن و ثبت', 'b')
    else:
        initial = [item for item in valid if not item['corrected']]
        if initial and view['open_date']:
            button(f"✅ تأیید گزارش ({readable(str(len(initial)))} مورد)", 'a')
        if initial and not view['open_date']:
            lines.append('مهلت ثبت اولیهٔ موارد آماده پایان یافته است.')
        for item in valid:
            if item['corrected']:
                n = item['number']
                button(f"✅ تأیید گزارش (مورد {readable(str(n))})", f'a{n}')
        if valid:
            lines.append(f"✅ {readable(str(len(valid)))} مورد صحیح")
        if pending:
            lines.append(f"⚠️ {readable(str(len(pending)))} مورد نیازمند اصلاح")
        for item in pending:
            n = item['number']
            lines.append(f"مورد {readable(str(n))} | {item['raw']}\n{item['reason']}")
            button(f"✏️ اصلاح مورد {readable(str(n))}",f'c{n}')
        for item in remaining:
            if item['status']=='FAILED':
                lines.append(f"مورد {readable(str(item['number']))} | {item['reason']}")
    for item in valid:
        record = item['record']
        lines.append(f"مورد {readable(str(item['number']))} | شیت {record['sheet']}\n{record['description']}")
    button('📋 وضعیت گزارش','s')
    return '\n\n'.join(lines), rows, False


async def emit(gateway, send, chat, text, rows, reply):
    bot = KeyboardLifecycle.bot(gateway)
    if bot is None:
        send(gateway,chat,text)
        return None
    from telegram import ForceReply, InlineKeyboardMarkup
    markup = (ForceReply() if reply else
              InlineKeyboardMarkup.de_json(dict(inline_keyboard=rows),bot) if rows else None)
    for index in range(0,len(text),3500):
        message = await bot.send_message(chat_id=chat,text=text[index:index+3500],parse_mode=None,
                                         reply_markup=markup if index+3500>=len(text) else None)
    return str(message.message_id)


class PartialReportUI:
    def __init__(self, run, runtime, tasks, *, present=emit):
        self.run, self.runtime, self.tasks, self.present = run, Path(runtime), tasks, present

    def handle(self,event,gateway,send,actor,chat,callback):
        payload = dict(actor=actor,chat=chat)
        if callback:
            match = CALLBACK.fullmatch(str(event.raw_message.get('data','')))
            if not match:
                send(gateway,chat,'این دکمه در دسترس نیست؛ «وضعیت گزارش» را بفرستید.')
                return HANDLED
            report, token, command = match.groups()
            action = {'a':'approve','b':'confirm','s':'status','c':'correct'}[command[0]]
            payload.update(action=action,report_id=report)
            if action != 'status':
                payload.update(token=token,origin=str(event.raw_message.get('origin_message_id') or ''))
            if len(command)>1:
                payload['number'] = int(command[1:])
        elif match := status_command(event.text):
            payload['action'] = 'status'
            if match[1]:
                payload['source_id'] = 'bale:'+match[1]
        elif declared_report(event.text):
            message = str(getattr(event,'message_id','') or '')
            if not MESSAGE_ID.fullmatch(message) or len(event.text)>16000:
                send(gateway,chat,'شناسه یا طول پیام گزارش معتبر نیست.')
                return HANDLED
            source = 'bale:'+message
            try:
                if not (self.runtime/'audit.sqlite3').is_file():
                    raise OSError
                remember(self.runtime,operation_id(actor,chat,source),actor,chat,source,event.text,status='partial_received')
            except ValueError as exc:
                send(gateway,chat,str(exc))
                return HANDLED
            except (OSError,sqlite3.Error):
                send(gateway,chat,'دفتر ثبت در دسترس نیست؛ دریافت گزارش تأیید نشد.')
                return HANDLED
            payload.update(action='prepare',source_id=source,raw=event.text)
        else:
            origin = str(getattr(event,'reply_to_message_id','') or '')
            try:
                if not origin or not has_correction_prompt(self.runtime,actor,chat,origin):
                    return None
            except (OSError,sqlite3.Error):
                send(gateway,chat,'دفتر ثبت در دسترس نیست؛ وضعیت گزارش را دوباره بررسی کنید.')
                return HANDLED
            payload.update(action='reply',origin=origin,raw=event.text,
                           reply_id=str(getattr(event,'message_id','') or ''))
        task = asyncio.get_running_loop().create_task(self.execute(payload,gateway,send))
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return HANDLED

    async def execute(self,payload,gateway,send):
        actor, chat = payload['actor'], payload['chat']
        try:
            response = await self.run(payload)
            if not response['ok']:
                send(gateway,chat,response['message'])
                return
            result = response['result']
            if result.get('legacy'):
                message = ('برای این پیام سابقهٔ ثبت وجود دارد.' if result['legacy']=='succeeded'
                           else 'این پیام در سوابق قبلی است؛ بررسی آن به تأیید مالک نیاز دارد.')
                send(gateway,chat,message)
                return
            if result.get('duplicate'):
                source = result['duplicate']
                mid = ' '+readable(source[5:]) if source.startswith('bale:') else ''
                send(gateway,chat,f'این متن قبلاً دریافت شده است. برای پیگیری بفرستید «وضعیت گزارش{mid}».')
                return
            if result.get('missing'):
                send(gateway,chat,'گزارشی برای این حساب و شناسه پیدا نشد.')
                return
            text, rows, reply = card(result)
            origin = await self.present(gateway,send,chat,text,rows,reply)
            if origin:
                bound = await self.run(dict(action='bind',actor=actor,chat=chat,report_id=result['id'],
                                            token=result['view']['token'],origin=origin))
                if not bound['ok']:
                    send(gateway,chat,bound['message'])
        except Exception:
            LOG.exception('Maintenance report approval interrupted')
            send(gateway,chat,'نتیجه نیازمند بررسی است؛ «وضعیت گزارش» را بفرستید.')
