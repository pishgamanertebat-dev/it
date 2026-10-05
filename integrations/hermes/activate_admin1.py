"""Publish the reviewed ADMIN-1 activation payload AFTER explicit user approval.

This local operator script never invokes an LLM or restarts Gateway itself.
The operator must perform the approved native Gateway start/restart separately.
"""
from pathlib import Path
from contextlib import closing
from datetime import datetime,timezone
import argparse,hashlib,json,shutil,sys
from uuid import uuid4
import yaml
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CORE=HOME/'hermes-agent'
sys.path.insert(0,str(ROOT))
from tools.authorization import AuthorizationStore
from tools.admin.core_fixture import patched_core_source

CORE_FILES=['gateway/profile_routing.py','gateway/run_turn.py','gateway/platforms/base.py','gateway/run_profile_reconcile.py']

def digest(path):return hashlib.sha256(Path(path).read_bytes()).hexdigest()

def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--prepared-report',required=True,type=Path)
    parser.add_argument('--enable-driver',action='store_true')
    args=parser.parse_args();report=args.prepared_report.resolve()
    expected=json.loads((report/'readiness-hashes.json').read_text(encoding='utf-8'))
    for raw,value in expected.items():
        if digest(raw)!=value:raise RuntimeError('Prepared source/runtime changed; review and validation required')
    data=json.loads((report/'activation-data.json').read_text(encoding='utf-8'))
    edits={CORE/rel:patched_core_source(rel).encode('utf-8') for rel in CORE_FILES}
    config=yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))
    route={'name':'organization-role-profiles','platform':'bale','resolver':'integrations.hermes.role_routing.resolve_profile'}
    routes=config['gateway']['profile_routes']
    if not any(r.get('resolver')==route['resolver'] for r in routes):routes.insert(0,route)
    config_bytes=yaml.safe_dump(config,allow_unicode=True,sort_keys=False).encode('utf-8')
    edits[HOME/'config.yaml']=config_bytes
    schedule=ROOT/'settings/schedules.yaml'
    if args.enable_driver:
        text=schedule.read_text(encoding='utf-8')
        old='  - id: driver_daily_office_supervisor\n    enabled: false'
        if old not in text:raise ValueError('Prepared disabled driver schedule required')
        edits[schedule]=text.replace(old,'  - id: driver_daily_office_supervisor\n    enabled: true',1).encode('utf-8')
    stamp=datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ');out=report/('activation-'+stamp);out.mkdir()
    records=[]
    for p in edits:
        backup=out/'backups'/str(len(records))/p.name;backup.parent.mkdir(parents=True);shutil.copy2(p,backup);assert digest(p)==digest(backup)
        records.append({'path':str(p),'backup':str(backup),'before_sha256':digest(p),'after_sha256':hashlib.sha256(edits[p]).hexdigest()})
    store=AuthorizationStore();backup=store.migrate_admin(out/'backups')
    store.assign_roles([(u,'business_admin') for u in data['business_admin_assignments']],actor='explicit-user-admin1-activation')
    with closing(store._connect()) as c:
        if c.execute('PRAGMA integrity_check').fetchone()[0]!='ok':raise RuntimeError('Authorization integrity failure')
        if c.execute('SELECT role,profile FROM auth_role_profiles WHERE active=1').fetchall()!=[('office_supervisor','admin')]:raise RuntimeError('Unexpected role profile mapping')
    for p,content in edits.items():
        temp=p.with_name(p.name+'.admin1-'+uuid4().hex+'.tmp');temp.write_bytes(content);temp.replace(p)
        assert digest(p)==hashlib.sha256(content).hexdigest()
    records.append({'path':str(store.path),'backup':str(backup),'backup_sha256':digest(backup),'integrity':'ok'})
    (out/'manifest.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
    (out/'status.json').write_text(json.dumps({'core_and_routes_published':True,'driver_schedule_enabled':args.enable_driver,'gateway_restart_performed':False},indent=2),encoding='utf-8')
    print('Approved payload published. Native Gateway restart/start is still required; this script did not restart it.')

if __name__=='__main__':main()
