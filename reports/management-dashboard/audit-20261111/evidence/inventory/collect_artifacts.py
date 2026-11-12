from collect_domain import *
import zipfile
import xml.etree.ElementTree as ET

def main():
    sources=[]
    for name in ['سرریز روزانه.xlsx','گزارش روزانه رانندگان2.xlsx','تعمیرات 1405.xlsx','موازنه روغن مصرفی.xlsx','عملکرد ماشین آلات.xlsx']:
        p=Path('E:/Function')/name
        item=stat(p)
        try:
            first=p.read_bytes();second=p.read_bytes()
            if first!=second:raise ValueError('source_changed_during_read')
            item.update(sha256=hashlib.sha256(first).hexdigest(),observation='two_equal_reads_not_writer_coordinated_snapshot')
            with zipfile.ZipFile(io.BytesIO(first)) as z:
                workbook=ET.fromstring(z.read('xl/workbook.xml'))
                item['sheet_count']=len(workbook.find('{http://schemas.openxmlformats.org/spreadsheetml/2006/main}sheets'))
                item['has_external_links']=any(n.startswith('xl/externalLinks/externalLink') for n in z.namelist())
        except (OSError,ValueError,zipfile.BadZipFile) as e:item.update(access='unavailable',error_type=type(e).__name__)
        sources.append(item)
    save('inventory/workbook-metadata.json',dict(observed_utc=NOW,sources=sources))
    retained=[]
    for p in (K/'runtime/scheduler/maintenance-ready').glob('*/ready.json'):
        pointer=json.loads(p.read_text('utf8'));version=pointer['manifest_sha256']
        # Find content addressed manifest without relying on a mutable app loader.
        candidates=[f for f in p.parent.glob('*.json') if f.name!='ready.json' and sha(f)==version]
        item=dict(date_folder=p.parent.name,pointer=str(p),manifest_sha256=version,manifest_bytes_verified=bool(candidates))
        if candidates:
            d=json.loads(candidates[0].read_text('utf8'))
            item.update({k:d.get(k) for k in ['state','target_date','prepared_at','source_sha256','pdf_sha256','report_sha256']})
            item['content_checks']={key:(p.parent/(d[key]+ext)).is_file() and sha(p.parent/(d[key]+ext))==d[key] for key,ext in [('source_sha256','.xlsx'),('pdf_sha256','.pdf'),('report_sha256','.report.json')]}
        retained.append(item)
    save('sanitized/pinned-artifacts.json',dict(observed_utc=NOW,maintenance=retained))
    journal={}
    for name in ['K-ENTRY','K-MAINT-ENTRY']:
        db=snapshot(DBS[name])
        item=dict(edits=rows(db,'SELECT status,COUNT(*) count FROM edits GROUP BY status'))
        if name=='K-ENTRY':
            item['draft_states']=rows(db,'SELECT status,COUNT(*) count FROM entry_drafts GROUP BY status')
            item['latest_fault_sync_error']=rows(db,'SELECT created,error FROM fault_sync_failures ORDER BY id DESC LIMIT 1')
        else:
            item['maintenance_failures']=rows(db,'SELECT identity,reason,first_seen,last_seen,attempts,local_hash,incoming_hash FROM maintenance_sync_failures')
        journal[name]=item;db.close()
    save('sanitized/entry-journal-summary.json',dict(observed_utc=NOW,**journal))
    # Saved output is evidence of computation, not evidence of a Bale delivery.
    runs=json.loads((OUT/'evidence/sanitized/hrms-saved-runs.json').read_text('utf8'))
    summaries=[]
    for run in runs[:3]:
        p=Path(run['path']);d=json.loads(p.read_text('utf8'))
        item=dict(path=str(p),state=d.get('state'),period=d.get('period'),finished_at_utc=d.get('finished_at_utc'),employees=d.get('employees'),forms_generated=d.get('forms_generated'),hr_approval_state=d.get('hr_approval_state'),publication_policy=d.get('publication_policy'),network_used=d.get('network_used'),record_count=d.get('record_count'))
        days=json.loads((p.parent/'daily_results.json').read_text('utf8'))
        if isinstance(days,dict):item['daily_keys']=list(days);days=days.get('days',days.get('records',[]))
        from collections import Counter
        item['rule_versions']=dict(Counter(x.get('rule_version') for x in days))
        monthly=json.loads((p.parent/'monthly_results.json').read_text('utf8'))
        if isinstance(monthly,dict):item['monthly_keys']=list(monthly)
        elif isinstance(monthly,list):
            item['monthly_count']=len(monthly)
            item['monthly_field_names']=sorted({key for x in monthly for key in x})
            item['publication_states']=dict(Counter(x.get('publication_decision',{}).get('publication_state','unknown') for x in monthly))
        summaries.append(item)
    save('sanitized/hrms-output-summary.json',summaries)
    # Historical test outcomes: select numerical fields and hash reference only.
    refs=[K/'runtime/two-step-entry-20261111/acceptance.json',K/'reports/scheduler/ready-delivery-20261110/acceptance.json',H/'reports/validation/G03_CUTOFF_20261108/acceptance.json',H/'reports/validation/G03_CUTOFF_20261108/full_suite_comparison.json',H/'reports/validation/G03_CUTOFF_20261108/c_focused_tests.json']
    tests=[]
    for p in refs:
        if p.is_file():
            d=json.loads(p.read_text('utf-8-sig'))
            tests.append(dict(path=str(p),sha256=sha(p),modified_utc=stat(p)['modified_utc'],keys=sorted(d),scalar_results={k:v for k,v in d.items() if isinstance(v,(int,float,bool)) or k in ['failures','errors','skipped','tests_run','testsRun','baseline','candidate']}))
    test_paths=list((K/'tools/scheduler').glob('test*.py'))+list((H/'tests').glob('test*g08*.py'))+list((H/'tests').glob('test*g09*.py'))+list((H/'tests').glob('test*personnel*.py'))
    save('test-references/test-inventory.json',dict(historical_results=tests,not_executed_in_this_audit=True,files=[dict(path=str(p),sha256=sha(p)) for p in test_paths]))
    print('WORKBOOKS',[(r['path'],r.get('bytes'),r.get('sheet_count')) for r in sources])
    print('PINNED',retained)
    print('HRMS',summaries)

if __name__=='__main__':main()
