"""Checks documentation contracts only. No application imports or external requests."""
from pathlib import Path
from datetime import datetime,timezone
import ast
import hashlib
import json
import re
import urllib.parse
import yaml

ROOT=Path(__file__).resolve().parents[2]
EV=ROOT/'evidence'
def sha(p):return hashlib.sha256(p.read_bytes()).hexdigest()
def load(p):return json.loads(p.read_text('utf-8-sig'))
def dump(p,data):p.write_text(json.dumps(data,ensure_ascii=False,indent=2),encoding='utf8')
failures=[];checks={}
def check(name,condition,detail=None):
    checks[name]=dict(passed=bool(condition),detail=detail)
    if not condition:failures.append(name)

required=['DASHBOARD-MASTER-PLAN.md','RESUME-PROJECT-LATER.md','CHANGELOG-AND-DECISIONS.md','00-EXECUTIVE-SUMMARY-FA.md','01-AS-IS-INFRASTRUCTURE.md','02-KOMATSOAI-AUDIT.md','03-HRMS-AUDIT.md','04-DATA-SOURCE-CATALOG.md','04-DATA-DICTIONARY.json','04-SOURCE-OF-TRUTH-MATRIX.md','05-ARCHITECTURE-BLUEPRINT.md','06-UI-UX-PRD.md','06-PAGE-SPECS.md','07-SECURITY-AND-ACCESS.md','08-API-CONTRACT.md','08-OPENAPI-DRAFT.yaml','09-FUTURE-WRITE-OPERATIONS.md','10-TEST-AND-ACCEPTANCE-PLAN.md','11-IMPLEMENTATION-ROADMAP.md','12-RISK-REGISTER.md','13-OPEN-QUESTIONS.md','14-SOURCE-EVIDENCE-INDEX.md','15-SELF-AUDIT.md']
check('required_deliverables',all((ROOT/name).is_file() for name in required),dict(count=len(required)))
code=load(EV/'inventory/code-index.json')
drift=[]
for row in code:
    p=Path(row['path'])
    if not p.is_file() or sha(p)!=row['sha256']:drift.append(str(p))
check('reviewed_source_code_hashes_unchanged',not drift,dict(files_checked=len(code),drift=drift))
actions=dict(audit_version='management-dashboard-audit-1',completed_utc=datetime.now(timezone.utc).isoformat(),
    write_scope=str(ROOT),production_code_mutations=0,operational_workbook_writes=0,
    source_sql_mutations=0,receipt_rewrites=0,backfills=0,replays=0,transport_sends=0,
    gateway_or_scheduler_restarts=0,role_or_permission_changes=0,firewall_or_vpn_changes=0,
    services_created=0,ports_opened=0,dependencies_installed=0,hrms_calculations_executed=0,
    source_read_methods=['file/code/metadata read','SQLite mode=ro query_only Online Backup to memory','selected aggregate queries','allowlisted JSON state','WinAPI/registry/network information'],
    external_requests='official technical documentation only; no private operational data sent',
    source_code_files_verified=len(code),source_code_drift=drift,
    production_state_note='Normal background jobs remained active. This audit makes no byte-equality claim for live databases or source workbooks over the full observation interval.')
dump(EV/'inventory/audit-actions.json',actions)

# Refresh the evidence hash table without rerendering polished documents.
p=ROOT/'14-SOURCE-EVIDENCE-INDEX.md';body=p.read_text('utf8')
start=body.find('| فایل | SHA256 prefix | گروه |')
end=body.find('## نقاط کلیدی کد',start)
if start>=0 and end>start:
    lines=['| فایل | SHA256 prefix | گروه |','|---|---|---|']
    for folder in ['inventory','sanitized','test-references']:
        for f in sorted((EV/folder).glob('*')):
            if f.suffix not in ['.json','.txt'] or f.name in ['validation.json','artifact-inventory.json']:continue
            lines.append(f"| [{f.name}]({f.relative_to(ROOT).as_posix()}) | {sha(f)[:16]}… | {folder} |")
    body=body[:start]+'\n'.join(lines)+'\n\n'+body[end:]
    p.write_text(body,encoding='utf8')
check('source_index_evidence_hash_table_refreshed',start>=0 and end>start)

json_errors=[]
for p in ROOT.rglob('*.json'):
    try:load(p)
    except (OSError,ValueError) as e:json_errors.append(dict(path=str(p.relative_to(ROOT)),error=type(e).__name__))
check('json_parses',not json_errors,json_errors)
api=yaml.safe_load((ROOT/'08-OPENAPI-DRAFT.yaml').read_text('utf8'))
check('openapi_version',api.get('openapi')=='3.1.0')
paths=api['paths'];schemas=api['components']['schemas']
refs=[]
def visit(x):
    if isinstance(x,dict):
        if '$ref' in x:refs.append(x['$ref'])
        for v in x.values():visit(v)
    elif isinstance(x,list):
        for v in x:visit(v)
visit(api)
missing_refs=[r for r in refs if not r.startswith('#/components/schemas/') or r.rsplit('/',1)[1] not in schemas]
check('openapi_references_resolve',not missing_refs,dict(ref_count=len(refs),missing=missing_refs))
ops=[v['get'] for v in paths.values()]
check('seventeen_read_routes',len(paths)==17 and all(set(v)=={'get'} for v in paths.values()),len(paths))
check('unique_operation_ids',len({x['operationId'] for x in ops})==len(ops))
check('all_api_routes_authenticated',all(x.get('security')==[{'cookieAuth':[]}] and x.get('x-required-permissions') for x in ops))
params_ok=True
for route,definition in paths.items():
    params=definition['get']['parameters']
    pairs=[(x['in'],x['name']) for x in params]
    if len(pairs)!=len(set(pairs)):params_ok=False
    for key in re.findall(r'\{([^}]+)\}',route):
        if not any(x['name']==key and x['in']=='path' and x.get('required') for x in params):params_ok=False
check('path_and_query_parameter_consistency',params_ok)
check('schemas_required_properties_consistent',all(set(s.get('required',[]))<=set(s.get('properties',{})) for s in schemas.values()))

def validate(value,s,path):
    if '$ref' in s:return validate(value,schemas[s['$ref'].rsplit('/',1)[1]],path)
    if 'const' in s and value!=s['const']:raise ValueError(path+' const')
    if 'enum' in s and value not in s['enum']:raise ValueError(path+' enum')
    types=s.get('type',[]);types=[types] if isinstance(types,str) else types
    t='null' if value is None else 'boolean' if isinstance(value,bool) else 'integer' if isinstance(value,int) else 'number' if isinstance(value,float) else 'object' if isinstance(value,dict) else 'array' if isinstance(value,list) else 'string'
    if types and t not in types:raise ValueError(path+' type '+t)
    if value is None:return
    if isinstance(value,dict):
        for k in s.get('required',[]):
            if k not in value:raise ValueError(path+' missing '+k)
        if s.get('additionalProperties') is False and set(value)-set(s.get('properties',{})):raise ValueError(path+' extra property')
        for k,v in value.items():
            child=s.get('properties',{}).get(k)
            if child is None and isinstance(s.get('additionalProperties'),dict):child=s['additionalProperties']
            if child:validate(v,child,path+'.'+k)
    elif isinstance(value,list):
        for i,v in enumerate(value):validate(v,s.get('items',{}),path+f'[{i}]')
    elif isinstance(value,str):
        if s.get('pattern') and not re.search(s['pattern'],value):raise ValueError(path+' pattern')
        if s.get('format')=='date-time':
            stamp=datetime.fromisoformat(value.replace('Z','+00:00'))
            if stamp.utcoffset() is None:raise ValueError(path+' timezone')
    elif isinstance(value,(int,float)) and not isinstance(value,bool):
        if 'minimum' in s and value<s['minimum']:raise ValueError(path+' minimum')
        if 'maximum' in s and value>s['maximum']:raise ValueError(path+' maximum')
examples=load(EV/'sanitized/api-examples.json')['examples']
example_errors=[]
for name,example in examples.items():
    try:validate(example,schemas[name],name)
    except ValueError as e:example_errors.append(str(e))
check('complete_synthetic_api_examples_match_schema',not example_errors,dict(count=len(examples),errors=example_errors))
check('inline_openapi_examples_present',all('example' in op['responses']['200'].get('content',{}).get('application/json',{}) for op in ops if 'application/json' in op['responses']['200'].get('content',{})))

catalog=load(ROOT/'04-DATA-DICTIONARY.json')
ids={s['source_id'] for s in catalog['sources']}
check('twenty_six_unique_sources',len(ids)==len(catalog['sources'])==26)
required_fields=['source_id','name_fa','owner','path','type','entities','authority','primary_key','relationships','change_frequency','last_modified_utc','safe_read','snapshot','lock_risks','performance','confidentiality','roles','quality','historical_limits','freshness','anonymous_example','api_mapping']
check('source_catalog_required_fields',all(set(required_fields)<=set(s) for s in catalog['sources']))
check('dictionary_eighty_four_tables',sum(len(v['tables']) for v in catalog['database_dictionary'].values())==84)
check('live_hrms_not_misrepresented',all(catalog['database_dictionary'][s]['provenance']=='installed_code_ddl_not_live_schema' and all(t['count'] is None for t in catalog['database_dictionary'][s]['tables'].values()) for s in ['H-PERSONNEL','H-BOT']))
jobs=load(EV/'sanitized/scheduler-facts.json')['jobs']
check('eight_current_enabled_jobs_covered',len(jobs)==8 and all(j['enabled'] and j['id'] in (ROOT/'02-KOMATSOAI-AUDIT.md').read_text('utf8') for j in jobs))
page_doc=(ROOT/'06-PAGE-SPECS.md').read_text('utf8')
check('all_eight_pages_specified',all(('صفحه '+s+' ') in page_doc for s in 'ABCDEFGH'))
hr_doc=(ROOT/'03-HRMS-AUDIT.md').read_text('utf8')
check('all_nine_hr_groups_covered',all(f'G{i:02}' in hr_doc for i in range(1,10)))
facts=load(EV/'sanitized/scheduler-facts.json')
check('four_recovery_receipt_chains_match',len(facts['recoveries'])==4 and all(x['receipt_chain_matches']==1 for x in facts['recoveries']))
retained=load(EV/'sanitized/recovery-retained-bytes.json')['operations']
check('four_recovery_artifact_source_bytes_match',len(retained)==4 and all(x['pdf']['bytes_match'] and x['source_snapshot']['bytes_match'] for x in retained))

# Inventory now exists so links to both final JSON artifacts resolve in this pass.
dump(EV/'inventory/artifact-inventory.json',dict(status='building'))
dump(EV/'inventory/validation.json',dict(status='building'))
broken=[];fence_errors=[];headers=[];count=0
for p in ROOT.glob('*.md'):
    text=p.read_text('utf8');count+=1
    if not all(token in text[:2500] for token in ['UTC','تهران','Commit','Schema','Source','UNKNOWN']):headers.append(p.name)
    if sum(1 for line in text.splitlines() if line.lstrip().startswith('~~~'))%2:fence_errors.append(p.name)
    for link in re.findall(r'(?<!!)\[[^\]]*\]\(([^)]+)\)',text):
        target=link.strip('<>').split('#',1)[0]
        if not target or target.startswith(('https://','http://','mailto:')):continue
        target=urllib.parse.unquote(target)
        if not (p.parent/target).exists():broken.append(dict(document=p.name,target=target))
check('markdown_local_links_resolve',not broken,dict(documents=count,broken=broken))
check('markdown_code_fences_balanced',not fence_errors,fence_errors)
check('report_headers_have_audit_provenance',not headers,headers)
py_errors=[]
for p in (EV/'inventory').glob('*.py'):
    try:ast.parse(p.read_text('utf8'))
    except SyntaxError as e:py_errors.append(dict(file=p.name,line=e.lineno))
check('audit_script_syntax',not py_errors,py_errors)
forbidden_binary=[str(p.relative_to(ROOT)) for p in ROOT.rglob('*') if p.is_file() and p.suffix.lower() in ['.db','.sqlite','.sqlite3','.xlsx','.xls','.pdf','.png','.jpg','.zip','.env']]
check('no_raw_database_workbook_private_binary_copies',not forbidden_binary,forbidden_binary)
secret_hits=[]
patterns=[r'sk-(?:proj-)?[A-Za-z0-9_-]{24,}',r'\b[0-9]{8,12}:[A-Za-z0-9_-]{24,}',r'-----BEGIN [A-Z ]*PRIVATE KEY-----',r'xox[baprs]-[A-Za-z0-9-]{20,}']
for p in ROOT.rglob('*'):
    if p.is_file() and p.suffix in ['.md','.json','.yaml','.txt']:
        t=p.read_text('utf-8-sig')
        for pattern in patterns:
            if re.search(pattern,t):secret_hits.append(str(p.relative_to(ROOT)))
check('common_secret_patterns_absent',not secret_hits,sorted(set(secret_hits)))
inventory=[dict(path=p.relative_to(ROOT).as_posix(),bytes=p.stat().st_size,sha256=sha(p)) for p in sorted(ROOT.rglob('*')) if p.is_file() and p.name not in ['artifact-inventory.json','validation.json']]
dump(EV/'inventory/artifact-inventory.json',dict(created_utc=datetime.now(timezone.utc).isoformat(),files=inventory,
    exclusions=['artifact-inventory.json','validation.json'],reason='avoid recursive self hashes'))
result=dict(completed_utc=datetime.now(timezone.utc).isoformat(),status='PASS' if not failures else 'FAIL',
    check_count=len(checks),failed=failures,checks=checks,
    scope='Documentation and static contract verification only; dashboard not built; domain/native tests not rerun',
    explicit_gaps=['HRMS live DB schema/count unreadable','WireGuard route and live task registrations unverified','legacy recipient/artifact proof incomplete','no new native Office or load benchmark'],
    formal_openapi_meta_schema_validator='not installed or run; structural/schema/example checks are enumerated above',
    production_mutations_attempted_by_audit=0)
dump(EV/'inventory/validation.json',result)
print(json.dumps(dict(status=result['status'],checks=len(checks),failed=failures,files=len(inventory)+2,bytes=sum(x['bytes'] for x in inventory),source_files_verified=len(code)),ensure_ascii=False))
if failures:raise SystemExit(1)
