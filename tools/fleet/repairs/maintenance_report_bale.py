"""Approved private report receipt writes maintenance without confirmation buttons."""
import asyncio
from copy import copy
import logging
from pathlib import Path
import re

from .maintenance_report_parse import declared_report, work_items
from .maintenance_report_policy import permission, CONFIG
from .maintenance_report_store import RUNTIME, operation_id, remember

log = logging.getLogger(__name__)


async def run_worker(payload):
    from .entry_bale import worker
    return await worker(payload,module='tools.fleet.repairs.maintenance_report')


def continue_coded(event, gateway, send, text):
    from .entry_ai_bale import _handler
    remaining = copy(event)
    remaining.text = text
    return _handler.handle(remaining,gateway,send=send,allow_start=True)


class MaintenanceReportHandler:
    def __init__(self, *, run=run_worker, authorize=permission, runtime=RUNTIME, followup=continue_coded):
        self.run, self.authorize, self.runtime, self.followup = run, authorize, runtime, followup
        self.tasks, self.busy = set(), set()

    def handle(self,event,gateway,*,send):
        source = event.source
        actor, chat = str(source.user_id), str(source.chat_id)
        if str(getattr(source.platform,'value',source.platform)).lower()!='bale' or source.chat_type!='dm' or actor!=chat:
            return None
        raw = getattr(event,'raw_message',None) or {}
        if isinstance(raw,dict) and raw.get('bale_inline_callback') is True:
            return None
        if not declared_report(event.text):
            return None
        try:
            settings = self.authorize(actor,chat)
        except (PermissionError,OSError,ValueError):
            return None
        if settings['scope']=='miscellaneous':
            try:
                if not any(item['code'] is None for item in work_items(event.text)):
                    return None
            except ValueError:
                return None
        message = str(getattr(event,'message_id','') or '')
        if not re.fullmatch(r'[1-9][0-9]*',message):
            send(gateway,chat,'شناسهٔ پیام گزارش در دسترس نیست؛ ثبت انجام نشد.')
            return {'action':'skip','reason':'maintenance-report-message-id'}
        source_id = 'bale:'+message
        operation = operation_id(actor,chat,source_id)
        if operation in self.busy:
            return {'action':'skip','reason':'maintenance-report-in-progress'}
        try:
            # The original text survives a crash before the worker even starts.
            remember(self.runtime,operation,actor,chat,source_id,event.text)
        except Exception as exc:
            send(gateway,chat,'ثبت گزارش متوقف شد: '+str(exc))
            return {'action':'skip','reason':'maintenance-report-preserve-failed'}
        self.busy.add(operation)
        async def execute():
            try:
                response = await self.run(dict(actor=actor,chat=chat,source_id=source_id,raw=event.text))
                if not response['ok']:
                    send(gateway,chat,'متن گزارش محفوظ است؛ ثبت اکسل انجام نشد: '+response['message'])
                    return
                result = response['result']
                codes = '، '.join(row['code'] for row in result['records'])
                message = (f"گزارش تعمیرات {result['date']} ثبت شد.\nثبت‌کننده: {result['recorder']}\n"
                           f"تعداد: {len(result['records'])}\nدستگاه‌ها / بخش: {codes}")
                if result['delivery']['status']=='correction_required':
                    message += '\nنسخهٔ قبلی PDF ارسال شده است؛ نیاز به بررسی اصلاحیه ثبت شد.'
                send(gateway,chat,message)
                if result.get('remaining_text'):
                    self.followup(event,gateway,send,result['remaining_text'])
            except Exception:
                log.exception('Maintenance report processing interrupted operation=%s',operation)
                send(gateway,chat,'متن گزارش نگه‌داری شد؛ نتیجهٔ ثبت نیاز به بررسی همان پیام دارد.')
            finally:
                self.busy.discard(operation)
        task = asyncio.get_running_loop().create_task(execute())
        self.tasks.add(task)
        task.add_done_callback(self.tasks.discard)
        return {'action':'skip','reason':'maintenance-report'}


_handler = MaintenanceReportHandler()
