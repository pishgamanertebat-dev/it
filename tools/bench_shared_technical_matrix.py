"""Fresh-session three-profile benchmark matrix, without sending any messages."""
from pathlib import Path
import argparse,ast,concurrent.futures,json,os,subprocess,time
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes');OUT=ROOT/'runtime/shared-technical-answering-20261005'
parser=argparse.ArgumentParser();parser.add_argument('--final',action='store_true');parser.add_argument('--messaging-identities',action='store_true');args=parser.parse_args()
if args.messaging_identities:OUT=OUT/'messaging-benchmarks'
elif args.final:OUT=OUT/'final-benchmarks'
OUT.mkdir(parents=True,exist_ok=True)
questions={'hd465':'ریتارد اهرمی ضعیف کارمیکند در دستگاه 465','part':'شماره فنی قطعه 6218-11-5830 برای HD785-7 را بررسی کن','general':'پایتخت ژاپن چیست؟'}
tree=ast.parse((ROOT/'tools/test_maintenance_source_routing.py').read_text(encoding='utf-8'))
fixture=next(ast.literal_eval(node.value) for node in tree.body if isinstance(node,ast.Assign) and any(isinstance(t,ast.Name) and t.id=='D' for t in node.targets))
questions['mixed']='HD785-7 '+fixture
for case,question in questions.items():(OUT/(case+'.txt')).write_text(question,encoding='utf-8')

def run(case,profile):
 folder=OUT/('bench-'+case+'-'+profile);folder.mkdir(exist_ok=True)
 command=[str(HOME/'hermes-agent/venv/Scripts/python.exe'),str(ROOT/'tools/bench_shared_fast_core.py'),'--profile',profile,'--question-file',str(OUT/(case+'.txt')),'--output',str(folder)]
 if args.messaging_identities:
  command.extend(['--user-id',{'default':'85539397','maintenance':'654806764','admin':'397185913'}[profile]])
 if (folder/'summary.json').exists():
  result=type('Completed',(),{'returncode':0})()
 else:
  with (folder/'runner.log').open('w',encoding='utf-8') as log:
   result=subprocess.run(command,cwd=ROOT,env=dict(os.environ,PYTHONIOENCODING='utf-8'),stdout=log,stderr=subprocess.STDOUT,creationflags=getattr(subprocess,'CREATE_NO_WINDOW',0))
 summary=json.loads((folder/'summary.json').read_text(encoding='utf-8')) if (folder/'summary.json').exists() else {}
 record={'case':case,'profile':profile,'exit_code':result.returncode,**{key:summary.get(key) for key in ['wall_seconds','api_calls','tool_sequence','media_count','finish_reason']}}
 print(json.dumps(record,ensure_ascii=True),flush=True);return record
records=[]
for case in ['hd465','part','general','mixed']:
 with concurrent.futures.ThreadPoolExecutor(max_workers=3) as pool:records.extend(pool.map(lambda profile:run(case,profile),['default','maintenance','admin']))
 (OUT/'benchmarks.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
 if any(r['exit_code']!=0 for r in records):raise SystemExit('Benchmark execution failure; inspect private logs')
