from pathlib import Path
import hashlib,json,os,sys
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes');OUT=ROOT/'runtime/shared-technical-answering-20261005'
def digest(path):return hashlib.sha256(path.read_bytes()).hexdigest()
allowed={str(ROOT/'integrations/hermes/plugins/komatso-maintenance-manual/__init__.py'),str(ROOT/'integrations/hermes/profiles/maintenance/SOUL.md'),str(ROOT/'tools/manual_worker_batch.py'),str(ROOT/'tools/manual_worker_web.py'),str(HOME/'profiles/maintenance/SOUL.md'),str(HOME/'profiles/maintenance/plugins/komatso-maintenance-manual/__init__.py')}
allowed|={str(h/'config.yaml') for h in [HOME,HOME/'profiles/maintenance',HOME/'profiles/admin']}
records=[]
for record in json.loads((OUT/'baseline.json').read_text(encoding='utf-8')):
 path=Path(record['path']);after=digest(path) if path.exists() else None
 cron=HOME/'cron'
 volatile=path.parent==cron and path.name in {'executions.db','jobs.json','ticker_heartbeat','ticker_last_success'} or path.is_relative_to(cron/'output')
 records.append({**record,'after_sha256':after,'changed':after!=record['before_sha256'],'authorized_change':str(path) in allowed,'volatile_existing_runtime':volatile})
(OUT/'hash-verification.json').write_text(json.dumps(records,indent=2),encoding='utf-8')
unexpected=[r for r in records if r['changed'] and not r['authorized_change'] and not r['volatile_existing_runtime']]
if any(r['changed'] and r['volatile_existing_runtime'] for r in records):
 assert json.loads((OUT/'schedule-parity.json').read_text(encoding='utf-8'))['schedule_definitions_equal']
print('Baseline files',len(records),'authorized changes',sum(r['changed'] and r['authorized_change'] for r in records),'unexpected changes',len(unexpected))
print(json.dumps([{'path':r['path'],'before':r['before_sha256'],'after':r['after_sha256']} for r in unexpected],indent=2))
assert not unexpected, 'Protected immutable file changed'
canonical=[]
for folder in [ROOT/'integrations/hermes/plugins/komatso-technical-docs',ROOT/'integrations/hermes/shared_fast_core']:
 for p in folder.glob('*'):
  if p.is_file():canonical.append({'path':str(p),'sha256':digest(p)})
for p in [ROOT/'integrations/hermes/technical_docs_boundary.py',ROOT/'tools/manual_worker_batch.py',ROOT/'tools/manual_worker_web.py',ROOT/'integrations/hermes/deploy_shared_technical.py']:canonical.append({'path':str(p),'sha256':digest(p)})
(OUT/'canonical-hashes.json').write_text(json.dumps(canonical,indent=2),encoding='utf-8')
(OUT/'gateway-after.json').write_bytes((HOME/'gateway_state.json').read_bytes())
