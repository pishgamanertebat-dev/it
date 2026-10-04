"""Fresh-session benchmark against the configured messaging tool surface.

No transport messages or production session DB writes. Transcripts remain in
project runtime; the configured provider, reasoning and tool policy are used.
"""
from pathlib import Path
import argparse, hashlib, json, logging, os, re, sys, time
ROOT=Path(__file__).resolve().parents[1]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')

def main():
    ap=argparse.ArgumentParser(description=__doc__)
    ap.add_argument('--question-file',type=Path,required=True)
    ap.add_argument('--output',type=Path,required=True)
    args=ap.parse_args()
    args.output.mkdir(parents=True,exist_ok=True)
    os.environ['HERMES_HOME']=str(HOME/'profiles/maintenance')
    sys.path.insert(0,str(HOME/'hermes-agent'))
    from hermes_cli.config import load_config
    from hermes_cli.runtime_provider import resolve_runtime_with_fallback
    from hermes_cli.tools_config import _get_platform_tools
    from hermes_cli.plugins import discover_plugins
    from hermes_constants import resolve_reasoning_config
    from agent.skill_utils import parse_config_string_list
    from run_agent import AIAgent
    discover_plugins()
    cfg=load_config(); model=cfg['model']['default']; provider=cfg['model']['provider']
    runtime,fallback=resolve_runtime_with_fallback(cfg,requested=provider,target_model=model)
    if fallback is not None: raise RuntimeError('Benchmark requires configured provider')
    log=logging.FileHandler(args.output/'agent.log',encoding='utf-8')
    log.setFormatter(logging.Formatter('%(asctime)s %(levelname)s %(name)s: %(message)s'))
    logging.getLogger().addHandler(log);logging.getLogger().setLevel(logging.INFO)
    agent=AIAgent(api_key=runtime.get('api_key'),base_url=runtime.get('base_url'),
        provider=runtime.get('provider'),requested_provider=runtime.get('requested_provider'),
        api_mode=runtime.get('api_mode'),model=model,
        reasoning_config=resolve_reasoning_config(cfg,model),
        enabled_toolsets=sorted(_get_platform_tools(cfg,'bale')),
        disabled_toolsets=parse_config_string_list((cfg.get('agent') or {}).get('disabled_toolsets')) or None,
        max_iterations=cfg['agent']['max_turns'],quiet_mode=True,platform='bale',cwd=str(ROOT),
        skip_background_review=True,session_db=None)
    names={t['function']['name'] for t in agent.tools}
    expected={'web_search','web_extract','skills_list','skill_view','delegate_task',
              'maintenance_manual_evidence','maintenance_partbook_lookup'}
    expected|={'public_browser_'+a for a in ['navigate','snapshot','click','type','hover','select','press','scroll','back','tabs','switch_tab','frame','drag','screenshot','images','console','close']}
    if names != expected: raise RuntimeError('Unexpected benchmark tool surface: '+str(sorted(names)))
    question=args.question_file.read_text(encoding='utf-8').strip()
    try:
        started=time.perf_counter()
        result=agent.run_conversation(question,conversation_history=None)
        elapsed=time.perf_counter()-started
        messages=result.get('messages') or []
        answer=result.get('final_response') or ''
        calls=[];packets=[]
        for message in messages:
            for call in message.get('tool_calls') or []:
                f=call.get('function',call);a=f.get('arguments') or '{}'
                calls.append({'name':f.get('name'),'arguments':json.loads(a) if isinstance(a,str) else a})
            if message.get('role')=='tool':
                try: packets.append(json.loads(message.get('content') or '{}'))
                except (ValueError,TypeError): pass
        log.flush()
        api=[dict(input=int(m[1]),output=int(m[2]),latency=float(m[3])) for m in
             re.finditer(r'API call #\d+: .*? in=(\d+) out=(\d+).*?latency=([0-9.]+)s',(args.output/'agent.log').read_text(encoding='utf-8'))]
        media=[s[6:].strip() for s in answer.splitlines() if s.startswith('MEDIA:')]
        report={'session_id':agent.session_id,'fresh_session':True,'history_messages':0,
                'question':question,'question_sha256':hashlib.sha256(question.encode()).hexdigest(),
                'wall_seconds':round(elapsed,2),'api_calls':result.get('api_calls'),'tool_calls':calls,
                'tool_sequence':[c['name']+(':'+c['arguments']['phase'] if c['arguments'].get('phase') else '') for c in calls],
                'api_usage':api,'model':model,'provider':provider,'reasoning_config':resolve_reasoning_config(cfg,model),
                'coverage':[p.get('evidence_coverage') for p in packets if p.get('evidence_coverage')],
                'media_count':len(media),'media_files_exist':all(Path(p).is_file() for p in media),
                'final_answer':answer,'finish_reason':result.get('finish_reason'),
                'broad':any(c['arguments'].get('broad') for c in calls),
                'refine':sum(c['arguments'].get('phase')=='retrieve' for c in calls)>1}
        (args.output/'transcript.json').write_text(json.dumps({'messages':messages},ensure_ascii=False,default=str),encoding='utf-8')
        (args.output/'summary.json').write_text(json.dumps(report,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
        print(json.dumps({k:report[k] for k in ['session_id','wall_seconds','api_calls','tool_sequence','media_count','media_files_exist','refine','broad']},ensure_ascii=True))
    finally:
        agent.close();logging.getLogger().removeHandler(log);log.close()
if __name__=='__main__':main()
