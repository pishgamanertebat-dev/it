"""Prepare exact-day maintenance candidates from pinned mine bytes; never transport."""
from pathlib import Path
import sys,json,hashlib
ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent
sys.path.insert(0,str(ROOT/'runtime/scheduler-repair-20261110/stage'))
from tools.fleet.maintenance_daily.report import scan,render,validate_pdf
from tools.authorization import AuthorizationStore
from tools.authorization.maintenance import CAPABILITY

def main():
    sources=json.loads((OUT/'current-sources.json').read_text('utf8'))
    meta=sources['maintenance_mine'];raw=Path(meta['snapshot']).read_bytes()
    assert hashlib.sha256(raw).hexdigest()==meta['sha256']
    auth=AuthorizationStore(ROOT/'reports/telegram_usage/telegram_users.db')
    recipients=auth.resolve_active_recipients(CAPABILITY)
    assert recipients.status=='ready'
    folder=ROOT/'artifacts/scheduler-recovery-20261110';folder.mkdir(exist_ok=True)
    result=[]
    for date in ('1405/08/17','1405/08/18'):
        report=scan(raw,date);assert report['rows_matched']>0
        pdf=folder/('maintenance-'+date.replace('/','-')+'-recovery-preview.pdf')
        render(report,pdf);validation=validate_pdf(pdf,report)
        result.append(dict(date=date,job='maintenance_daily_report',recipients=list(recipients.recipients),
            source_snapshot=meta['snapshot'],source_sha256=meta['sha256'],pdf=str(pdf),
            pdf_sha256=hashlib.sha256(pdf.read_bytes()).hexdigest(),validation=validation,
            rows=report['rows_matched'],requires_owner_approval=True,transport_calls=0))
    (OUT/'prepared-recovery-candidates.json').write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps([dict(date=r['date'],rows=r['rows'],recipients=r['recipients'],pdf_sha256=r['pdf_sha256']) for r in result]))

if __name__=='__main__':main()
