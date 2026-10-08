"""Operator-only, bounded Pakdel delivery deployment and additive rollback.

Never sends documents, issues work orders, updates Hermes, or restarts Gateway.
Restart uses the separately verified installed lifecycle command.
"""
from contextlib import closing
from datetime import datetime, timezone
import argparse
import hashlib
import json
from pathlib import Path
import shutil
import sqlite3
from uuid import uuid4

ROOT = Path('E:/KomatsoAI')
STAGE = ROOT/'runtime/pakdel-delivery/stage'
HOME = Path('C:/Users/win-10/AppData/Local/hermes')
FILES = (
    'tools/authorization/office_delivery.py',
    'tools/scheduler/office_receipts.py',
    'tools/scheduler/office_delivery.py',
    'tools/fleet/work_orders/core/final_copy.py',
    'tools/fleet/work_orders/core/delivery.py',
    'tools/scheduler/no_data.py',
    'tools/scheduler/tasks.py',
    'tools/scheduler/runner.py',
    'tools/authorization/test_authorization.py',
    'tools/authorization/test_mechanical.py',
    'tools/authorization/test_metalwork.py',
    'tools/scheduler/test_misfire.py',
    'tools/scheduler/test_office_delivery.py',
    'tools/fleet/work_orders/dev/test_final_copy.py',
    'tools/fleet/work_orders/dev/test_work_order_menu.py',
    'tools/authorization/run_pakdel_checks.py',
    'integrations/hermes/deploy_pakdel_delivery.py',
    'settings/schedules.yaml',
)


def digest(path):
    return hashlib.sha256(Path(path).read_bytes()).hexdigest()


def atomic_copy(source, destination):
    destination=Path(destination);destination.parent.mkdir(parents=True,exist_ok=True)
    temp=destination.with_name(destination.name+'.pakdel-'+uuid4().hex+'.tmp')
    try:
        shutil.copyfile(source,temp)
        temp.replace(destination)
    finally:
        temp.unlink(missing_ok=True)


def audit_dir():
    return Path((ROOT/'runtime/pakdel-delivery/latest-audit.txt').read_text(encoding='utf-8'))


def integrity(c):
    if [tuple(r) for r in c.execute('PRAGMA integrity_check')]!=[('ok',)] or c.execute('PRAGMA foreign_key_check').fetchall():
        raise RuntimeError('SQLite integrity/FK gate failed')


def backup(database, destination):
    with closing(sqlite3.connect(Path(database).as_uri()+'?mode=ro',uri=True)) as source, closing(sqlite3.connect(destination)) as dest:
        source.backup(dest);integrity(dest)


def check():
    from tools.authorization.store import AuthorizationStore
    store=AuthorizationStore(ROOT/'reports/telegram_usage/telegram_users.db')
    expected={'397185913':('امامی','office_supervisor','admin'),
              '514458396':('هوشمند پاکدل','business_admin','admin'),
              '641220453':('حمید هدایتی','net_manager','net'),
              '1294822197':('پوریا آسترکی','net_manager_deputy','net')}
    for user,(label,role,profile) in expected.items():
        if store.identity_label(user)!=label or role not in store.roles(user) or store.resolve_profile(user,user)!=profile:
            raise RuntimeError('Live identity/role/profile changed: '+user)
    for database in ['reports/telegram_usage/telegram_users.db','data/fleet/db/fleet_ops.db','runtime/scheduler/runs.sqlite3']:
        with closing(sqlite3.connect((ROOT/database).as_uri()+'?mode=ro',uri=True)) as c:integrity(c)
    for name in ['auth-scheduler-final','work-orders-final','supervisor-final']:
        evidence=json.loads((ROOT/('runtime/pakdel-delivery/'+name+'.json')).read_text(encoding='utf-8'))
        if not evidence.get('success'):raise RuntimeError('Test gate failed: '+name)
    gateway=json.loads((HOME/'gateway_state.json').read_text(encoding='utf-8'))
    if (gateway.get('gateway_state')!='running' or gateway.get('active_agents')!=0
        or gateway.get('active_work') or gateway.get('session_store',{}).get('status')!='ok'
        or any(gateway.get('platforms',{}).get(p,{}).get('state')!='connected' for p in ['bale','telegram'])):
        raise RuntimeError('Gateway not idle and healthy')
    with closing(sqlite3.connect((ROOT/'data/fleet/db/fleet_ops.db').as_uri()+'?mode=ro',uri=True)) as c:
        if c.execute("SELECT 1 FROM service_work_order_dispatch WHERE state='SENDING'").fetchone():
            raise RuntimeError('Dispatch in flight')
    manifest={k.replace('\\','/'):v for k,v in json.loads((audit_dir()/'manifest-before.json').read_text(encoding='utf-8')).items()}
    for rel in FILES:
        if not (STAGE/rel).is_file():raise RuntimeError('Missing staged file: '+rel)
        if (ROOT/rel).exists() and rel in manifest and digest(ROOT/rel) not in {manifest[rel],digest(STAGE/rel)}:
            raise RuntimeError('Concurrent canonical code edit: '+rel)
        if (ROOT/rel).exists() and rel not in manifest and digest(ROOT/rel)!=digest(STAGE/rel):
            raise RuntimeError('Unexpected new canonical file: '+rel)
    return store


def deploy():
    store=check();audit=audit_dir();pre=audit/'immediate-predeploy'/datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%S%fZ');pre.mkdir(parents=True,exist_ok=True)
    for rel in ['reports/telegram_usage/telegram_users.db','data/fleet/db/fleet_ops.db','runtime/scheduler/runs.sqlite3']:
        backup(ROOT/rel,pre/Path(rel).name)
    manifest={k.replace('\\','/'):v for k,v in json.loads((audit/'manifest-before.json').read_text(encoding='utf-8')).items()}
    before={rel:manifest[rel] for rel in FILES if rel in manifest}
    # Modules first, dispatch/scheduler bridges next, YAML last. Each replacement atomic.
    for rel in FILES:atomic_copy(STAGE/rel,ROOT/rel)
    from tools.authorization.office_delivery import migrate, OFFICE_ROLE, COPY_ROLE
    from tools.fleet.work_orders.core.final_copy import activate
    from tools.fleet.work_orders.core.db import connect_db
    from tools.authorization.maintenance import CAPABILITY as MAINTENANCE
    maintenance_before=store.resolve_active_recipients(MAINTENANCE)
    migrate(store,audit/'assignment-backups',assignments=(('514458396',OFFICE_ROLE),('514458396',COPY_ROLE)),actor='explicit-user-pakdel-delivery-20261008')
    if store.resolve_active_recipients(MAINTENANCE)!=maintenance_before:
        raise RuntimeError('Maintenance recipients changed')
    with closing(connect_db()) as c,c:
        integrity(c);activate(c);integrity(c)
        activation=c.execute("SELECT value FROM final_copy_meta WHERE key='activated_at'").fetchone()[0]
        if c.execute('SELECT COUNT(*) FROM work_order_final_copy').fetchone()[0]:
            raise RuntimeError('Unexpected historical copy obligations at activation')
    after={rel:digest(ROOT/rel) for rel in FILES}
    (audit/'deployment.json').write_text(json.dumps(dict(activated_at=activation,before=before,after=after,files=FILES),indent=2),encoding='utf-8')
    print('Canonical code deployed; receive-only roles assigned; future SENT outbox activated. Gateway restart still required.')


def rollback():
    from tools.authorization.store import AuthorizationStore,now
    from tools.authorization.office_delivery import OFFICE_ROLE,COPY_ROLE
    audit=audit_dir();record=json.loads((audit/'deployment.json').read_text(encoding='utf-8'))
    store=AuthorizationStore(ROOT/'reports/telegram_usage/telegram_users.db')
    store.backup(audit/'rollback-backups')
    with closing(store._connect(write=True)) as c,c:
        for role in (OFFICE_ROLE,COPY_ROLE):
            c.execute("UPDATE auth_user_roles SET active=0,updated_at=? WHERE platform='bale' AND user_id='514458396' AND role=?",(now(),role))
            c.execute("INSERT INTO auth_events(platform,user_id,role,event_type,actor,created_at) VALUES('bale','514458396',?,'deactivated','pakdel-delivery-rollback',?)",(role,now()))
    with closing(sqlite3.connect(ROOT/'data/fleet/db/fleet_ops.db')) as c,c:
        c.execute("DELETE FROM final_copy_meta WHERE key='activated_at'")
    for rel,old in record['before'].items():
        if digest(ROOT/rel)!=record['after'][rel]:raise RuntimeError('Concurrent edit prevents rollback: '+rel)
        source=audit/'code'/rel
        if not source.is_file() or digest(source)!=old:raise RuntimeError('Rollback code backup mismatch: '+rel)
        atomic_copy(source,ROOT/rel)
    print('Added roles disabled; copy delivery disabled; prior code restored. Receipts and later production data retained. Controlled Gateway restart required.')


def main():
    parser=argparse.ArgumentParser();parser.add_argument('action',choices=['check','deploy','rollback']);args=parser.parse_args()
    if args.action=='check':check();print('Production deployment gates PASS')
    elif args.action=='deploy':deploy()
    else:rollback()


if __name__=='__main__':main()
