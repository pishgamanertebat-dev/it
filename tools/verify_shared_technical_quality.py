"""Evidence parity, invariant snapshots, and semantic schedule verification."""
from pathlib import Path
import argparse,hashlib,json,re,sys
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes');OUT=ROOT/'runtime/shared-technical-answering-20261005';BENCH=OUT/'final-benchmarks'
parser=argparse.ArgumentParser();parser.add_argument('--messaging-identities',action='store_true');args=parser.parse_args()
if args.messaging_identities:BENCH=OUT/'messaging-benchmarks'
SUFFIX='-messaging' if args.messaging_identities else ''
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
def summary(case,profile):return json.loads((BENCH/('bench-'+case+'-'+profile)/'summary.json').read_text(encoding='utf-8'))
profiles=['default','maintenance','admin'];checks={};metrics=[]
for case in ['hd465','part','general','mixed']:
 runs=[summary(case,p) for p in profiles]
 checks[case+'_identical_prompt']=len({r['question_sha256'] for r in runs})==1
 checks[case+'_fresh_sessions']=all(r['fresh_session'] and r['history_messages']==0 for r in runs) and len({r['session_id'] for r in runs})==3
 checks[case+'_same_technical_schema']=len({json.dumps(sorted((t for t in r['tool_schema'] if t['function']['name'] in {'maintenance_manual_evidence','maintenance_partbook_lookup'}),key=lambda t:t['function']['name']),sort_keys=True) for r in runs})==1
 if args.messaging_identities:
  checks[case+'_existing_approved_identity_bound']=all(r['approved_identity_bound'] for r in runs)
  checks[case+'_operational_tools_remain_scoped']=not any(t['function']['name'].startswith('function_') for t in runs[0]['tool_schema']) and all(len(r['tool_schema'])==30 for r in runs[1:])
 for r in runs:metrics.append({key:r[key] for key in ['profile','question','session_id','model','reasoning_config','api_calls','tool_count','tool_sequence','wall_seconds','input_tokens','output_tokens','context_actual_input_tokens','context_tokens','coverage','media_count']})
 if case=='general':checks['general_no_domain_calls']=all(not r['tool_calls'] for r in runs)
 if case=='mixed':checks['mixed_both_streams']=all({'maintenance_manual_evidence','maintenance_partbook_lookup'}<={c['name'] for c in r['tool_calls']} and r['tool_calls'][0]['name']=='maintenance_partbook_lookup' for r in runs)
 if case=='part':
  packets=[r['evidence_packets'][0] for r in runs]
  checks['part_direct_path']=all(r['tool_sequence']==['maintenance_partbook_lookup'] and r['api_calls']==2 for r in runs)
  checks['part_candidates_equal']=packets[0]['candidates']==packets[1]['candidates']==packets[2]['candidates']
  checks['part_coverage_equal']=len({(p['coverage_complete'],p['coverage_note']) for p in packets})==1
  checks['part_same_verified_image']=len({digest(Path(re.search(r'^MEDIA:(.*)$',r['final_answer'],re.M).group(1))) for r in runs})==1
 if case=='hd465':
  packets=[r['evidence_packets'][0]['retrieval']['manual_packet'] for r in runs]
  checks['hd465_manual_first']=all(r['tool_calls'][0]['name']=='maintenance_manual_evidence' and r['tool_calls'][0]['arguments']['phase']=='retrieve' for r in runs)
  checks['hd465_model_correct']=all(r['tool_calls'][0]['arguments']['model']=='HD465-7R' for r in runs)
  checks['hd465_coverage_complete']=all(r['coverage'][0]['status']=='complete' for r in runs)
  h11=[next(e for e in p['evidence'] if e['pdf_page']==1322) for p in packets]
  checks['hd465_h11_text_equal']=h11[0]==h11[1]==h11[2]
  checks['hd465_same_source_manual']=len({p['manual'] for p in packets})==1
  checks['hd465_index_not_evidence']=all(p['index_is_evidence'] is False for p in packets)
  checks['hd465_no_wandering']=all(c['name']=='maintenance_manual_evidence' for r in runs for c in r['tool_calls'])
  images=[]
  for r in runs:
   bypage={}
   for filename in re.findall(r'^MEDIA:(.*)$',r['final_answer'],re.M):
    match=re.search(r'-p([0-9]+)\.png',filename)
    if match:bypage[int(match.group(1))]=digest(Path(filename))
   images.append(bypage)
  shared=set.intersection(*(set(p) for p in images))
  checks['hd465_common_image_hashes_equal']=all(len({p[page] for p in images})==1 for page in shared)
  checks['hd465_all_media_exists']=all(r['media_files_exist'] for r in runs)
  def norm(text):return text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٫','0123456789.'))
  checks['hd465_retarder_pressure_grounded']=all('8.7' in norm(r['final_answer']) and '0.64' in norm(r['final_answer']) for r in runs)
  checks['hd465_manual_gauge_grounded']=all('39.2' in norm(r['final_answer']) for r in runs)
# Persist both observed benchmark metrics and evidence-only parity checks.
(OUT/('benchmark-metrics'+SUFFIX+'.json')).write_text(json.dumps(metrics,ensure_ascii=False,indent=2),encoding='utf-8')
(OUT/('quality-parity'+SUFFIX+'.json')).write_text(json.dumps({'checks':checks,'common_hd465_image_pages':sorted(shared),'manual_h11_sha256':hashlib.sha256(json.dumps(h11[0],sort_keys=True).encode()).hexdigest(),'manual_review':'All HD465 answers distinguish variant assumption, document facts, diagnostic inference and safety. Mixed lookup remains partial/unverified for the requested valve; all three avoid inventing a PN.'},indent=2),encoding='utf-8')
# A live ticker advances runtime metadata; schedule definitions must remain identical.
base=json.loads((OUT/'baseline.json').read_text(encoding='utf-8'));record=next(r for r in base if r['path'].replace('\\','/').endswith('/cron/jobs.json'))
before=json.loads(Path(record['backup']).read_text(encoding='utf-8'));after=json.loads(Path(record['path']).read_text(encoding='utf-8'))
def schedule_intent(value):
 value=json.loads(json.dumps(value));value.pop('updated_at',None)
 for job in value.get('jobs',[]):
  for key in ['next_run_at','last_run_at','last_dispatch']:job.pop(key,None)
  if isinstance(job.get('repeat'),dict):job['repeat'].pop('completed',None)
 return value
schedule_equal=schedule_intent(before)==schedule_intent(after)
(OUT/'schedule-parity.json').write_text(json.dumps({'schedule_definitions_equal':schedule_equal,'before_semantic_sha256':hashlib.sha256(json.dumps(schedule_intent(before),sort_keys=True).encode()).hexdigest(),'after_semantic_sha256':hashlib.sha256(json.dumps(schedule_intent(after),sort_keys=True).encode()).hexdigest(),'volatile_runtime_fields':'updated_at, next_run_at, last_run_at, last_dispatch, repeat.completed advance under existing ticker; cron output rotation and history are not schedule edits'},indent=2),encoding='utf-8')
state_before=json.loads((OUT/'authorization-before.json').read_text(encoding='utf-8'));state_after=json.loads((OUT/'authorization-after.json').read_text(encoding='utf-8'))
assert state_before==state_after
before_gateway=json.loads((OUT/'gateway-before.json').read_text(encoding='utf-8'));gateway=json.loads((HOME/'gateway_state.json').read_text(encoding='utf-8'))
assert before_gateway['pid']==gateway['pid'] and before_gateway['code_sha']==gateway['code_sha']
(OUT/'gateway-final.json').write_text(json.dumps(gateway,indent=2),encoding='utf-8')
print(json.dumps({'evidence_checks':checks,'schedule_definitions_equal':schedule_equal,'authorization_equal':True,'gateway_pid':gateway['pid'],'gateway':gateway['gateway_state'],'platforms':{k:v['state'] for k,v in gateway['platforms'].items()}},indent=2))
assert all(checks.values()) and schedule_equal
