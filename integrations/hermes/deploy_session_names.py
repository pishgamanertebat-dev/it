"""Deploy only session name hooks; never start, stop, update, migrate or send."""
from pathlib import Path
import hashlib,json,sqlite3,sys
from datetime import datetime,timezone
ROOT=Path(r'E:\KomatsoAI');HOME=Path(r'C:\Users\win-10\AppData\Local\hermes');CORE=HOME/'hermes-agent';OUT=ROOT/'runtime/desktop-session-names'

def digest(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def table_hashes(path):
    db=sqlite3.connect(path.resolve().as_uri()+'?mode=ro',uri=True)
    try:
        tables=[r[0] for r in db.execute("SELECT name FROM sqlite_master WHERE type='table' AND (name LIKE 'auth_%' OR name IN ('channel_users','access_requests','access_events','users')) ORDER BY name")]
        return {t:hashlib.sha256(json.dumps(db.execute('SELECT * FROM "'+t+'" ORDER BY rowid').fetchall(),ensure_ascii=False,default=str).encode()).hexdigest() for t in tables}
    finally:db.close()
def history_hashes(cutoff=None):
    result={}
    for home in [HOME,HOME/'profiles/maintenance',HOME/'profiles/admin']:
        db=sqlite3.connect((home/'state.db').as_uri()+'?mode=ro',uri=True)
        try:
            rows=db.execute('SELECT id,title,user_id,session_key FROM sessions'+(' WHERE started_at<=?' if cutoff is not None else '')+' ORDER BY id', (cutoff,) if cutoff is not None else ()).fetchall()
            result[str(home)]={'rows':len(rows),'sha256':hashlib.sha256(json.dumps(rows,ensure_ascii=False).encode()).hexdigest()}
        finally:db.close()
    return result

def protected():
    files=[HOME/'config.yaml',HOME/'profiles/maintenance/config.yaml',HOME/'profiles/admin/config.yaml']
    files+=list((HOME/'gateway-service').glob('Hermes_Gateway.*'))
    files+=[CORE/'gateway/run.py',CORE/'gateway/run_turn.py',CORE/'gateway/hooks.py',CORE/'gateway/run_adapters.py',CORE/'gateway/session_identity.py',CORE/'gateway/authz_mixin.py',CORE/'gateway/run_agent_cache.py',CORE/'gateway/restart.py',CORE/'hermes_cli/gateway_windows.py',CORE/'hermes_cli/gateway_supervised_restart.py',ROOT/'integrations/hermes/role_routing.py']
    return {str(p):digest(p) for p in files if p.is_file()}

mode=sys.argv[1]
if mode=='baseline':
    state=json.loads((HOME/'gateway_state.json').read_text(encoding='utf-8'))
    cutoff=datetime.now(timezone.utc).timestamp()
    payload={'historical_cutoff':cutoff,'gateway':state,'protected':protected(),'authorization':table_hashes(ROOT/'reports/telegram_usage/telegram_users.db'),'historical_sessions':history_hashes(cutoff)}
    (OUT/'production-baseline.json').write_text(json.dumps(payload,indent=2),encoding='utf-8')
    print('Baseline saved; PID',state['pid'],'version',state['code_version'],'active_agents',state['active_agents'])
elif mode=='deploy':
    baseline=json.loads((OUT/'production-baseline.json').read_text())
    assert protected()==baseline['protected'],'Protected files changed before deploy'
    assert table_hashes(ROOT/'reports/telegram_usage/telegram_users.db')==baseline['authorization'],'Identity/authorization drift before deploy'
    records=[]
    for home in [HOME,HOME/'profiles/maintenance',HOME/'profiles/admin']:
        for filename in ['handler.py','HOOK.yaml']:
            source=ROOT/'integrations/hermes/hooks/komatso-session-namer'/filename
            target=home/'hooks/komatso-session-namer'/filename
            if target.exists() and source.read_bytes()==target.read_bytes():continue
            if target.exists():
                backup=OUT/(home.name+'-'+filename+'.before-deploy')
                assert not backup.exists(),'Backup already exists'
                backup.write_bytes(target.read_bytes())
            else:backup=None
            target.parent.mkdir(parents=True,exist_ok=True)
            temporary=target.with_name(filename+'.session-names.tmp');temporary.write_bytes(source.read_bytes());temporary.replace(target)
            assert digest(target)==digest(source)
            records.append({'source':str(source),'target':str(target),'sha256':digest(target),'backup':str(backup) if backup else None})
    assert protected()==baseline['protected']
    assert table_hashes(ROOT/'reports/telegram_usage/telegram_users.db')==baseline['authorization']
    assert history_hashes(baseline.get('historical_cutoff'))==baseline['historical_sessions'],'Unexpected historical rewrite'
    (OUT/'deployment.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    print('Deployed',len(records),'necessary hook files; protected files, authorization, historical sessions unchanged')
elif mode=='verify':
    baseline=json.loads((OUT/'production-baseline.json').read_text())
    state=json.loads((HOME/'gateway_state.json').read_text())
    checks={'new_pid':state['pid']!=baseline['gateway']['pid'],'running':state['gateway_state']=='running','same_version':state['code_version']=='0.21.4','bale_connected':state['platforms']['bale']['state']=='connected','telegram_connected':state['platforms']['telegram']['state']=='connected','profiles':sorted(state['served_profiles'])==sorted(baseline['gateway']['served_profiles']),'protected_files_unchanged':protected()==baseline['protected'],'authorization_unchanged':table_hashes(ROOT/'reports/telegram_usage/telegram_users.db')==baseline['authorization'],'historical_sessions_unchanged':history_hashes(baseline.get('historical_cutoff'))==baseline['historical_sessions']}
    (OUT/'production-verification.json').write_text(json.dumps({'pid':state['pid'],'checks':checks},indent=2),encoding='utf-8')
    print(json.dumps({'pid':state['pid'],'checks':checks},indent=2))
    assert all(checks.values()),'Production verification incomplete'
