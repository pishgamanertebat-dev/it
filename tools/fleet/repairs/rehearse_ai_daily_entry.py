"""Installed maintenance/net bridge → fake Bale → copied Drivers2 → existing Writer.

Extraction is a labelled fixture here. Real language quality is a separate mandatory
check in verify_ai_daily_model.py; this rehearsal does not claim to prove it.
"""
import argparse
import asyncio
from contextlib import closing, ExitStack
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import shutil
import sqlite3
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch
import uuid

import openpyxl

from tools.authorization import AuthorizationStore
from tools.bale_ui import StateStore
from . import entry_service as entry, entry_draft, entry_extract, entry_bale, entry_ai_bale, sync_service
from .entry_draft_store import DraftStore
from .rehearse_safe_manual_excel import read_snapshot, preserved
from .rehearse_seven_day_entry import backup_db, PEOPLE

DAY='1405/08/18'
HOME=Path('C:/Users/win-10/AppData/Local/hermes')


def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def fixture_extraction(raw,actor):
    clauses=[('707','تعویض واترپمپ','دامتراک 707 تعویض واترپمپ')]
    if '473' in raw:
        clauses += [('473','باز کردن موتور','لودر 473 باز کردن موتور'),
                    ('473','باز کردن سیم کشی','473 باز کردن سیم کشی')]
    return entry_extract.validate(dict(raw_text=raw,report_date=None,unresolved=[],items=[
        dict(raw_code=code,raw_description=desc,source_span=dict(text=quote,occurrence=0))
        for code,desc,quote in clauses]),raw)


async def run(out,fixtures):
    from tools.bale_ui import runtime
    from tools.fleet.work_orders.core import paths
    paths.PROJECT_ROOT=entry.ROOT;paths.DB_PATH=out/'unused.db';paths.WORK_ORDER_OUTPUT_ROOT=out/'orders'
    config_data=json.loads(entry.CONFIG.read_text('utf8'))
    source=out/'drivers.xlsx';shutil.copy2(fixtures/Path(config_data['source']).name,source)
    mine=out/'drivers-mine.xlsx';shutil.copy2(source,mine)
    config_data.update(source=str(source),template=str(entry.ROOT/'templates/repairs/daily_report.xlsx'))
    config=out/'drivers.json';config.write_text(json.dumps(config_data,ensure_ascii=False),'utf8')
    audit=out/'journal';kw=dict(config=config,runtime=audit,day=DAY,fleet_db=out/'missing.db')
    auth_path=out/'auth.sqlite3';backup_db(fixtures/'auth.sqlite3',auth_path);auth=AuthorizationStore(auth_path)
    original=read_snapshot(source);sync_service.initialize(mine,config=config,runtime=audit)
    messages=[];calls=[];rows=set();cells=set();sheets=set()
    async def send_message(**kw):messages.append(kw);return SimpleNamespace(message_id=len(messages))
    bot=SimpleNamespace(send_message=AsyncMock(side_effect=send_message),edit_message_reply_markup=AsyncMock(),delete_message=AsyncMock())
    gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(_bot=bot)})
    def fallback(g,c,t):messages.append(dict(chat_id=c,text=t))
    async def worker(payload):
        calls.append(payload)
        try:
            if payload['action'].startswith('ai_'):result=entry_draft.execute(payload,**kw)
            elif payload['action']=='dates':result={'dates':entry_draft.entry_dates.choices(DAY)}
            elif payload['action']=='preview':result=entry.preview(payload['actor'],payload['code'],payload['section'],date=payload.get('date'),**{k:v for k,v in kw.items() if k!='runtime'})
            else:result=entry.commit(payload['actor'],payload['request'],**kw)
            return dict(ok=True,result=result)
        except (ValueError,PermissionError) as exc:return dict(ok=False,message=str(exc))
    async def extractor(raw,actor):return fixture_extraction(raw,actor)
    ai=entry_ai_bale.DailyAIHandler(drafts=DraftStore(audit),state_store=StateStore(out/'ai-form.json'),run=worker,extractor=extractor)
    manual=entry_bale.RepairsEntryHandler(state_store=StateStore(out/'manual-form.json'),run=worker,safe_capture=True)
    plugins={}
    for profile in ('maintenance','net'):
        path=HOME/f'profiles/{profile}/plugins/komatso-bale-registry/__init__.py'
        spec=importlib.util.spec_from_file_location('phase4_'+profile,path);module=importlib.util.module_from_spec(spec)
        with patch.dict('sys.modules',{'gateway.pairing':SimpleNamespace(PairingStore=Mock())}):spec.loader.exec_module(module)
        module.DB_PATH=auth_path;plugins[profile]=(module,path)
    telegram=SimpleNamespace(InlineKeyboardMarkup=SimpleNamespace(de_json=lambda value,bot:value),
        KeyboardButton=lambda text:dict(text=text),
        ReplyKeyboardMarkup=lambda rows,**options:dict(keyboard=rows,**options),
        ReplyKeyboardRemove=lambda:dict(remove_keyboard=True))
    def event(actor,text='',raw=None):return SimpleNamespace(text=text,raw_message=raw,message_id=uuid.uuid4().hex,
        source=SimpleNamespace(platform='bale',chat_type='dm',user_id=actor,chat_id=actor))
    async def deliver(actor,text='',action=None,handler=ai):
        module,_=plugins['net' if actor=='641220453' else 'maintenance']
        raw=None
        if action:
            s=handler.sessions[(actor,actor)]
            raw=dict(bale_inline_callback=True,data=f"ik:{handler.namespace}:{s['revision']}:{action}",origin_message_id=s['message_id'])
        result=module._handle_bale(event(actor,text,raw),gateway)
        await asyncio.gather(*list(ai.tasks),*list(manual.tasks))
        return result
    roles={actor:auth.roles(actor) for actor in PEOPLE}
    with ExitStack() as stack:
        stack.enter_context(patch.dict('sys.modules',{'telegram':telegram}))
        stack.enter_context(patch.object(entry_extract,'enabled',return_value=True))
        stack.enter_context(patch('tools.authorization.net.AuthorizationStore',return_value=auth))
        stack.enter_context(patch.object(entry_bale,'CONFIG',config))
        stack.enter_context(patch.object(entry_bale,'_handler',manual))
        stack.enter_context(patch.object(entry_ai_bale,'_handler',ai))
        stack.enter_context(patch.object(runtime.router,'handlers',{}))
        for module,_ in plugins.values():
            stack.enter_context(patch.object(module,'_handle_overflow_report',return_value=None))
            stack.enter_context(patch.object(module,'_send',fallback))
        operations=[]
        for actor,section,date,multi in [('1732374823','mechanical','1405/08/13',True),
                ('654806764','mechanical','1405/08/11',False),('387679249','metalwork','1405/08/13',False),
                ('641220453','mechanical',DAY,False),('641220453','metalwork',DAY,False)]:
            before=digest(source)
            text='ثبت کن دامتراک 707 تعویض واترپمپ'+('، لودر 473 باز کردن موتور، 473 باز کردن سیم کشی' if multi else '')
            result=await deliver(actor,text);assert result['reason']=='daily-ai',result
            if actor=='641220453':
                assert ai.current((actor,actor))['payload']['section'] is None
                await deliver(actor,action='sections');await deliver(actor,action=section)
            await deliver(actor,action='dates');await deliver(actor,action='date_'+date.replace('/',''))
            d=ai.current((actor,actor))
            for index,g in enumerate(d['payload']['groups']):
                if g['expected']:await deliver(actor,action=f'append_{index}')
            d=ai.current((actor,actor));assert not d['payload']['issues'],d['payload']['issues']
            assert digest(source)==before
            await deliver(actor,action='confirm_text');assert digest(source)==before
            await deliver(actor,'بله');assert digest(source)==before
            await deliver(actor,action='confirm_write')
            saved=ai.drafts.load(d['id'],actor,actor);assert saved['status']=='DONE',messages[-1]
            book=openpyxl.load_workbook(source)
            for g in saved['payload']['groups']:
                sheet=entry.today_sheet(book,g['date']);cols=entry.layout(sheet);row=entry.find_row(sheet,g['code'],cols)
                assert sheet.cell(row,cols[g['section']]).value==g['final_description']
                cells.add((sheet.title,sheet.cell(row,cols[g['section']]).coordinate));rows.add((sheet.title,row));sheets.add(sheet.title)
                operations.append(dict(actor=actor,date=date,section=section,operation=g['operation'],code=g['code']))
            book.close()
        # Actual Reply label, text command, dates and both modes with AI unavailable.
        manual_start=len(calls)
        actor='1732374823'
        for mode,command in [('append','🛠 شرح خرابی'),('replace','شرح خرابی')]:
            await deliver(actor,command,handler=manual)
            await deliver(actor,action='dates',handler=manual)
            choices=manual.sessions[(actor,actor)]['date_choices'];assert len(choices)==8
            await deliver(actor,action='date_14050813',handler=manual)
            await deliver(actor,'707',handler=manual)
            await deliver(actor,'ورود دستی آفلاین '+mode,handler=manual)
            assert manual.sessions[(actor,actor)]['stage']=='CAPTURE'
            await deliver(actor,action='use_text',handler=manual)
            await deliver(actor,action=mode,handler=manual)
            assert '✅' in messages[-1]['text'],messages[-1]
        assert all(not p['action'].startswith('ai_') for p in calls[manual_start:])
        assert {actor:auth.roles(actor) for actor in PEOPLE}==roles
    for handler in (ai,manual):
        for timer in handler.lifecycle.timers.values():timer.cancel()
    after=read_snapshot(source)
    preserved(original,{name:after[name] for name in original},cells,sheets,rows)
    # A disjoint mine cell must survive together with the published AI descriptions.
    book=openpyxl.load_workbook(mine);sheet=entry.dated_sheets(book)[0][1];cols=entry.layout(sheet)
    row=entry.machine_rows(sheet,cols)[0];sheet.cell(row,cols['metalwork'],'INDEPENDENT MINE COPY')
    book.save(mine);book.close()
    merged=sync_service.merge_snapshot(mine,source,config=config,runtime=audit);assert merged['status']=='merged',merged
    with closing(entry.open_audit(audit)) as db:
        journal=[dict(r) for r in db.execute("SELECT operation,actor,request,status FROM edits WHERE actor!='mine-file-sync'")]
    assert len(journal)==len(operations)+2 and all(r['status']=='succeeded' for r in journal)
    assert all(isinstance(json.loads(r['request'])['description'],str) for r in journal)
    assert after.keys()<=read_snapshot(source).keys()
    return dict(passed=True,output=str(source),operations=operations,manual_saves=2,
        offline_reply_and_text_and_eight_dates=True,manual_requires_no_model=True,
        original_history_styles_formulas_preserved=True,sync_merge=merged['status'],
        atomic_report=False,extraction_fixture=True,real_model_quality_claimed=False,
        installed_plugins={n:dict(path=str(p),sha256=digest(p)) for n,(_,p) in plugins.items()})


def main():
    parser=argparse.ArgumentParser(description=__doc__);parser.add_argument('--output',type=Path,required=True);parser.add_argument('--fixtures',type=Path,required=True)
    args=parser.parse_args();args.output.mkdir(parents=True,exist_ok=False)
    loop=asyncio.new_event_loop()
    try:
        keys={key:'' for key in os.environ if 'API_KEY' in key};keys.update(OPENAI_API_KEY='',ANTHROPIC_API_KEY='')
        with patch.dict(os.environ,keys),patch('socket.socket.connect',side_effect=AssertionError('External AI/API network forbidden')):
            result=loop.run_until_complete(run(args.output,args.fixtures))
    finally:loop.close()
    (args.output/'evidence.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps(dict(passed=result['passed'],operations=len(result['operations']),manual_saves=result['manual_saves'],sync=result['sync_merge'])))


if __name__=='__main__':main()
