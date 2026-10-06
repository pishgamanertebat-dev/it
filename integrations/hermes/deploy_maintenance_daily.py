"""Guarded canonical maintenance rollout. No restart, reload, update or Windows Task edits.

prepare -> assign -> one authorized operator test -> activate.
"""
from pathlib import Path
from contextlib import closing
from unittest.mock import patch
from tempfile import NamedTemporaryFile
from datetime import datetime
from zoneinfo import ZoneInfo
import argparse,hashlib,json,os,shutil
import yaml
from tools.authorization import AuthorizationStore
from tools.authorization.maintenance import ROLE,CAPABILITY,RESOURCE,EXTENSION,migrate
from integrations.hermes.deploy_metalwork import bootstrap,state,identities
from integrations.hermes import role_routing
from tools.fleet.maintenance_daily.report import source_snapshot,scan,render,validate_pdf
from tools.scheduler.tasks import overflow_report_date
from tools.scheduler.runner import load_config
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'runtime/maintenance_daily_phase'
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
TARGETS=('397185913','514458396')  # Explicit rollout assignments, never scheduler recipients.
JOB=dict(id='maintenance_daily_report',enabled=True,task='maintenance_daily_report',recipient_capability=CAPABILITY,timezone='Asia/Tehran',trigger=dict(type='cron',hour=9,minute=0),params={})
def load(name):return json.loads((OUT/name).read_text(encoding='utf-8'))
def save(name,v):(OUT/name).write_text(json.dumps(v,ensure_ascii=False,indent=2,default=str),encoding='utf-8')
def digest(p):return hashlib.sha256(Path(p).read_bytes()).hexdigest()
def all_users(store):
    from gateway.profile_routing import parse_profile_routes,match_profile_route
    routes=parse_profile_routes(yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))['gateway']['profile_routes'])
    records={}
    with closing(store._connect()) as c:ids=[r[0] for r in c.execute("SELECT user_id FROM channel_users WHERE platform='bale' ORDER BY user_id")]
    with patch.object(role_routing,'AuthorizationStore',return_value=store):
        for user in ids:
            route=match_profile_route(routes,'bale',user_id=user,chat_id=user)
            roles=store.roles(user)
            records[user]={'roles':roles,'db_profile':store.resolve_profile(user,user),'effective_profile':route.profile if route else 'default','capabilities':sorted({cap for role in roles for cap in store.capabilities_for_role(role)}),'function_scope':store.function_scope(user).__dict__}
    return json.loads(json.dumps(records))
def recipient_sets(store):return {cap:store.resolve_active_recipients(cap).__dict__ for cap in load('recipients-before.json')}
def preflight(store,*,assigned=False):
    original_identities=load('identities-before.json');current_identities=identities(store)
    # The live gateway legitimately refreshes updated_at. Every identity,
    # private chat, approval, verified identity and other registry field remains guarded.
    stable=lambda rows:[{k:v for k,v in row.items() if k!='updated_at'} for row in rows]
    if stable(current_identities)!=stable(original_identities):raise RuntimeError('Identity or approval registry changed since baseline')
    save('registry-runtime-timestamps.json',{'ignored_field':'updated_at','authorization_fields_unchanged':True,
        'changed_user_ids':[a['user_id'] for a,b in zip(current_identities,original_identities) if a['updated_at']!=b['updated_at']]})
    if any(digest(p)!=h for p,h in load('protected-hashes.json').items()):raise RuntimeError('Protected runtime file changed')
    if (ROOT/'settings/schedules.yaml').read_bytes()!=(OUT/'files-before/settings/schedules.yaml').read_bytes():raise RuntimeError('Existing schedules changed')
    if not assigned and state(store)!=load('authorization-before.json'):raise RuntimeError('Authorization baseline changed')
    from gateway.control_socket import identify_gateway
    current=identify_gateway(HOME,timeout=15);before=load('gateway-before.json')
    if not current or any(current[k]!=before[k] for k in ['pid','start_time','code_sha','code_version']):raise RuntimeError('Gateway identity or health changed')
    return current

def verify(store):
    before=load('authorization-before.json');after=state(store)
    expect={'auth_roles':[[ROLE,'گیرنده گزارش روزانه تعمیرات']], 'auth_capabilities':[[CAPABILITY,RESOURCE]],'auth_role_capabilities':[[ROLE,CAPABILITY]],'auth_role_profiles':[], 'auth_migrations':[]}
    for table in before:
        if any(row not in after[table] for row in before[table]):raise RuntimeError('Existing authorization changed: '+table)
        added=[row for row in after[table] if row not in before[table]]
        if table in expect and added!=expect[table]:raise RuntimeError('Unexpected authorization addition: '+table)
        if table=='auth_extensions' and (len(added)!=1 or added[0][0]!=EXTENSION):raise RuntimeError('Unexpected extension')
        if table=='auth_user_roles' and (len(added)!=2 or sorted((r[1],r[2],r[3]) for r in added)!=[(u,ROLE,1) for u in TARGETS]):raise RuntimeError('Unexpected assignments')
        if table=='auth_events' and (len(added)!=2 or sorted((r[2],r[3],r[4]) for r in added)!=[(u,ROLE,'assigned') for u in TARGETS]):raise RuntimeError('Unexpected assignment audit')
    original=load('all-users-before.json');current=all_users(store)
    for u,b in original.items():
        a=current[u]
        if u in TARGETS:
            if a['roles']!=sorted(b['roles']+[ROLE]) or a['capabilities']!=sorted(b['capabilities']+[CAPABILITY]):raise RuntimeError('Wrong recipient grant')
            for k in ['db_profile','effective_profile','function_scope']:
                if a[k]!=b[k]:raise RuntimeError('Recipient profile or Function scope changed')
        elif a!=b:raise RuntimeError('Other identity changed')
    if json.loads(json.dumps(recipient_sets(store)))!=load('recipients-before.json'):raise RuntimeError('Existing recipient sets changed')
    if store.resolve_active_recipients(CAPABILITY).recipients!=TARGETS:raise RuntimeError('Production recipient set incorrect')
    if store.has_capability('455740857',CAPABILITY) or ROLE in store.roles('455740857'):raise RuntimeError('Test identity gained production permission')
    return {'success':True,'integrity':'ok','foreign_keys':'ok','users':current,'recipient_ids':TARGETS,'existing_recipient_sets_unchanged':True,'profile_mappings_unchanged':True,'function_scopes_unchanged':True,'identities_checked':len(current)}

def prepare(store):
    preflight(store)
    for n in ['maintenance-tests.json','regression-tests.json']:
        if not load(n)['success']:raise RuntimeError('Tests failed: '+n)
    save('all-users-before.json',all_users(store))
    proposed=OUT/'schedules-proposed.yaml'
    old=(OUT/'files-before/settings/schedules.yaml').read_text(encoding='utf-8')
    if any(j['id']==JOB['id'] or j['task']==JOB['task'] for j in yaml.safe_load(old)['schedules']):raise RuntimeError('Job already exists')
    addition='\n  - id: maintenance_daily_report\n    enabled: true\n    task: maintenance_daily_report\n    recipient_capability: '+CAPABILITY+'\n    timezone: Asia/Tehran\n    trigger:\n      type: cron\n      hour: 9\n      minute: 0\n    params: {}\n'
    proposed.write_text(old+addition,encoding='utf-8');load_config(proposed)
    backup=store.backup(OUT/'backups');candidate=OUT/'candidate.sqlite3';shutil.copy2(backup,candidate)
    trial=AuthorizationStore(candidate);migrate(trial,OUT/'candidate-backups',assignments=TARGETS);save('candidate-verification.json',verify(trial))
    first=state(trial);migrate(trial,OUT/'candidate-backups',assignments=TARGETS+TARGETS)
    if first!=state(trial):raise RuntimeError('Migration is not idempotent')
    occurrence=datetime.now(ZoneInfo('Asia/Tehran'));target=overflow_report_date(occurrence)
    with source_snapshot() as (data,h):
        production=scan(data,target)
        sample=production;fallback=not bool(production['devices'])
        if fallback:
            if not production['available_dates']:raise RuntimeError('No valid device records for test')
            sample=scan(data,production['available_dates'][-1])
        output=OUT/'maintenance-test.pdf'
        with patch('requests.sessions.Session.request',side_effect=AssertionError('Offline smoke: network forbidden')):
            validation=render(sample,output,test_sample=True)
        validation=validate_pdf(output,sample,test_sample=True)
        if h!=load('baseline-complete.json')['source_hash']:raise RuntimeError('Source version changed since inspection')
        # Independent XML inspection supplies a second exact-date count, not parser output.
        import zipfile
        from xml.etree import ElementTree as ET
        ns={'m':'http://schemas.openxmlformats.org/spreadsheetml/2006/main'}
        with zipfile.ZipFile(__import__('io').BytesIO(data)) as z:
            strings=[''.join(n.itertext()) for n in ET.fromstring(z.read('xl/sharedStrings.xml')).findall('m:si',ns)]
            counts={}
            inspection=load('workbook-inspection.json')
            for i,sheet in enumerate(inspection['sheets'],1):
                if sheet['sheet'] in ['متفرقه','تعمیرگاه']:continue
                dc=__import__('openpyxl').utils.get_column_letter(sheet['date_column']);count=0
                for row in ET.fromstring(z.read('xl/worksheets/sheet'+str(i)+'.xml')).findall('.//m:row',ns):
                    for cell in row.findall('m:c',ns):
                        if cell.get('r','').startswith(dc) and re_column(cell.get('r'))==dc and cell.get('t')=='s':
                            v=cell.find('m:v',ns)
                            if v is not None and strings[int(v.text)].strip()==sample['date']:count+=1
                if count:counts[sheet['sheet']]=count
        if counts!={d['sheet']:len(d['rows']) for d in sample['devices']}:raise RuntimeError('Independent XML record counts disagree')
        save('real-smoke.json',{'success':True,'offline':True,'occurrence':occurrence.isoformat(),'production_target':target,'production_records':production['rows_matched'],'production_status':'skipped' if not production['devices'] else 'ready','test_only_fallback':fallback,'test_date':sample['date'],'source_hash_before':h,'source_hash_after':h,'pdf':str(output),'validation':validation,'independent_xml_counts':counts,'device_ids':[d['sheet'] for d in sample['devices']]})
    preflight(store);save('prepared.json',{'success':True,'candidate_verified':True,'idempotent':True,'no_production_mutation':True,'schedule_proposed_sha256':digest(proposed)})
    print('PREPARED: candidate, native profiles, offline real workbook and PDF validated')

def re_column(v):
    import re
    return re.match(r'[A-Z]+',v).group()

def assign(store):
    if not load('prepared.json')['success']:raise RuntimeError('Prepare required')
    preflight(store)
    backup=migrate(store,OUT/'backups',assignments=TARGETS,actor='explicit-user-maintenance-daily-phase')
    result=verify(store);save('production-authorization.json',result);save('authorization-after.json',state(store));save('assignment.json',{'success':True,'backup':str(backup),'backup_sha256':digest(backup)})
    print('ASSIGNED: receive-only role to exactly two users; profiles unchanged; schedule inactive')

def activate(store):
    preflight(store,assigned=True);verify(store)
    for n in ['prepared.json','production-authorization.json','maintenance-tests.json','regression-tests.json','real-smoke.json','test-send-result.json']:
        if not load(n)['success']:raise RuntimeError('Required acceptance failed: '+n)
    test=load('test-send-result.json')
    if test['recipient']!='455740857' or test['attempt_count']!=1:raise RuntimeError('Authorized one-time test not proven')
    proposed=OUT/'schedules-proposed.yaml'
    if digest(proposed)!=load('prepared.json')['schedule_proposed_sha256']:raise RuntimeError('Proposed schedule changed')
    old=load('schedule-before.json');new=yaml.safe_load(proposed.read_text(encoding='utf-8'))
    if new['schedules'][:-1]!=old['schedules'] or new['schedules'][-1]!=JOB or {k:v for k,v in old.items() if k!='schedules'}!={k:v for k,v in new.items() if k!='schedules'}:raise RuntimeError('Schedules differ from approved delta')
    load_config(proposed)
    with source_snapshot() as (_,h):
        if h!=load('real-smoke.json')['source_hash_before']:raise RuntimeError('Source changed before activation')
    p=ROOT/'settings/schedules.yaml'
    with NamedTemporaryFile(prefix='maintenance-',suffix='.tmp',dir=p.parent,delete=False) as f:
        tmp=Path(f.name);f.write(proposed.read_bytes());f.flush();os.fsync(f.fileno())
    try:os.replace(tmp,p)
    finally:tmp.unlink(missing_ok=True)
    _,_,jobs=load_config(p);job=next(j for j in jobs if j['id']==JOB['id']);now=datetime.now(ZoneInfo('Asia/Tehran'))
    from gateway.control_socket import identify_gateway
    current=identify_gateway(HOME,timeout=15);before=load('gateway-before.json')
    if not current or any(current[k]!=before[k] for k in ['pid','start_time','code_sha','code_version']):raise RuntimeError('Gateway identity changed')
    save('gateway-after.json',current)
    if any(digest(p)!=h for p,h in load('protected-hashes.json').items()):raise RuntimeError('Protected runtime changed')
    save('activation.json',{'success':True,'active':True,'job':JOB,'schedule_sha256':digest(p),'next_occurrence':job['trigger'].get_next_fire_time(None,now).isoformat(),'restart':False,'reload':False,'hermes_update':False,'windows_task_changed':False,'existing_jobs_unchanged':True,'source_sha256':h})
    print('READY — DAILY MAINTENANCE REPORT ACTIVE')

def main():
    p=argparse.ArgumentParser(description=__doc__);p.add_argument('mode',choices=['prepare','assign','activate']);a=p.parse_args();bootstrap();st=AuthorizationStore();globals()[a.mode](st)
if __name__=='__main__':main()
