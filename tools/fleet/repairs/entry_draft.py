"""Small daily-entry drafts in the existing audit DB; existing Writer alone saves Excel."""
import json
import re
import uuid

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
        mode=p['modes'].get(code)
        if group['expected'] and mode not in ('append','replace'):
            issues.append(f'{code}: افزودن یا جایگزینی شرح قبلی را انتخاب کنید.')
        group['write_mode']=mode or 'replace'
        group['final_description']=(group['expected']+'\n'+group['description']
                                      if mode=='append' and group['expected'] else group['description'])
        if len(group['final_description'])>1800:
            issues.append(f'{code}: متن نهایی بیش از ۱۸۰۰ نویسه است.')
        group['operation']=uuid.uuid4().hex
    p.update(groups=list(groups.values()),issues=list(dict.fromkeys(issues)),sections=list(sections))
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
        p['date_override']=entry.selected_date(value,day);p['resolved']=list(set(p['resolved'])|{'date'})
    elif field=='section':
        entry.permission(d['actor'],config,section=value);p['section']=value
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
            raise entry.EntryError('دو تأیید مستقل برای همین نسخه لازم است.')
    if d['text_ok']['message']==d['write_ok']['message'] or d['text_ok']['token']==d['write_ok']['token']:
        raise entry.EntryError('پیام تأیید دوم باید مستقل باشد.')
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
    for group in d['payload']['groups']:
        op=group['operation']
        if d['results'].get(op,{}).get('status')=='succeeded':continue
        request={k:group[k] for k in ('code','name','date','section','expected','description','write_mode','operation')}
        try:
            result=entry.commit(actor,request,config=config,runtime=runtime,day=day,fleet_db=fleet_db,approval_check=check)
            d['results'][op]=dict(status='succeeded',result=result)
        except Exception as exc:
            # Never assert absence of a write after a lost return or a prepared journal.
            d['results'][op]=dict(status='unresolved',message=str(exc) or type(exc).__name__)
        latest=store.load(identifier,actor,chat)
        if latest['hash']!=d['hash'] or latest['version']!=d['version']:
            raise entry.EntryError('پیش‌نویس هنگام ثبت تغییر کرد؛ نتیجه نیازمند بررسی است.')
        latest['results'][op]=d['results'][op]
        d=store.update(latest,'operation_result',evidence=dict(operation=op,**latest['results'][op]))
    d['status']='DONE' if all(d['results'].get(g['operation'],{}).get('status')=='succeeded' for g in d['payload']['groups']) else 'PARTIAL'
    return store.update(d,'batch_result')


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
        if d['status'] in ('TEXT_OK','COMMITTING','PARTIAL'):
            entry.permission(actor,config,section=d['payload']['section'])
            entry.selected_date(d['payload']['date'],day)
        return d
    if payload['action']=='ai_edit':return edit(d,payload['field'],payload.get('value'),index=payload.get('index'),**kw)
    raise entry.EntryError('درخواست پیش‌نویس معتبر نیست.')
