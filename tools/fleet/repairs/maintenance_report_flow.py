"""Durable two-step approval for a group of independently checked report items."""
from contextlib import closing, contextmanager
from copy import deepcopy
import hashlib
import io
import json
from pathlib import Path
import re
import secrets
import time
import uuid

import openpyxl

from . import entry_dates, maintenance_identity as identity, maintenance_service as service
from .entry_service import (EntryError, EntryAuthorizationDenied, commit_workbook, open_audit, recover_prepared,
                            source_reader, writer_lock)
from .maintenance_report import KIND, attach_records, delivery_review, verify_operation
from .maintenance_report_parse import ALIAS_VERSION, DEVICE_CODE, DIGITS, PREFIX_CODE, TOKEN, parse, work_items
from .maintenance_report_policy import CONFIG, enabled, partial_enabled, permission
from .maintenance_report_store import (correction_prompt,load_workflow,open_workflows,operation_id,remember,save_workflow)
from tools.fleet.maintenance_daily.prepared import source_proof
from tools.scheduler.report_errors import ReportFailure

TTL = 900
EXPIRED = 'این دکمه منقضی شده است؛ «وضعیت گزارش» را بفرستید.'
SOURCE_ID = re.compile(r'bale:[1-9][0-9]*\Z')
MESSAGE_ID = re.compile(r'[1-9][0-9]*\Z')
ITEM_ID = re.compile(r'[0-9a-f]{32}\Z')
RETRYABLE = ('VALID', 'FAILED')


class AllItemsRejected(EntryError):
    def __init__(self, rejected):
        super().__init__('موارد این تأیید به اصلاح نیاز دارند.')
        self.rejected = rejected


def _prove_source(digest, runtime, source):
    try:
        source_proof(digest,runtime=runtime,source=source)
    except ReportFailure as exc:
        raise EntryError('نسخهٔ اکسل با دفتر ثبت قابل تأیید نیست؛ بررسی فایل لازم است.') from exc


def _target(book, parsed):
    target = service.machine(book, parsed['code'] or 'متفرقه', independent=True)
    prefix = re.match(r'[A-Z]+',target['canonical'])
    for label in parsed['device_labels']:
        expected = ('HD' if label.startswith(('تراک','دام')) else 'EX' if label == 'بیل'
                    else 'WA' if label == 'لودر' else 'D' if label.startswith('بول') else None)
        if expected and prefix and prefix[0] != expected:
            raise EntryError('نوع دستگاه با کد واردشده تطابق ندارد؛ کد دقیق را وارد کنید.')
    return target


def _check_item(book, parsed, date, settings):
    if parsed['issue']:
        raise EntryError(parsed['issue'])
    target = _target(book,parsed)
    sheet = book[target['sheet']]
    header, columns = service.record_columns(sheet)
    if 'recorder' not in columns:
        raise EntryError('ستون ثبت‌کننده در شیت دستگاه موجود نیست؛ قالب فایل باید بررسی شود.')
    record = dict(target,date=date,description=parsed['description'],parts='',
                  **{key:settings[key] for key in ('recorder','department','mechanic')})
    service.validate_text(record['description'])
    entered = service.key(record['description'])
    for row in range(header+1,sheet.max_row+1):
        values = {field:sheet.cell(row,col).value for field,col in columns.items()}
        try:
            saved_date = entry_dates.formatted(entry_dates.jalali_date(values['date']))
        except (ValueError, TypeError):
            continue
        if saved_date != date or service.key(values['recorder']) != service.key(record['recorder']):
            continue
        if target['sheet'] == 'متفرقه' and service.key(values['code']) != service.key(record['department']):
            continue
        saved_description = str(values['description'] or '')
        if entered == service.key(saved_description) or entered in map(service.key,saved_description.splitlines()):
            raise EntryError('این بند برای همین تاریخ و دستگاه تکراری است؛ شرح مورد را بررسی کنید.')
    return record


def _classify(book, report, settings, items):
    for item in items:
        item.pop('record',None)
        try:
            record = _check_item(book,item['parsed'],report['date'],settings)
            service.append_record(book,record,recorder=settings['recorder'],independent=True)
        except EntryError as exc:
            item.update(status='PENDING_CORRECTION',reason=str(exc))
        else:
            item.update(status='VALID',record=record,reason='')


def _candidate_cells(data, result, *, require_identity):
    with closing(openpyxl.load_workbook(io.BytesIO(data))) as book:
        for saved in result['records']:
            record, row = saved['record'], saved['row']
            sheet = book[record['sheet']]
            header, columns = service.record_columns(sheet)
            expected = {field:record[field] or None for field in columns}
            if record['sheet'] == 'متفرقه':
                expected['code'] = record['department']
            if row <= header or 'recorder' not in columns or any(
                    sheet.cell(row,col).value != expected[field] for field,col in columns.items()):
                raise EntryError('صحت ردیف در نسخهٔ ذخیره‌شده تأیید نشد؛ ثبت متوقف شد.')
        if require_identity:
            # The OOXML writer already validates preservation of native Notes.
            # Also require each newly assigned Writer UUID on the serialized rows.
            if not identity.active(book):
                raise EntryError('IDENTITY_SCHEMA_MISSING')
            for group, saved in enumerate(result['records']):
                sheet = book[saved['sheet']]
                anchor = sheet.cell(saved['row'],service.layout(sheet)[1]['date'])
                note = anchor.comment
                expected = uuid.uuid5(identity.NAMESPACE,f"{result['operation']}:{group}:{sheet.title}").hex
                if (not note or not note.text.startswith(identity.PREFIX) or
                        json.loads(note.text[len(identity.PREFIX):]).get('id') != expected):
                    raise EntryError('IDENTITY_ROW_CHANGED_OR_MOVED')


@contextmanager
def _snapshot(settings, runtime, codec):
    source = Path(settings['source']).resolve(strict=True)
    with writer_lock(runtime), closing(open_audit(runtime)) as db, source_reader(source) as stream:
        original = stream.read()
        digest = hashlib.sha256(original).hexdigest()
        recover_prepared(db,source,digest)
        _prove_source(digest,runtime,source)
        read = codec.get('workbook_reader')
        with closing(read(original) if read else openpyxl.load_workbook(io.BytesIO(original))) as book:
            if identity.active(book) and not read:
                with closing(identity.load(original)):
                    pass
            yield book


def _make_view(report, phase, now, **fields):
    report['view'] = dict(phase=phase,token=secrets.token_hex(8),origin=None,expires=now()+TTL,**fields)


def _response(report, *, committed=None, delivery=None):
    items = []
    for item in report['items']:
        items.append({key:item[key] for key in ('number','status','reason')} |
                     dict(raw=item['parsed']['raw'],record=item.get('record'),corrected=bool(item['correction'])))
    return dict(id=report['id'],source_id=report['source_id'],date=report['date'],
                items=items,view=report.get('view'),committed=committed,delivery=delivery)


def _bound(report, token, origin, now, phase):
    view = report.get('view') or {}
    if (not isinstance(token,str) or not origin or view.get('origin') != origin or
            view.get('token') != token or view.get('phase') != phase or view['expires'] <= now()):
        raise EntryError(EXPIRED)
    return view


def _corrected(item, raw):
    if not isinstance(raw,str) or not 1 <= len(raw) <= 1800 or len(raw.splitlines()) != 1:
        raise EntryError('اصلاح را در یک خط، با کد دستگاه و شرح همان مورد بفرستید.')
    clean = raw.strip().translate(DIGITS)
    if re.fullmatch(TOKEN,clean):
        previous = item['parsed']['raw']
        plain = previous.translate(DIGITS).replace('\u200c',' ').replace('ي','ی').replace('ك','ک')
        match = DEVICE_CODE.search(plain) or PREFIX_CODE.search(plain)
        if not match or item['parsed']['issue']:
            raise EntryError('کد و شرح کامل همین مورد را در یک خط بفرستید.')
        start, end = match.span(1)
        raw = previous[:start] + raw.strip() + previous[end:]
    parsed, = work_items('گزارش\n'+raw,independent=True)
    if item['parsed']['code'] is not None or item['parsed']['device_labels'] or item['parsed']['issue']:
        if not parsed['code']:
            raise EntryError('کد دقیق دستگاه را همراه شرح وارد کنید.')
    # Original spans always refer to the incoming Bale report.
    parsed['span'] = item['parsed']['span']
    return parsed


def _commit(actor, report, request, settings, *, runtime, codec, authorized, day, ledger):
    correction = request['correction']
    require_identity = False
    def date_check():
        if correction:
            if (len(request['approved_items']) != 1 or request['date'] != report['date'] or
                    report['items'][request['approved_items'][0]['number']-1]['correction'] is None):
                raise EntryError('تأیید اصلاح به بند و تاریخ گزارش اصلی وابسته است.')
        else:
            entry_dates.validate_selected(request['date'],day)

    def mutate(book):
        nonlocal require_identity
        source = Path(settings['source']).resolve()
        original = source.read_bytes()
        require_identity = identity.active(book) and not identity.historical(book)
        if identity.active(book) and not codec:
            with closing(identity.load(original)):
                pass
        _prove_source(hashlib.sha256(original).hexdigest(),runtime,source)
        records, accepted, rejected = [], [], {}
        # The approval authorizes each frozen row. A later item-level
        # conflict can only remove that row. Global errors abort publication.
        for item in request['approved_items']:
            try:
                current = _check_item(book,report['items'][item['number']-1]['parsed'],report['date'],settings)
                if current != item['record']:
                    raise EntryError('مقصد ثبت تغییر کرده است؛ کد دقیق دستگاه را دوباره وارد کنید.')
                row = service.append_record(book,current,recorder=settings['recorder'],independent=True)
            except EntryError as exc:
                rejected[item['id']] = str(exc)
            else:
                records.append(dict(row,record=current))
                accepted.append(item['id'])
        if not records:
            raise AllItemsRejected(rejected)
        attach_records(book,records,request['operation'])
        result = dict(operation=request['operation'],date=request['date'],recorder=settings['recorder'],
                      records=records,item_ids=accepted,rejected_items=rejected,
                      delivery=delivery_review(request['date'],ledger))
        verify_operation(request,result)
        return result

    return commit_workbook(actor,request,settings,mutate,runtime=runtime,command='گزارش تعمیرات',
                           authorization_check=authorized,replay_check=service.verify_replay,date_check=date_check,
                           verify_candidate=lambda data,result:_candidate_cells(data,result,require_identity=require_identity),
                           **codec)


def _complete(report, result):
    for item in report['items']:
        if item['id'] in result['item_ids']:
            item.update(status='COMMITTED',reason='',operation=result['operation'])
        elif item['id'] in result['rejected_items']:
            item.pop('record',None)
            item.update(status='PENDING_CORRECTION',reason=result['rejected_items'][item['id']])
    report['attempt'] = None
    report['view'] = None


def _recover(report, actor, settings, runtime):
    request = report.get('attempt')
    if not request:
        return None
    source = Path(settings['source']).resolve(strict=True)
    fingerprint = hashlib.sha256(json.dumps([actor,str(source),request],sort_keys=True,ensure_ascii=False).encode()).hexdigest()
    with writer_lock(runtime), closing(open_audit(runtime)) as db, source_reader(source) as stream:
        digest = hashlib.sha256(stream.read()).hexdigest()
        recover_prepared(db,source,digest)
        _prove_source(digest,runtime,source)
        row = db.execute('SELECT * FROM edits WHERE operation=?',(request['operation'],)).fetchone()
        if row and (row['fingerprint'] != fingerprint or row['status'] not in ('succeeded','not_applied')):
            raise EntryError('نتیجهٔ ثبت قبلی قطعی نیست؛ بررسی دفتر ثبت لازم است.')
        if row and row['status'] == 'succeeded':
            service.verify_replay(db,source,digest,row)
            result = json.loads(row['result'])
            verify_operation(request,result)
            _complete(report,result)
            return result
    report['attempt'] = None
    return None


def _approved_history(db, settings, actor, chat, source_id, operation):
    """Allow a fresh review of an exact failed receipt without rewriting it."""
    admissions = settings.get('historical_recovery', {})
    approval = admissions.get(source_id) if isinstance(admissions, dict) else None
    if approval is None:
        return None, None
    if not isinstance(approval, dict) or set(approval) != {'raw_sha256', 'date'}:
        raise EntryError('مجوز بررسی گزارش قبلی معتبر نیست.')
    receipts = db.execute('''SELECT rowid,* FROM maintenance_report_messages
        WHERE operation=? AND actor=? AND chat=? AND source_id=?''',
        (operation, actor, chat, source_id)).fetchall()
    if len(receipts) != 1:
        raise EntryError('نسخهٔ یکتای گزارش قبلی در دفتر ثبت پیدا نشد.')
    receipt = receipts[0]
    if (receipt['status'] != 'needs_review' or receipt['raw_hash'] != approval['raw_sha256'] or
            hashlib.sha256(receipt['raw_text'].encode('utf8')).hexdigest() != receipt['raw_hash']):
        raise EntryError('سابقهٔ گزارش با مجوز بررسی قبلی تطبیق ندارد.')
    if db.execute('''SELECT 1 FROM edits WHERE operation=? OR
        (actor=? AND (json_extract(request,'$.source_id')=? OR json_extract(request,'$.raw_text')=?))
        LIMIT 1''', (operation, actor, source_id, receipt['raw_text'])).fetchone():
        raise EntryError('برای این گزارش سابقهٔ عملیات ثبت وجود دارد؛ عملیات قبلی باید بررسی شود.')
    return receipt, dict(approval)


def process(action, actor, chat, *, source_id=None, raw=None, report_id=None, token=None, origin=None,
            number=None, reply_id=None, config=CONFIG, entry_config=service.CONFIG, runtime=service.RUNTIME,
            day=None, ledger=None, now=time.time):
    actor, chat, runtime = str(actor), str(chat), Path(runtime)
    settings = permission(actor,chat,config=config,entry_config=entry_config)
    if actor != chat or not partial_enabled(settings) or not enabled(config):
        raise PermissionError('مجوز این مسیر ثبت گزارش وجود ندارد.')
    for field in ('recorder','mechanic','department'):
        service.validate_text(settings[field],200)
    if source_id is not None:
        if not SOURCE_ID.fullmatch(source_id):
            raise EntryError('شناسهٔ پیام گزارش معتبر نیست.')
        report_id = operation_id(actor,chat,source_id)
    if report_id is not None and not ITEM_ID.fullmatch(report_id):
        raise EntryError(EXPIRED)
    if not (runtime/'audit.sqlite3').is_file():
        raise EntryError('دفتر ثبت تعمیرات در دسترس نیست.')
    from .maintenance_publication import writer_codec
    codec = writer_codec(entry_config,runtime)
    ledger = ledger or service.ROOT/'runtime/scheduler/runs.sqlite3'

    def authorized():
        try:
            current = permission(actor,chat,config=config,entry_config=entry_config)
        except PermissionError as exc:
            raise EntryAuthorizationDenied(str(exc)) from exc
        if not enabled(config) or current != settings:
            raise EntryAuthorizationDenied('مجوز یا مقصد ثبت گزارش تغییر کرده است.')

    with writer_lock(runtime/'maintenance-report-flow'), closing(open_workflows(runtime)) as db:
        report = load_workflow(db,actor,chat,report_id)
        historical_approval = None
        historical_receipt = None
        if action == 'reply':
            if not origin or not MESSAGE_ID.fullmatch(str(reply_id or '')):
                raise EntryError(EXPIRED)
            row = correction_prompt(db,actor,chat,origin)
            report = load_workflow(db,actor,chat,row['operation']) if row else None
        if action == 'status' and not report:
            row = db.execute("""SELECT * FROM maintenance_report_messages WHERE actor=? AND chat=?
                AND status='partial_received' AND (? IS NULL OR operation=?) ORDER BY rowid DESC LIMIT 1""",
                (actor,chat,report_id,report_id)).fetchone()
            if row is None and source_id:
                historical_receipt, historical_approval = _approved_history(
                    db, settings, actor, chat, source_id, report_id)
                row = historical_receipt
            if row:
                action, source_id, report_id, raw = 'prepare', row['source_id'], row['operation'], row['raw_text']
        if action == 'prepare':
            if not source_id or not isinstance(raw,str) or not 1 <= len(raw) <= 16000:
                raise EntryError('متن یا شناسهٔ گزارش معتبر نیست.')
            receipt = historical_receipt
            if receipt is None:
                digest = remember(runtime,report_id,actor,chat,source_id,raw,status='partial_received')
                receipt = db.execute('''SELECT rowid,raw_hash,status FROM maintenance_report_messages
                    WHERE operation=? AND raw_hash=?''',(report_id,digest)).fetchone()
            if receipt['status'] != 'partial_received' and historical_approval is None:
                return dict(legacy=receipt['status'])
            other = db.execute("""SELECT source_id FROM maintenance_report_messages
                WHERE actor=? AND chat=? AND raw_hash=? AND rowid<? AND status<>'conflict'
                ORDER BY rowid LIMIT 1""",(actor,chat,receipt['raw_hash'],receipt['rowid'])).fetchone()
            if other and not report:
                return dict(duplicate=other['source_id'])
            if not report:
                parsed = parse(raw,day,independent=True)
                if historical_approval and parsed['date'] != historical_approval['date']:
                    raise EntryError('تاریخ گزارش با مجوز بررسی قبلی تطبیق ندارد.')
                report = dict(id=report_id,source_id=source_id,date=parsed['date'],raw=raw,settings=settings,
                              attempt=None,view=None,items=[
                                  dict(id=uuid.uuid5(uuid.UUID(report_id),str(n)).hex,number=n,parsed=item,
                                       status='VALID',reason='',correction=None)
                                  for n,item in enumerate(parsed['items'],1)])
                if historical_approval:
                    report['historical_recovery'] = historical_approval
                with _snapshot(settings,runtime,codec) as book:
                    _classify(book,report,settings,report['items'])
                save_workflow(db,actor,chat,report)
            else:
                return dict(duplicate=report['source_id'])
        if not report:
            if action == 'status':
                return dict(missing=True)
            raise EntryError(EXPIRED)
        if report['settings'] != settings:
            raise PermissionError('هویت ثبت‌کننده یا مقصد گزارش تغییر کرده است؛ بررسی لازم است.')

        if report.get('attempt'):
            if report['attempt'].get('owner_approval'):
                raise EntryError('نتیجهٔ ثبت مدیریتی این گزارش در دفتر ثبت در حال بررسی است.')
            if action != 'status':
                raise EntryError('ابتدا «وضعیت گزارش» را برای بررسی ثبت قبلی بفرستید.')
            recovered = _recover(report,actor,settings,runtime)
            save_workflow(db,actor,chat,report)
            if recovered:
                _make_view(report,'offer',now,open_date=report['date'] in entry_dates.choices(day))
                save_workflow(db,actor,chat,report)
                return _response(report,committed=len(recovered['item_ids']),delivery=recovered['delivery'])
            action = 'status'

        if action in ('prepare','status'):
            retry = [item for item in report['items'] if item['status'] in RETRYABLE]
            date_open = report['date'] in entry_dates.choices(day)
            if retry and (action == 'status' or not date_open):
                retryable = [item for item in retry if date_open or item['correction']]
                if retryable:
                    with _snapshot(settings,runtime,codec) as book:
                        _classify(book,report,settings,retryable)
            _make_view(report,'offer',now,open_date=date_open)
        elif action == 'bind':
            view = report.get('view') or {}
            if (not token or view.get('token') != token or view.get('origin') is not None
                    or not isinstance(origin,str) or not MESSAGE_ID.fullmatch(origin) or view['expires'] <= now()):
                raise EntryError(EXPIRED)
            view['origin'] = origin
        elif action == 'approve':
            _bound(report,token,origin,now,'offer')
            valid = [item for item in report['items'] if item['status']=='VALID']
            if number is None:
                entry_dates.validate_selected(report['date'],day)
                valid = [item for item in valid if not item['correction']]
            else:
                valid = [item for item in valid if item['number']==number and item['correction']]
            if not valid:
                raise EntryError('مورد آمادهٔ تأیید وجود ندارد.')
            _make_view(report,'text',now,numbers=[item['number'] for item in valid],
                       correction=number is not None,offer_message=origin)
        elif action == 'correct':
            _bound(report,token,origin,now,'offer')
            if not any(item['number']==number and item['status']=='PENDING_CORRECTION' for item in report['items']):
                raise EntryError('این بند در انتظار اصلاح نیست.')
            _make_view(report,'reply',now,number=number)
        elif action == 'reply':
            view = report['view']
            _bound(report,view['token'],origin,now,'reply')
            if any(entry['message_id']==reply_id for item in report['items']
                   for entry in item.get('revisions',[])):
                raise EntryError('این پیام اصلاح قبلاً دریافت شده است.')
            item = report['items'][view['number']-1]
            edits = item.setdefault('revisions',[])
            edits.append(dict(message_id=reply_id,raw=raw))
            try:
                item['parsed'] = _corrected(item,raw)
            except (EntryError,ValueError) as exc:
                item['reason'] = str(exc)
            else:
                item['correction'] = reply_id
                item.update(status='FAILED',reason='بررسی اصلاح در اکسل باید تکمیل شود.')
                report['view'] = None
                save_workflow(db,actor,chat,report)
                with _snapshot(settings,runtime,codec) as book:
                    _classify(book,report,settings,[item])
            _make_view(report,'offer',now,open_date=report['date'] in entry_dates.choices(day))
        elif action == 'confirm':
            view = _bound(report,token,origin,now,'text')
            selected = [report['items'][n-1] for n in view['numbers']]
            if any(item['status']!='VALID' for item in selected):
                raise EntryError(EXPIRED)
            request = dict(_kind=KIND,operation=uuid.uuid4().hex,report_id=report['id'],source_id=report['source_id'],
                           date=report['date'],raw_text=report['raw'],recorder=settings['recorder'],
                           correction=view['correction'],alias_version=ALIAS_VERSION,
                           approvals=dict(report_message=view['offer_message'],text_message=origin),
                           approved_items=[dict(id=item['id'],number=item['number'],record=deepcopy(item['record']),
                                                correction=item['correction']) for item in selected])
            if report.get('historical_recovery'):
                request['historical_recovery'] = report['historical_recovery']
            for item in selected:
                item.update(status='FAILED',reason='نتیجهٔ تلاش ثبت باید بررسی شود.')
            report['attempt'], report['view'] = request, None
            save_workflow(db,actor,chat,report)
            # An interrupted write leaves the same operation available to
            # reconcile against the Journal and exact source bytes.
            try:
                result = _commit(actor,report,request,settings,runtime=runtime,codec=codec,
                                 authorized=authorized,day=day,ledger=ledger)
            except AllItemsRejected as exc:
                _complete(report,dict(item_ids=[],rejected_items=exc.rejected))
                _make_view(report,'offer',now,open_date=report['date'] in entry_dates.choices(day))
                save_workflow(db,actor,chat,report)
                return _response(report)
            _complete(report,result)
            _make_view(report,'offer',now,open_date=report['date'] in entry_dates.choices(day))
            save_workflow(db,actor,chat,report)
            return _response(report,committed=len(result['item_ids']),delivery=result['delivery'])
        else:
            raise EntryError('درخواست گزارش معتبر نیست.')
        save_workflow(db,actor,chat,report)
        return _response(report)
