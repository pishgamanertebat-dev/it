"""Explicit one-time operator delivery only; never imported by scheduled reporting."""
from pathlib import Path
from datetime import datetime,timezone
from contextlib import closing
import json,os
from tools.authorization import AuthorizationStore
from tools.authorization.maintenance import ROLE,CAPABILITY
from tools.fleet.maintenance_daily.report import source_snapshot,scan,validate_pdf
from tools.scheduler.tasks import BaleSender
ROOT=Path(__file__).resolve().parents[2]
OUT=ROOT/'runtime/maintenance_daily_phase'
TEST_RECIPIENT='455740857'

def claim_once(path, record):
    # Persist before any network POST. An uncertain result must never be retried.
    fd=os.open(path,os.O_WRONLY|os.O_CREAT|os.O_EXCL,0o600)
    with os.fdopen(fd,'w',encoding='utf-8') as f:
        json.dump(record,f,ensure_ascii=False,indent=2);f.flush();os.fsync(f.fileno())

def main():
    from integrations.hermes.deploy_maintenance_daily import bootstrap,preflight,verify,load,save,digest
    bootstrap();store=AuthorizationStore();preflight(store,assigned=True);verify(store)
    for name in ['maintenance-tests.json','regression-tests.json','production-authorization.json','real-smoke.json']:
        if not load(name)['success']:raise RuntimeError('Acceptance required before send')
    smoke=load('real-smoke.json');path=Path(smoke['pdf'])
    if path.resolve()!=(OUT/'maintenance-test.pdf').resolve():raise RuntimeError('Unexpected test PDF path')
    original=next((r for r in load('identities-before.json') if r['platform']=='bale' and r['user_id']==TEST_RECIPIENT),None)
    expected_identity=None if original is None else (original['chat_id'],original['registration_status'])
    # User explicitly authorizes this single destination even when absent from
    # the production registry. Never register/approve it or grant a capability.
    # Any change to an existing identity, including revocation, still blocks upload.
    def check_test_identity():
        with closing(store._connect()) as c:
            current=c.execute("SELECT chat_id,registration_status FROM channel_users WHERE platform='bale' AND user_id=?",(TEST_RECIPIENT,)).fetchone()
        if current!=expected_identity or (current is not None and current!=(TEST_RECIPIENT,'approved')):
            raise RuntimeError('Test identity or approval changed')
    check_test_identity()
    if ROLE in store.roles(TEST_RECIPIENT) or store.has_capability(TEST_RECIPIENT,CAPABILITY):raise RuntimeError('Test identity must not have production role/capability')
    with source_snapshot() as (data,h):
        if h!=smoke['source_hash_before']:raise RuntimeError('Workbook version changed')
        report=scan(data,smoke['test_date']);validation=validate_pdf(path,report,test_sample=True)
        if validation!=smoke['validation']:raise RuntimeError('Validated PDF changed')
        caption='نمونه آزمایشی گزارش روزانه تعمیرات ماشین‌آلات\nتاریخ گزارش: '+report['date']+'\nاین ارسال صرفاً جهت بررسی قالب PDF است.'
        if smoke['test_only_fallback']:caption+='\nنمونه آزمایشی قالب گزارش\nتاریخ داده: '+report['date']
        sender=BaleSender();sent=False;calls=0;message_id=None;claimed=False
        original_post=sender.session.post
        def capture_post(*args,**kwargs):
            nonlocal calls,message_id
            calls+=1
            if calls!=1:raise RuntimeError('Exactly one upload request authorized')
            kwargs['allow_redirects']=False
            response=original_post(*args,**kwargs)
            try:
                payload=response.json()
                if response.status_code==200 and payload.get('ok'):message_id=payload.get('result',{}).get('message_id')
            except (ValueError,TypeError):pass
            return response
        sender.session.post=capture_post
        journal=OUT/'one-time-test-send-455740857.json'
        try:
            claim_once(journal,{'recipient':TEST_RECIPIENT,'date':report['date'],'claimed_at':datetime.now(timezone.utc).isoformat(),'pdf_sha256':validation['pdf_sha256'],'source_sha256':h,'attempt_limit':1,'automatic_retry':False})
            claimed=True
            # Authorization is an explicit operator exception, not a role assignment.
            check_test_identity()
            sender.document(TEST_RECIPIENT,path,caption);sent=True
        finally:
            sender.close()
            if claimed:save('test-send-result.json',{'success':sent,'recipient':TEST_RECIPIENT,'date':report['date'],'test_only_fallback':smoke['test_only_fallback'],'attempt_count':calls,'message_id':message_id,'pdf_validation':validation,'source_hash_before':h,'source_hash_after':h,'caption':caption,'automatic_retry':False,'production_role_assigned':False,'production_capability_assigned':False,'test_registration_before':expected_identity,'registry_mutated':False})
        verify(store)
    print(json.dumps({'success':sent,'recipient':TEST_RECIPIENT,'date':report['date'],'attempt_count':calls,'message_id':message_id},ensure_ascii=False))
if __name__=='__main__':main()
