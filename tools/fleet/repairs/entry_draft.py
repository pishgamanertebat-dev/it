"""Small daily-entry drafts in the existing audit DB; existing Writer alone saves Excel."""
import json
import re
import uuid
import time
from contextlib import closing
import hashlib
from pathlib import Path

from . import entry_service as entry, entry_dates

MONTHS = ('فروردین', 'اردیبهشت', 'خرداد', 'تیر', 'مرداد', 'شهریور', 'مهر', 'آبان', 'آذر', 'دی', 'بهمن', 'اسفند')


from .entry_draft_store import DraftStore, ACTORS


def gate(actor, chat, config):
    if str(actor) not in ACTORS or str(actor) != str(chat):
        raise entry.EntryAuthorizationDenied('اجازهٔ تهیهٔ پیش‌نویس روزانه را ندارید.')
    return entry.permission(actor, config)


def report_date(raw, current):
    if raw is None:
        return current
    text = raw.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).replace('آ','ا')
    match = re.search(r'1[34]\d{2}[/.-]\d{1,2}[/.-]\d{1,2}',text)
    if match:
        return entry_dates.formatted(entry_dates.jalali_date(match[0]))
    if text.strip() == 'امروز':
        return current
    for month,name in enumerate(MONTHS,1):
        match = re.search(r'(\d{1,2})\s*'+name.replace('آ','ا')+r'(?:\s*(1[34]\d{2}))?',text)
        if match:
            return entry_dates.formatted(entry_dates.jalali_date(f'{match[2] or current[:4]}/{month}/{match[1]}'))
    raise entry.EntryError('تاریخ ذکرشده روشن نیست؛ از «📅 انتخاب تاریخ» تاریخ درست را انتخاب کنید.')


def refresh(d, actor, *, config, day=None, fleet_db=entry.FLEET_DB):
    p=d['payload']; current=day or entry_dates.today(); issues=[]; groups={}
    previous={(g['date'],g['section'],g['code']):g['expected'] for g in p.get('groups',[])}
    from tools.authorization.net import repairs_sections
    sections=repairs_sections(actor, json.loads(config.read_text('utf8')))
    if p['section'] not in sections:
        issues.append('بخش مجاز را صریحاً انتخاب کنید.')
    try:
        p['date'] = p.get('date_override') or report_date(p['extraction']['report_date'],current)
        entry.selected_date(p['date'],current)
    except ValueError as exc:
        p['date']=p.get('date_override') or p['extraction']['report_date']
        issues.append(str(exc))
    unresolved=p['extraction']['unresolved']
    settings=entry.permission(actor,config)
    book=entry.openpyxl.load_workbook(settings['source'])
    try:
        for i,item in enumerate(p['items']):
            if item['excluded']:
                continue
            for issue in unresolved:
                if issue['item'] == i and issue['field'] not in item['resolved']:
                    issues.append(f"مورد {i+1}: {issue['question']}")
            if not item['code']:
                issues.append(f'مورد {i+1}: کد دستگاه لازم است.')
                continue
            if not item['description'].strip() or len(item['description'])>1800:
                issues.append(f'مورد {i+1}: شرح باید بین ۱ و ۱۸۰۰ نویسه باشد.')
                continue
            try:
                selected=entry.preview(actor,item['code'],p['section'],config=config,day=current,date=p['date'],fleet_db=fleet_db,_book=book)
                entry.require_target_row(book, selected['code'], p['date'])
                code=selected['code']; group=groups.setdefault(code,dict(selected, indices=[],description=''))
                group['indices'].append(i)
                group['description'] += ('\n' if group['description'] else '')+item['description']
            except (ValueError,PermissionError) as exc:
                issues.append(f'مورد {i+1}: {exc}')
    finally:
        book.close()
    for issue in unresolved:
        if issue['item'] is None and issue['field'] not in p['resolved']:
            issues.append(issue['question'])
    if not groups:
        issues.append('هیچ دستگاه معتبر برای ثبت انتخاب نشده است.')
    for code,group in groups.items():
        field=(group['date'],group['section'],code)
        if field in previous and previous[field] != group['expected']:
            p['modes'].pop(code,None)
        mode=p['modes'].get(code)
        if group['expected'] and mode not in ('append','replace'):
            issues.append(f'{code}: افزودن یا جایگزینی شرح قبلی را انتخاب کنید.')
        group['write_mode']=mode or 'replace'
        group['final_description']=(group['expected']+'\n'+group['description']
                                      if mode=='append' and group['expected'] else group['description'])
        if len(group['final_description'])>1800:
            issues.append(f'{code}: متن نهایی بیش از ۱۸۰۰ نویسه است.')
        group['operation']=uuid.uuid4().hex
        group['duplicate_text'] = bool(group['expected'] and
            (group['description'].strip() == group['expected'].strip()
             or group['description'].strip() in group['expected'].splitlines()))
    p.update(groups=list(groups.values()),issues=list(dict.fromkeys(issues)),sections=list(sections),
             workflow='two_step_v1',batch_operation=uuid.uuid4().hex)
    return d


def create(actor,chat,extraction,*,config=entry.CONFIG,runtime=entry.RUNTIME,day=None,fleet_db=entry.FLEET_DB):
    gate(actor,chat,config)
    # Revalidate the strict original schema at the worker boundary.
    from .entry_extract import validate
    original=dict(extraction,items=[dict(item,source_span={k:v for k,v in item['source_span'].items() if k in ('text','occurrence')}) for item in extraction['items']])
    extraction=validate(original,extraction['raw_text'])
    from tools.authorization.net import repairs_sections
    sections=repairs_sections(actor,json.loads(config.read_text('utf8')))
    p=dict(extraction=extraction,date=None,section=sections[0] if len(sections)==1 else None,
           modes={},resolved=[],items=[dict(code=item['raw_code'],description=item['raw_description'],excluded=False,resolved=[]) for item in extraction['items']])
    d=refresh({'payload':p},actor,config=config,day=day,fleet_db=fleet_db)
    return DraftStore(runtime).create(actor,chat,d['payload'])


def edit(d,field,value,*,index=None,config=entry.CONFIG,runtime=entry.RUNTIME,day=None,fleet_db=entry.FLEET_DB):
    gate(d['actor'],d['chat'],config)
    if d['status'] in ('COMMITTING','PARTIAL','DONE') or d['results']:
        raise entry.EntryError('ثبت شروع شده؛ نتیجهٔ همان عملیات را بازیابی کنید.')
    p=d['payload']
    if field=='date':
        p['date_override']=entry.selected_date(value,day);p['resolved']=list(set(p['resolved'])|{'date'});p['modes']={}
    elif field=='section':
        entry.permission(d['actor'],config,section=value);p['section']=value;p['modes']={}
    elif field=='mode':
        if value not in ('append','replace') or index not in [g['code'] for g in p['groups']]:
            raise entry.EntryError('روش ذخیره معتبر نیست.')
        p['modes'][index]=value
    elif field in ('code','description','exclude','accept_raw'):
        if type(index) is not int or not 0<=index<len(p['items']):
            raise entry.EntryError('شمارهٔ مورد معتبر نیست.')
        item=p['items'][index]
        if field=='exclude':item['excluded']=True
        elif field=='accept_raw':item['resolved']=list(set(item['resolved'])|{'description'})
        else:
            if not isinstance(value,str) or not value.strip() or '\x00' in value or len(value)>(30 if field=='code' else 1800):
                raise entry.EntryError('متن اصلاح معتبر نیست.')
            item[field]=value.strip();item['resolved']=list(set(item['resolved'])|{field})
    elif field!='review':
        raise entry.EntryError('اصلاح معتبر نیست.')
    refresh(d,d['actor'],config=config,day=day,fleet_db=fleet_db)
    return DraftStore(runtime).update(d,'edited',edit=True,evidence=dict(field=field,index=index))


def approval(d):
    for name in ('text_ok','write_ok'):
        proof=d[name] or {}
        if proof.get('hash')!=d['hash'] or proof.get('version')!=d['version']:
            raise entry.EntryError('تأیید صریح متن و ثبت برای همین نسخه لازم است.')
    if (d['payload'].get('workflow') != 'two_step_v1' or d['text_ok'] != d['write_ok']
            or d['text_ok'].get('intent') != 'confirm_text_and_write'
            or d['text_ok'].get('write_intent') is not True
            or d['text_ok'].get('actor') != d['actor'] or d['text_ok'].get('chat') != d['chat']
            or d['text_ok'].get('stage') != 'TEXT_CONFIRM' or d['text_ok'].get('action') != 'confirm_text'
            or d['text_ok'].get('expires',0) < time.time()):
        raise entry.EntryError('صفحهٔ بررسی قدیمی است؛ متن را در صفحهٔ جدید دوباره تأیید کنید.')
    if d['status'] not in ('COMMITTING','PARTIAL','DONE') or d['payload']['issues']:
        raise entry.EntryError('پیش‌نویس اجازهٔ ثبت ندارد.')


def commit(actor,chat,identifier,*,config=entry.CONFIG,runtime=entry.RUNTIME,day=None,fleet_db=entry.FLEET_DB):
    gate(actor,chat,config);store=DraftStore(runtime);d=store.load(identifier,actor,chat);approval(d)
    def check():
        latest=store.load(identifier,actor,chat);approval(latest)
        if latest['hash']!=d['hash'] or latest['version']!=d['version']:
            raise entry.EntryError('تأیید پیش‌نویس تغییر کرده است.')
        entry.selected_date(d['payload']['date'],day)
    check()
    requests=[{k:g[k] for k in ('code','name','date','section','expected','description','write_mode','operation')}
              for g in d['payload']['groups']]
    try:
        result=entry.commit_batch(actor,requests,d['payload']['batch_operation'],config=config,runtime=runtime,
                                  day=day,fleet_db=fleet_db,approval_check=check)
    except Exception as exc:
        return recover(d,config=config,runtime=runtime,day=day,fleet_db=fleet_db,error=str(exc))
    return complete(store.load(identifier,actor,chat),result,store)


def complete(d,result,store):
    for g,r in zip(d['payload']['groups'],result['devices'],strict=True):
        d['results'][g['operation']]=dict(status='succeeded',result=dict(r,delivery=result.get('delivery',{})))
    d['status']='DONE'
    # The receipt evidence also lives durably in the Writer journal/batch event.
    return store.update(d,'batch_result',evidence=result)


def recover(d,*,config=entry.CONFIG,runtime=entry.RUNTIME,day=None,fleet_db=entry.FLEET_DB,error=None):
    """Read/reconcile the existing operation; never publish or retry a write."""
    store=DraftStore(runtime);settings=gate(d['actor'],d['chat'],config)
    batch=d['payload'].get('batch_operation')
    with entry.writer_lock(Path(runtime)), closing(entry.open_audit(runtime)) as db:
        source=Path(settings['source']).resolve(strict=True)
        with entry.source_reader(source) as stream:
            digest=hashlib.sha256(stream.read()).hexdigest()
            try:
                entry.recover_prepared(db,source,digest)
            except entry.EntryError as exc:
                error=str(exc)
            row=db.execute('SELECT * FROM edits WHERE operation=?',(batch,)).fetchone() if batch else None
            if row and row['status']=='succeeded':
                return complete(store.load(d['id'],d['actor'],d['chat']),json.loads(row['result']),store)
            if (row and row['status']=='prepared') or not batch:
                d=store.load(d['id'],d['actor'],d['chat'])
                # Legacy partial reports are never replayed by a new callback.
                for g in d['payload']['groups']:
                    old=db.execute('SELECT * FROM edits WHERE operation=?',(g['operation'],)).fetchone()
                    if old and old['status']=='succeeded':
                        d['results'][g['operation']]=dict(status='succeeded',result=json.loads(old['result']))
                d.update(status='PARTIAL',binding=None)
                return store.update(d,'recovery_required',evidence=error)
    # The journal proves no publication. Rebase the preview and require fresh approval.
    d=store.load(d['id'],d['actor'],d['chat'])
    d.update(status='EDIT',results={},text_ok=None,write_ok=None,binding=None)
    refresh(d,d['actor'],config=config,day=day,fleet_db=fleet_db)
    d['payload']['notice']='ثبت انجام نشد. '+(error or 'متن و وضعیت فعلی را بررسی و دوباره تأیید کنید.')
    return store.update(d,'write_not_applied',edit=True,evidence=error)


def execute(payload,*,config=entry.CONFIG,runtime=entry.RUNTIME,day=None,fleet_db=entry.FLEET_DB):
    from .entry_extract import enabled
    if not enabled():
        raise entry.EntryError('مسیر پیش‌نویس AI هنوز فعال نشده است؛ از فرم دستی «شرح خرابی» استفاده کنید.')
    actor,chat=payload['actor'],payload['chat'];gate(actor,chat,config)
    kw=dict(config=config,runtime=runtime,day=day,fleet_db=fleet_db)
    if payload['action']=='ai_create':return create(actor,chat,payload['extraction'],**kw)
    if payload['action']=='ai_commit':return commit(actor,chat,payload['draft'],**kw)
    d=DraftStore(runtime).load(payload['draft'],actor,chat)
    if payload['action']=='ai_resume':
        if d['status'] in ('COMMITTING','PARTIAL'):
            entry.permission(actor,config,section=d['payload']['section'])
            return recover(d,**kw)
        if d['status'] in ('EDIT','TEXT_OK'):
            return edit(d,'review',None,**kw)
        return d
    if payload['action']=='ai_edit':return edit(d,payload['field'],payload.get('value'),index=payload.get('index'),**kw)
    raise entry.EntryError('درخواست پیش‌نویس معتبر نیست.')
