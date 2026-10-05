"""Real messaging schema and read-only identity invariants; no transport sends."""
from pathlib import Path
import ast,hashlib,json,logging,os,sqlite3,sys
from types import SimpleNamespace
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes');OUT=ROOT/'runtime/shared-technical-answering-20261005'
phase=sys.argv[1]
os.environ['HERMES_HOME']=str(HOME);sys.path.insert(0,str(HOME/'hermes-agent'));sys.path.append(str(ROOT))
from hermes_constants import set_hermes_home_override,reset_hermes_home_override
from hermes_cli.config import load_config
from hermes_cli.plugins import discover_plugins
from model_tools import get_tool_definitions
from integrations.hermes.role_routing import AuthorizationStore
store=AuthorizationStore();ids=['397185913','514458396','1636934401','654806764','1732374823','455740857']
tree=ast.parse((HOME/'hermes-agent/gateway/run_turn.py').read_text(encoding='utf-8'))
node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=='_resolve_enabled_toolsets_for_source')
namespace={'SessionSource':object,'logger':logging.getLogger('surface')}
exec(compile(ast.Module(body=[node],type_ignores=[]),'<native-resolver>','exec'),namespace)
runner=SimpleNamespace(_delivery_adapter_for=lambda source:None)
for profile,home in [('default',HOME),('maintenance',HOME/'profiles/maintenance'),('admin',HOME/'profiles/admin')]:
 token=set_hermes_home_override(home)
 try:
  discover_plugins();cfg=load_config();records=[]
  for platform in ['bale','telegram']:
   for user in ids+['unregistered-synthetic']:
    source=SimpleNamespace(user_id=user,chat_id=user,chat_type='dm')
    selected=namespace['_resolve_enabled_toolsets_for_source'](runner,cfg,source,platform)
    definitions=get_tool_definitions(selected,quiet_mode=True,skip_tool_search_assembly=True)
    names=sorted(d['function']['name'] for d in definitions)
    forbidden={'terminal','PowerShell','execute_code','read_file','write_file','patch','search_files','skill_manage','process_manage'}
    assert not forbidden.intersection(names)
    if profile=='default':assert not any(n.startswith('function_') for n in names)
    capable=bool(store.function_scope(user,platform)) and platform=='bale'
    if not capable:assert not any(n.startswith('function_') for n in names)
    records.append({'platform':platform,'user':user,'toolsets':selected,'tools':names,'count':len(names),'function_capability':capable})
  (OUT/(phase+'-'+profile+'-identity-surfaces.json')).write_text(json.dumps(records,indent=2),encoding='utf-8')
  print(profile,sorted(set(r['count'] for r in records)),flush=True)
 finally:reset_hermes_home_override(token)
conn=sqlite3.connect((ROOT/'reports/telegram_usage/telegram_users.db').resolve().as_uri()+'?mode=ro',uri=True)
tables=[r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' AND name LIKE 'auth_%'")]
state={t:sorted(conn.execute('SELECT * FROM '+t).fetchall(),key=repr) for t in tables}
state['identities']=conn.execute('SELECT platform,user_id,registration_status FROM channel_users WHERE user_id IN ('+','.join('?' for _ in ids)+') ORDER BY platform,user_id',ids).fetchall();conn.close()
(OUT/('authorization-'+phase+'.json')).write_text(json.dumps(state,ensure_ascii=False,indent=2),encoding='utf-8')
if phase=='after':assert state==json.loads((OUT/'authorization-before.json').read_text(encoding='utf-8')) or json.loads(json.dumps(state))==json.loads((OUT/'authorization-before.json').read_text(encoding='utf-8'))
