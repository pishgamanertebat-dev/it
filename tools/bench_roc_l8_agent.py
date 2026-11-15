"""Actual configured Hermes agent, isolated session/index, technical tools only.

No transport adapters, no operational session DB, no delegation/operational tools.
Uses only newly authored, non-private UAT questions. Writes evidence in runtime.
"""
from pathlib import Path
import argparse
import json
import logging
import os
import sys
import time

ROOT=Path(__file__).resolve().parents[1]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CASES={
 'b362':'از نقشه اصلی برق ROC-L8، اتصال سنسور B362 را از روی تصویر واقعی بخوان: به کدام CPU و ورودی می‌رود، شماره پایه‌های روی سنسور و رنگ سیم خروجی چیست؟ ابتدا شیت واقعی را با inspect_images=true ببین؛ اگر جزئیات خوانا نبود Crop بگیر، بدون شاهد حدس نزن. تصویر مرتبط را هم بده.',
 'compare':'در کتاب اصلی ROC-L8، Ref 4 مجموعه 3222 3060 41 را با Ref 4 مجموعه 3222 3288 28 در کتاب ROC L8(30)LF مقایسه کن. هر منبع را جدا بررسی کن و درباره تطابق با دستگاه واقعی ادعای قطعی نکن.',
 'legacy':'در پارت‌بوک PC800-8R شماره 209-01-42232 را بررسی کن؛ شماره، تعداد و محدودیت تطابق کتاب با دستگاه واقعی را بگو.',
 'feed':'فید دستگاه ROC-L8 چطور کار می‌کند؟ توضیح عمومی بده و اگر تنظیمات به نسخه وابسته است، محدودیت را بگو.',
 'cabin':'نقشه برق کابین ROC-L8 برای X50 و Power board را نشان بده. تصویر واقعی مرتبط بفرست؛ درباره پین‌ها بدون دیدن تصویر حکم نده.',
 'part':'در پارت‌بوک اصلی ROC-L8، شماره 0147 1405 03 را بررسی کن. شماره، تعداد و محدودیت تطابق با دستگاه واقعی را بگو.',
 'unknown':'برای ROC-L8 تلرانس دقیق بازسازی میل‌لنگ موتور و گشتاور پیچ یاتاقان اصلی را فقط اگر در منابع موجود واقعاً پیدا می‌شود بگو؛ اگر سند کافی نیست، صریح اعلام کن.',
}

def main():
    ap=argparse.ArgumentParser();ap.add_argument('case',choices=CASES)
    ap.add_argument('--db',type=Path,required=True);ap.add_argument('--profile',default='maintenance',choices=['default','maintenance','admin','net'])
    ap.add_argument('--surface-only',action='store_true',help='Load installed profile schema in a fresh process, no model call')
    args=ap.parse_args();out=ROOT/'runtime/roc-l8-onboarding'/('agent-'+args.case);out.mkdir(exist_ok=True)
    os.environ['PARTBOOK_TEST_DB']=str(args.db.resolve())
    os.environ['HERMES_HOME']=str(HOME if args.profile=='default' else HOME/'profiles'/args.profile)
    os.environ['HERMES_SESSION_PLATFORM']='bale'
    sys.path[:0]=[str(HOME/'hermes-agent'),str(ROOT)]
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_with_fallback
    from hermes_cli.plugins import discover_plugins
    from hermes_constants import resolve_reasoning_config
    from run_agent import AIAgent
    if args.surface_only:
        from model_tools import get_tool_definitions
        discover_plugins()
        definitions=get_tool_definitions(['komatso_technical_docs'],quiet_mode=True,skip_tool_search_assembly=True)
        funcs={d['function']['name']:d['function'] for d in definitions}
        assert set(funcs)=={'maintenance_manual_evidence','maintenance_partbook_lookup','technical_render_region'}
        assert 'ROC-L8' in funcs['maintenance_manual_evidence']['parameters']['properties']['model']['enum']
        for f in funcs.values():assert 'document_id' in f['parameters']['properties'] or 'book_id' in f['parameters']['properties']
        dest=ROOT/'runtime/roc-l8-onboarding'/('schema-'+args.profile+'.json')
        dest.write_text(json.dumps(definitions,ensure_ascii=False,indent=2),encoding='utf-8')
        print(json.dumps(dict(profile=args.profile,status='FRESH_PROCESS_SCHEMA_VERIFIED',tools=sorted(funcs))))
        return
    discover_plugins();cfg=load_config();model=cfg['model']['default']
    runtime,fallback=resolve_runtime_with_fallback(cfg,requested=cfg['model']['provider'],target_model=model)
    if fallback is not None:raise RuntimeError('Configured provider unavailable; no alternate provider used')
    log=logging.FileHandler(out/'agent.log',mode='w',encoding='utf-8');logging.getLogger().addHandler(log)
    agent=AIAgent(api_key=runtime.get('api_key'),base_url=runtime.get('base_url'),provider=runtime.get('provider'),
        requested_provider=runtime.get('requested_provider'),api_mode=runtime.get('api_mode'),model=model,
        reasoning_config=resolve_reasoning_config(cfg,model),enabled_toolsets=['komatso_technical_docs','no_mcp'],
        max_iterations=10,run_budget_seconds=180,quiet_mode=True,platform='bale',cwd=str(ROOT),skip_context_files=True,
        load_soul_identity=False,skip_memory=True,skip_background_review=True,session_db=None,
        ephemeral_system_prompt='Isolated technical acceptance test. Use the registered technical tools. No operational messages or delegation. Answer in Persian; never infer exact values without real source evidence.')
    try:
        names={t['function']['name'] for t in agent.tools}
        allowed={'maintenance_manual_evidence','maintenance_partbook_lookup','technical_render_region'}
        if names!=allowed:raise RuntimeError('Unexpected isolated tool surface: '+str(names))
        (out/'surface.json').write_text(json.dumps(agent.tools,ensure_ascii=False,indent=2),encoding='utf-8')
        start=time.perf_counter();result=agent.run_conversation(CASES[args.case]);seconds=time.perf_counter()-start
        messages=result.get('messages') or [];calls=[]
        for m in messages:
            for c in m.get('tool_calls') or []:
                f=c.get('function',c);raw=f.get('arguments') or '{}'
                calls.append(dict(name=f.get('name'),arguments=json.loads(raw) if isinstance(raw,str) else raw))
        answer=result.get('final_response') or ''
        report=dict(case=args.case,question=CASES[args.case],model=model,session_id=agent.session_id,seconds=round(seconds,2),
            api_calls=result.get('api_calls'),tool_calls=calls,final_answer=answer,finish_reason=result.get('finish_reason'),
            tool_schema=agent.tools,transport_sent=False,operational_session_db=False)
        (out/'result.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        (out/'transcript.json').write_text(json.dumps(messages,ensure_ascii=False,default=str),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ('case','model','session_id','seconds','api_calls','finish_reason')}))
    finally:agent.close();logging.getLogger().removeHandler(log);log.close()

if __name__=='__main__':main()
