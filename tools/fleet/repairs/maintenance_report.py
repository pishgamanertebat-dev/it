"""Deterministic report parsing, raw spans and append-only maintenance batches."""
from contextlib import closing
import json
from pathlib import Path
import re
import sqlite3

from tools.fleet.equipment_codes import ALIAS_VERSION
from . import entry_dates, maintenance_service as service
from .entry_service import EntryError, EntryAuthorizationDenied, commit_workbook
from .maintenance_report_policy import CONFIG, permission

KIND = 'maintenance_report_batch_v1'
from .maintenance_report_parse import parse
from .maintenance_report_store import operation_id, remember, record_outcome


def delivery_review(date, ledger):
    from datetime import datetime, timedelta
    from zoneinfo import ZoneInfo
    import jdatetime
    evidence = []
    try:
        with closing(sqlite3.connect(Path(ledger).resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory = sqlite3.Row
            tables = {r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table'")}
            for row in db.execute('''SELECT a.* FROM receipts a JOIN runs r
                ON r.schedule_id=a.schedule_id AND r.due=a.due
                WHERE r.task='maintenance_daily_report' AND a.delivery_kind='report'
                AND a.status IN ('sent','uncertain','sending')'''):
                due = datetime.fromisoformat(row['due'])
                if due.utcoffset() is None:
                    raise ValueError('Timezone required')
                target = jdatetime.date.fromgregorian(date=due.astimezone(ZoneInfo('Asia/Tehran')).date()-timedelta(days=1))
                if entry_dates.formatted(target) == date:
                    evidence.append(dict(row))
            if 'report_recovery_operation' in tables:
                evidence.extend(dict(row) for row in db.execute('''SELECT * FROM report_recovery_operation
                    WHERE target_date=? AND status IN ('sent','uncertain','sending')''',(date,)))
    except (OSError, sqlite3.Error, ValueError):
        return dict(status='verification_required',section='maintenance',receipts=[])
    return dict(status='correction_required' if evidence else 'no_recorded_delivery',
                section='maintenance',receipts=evidence)


def apply_records(book, records):
    """Append journal-identified records; used by the same Writer and guarded Sync."""
    return [dict(service.append_record(book, record, recorder=record['recorder']),
                 record=record) for record in records]


def verify_operation(request, result):
    if (request.get('_kind') != KIND or not isinstance(request.get('raw_text'),str)
            or not isinstance(result.get('records'),list) or not result['records']):
        raise EntryError('MAINTENANCE_SYNC_INVALID_REPORT_OPERATION')
    for saved in result['records']:
        record = saved.get('record',{})
        if (saved.get('sheet') != record.get('sheet') or saved.get('date') != request.get('date')
                or record.get('date') != request.get('date') or saved.get('recorder') != request.get('recorder')
                or record.get('recorder') != request.get('recorder')
                or not all(field in record for field in ('canonical','code','description','mechanic','parts'))):
            raise EntryError('MAINTENANCE_SYNC_INVALID_REPORT_RECORD')


def receive(actor, chat, source_id, raw, *, config=CONFIG, entry_config=service.CONFIG,
            runtime=service.RUNTIME, day=None, ledger=None):
    settings = permission(actor, chat, config=config, entry_config=entry_config)
    if not isinstance(source_id,str) or not re.fullmatch(r'[A-Za-z0-9:/_.-]{1,240}',source_id):
        raise EntryError('شناسهٔ پیام معتبر نیست؛ ثبت انجام نشد.')
    if not isinstance(raw,str) or not 1 <= len(raw) <= 16000:
        raise EntryError('طول گزارش معتبر نیست.')
    operation = operation_id(actor,chat,source_id)
    digest = remember(runtime,operation,actor,chat,source_id,raw)
    try:
        parsed = parse(raw,day)
        items = parsed['items']
        selected = [item for item in items if settings['scope']=='all' or item['code'] is None]
        remaining = [item['raw'] for item in items if settings['scope']=='miscellaneous' and item['code'] is not None]
        if not selected:
            raise EntryError('این گزارش مورد بدون کد برای متفرقه ندارد؛ مسیر دستگاه‌دار قبلی را ادامه دهید.')
        request = dict(_kind=KIND,operation=operation,source_id=source_id,date=parsed['date'],
                       raw_text=raw,items=selected,recorder=settings['recorder'],
                       department=settings['department'],mechanic=settings['mechanic'],alias_version=ALIAS_VERSION)
        def authorized():
            try:
                current = permission(actor,chat,config=config,entry_config=entry_config)
            except PermissionError as exc:
                raise EntryAuthorizationDenied(str(exc)) from exc
            if current != settings:
                raise EntryError('هویت، مقصد یا سیاست ثبت هم‌زمان تغییر کرد؛ ثبت متوقف شد.')
        def mutate(book):
            from tools.fleet.maintenance_daily.prepared import source_proof
            import hashlib
            source_proof(hashlib.sha256(Path(settings['source']).read_bytes()).hexdigest(),
                         runtime=runtime,source=Path(settings['source']).resolve())
            with closing(sqlite3.connect((Path(runtime)/'audit.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as audit:
                for previous in audit.execute("SELECT operation,request FROM edits WHERE actor=? AND status='succeeded'",(str(actor),)):
                    old = json.loads(previous[1])
                    if previous[0] != operation and old.get('_kind') == KIND and old.get('raw_text') == raw:
                        raise EntryError('متن یکسان قبلاً ثبت شده است؛ پیام تازه محفوظ است و ثبت تکراری نیاز به بررسی دارد.')
            # Re-resolve every target under the Writer lock. All rows publish once.
            groups = {}
            for item in selected:
                target = service.machine(book,item['code'] or 'متفرقه')
                group = groups.setdefault(target['sheet'],dict(target,date=request['date'],
                    mechanic=settings['mechanic'],department=settings['department'],
                    recorder=settings['recorder'],description='',parts=''))
                group['description'] += ('\n' if group['description'] else '') + item['description']
            for record in groups.values():
                service.validate_text(record['description'])
            records = apply_records(book,list(groups.values()))
            result = dict(date=request['date'],records=records,recorder=settings['recorder'],
                          remaining_text=raw.strip().splitlines()[0]+'\n'+'\n'.join(remaining) if remaining else '',
                          delivery=delivery_review(request['date'],ledger or service.ROOT/'runtime/scheduler/runs.sqlite3'))
            verify_operation(request,result)
            return result
        result = commit_workbook(actor,request,settings,mutate,runtime=runtime,day=day,
            command='گزارش تعمیرات',authorization_check=authorized,replay_check=service.verify_replay,
            date_check=lambda:entry_dates.validate_selected(request['date'],day))
        record_outcome(runtime,operation,digest,'succeeded',result)
        return dict(result,operation=operation)
    except Exception as exc:
        record_outcome(runtime,operation,digest,'needs_review',dict(error=str(exc)))
        raise


def main():
    import sys
    payload = json.load(sys.stdin)
    try:
        if set(payload) != {'actor','chat','source_id','raw'}:
            raise EntryError('ساختار درخواست معتبر نیست.')
        result = receive(**payload)
        print(json.dumps(dict(ok=True,result=result)))
    except Exception as exc:
        print(json.dumps(dict(ok=False,message=str(exc))))


if __name__=='__main__':main()
