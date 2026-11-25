"""Button-free receipt and item-bound correction for the authorized Bale DM."""
from contextlib import closing
import asyncio
import logging
import re
import sqlite3

from . import entry_extract
from .entry_dates import readable
from .maintenance_report_parse import declared_report
from .maintenance_report_store import operation_id, remember
from .maintenance_report_ui import HANDLED, MESSAGE_ID, status_command

OWNER = '455740857'
TECHNICAL = '⚠️ گزارش شما دریافت شد. ثبت در اکسل هنوز تأیید نشده است.'
INVALID = '❌ گزارش ثبت نشد. لطفاً تاریخ، کد دقیق دستگاه و شرح تعمیرات را ارسال کنید.'
WORK = re.compile(r'تعویض|تعمیر|گرفتن|رفع|باز\s*کردن|بستن|بازدید|بررسی|سرویس|گریس|جوش|نصب|شست|تست|تنظیم|شارژ|تخلیه|پاکسازی|روغنکاری|انجام|تکمیل|تراش|ساخت|آچارکشی|بازسازی|تمیز|عیب.?یابی|راه.?اندازی')
REQUEST = re.compile(r'حذف|جایگزین|بازنویسی|اصلاح|لطفا|بفرست|بدهید|کنید|کنم|کنیم|باید|خواهد|می.?شود[؟?]|[؟?]')


def validate_analysis(raw, parsed, analysis):
    if not analysis or not analysis.get('report_date') or analysis['report_date'] not in raw.splitlines()[0]:
        raise ValueError('تاریخ استخراج تأیید نشد.')
    issues = analysis['unresolved']
    if any(i['field']=='date' for i in issues):
        raise ValueError('تاریخ استخراج تأیید نشد.')
    extracted = {(i['source_span']['start'], i['source_span']['end']): (n,i)
                 for n,i in enumerate(analysis['items'])}
    for item in parsed['items']:
        span = item['span']
        n, model = extracted.get((span['start'],span['end']), (None,None))
        text = item['raw']
        if (not model or model['raw_description'] != text or not WORK.search(text)
                or REQUEST.search(text) or entry_extract.intent_decision(text)[1]=='technical_question'):
            item['issue'] = 'شرح کار انجام‌شده روشن نیست.'
        elif any(i['item']==n and i['field']=='description' for i in issues):
            item['issue'] = 'شرح فعالیت نیازمند اصلاح است.'
        elif (not item['code'] or not model.get('raw_code') or
              any(i['item']==n and i['field']=='code' for i in issues)):
            item['auto_code_unresolved'] = True


def field(message, key):
    if isinstance(message,dict):
        return message.get(key)
    return getattr(message,key,None) or (getattr(message,'api_kwargs',{}) or {}).get(key)


def handle(ui,event,gateway,send,actor,chat,callback):
    if callback:
        return HANDLED
    raw = getattr(event,'raw_message',None)
    if raw is None or any(field(raw,key) for key in ('forward_origin','forward_date','forward_from',
            'forward_sender_name','forward_from_chat','is_automatic_forward','edit_date','via_bot','sender_chat')):
        return HANDLED
    mid = str(getattr(event,'message_id','') or '')
    source = 'bale:'+mid
    payload = dict(actor=actor,chat=chat)
    status = status_command(event.text)
    try:
        if status:
            payload.update(action='status')
            if status[1]:
                payload['source_id'] = 'bale:'+status[1]
        elif declared_report(event.text):
            title = event.text.replace('ابان','آبان').replace('اذر','آذر')
            if (entry_extract.intent_decision(title)[1]!='declared_report'
                    or REQUEST.search(title.splitlines()[0]) or not MESSAGE_ID.fullmatch(mid)):
                send(gateway,chat,INVALID)
                return HANDLED
            if len(event.text)>16000:
                send(gateway,chat,INVALID)
                return HANDLED
            if not (ui.runtime/'audit.sqlite3').is_file():
                raise OSError('journal missing')
            remember(ui.runtime,operation_id(actor,chat,source),actor,chat,source,event.text,status='auto_received')
            payload.update(action='auto_prepare',source_id=source,raw=event.text)
        else:
            origin = str(getattr(event,'reply_to_message_id',None) or '')
            if not origin or not MESSAGE_ID.fullmatch(mid):
                if entry_extract.intent_decision(event.text)[0] != 'none':
                    send(gateway,chat,INVALID)
                    return HANDLED
                return None
            with closing(sqlite3.connect((ui.runtime/'audit.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
                if not db.execute("SELECT 1 FROM sqlite_master WHERE name='maintenance_auto_prompts'").fetchone():
                    return None
                if not db.execute('SELECT 1 FROM maintenance_auto_prompts WHERE actor=? AND chat=? AND origin=?',
                                  (actor,chat,origin)).fetchone():
                    return None
            payload.update(action='auto_reply',origin=origin,reply_id=mid,raw=event.text)
    except ValueError as exc:
        send(gateway,chat,str(exc))
        return HANDLED
    except (OSError,sqlite3.Error):
        send(gateway,chat,'⚠️ دریافت گزارش در دفتر ثبت تأیید نشد.')
        send(gateway,OWNER,'خطای دفتر ثبت گزارش کرمعلی، پیام '+mid)
        return HANDLED
    task = asyncio.get_running_loop().create_task(execute(ui,payload,gateway,send))
    ui.tasks.add(task)
    task.add_done_callback(ui.tasks.discard)
    return HANDLED


async def execute(ui,payload,gateway,send):
    actor,chat = payload['actor'],payload['chat']
    reported = False
    try:
        if payload['action']=='auto_prepare':
            payload['analysis'] = await entry_extract.extract(payload['raw'],actor,maintenance_blocks=True)
        reply = await ui.run(payload)
        if not reply['ok']:
            raise ValueError(reply['message'])
        result = reply['result']
        if result.get('resume') and payload['action']=='status':
            await execute(ui,dict(action='auto_prepare',actor=actor,chat=chat,source_id=result['resume'],raw=result['raw']),gateway,send)
            return
        if result.get('legacy') or result.get('duplicate'):
            send(gateway,chat,'این گزارش قبلاً دریافت شده است.')
            return
        if result.get('correction_needed'):
            send(gateway,chat,'لطفاً در پاسخ به همان مورد، فقط کد دقیق دستگاه را بفرستید.')
            return
        if result.get('invalid') or result.get('missing'):
            send(gateway,chat,INVALID)
            return
        count = sum(i['status']=='COMMITTED' for i in result['items'])
        pending = [i for i in result['items'] if i['status']=='PENDING_CORRECTION']
        if any(i['status']=='FAILED' for i in result['items']):
            raise RuntimeError('unreconciled Writer operation')
        if count:
            amount = result.get('committed') or count
            send(gateway,chat,f"✅ {readable(str(amount))} فعالیت در اکسل Function ثبت شد.\nتاریخ {readable(result['date'])}")
            reported = True
        else:
            send(gateway,chat,INVALID)
        for item in pending:
            text = f"⚠️ مورد {readable(str(item['number']))} ثبت نشد.\n{item['raw']}\n{item['reason']}"
            origin = await ui.present(gateway,send,chat,text+'\nبرای اصلاح کد، در پاسخ به همین پیام کد دقیق را بفرستید.',[],False)
            if not origin:
                raise RuntimeError('correction prompt undelivered')
            bound = await ui.run(dict(action='auto_bind',actor=actor,chat=chat,report_id=result['id'],
                                      origin=origin,number=item['number']))
            if not bound['ok']:
                raise RuntimeError('correction prompt unbound')
    except Exception:
        logging.getLogger(__name__).warning('Automatic maintenance report requires review: %s',payload.get('source_id') or payload.get('reply_id') or 'status')
        send(gateway,chat,'برای پیگیری بند معلق، «وضعیت گزارش» را بفرستید.' if reported else TECHNICAL)
        send(gateway,OWNER,'ثبت خودکار کرمعلی نیاز به بررسی دارد. پیام '+str(payload.get('source_id') or payload.get('reply_id') or 'وضعیت'))
