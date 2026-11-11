"""Execute only the four explicitly owner-authorized recovery operations."""
from pathlib import Path
import json
import sqlite3
import sys
import tempfile
from datetime import datetime, timezone

ROOT=Path('E:/KomatsoAI')
sys.path.insert(0,str(ROOT))
from tools.authorization import AuthorizationStore
from tools.scheduler.control import worker_lock
from tools.scheduler.runner import connect
from tools.scheduler.recovery_delivery import authorize, execute, digest

OUT=Path(__file__).resolve().parent
PLAN=ROOT/'reports/scheduler/repair-20261110/recovery-plan.json'
IDS={'d6f8f4f75bd65e5eacecf08e4772d919','38b37b4e949e5eb7b906280ea54e4487',
     '77ef651b80bb56c68216267e119d96fa','d24c5f7fe66b5f14aa533aca0805ee1b'}
HASHES={'1405/08/17':'61b92d5dcef76b55ed0a2b7990b832149ca52ddca0e594ccbce6a52c639fe9de',
        '1405/08/18':'9e53446078f1a84ea4db50179ed6acc1b80fa0f4a5d5a7272d49eae660f77fbb'}


def historical(conn):
    return {t:[tuple(r) for r in conn.execute(f'SELECT * FROM {t} WHERE schedule_id=? AND due IN (?,?) ORDER BY due,1',
        ('maintenance_daily_report','2026-11-09T08:30:00+00:00','2026-11-10T08:30:00+00:00'))]
        for t in ('runs','receipts','office_artifact_receipts','report_recovery_hold')}


def main():
    proposals=json.loads(PLAN.read_text('utf8'))['ready_proposals']
    assert len(proposals)==4 and {p['operation_id'] for p in proposals}==IDS
    assert {(p['data_date'],p['recipient_id']) for p in proposals}=={
        (d,u) for d in HASHES for u in ('397185913','514458396')}
    assert all(p['pdf_sha256']==HASHES[p['data_date']] for p in proposals)
    approval=dict(approval_ref='owner-explicit-four-maintenance-deliveries-20261110',
        recorded_at=datetime.now(timezone.utc).isoformat(),plan_sha256=digest(PLAN),
        owner_instruction='اجازه اجرای ارسال واقعی همین چهار عملیات، مشروط به کنترل‌های ضروری صحت فایل، هویت گیرنده و جلوگیری از ارسال تکراری، داده شده است.',
        excluded_dates=['1405/08/15','1405/08/16'],
        operations=[dict(operation_id=p['operation_id'],target_date=p['data_date'],recipient_id=p['recipient_id'],sha256=p['pdf_sha256']) for p in proposals])
    with worker_lock(ROOT/'runtime/scheduler') as acquired:
        if not acquired:raise RuntimeError('Scheduler worker active; no send attempted')
        conn=connect(ROOT/'runtime/scheduler/runs.sqlite3')
        try:
            before=historical(conn)
            with sqlite3.connect(OUT/'ledger-immediately-before-send.sqlite3') as backup:conn.backup(backup)
            if not (OUT/'pre-send-validation.json').exists():
                with tempfile.TemporaryDirectory(dir=OUT) as directory:
                    validation=authorize(conn,proposals,approval,AuthorizationStore(),directory)
                (OUT/'execution-authorization.json').write_text(json.dumps(approval,ensure_ascii=False,indent=2),'utf8')
                (OUT/'pre-send-validation.json').write_text(json.dumps(validation,ensure_ascii=False,indent=2),'utf8')
            else:
                validation=json.loads((OUT/'pre-send-validation.json').read_text('utf8'))
                for op in proposals:
                    assert validation[op['operation_id']]['pdf_sha256']==digest(op['pdf'])==op['pdf_sha256']
                    assert digest(op['source_snapshot'])==op['source_sha256']
            print('All four operations authorized; PDF/source/content and registry verified. Starting official sender.',flush=True)
            result=execute(conn,proposals,AuthorizationStore())
            assert historical(conn)==before,'Historical records changed'
            evidence=dict(operations=result,historical_rows_preserved=True,integrity=conn.execute('PRAGMA integrity_check').fetchone()[0],
                          completed=datetime.now(timezone.utc).isoformat())
            (OUT/'delivery-result.json').write_text(json.dumps(evidence,ensure_ascii=False,indent=2),'utf8')
            with sqlite3.connect(OUT/'ledger-after-send.sqlite3') as backup:conn.backup(backup)
            print(json.dumps(evidence,ensure_ascii=False,indent=2),flush=True)
        finally:conn.close()


if __name__=='__main__':main()
