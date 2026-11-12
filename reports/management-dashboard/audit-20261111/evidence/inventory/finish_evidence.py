from collect_readonly import *
import re
import yaml
from collections import Counter

def main():
    plan=K/'reports/scheduler/repair-20261110/recovery-plan.json'
    data=json.loads(plan.read_text('utf8'));out=[]
    for op in data['ready_proposals']:
        if op['operation_id'] not in {'d6f8f4f75bd65e5eacecf08e4772d919','38b37b4e949e5eb7b906280ea54e4487','77ef651b80bb56c68216267e119d96fa','d24c5f7fe66b5f14aa533aca0805ee1b'}:continue
        row=dict(operation_id=op['operation_id'],target_date=op['data_date'])
        for field,hashfield in [('pdf','pdf_sha256'),('source_snapshot','source_sha256')]:
            p=Path(op[field])
            row[field]=dict(path=str(p),retained=p.is_file(),expected_sha256=op[hashfield],current_sha256=sha(p) if p.is_file() else None)
            row[field]['bytes_match']=row[field]['current_sha256']==op[hashfield]
        out.append(row)
    save('sanitized/recovery-retained-bytes.json',dict(observed_utc=datetime.now(timezone.utc).isoformat(),
        provenance='historical proposal paths only; sent status from current receipt chain, not old plan status',operations=out))
    # Correct only the aggregate projection of immutable saved runs.
    p=OUT/'evidence/sanitized/hrms-output-summary.json'
    summaries=json.loads(p.read_text('utf8'))
    for summary in summaries:
        f=Path(summary['path']).parent/'monthly_results.json'
        d=json.loads(f.read_text('utf8'))
        summary['publication_states']=dict(Counter(x.get('publication',{}).get('publication_state','unknown') for x in d))
    save('sanitized/hrms-output-summary.json',summaries)
    # Complete synthetic examples for every response; no real source values.
    api=yaml.safe_load((OUT/'08-OPENAPI-DRAFT.yaml').read_text('utf8'))
    schemas=api['components']['schemas']
    def example(s,field=''):
        if '$ref' in s:return example(schemas[s['$ref'].rsplit('/',1)[1]],field)
        if 'const' in s:return s['const']
        if 'enum' in s:return 'unknown' if 'unknown' in s['enum'] else s['enum'][0]
        t=s.get('type')
        if isinstance(t,list):return None
        if t=='object':return {k:example(v,k) for k,v in s.get('properties',{}).items()}
        if t=='array':return []
        if t=='boolean':return False
        if t=='integer':return s.get('minimum',0)
        if s.get('format')=='date-time':return '2026-11-11T09:00:00+00:00'
        pattern=s.get('pattern','')
        if pattern.startswith('^1405/'):return '1405/08' if field=='period' else '1405/08/17'
        if '0-9a-f' in pattern:return 'a'*64
        if field in ['id','occurrence_id']:return 'synthetic_id_001'
        return 'SYNTHETIC'
    examples={name:example(s) for name,s in schemas.items() if name.endswith('Response')}
    # Explicit full occurrence and unit example rather than empty collection.
    ex=example(schemas['Occurrence'])
    ex.update(schedule_id='driver_daily_office_supervisor',title_fa='گزارش نمونه مصنوعی دفتر',
              primary_status='partial',expected_artifacts=2,expected_deliveries=4,
              attention_required=True,recipient_set_provenance='historical_reservation')
    examples['ReportResponse']['data']=ex
    examples['ReportsResponse']['data']=[ex]
    examples['OverviewResponse']['data']['counts'].update(occurrences=1,logical_artifacts=2,recipient_deliveries=4,sent_with_receipt=3,complete_denominator=True)
    save('sanitized/api-examples.json',dict(examples_are_synthetic=True,examples=examples))
    for path,value in api['paths'].items():
        response=value['get']['responses'].get('200',{}).get('content',{}).get('application/json')
        if response:
            name=response['schema']['$ref'].rsplit('/',1)[1]
            response['example']=examples[name]
    (OUT/'08-OPENAPI-DRAFT.yaml').write_text(yaml.safe_dump(api,allow_unicode=True,sort_keys=False,width=130),encoding='utf8')
    print('Recovery retained byte matches',[(x['target_date'],x['pdf']['bytes_match'],x['source_snapshot']['bytes_match']) for x in out])

if __name__=='__main__':main()
