"""Freeze live evidence and produce a Persian, per-recipient recovery plan. No transport."""
from pathlib import Path
from datetime import datetime,timedelta,timezone
from zoneinfo import ZoneInfo
from collections import Counter
from contextlib import closing
import json,sqlite3,hashlib,uuid,subprocess,sys
import jdatetime
from openpyxl import Workbook
from openpyxl.styles import Font,Alignment,PatternFill

ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent;TZ=ZoneInfo('Asia/Tehran')
sys.path.insert(0,str(ROOT))
from tools.scheduler.runner import load_config
from tools.scheduler.tasks import overflow_report_date
from tools.scheduler.delivery_alert import _delivery_summary,occurrence_recipients,_operators
from tools.scheduler.daily_summary import snapshot,format_messages,ensure_schema
from tools.authorization import AuthorizationStore

META={
 'overflow_daily_test':('سرریز دفتر','overflow','PNG'),
 'overflow_daily_mechanical':('سرریز مکانیکی','overflow','PNG'),
 'driver_daily_office_supervisor':('رانندگان دفتر','drivers','PDF×2'),
 'driver_daily_mechanical':('رانندگان مکانیکی','drivers','PDF'),
 'driver_daily_metalwork':('آهنگری صادقیان','drivers','PDF'),
 'maintenance_daily_report':('تعمیرات روزانه','maintenance_local','PDF'),
 'oil_balance_daily_report':('موازنه روغن','oil','PDF'),
 'machinery_performance_daily_report':('عملکرد ماشین‌آلات','performance','PDF')}

def save(name,value):
    (OUT/name).write_text(json.dumps(value,ensure_ascii=False,indent=2,default=str),'utf8')
def jd(d):return jdatetime.date.fromgregorian(date=d).strftime('%Y/%m/%d')
def local(value):
    if not value:return 'ثبت نشده'
    x=datetime.fromisoformat(value).astimezone(TZ);return jd(x.date())+' '+x.strftime('%H:%M:%S')
def cell(value):return str(value or '—').replace('|','/').replace('\n','؛ ')
def backup_db(source,target):
    with closing(sqlite3.connect(source.as_uri()+'?mode=ro',uri=True)) as a,closing(sqlite3.connect(target)) as b:
        a.backup(b);assert b.execute('pragma integrity_check').fetchone()[0]=='ok'

def source_status(sources,sid,target):
    key=META[sid][1];meta=sources[key]
    item=next((x for x in meta.get('reports',[]) if x['date']==target),{})
    report=item.get('report',{})
    if meta.get('error'):return 'نامطمئن: '+meta['error'],False,meta
    if key=='drivers':
        sections=('mechanical','metalwork') if sid=='driver_daily_office_supervisor' else ('metalwork',) if sid=='driver_daily_metalwork' else ('mechanical',)
        ready=all(report.get('sections',{}).get(s,{}).get('status') in {'ready','no_defects'} for s in sections)
        return ('شیت دقیق موجود' if ready else 'شیت دقیق این تاریخ موجود نیست'),ready,meta
    if key=='overflow':
        ready=bool(report) and not item.get('error');return ('موجود' if ready else 'داده همان تاریخ موجود نیست'),ready,meta
    if key=='performance':
        missing=report.get('missing_codes',[]);ready=bool(report.get('rows')) and not missing
        return ('کامل' if ready else 'ناقص؛ '+str(len(report.get('rows',[])))+'/'+str(len(report.get('machines',[])))+' دستگاه'+('؛ کد فاقد داده: '+','.join(missing) if len(missing)<=5 else '؛ داده کامل همان تاریخ وجود ندارد')),ready,meta
    ready=bool(report.get('rows_matched'));return str(report.get('rows_matched',0))+' رکورد همان تاریخ',ready,meta

def main():
    now=datetime.now(timezone.utc);stamp=now.strftime('%Y%m%dT%H%M%SZ')
    dbpath=OUT/('ledger-after-'+stamp+'.sqlite3');authpath=OUT/('authorization-after-'+stamp+'.sqlite3')
    backup_db(ROOT/'runtime/scheduler/runs.sqlite3',dbpath)
    backup_db(ROOT/'reports/telegram_usage/telegram_users.db',authpath)
    db=sqlite3.connect(dbpath);db.row_factory=sqlite3.Row;store=AuthorizationStore(authpath)
    jobs=load_config()[2];byid={j['id']:j for j in jobs};sources=json.loads((OUT/'current-sources.json').read_text('utf8'))
    before=json.loads((OUT/'installed.json').read_text('utf8'));previous=sqlite3.connect(Path(before['backup'])/'ledger.sqlite3')
    receipt_checks={}
    for table in ('receipts','office_artifact_receipts'):
        old=previous.execute('SELECT * FROM '+table).fetchall();current=[tuple(r) for r in db.execute('SELECT * FROM '+table)]
        receipt_checks[table]=dict(previous_rows=len(old),unchanged=all(r in current for r in old))
    assert all(r['unchanged'] for r in receipt_checks.values())
    oldauth=sqlite3.connect(Path(before['backup'])/'authorization.sqlite3')
    grants={}
    for table in ('auth_roles','auth_capabilities','auth_role_capabilities','auth_user_roles'):
        old=oldauth.execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
        cur=sqlite3.connect(authpath).execute('SELECT * FROM '+table+' ORDER BY rowid').fetchall()
        grants[table]=old==cur
    assert all(grants.values())
    prepared=json.loads((OUT/'prepared-recovery-candidates.json').read_text('utf8'))
    for p in prepared:
        assert hashlib.sha256(Path(p['pdf']).read_bytes()).hexdigest()==p['pdf_sha256']
        assert sources['maintenance_local']['sha256']==sources['maintenance_mine']['sha256']==p['source_sha256']
    candidate={p['date']:p for p in prepared}
    occurrences=[];rows=[];artifacts=[];proposals=[]
    known=[dict(r) for r in db.execute('SELECT * FROM runs')]
    for day in range(15,20):
        target=f'1405/08/{day:02d}';data_day=jdatetime.date(1405,8,day).togregorian();due_day=data_day+timedelta(days=1)
        for sid in META:
            job=byid[sid]
            matching=[r for r in known if r['schedule_id']==sid and overflow_report_date(datetime.fromisoformat(r['due']))==target]
            assert len(matching)<=1,'Multiple identities for one data date require explicit review'
            scheduled=job['trigger'].get_next_fire_time(None,datetime.combine(due_day,datetime.min.time(),TZ))
            run=matching[0] if matching else dict(schedule_id=sid,due=scheduled.astimezone(timezone.utc).isoformat(),status='not_recorded',error=None,finished=None,started=None,recipient=job['recipient'])
            future=datetime.fromisoformat(run['due'])>now
            expected=occurrence_recipients(db,job,run,store,now)
            if future:expected=store.resolve_active_recipients(job['recipient_capability']).recipients
            delivery=_delivery_summary(db,job,run,expected or (),store)
            source,available,meta=source_status(sources,sid,target)
            hold=db.execute("SELECT * FROM report_recovery_hold WHERE schedule_id=? AND due=? AND status='held'",(sid,run['due'])).fetchone()
            corrections=[dict(c) for c in db.execute('SELECT * FROM report_correction_review WHERE schedule_id=? AND due=?',(sid,run['due']))]
            parent_states=[]
            for user in expected or ('unknown',):
                delivered=next((x for x in delivery if x['recipient']==user),dict(status='missing',message_id=None))
                rr=db.execute("SELECT * FROM receipts WHERE schedule_id=? AND due=? AND recipient_id=? AND delivery_kind='report'",(sid,run['due'],user)).fetchone()
                parts=[dict(x) for x in db.execute('SELECT * FROM office_artifact_receipts WHERE schedule_id=? AND due=? AND recipient_id=?',(sid,run['due'],user))]
                correction=[x for x in corrections if x['recipient_id']==user]
                state=('future' if future else 'sent' if delivered['status']=='sent' else 'uncertain' if delivered['status'] in {'uncertain','sending'} else 'closed' if run['status']=='no_data_final' else 'review' if hold else 'error')
                parent_states.append(state)
                reason=run.get('error') or ('رسید گزارش تأیید شد' if state=='sent' else 'رسید گزارش کامل نیست')
                if future:reason='موعد اصلی هنوز نرسیده است'
                if hold and state=='review':reason='توقف مستقل برای تصمیم مالک؛ آخرین علت اجرا: '+str(run.get('error') or 'ثبت نشده')
                safe=bool(sid=='maintenance_daily_report' and target in candidate and state not in {'sent','uncertain','future'} and available)
                action=('ارسال مجدد ممنوع؛ رسید موجود' if state=='sent' else 'بررسی رسید؛ بازفرستادن کور ممنوع' if state=='uncertain' else 'انتظار موعد اصلی' if future else 'تأیید مستقل مالک و ارسال با هویت بازیابی جدید' if safe else 'اصلاح یا تکمیل منبع؛ سپس بررسی بازیابی با تأیید مالک')
                pdf='غیر PDF' if META[sid][2]=='PNG' else 'بایت تاریخی PDF نگهداری نشده؛ رسید به‌تنهایی صحت محتوا را ثابت نمی‌کند'
                hashes={p['artifact_id']:p['sha256'] for p in parts if p['sha256']}
                if sid=='machinery_performance_daily_report':
                    from tools.fleet.machinery_performance.delivery import pinned
                    pin=pinned(target)
                    if pin:hashes['report']=hashlib.sha256(Path(pin['pdf']).read_bytes()).hexdigest();pdf='PDF پین‌شده این تاریخ دوباره اعتبارسنجی شد'
                if safe:pdf='پیش‌نمایش بازیابی کامل اعتبارسنجی شده؛ هنوز ارسال نشده';hashes['recovery_preview']=candidate[target]['pdf_sha256']
                if sid=='driver_daily_office_supervisor' and target=='1405/08/17':pdf='آهنگری اولیه ناقص؛ اصلاحیه مستقل سالم با ۲۶ شرح و رسید محفوظ'
                if sid=='driver_daily_metalwork' and target=='1405/08/17':
                    pdf='PARTIAL: محتوای فایل اولیه 7321 بدون بایت/Hash اختصاصی قابل اثبات نیست'
                    action='رسید اولیه حفظ شود؛ بررسی نسخه دریافت‌شده و تصمیم مالک درباره اصلاحیه'
                row=dict(data_date=target,job=sid,title=META[sid][0],file_type=META[sid][2],recipient_id=user,recipient=store.identity_label(user) or user,
                    original_due=run['due'],last_attempt=run.get('finished') or run.get('started'),run_status=run['status'],status=state,reason=reason,
                    current_source=source,source_available=available,source_sha256=meta.get('sha256'),source_snapshot=meta.get('snapshot'),snapshot_integrity='ZIP/hash verified' if meta.get('sha256') else 'unverified',
                    pdf_status=pdf,file_hashes=hashes,report_receipt=dict(rr) if rr else None,message_ids=delivered['message_id'],corrections=correction,
                    safe_candidate=safe,requires_owner_approval=True,action=action,recovery_hold=bool(hold))
                rows.append(row)
                for part in parts:artifacts.append(dict(data_date=target,**part))
                if safe:
                    p=candidate[target];operation=uuid.uuid5(uuid.NAMESPACE_URL,'|'.join([sid,run['due'],user,p['pdf_sha256']])).hex
                    proposals.append(dict(operation_id=operation,kind='historical_backfill',original_job=sid,original_due=run['due'],data_date=target,recipient_id=user,
                        pdf=p['pdf'],pdf_sha256=p['pdf_sha256'],source_snapshot=p['source_snapshot'],source_sha256=p['source_sha256'],validation=p['validation'],
                        caption=f'بازیابی گزارش تعمیرات — داده {target}؛ موعد اصلی {local(run["due"])}. این ارسال مستقل از نوبت تاریخی و پس از تأیید مالک انجام می‌شود.',
                        proposed_receipt_schedule_id='manual-maintenance-recovery-'+target.replace('/','-')+'-'+p['pdf_sha256'][:12],
                        approved=False,approval_ref=None,execution_status='not_sent',historical_run_and_receipts_must_remain_unchanged=True))
            state='sent' if parent_states and set(parent_states)=={'sent'} else 'future' if future else 'uncertain' if 'uncertain' in parent_states else 'closed' if run['status']=='no_data_final' else 'review' if hold else 'error'
            occurrences.append(dict(data_date=target,job=sid,original_due=run['due'],status=state))
    plan=dict(created_at=now.isoformat(),execution_authorized=False,transport_calls=0,ready_proposals=proposals,
        pending_corrections=[r for r in rows if any(c['status'] not in {'sent','resolved'} for c in r['corrections'])],
        blocked_source_rows=[r for r in rows if r['status'] not in {'sent','future'} and not r['safe_candidate']],
        execution_steps=['اخذ تأیید مالک برای operation_id و گیرندگان مشخص، بدون بازنشانی نوبت قدیمی',
            'بازبینی مجوز فعلی گیرنده و Hash همان Snapshot و PDF تأییدشده',
            'بررسی دوباره Ledger برای نبود رسید sent/sending/uncertain برای همین عملیات و فایل',
            'رزرو Idempotency مستقل پیش از Transport؛ ارسال با عنوان بازیابی یا اصلاحیه و پیوند به موعد اصلی',
            'ثبت Message ID و Hash جدید و پیوند بازیابی؛ حفظ کامل نوبت و Receipt اولیه',
            'هر نتیجه uncertain یا قطع پس از رزرو، نیازمند بررسی مستقل است و نباید کورکورانه تکرار شود'])
    save('recovery-plan.json',plan)
    ensure_schema(db);summary=snapshot(db,jobs,now,store);save('manager-summary-preview.json',summary)
    plan['unresolved_outside_primary_range']=[r for r in summary['unresolved'] if r['target']<'1405/08/15']
    save('recovery-plan.json',plan)
    (OUT/'manager-summary-preview-fa.txt').write_text('\n\n'.join(format_messages(summary)),'utf8')
    alerts=[dict(r) for r in db.execute("SELECT * FROM delivery_alert_receipt WHERE updated>=? ORDER BY updated",(before['installed_at'],))]
    save('final-evidence.json',dict(captured_at=now.isoformat(),ledger_snapshot=str(dbpath),authorization_snapshot=str(authpath),
        receipt_preservation=receipt_checks,permission_grants_unchanged=grants,occurrences=occurrences,recipients=rows,artifacts=artifacts,manager_alert_receipts=alerts))
    counts=Counter(o['status'] for o in occurrences if o['status']!='future');recipient_counts=Counter(r['status'] for r in rows)
    save('final-summary.json',dict(captured_at=now.isoformat(),occurrence_counts=dict(counts),future_occurrences=sum(o['status']=='future' for o in occurrences),
        recipient_counts=dict(recipient_counts),ready_operations=len(proposals),receipt_preservation=receipt_checks,permission_grants_unchanged=all(grants.values()),
        manager_sent_receipts=sum(a['status']=='sent' for a in alerts)))
    wb=Workbook();ws=wb.active;ws.title='همه گیرندگان';ws.sheet_view.rightToLeft=True
    fields=[('data_date','تاریخ داده'),('job','Job'),('file_type','فایل'),('recipient','گیرنده'),('recipient_id','شناسه بله'),('original_due','موعد اصلی UTC'),
        ('last_attempt','آخرین تلاش UTC'),('run_status','وضعیت Ledger'),('status','نتیجه رسید'),('reason','علت'),('current_source','داده فعلی همان تاریخ'),('source_sha256','Hash منبع فعلی'),
        ('source_snapshot','Snapshot'),('snapshot_integrity','سلامت Snapshot'),('pdf_status','وضعیت PDF'),('file_hashes','Hash فایلها'),('report_receipt','Receipt اولیه'),
        ('message_ids','Message ID'),('corrections','اصلاحیه'),('safe_candidate','بازیابی آماده؟'),('action','اقدام لازم')]
    ws.append([label for field,label in fields])
    for row in rows:ws.append([json.dumps(row.get(k),ensure_ascii=False) if isinstance(row.get(k),(dict,list)) else row.get(k) for k,label in fields])
    ws.freeze_panes='A2';ws.auto_filter.ref=ws.dimensions
    for c in ws[1]:c.font=Font(bold=True,color='FFFFFF');c.fill=PatternFill('solid',fgColor='234E70')
    for column in ws.columns:
        ws.column_dimensions[column[0].column_letter].width=24
        for c in column:c.alignment=Alignment(horizontal='right',vertical='top',wrap_text=True)
    wb.save(OUT/'recovery-audit-fa.xlsx');wb.close()
    commits=subprocess.check_output(['git','log','--format=%h %s','28142e9..HEAD'],cwd=ROOT,text=True,encoding='utf8').strip()
    changed=subprocess.check_output(['git','diff','--name-status','28142e9','HEAD'],cwd=ROOT,text=True,encoding='utf8').strip()
    lines=['# نتیجه اصلاح صف، هشدار و بازیابی KomatsoAI','',f'زمان Snapshot: {local(now.isoformat())} تهران. تاریخ داده از موعد اصلی هر نوبت استخراج شده است؛ زمان Retry جایگزین آن نشده است.','',
      '**صف مستقل و سیاست ۳۶ ساعت فعال‌اند؛ Sync تعمیرات رفع و واقعاً اجرا شده است. بخش تعیین نقص فایل اولیه صادقیان PARTIAL است.**','',
      f"در داده‌های ۱۵ تا ۱۸ آبان و تا این لحظه، {sum(counts.values())} نوبت موعدگذشته وجود دارد: {counts['sent']} کامل با رسید، {counts['closed']} بسته بدون ارسال، {counts['review']} در بررسی تاریخی، {counts['uncertain']} نامطمئن و {counts['error']} خطادار. دو نوبت داده ۱۸ آبان هنوز موعد ۱۵:۰۰ دارند. همه هشت نوبت داده ۱۹ آبان، موعدشان ۲۰ آبان است و فعلاً آینده‌اند.",'',
      '## سیاست و پاک‌سازی','',
      'FIFO اختصاصی عملکرد و فایل queue.py حذف شده‌اند. همه هشت Job فعال از Replay مشترک با تاریخ‌های مستقل استفاده می‌کنند. نبود داده برای هر هشت مسیر از جمله صادقیان، ۳۶ ساعت از موعد اصلی همان نوبت است؛ افق هفت‌روزه خطاهای فنی و Retry گیرندگان ناموفق محفوظ است. رسید sent یا uncertain قابل بازنویسی با callback تکراری نیست. گیرندگان تاریخی از رسید همان نوبت استخراج می‌شوند؛ عضویت جدید به نوبت قدیمی اضافه نمی‌شود و مجوز جاری همچنان دوباره کنترل می‌شود.','',
      'صادقیانِ داده ۱۵ و ۱۶ آبان در همین نصب و با ثبت previous_run در missing_data_closures بسته شد. مهلت محاسبه‌شده آنها به‌ترتیب ۱۷ آبان ساعت ۲۱:۰۰ و ۱۸ آبان ساعت ۲۱:۰۰ بود؛ زمان بسته‌شدن واقعی، امروز حدود ۱۲:۰۰ است. این تغییر تاریخی با حذف خاموش یا ارسال همراه نبود و تاریخ نوبت‌ها جابه‌جا نشد.','',
      'دو Job قدیمی آخرین بار 2026-10-01 اجرا شده‌اند: repairs_daily تا 08:01:35 تهران و metalwork_daily تا 08:02:41 تهران. هیچ فراخوانی مستقل نصب‌شده در Taskهای ویندوز یا Cron برای آنها یافت نشد. تعریف YAML، Handler و Validator اختصاصی repairs، CLI ساخت از آخرین شیت، و سه اسکریپت خدماتی اختصاصی حذف شدند. section_columns، export_pdf، latest_sheet مورد استفاده rehearsal، فرم‌های دستی، Journal، Migration و همه سوابق تاریخی حفظ شدند.','',
      '## هشدارها','',
      'مدیر 455740857 از Capability موجود reports.delivery_failure_receive دریافت می‌کند؛ هیچ مجوز Excel، نقش یا اختیار تازه‌ای ایجاد نشد. اعلان گیرنده suppressed یا notice=sent، موفقیت گزارش محسوب نمی‌شود. نبود داده، خطای تولید، خطای PDF، شکست بله، ارسال جزئی، uncertain و پایان مهلت Fingerprint مستقل دارند. خطای حل‌نشده ۱۵ دقیقه انتظار دارد، uncertain فوری است و بسته‌شدن مهلت پیام نهایی مستقل دارد. Recovery فقط با رسید واقعی ارسال هشدار قبلی برای همان مدیر انجام می‌شود. عملکرد هم مدیر و هم هدایتی را پوشش می‌دهد؛ یادآوری تکمیل داده هدایتی محفوظ است.','',
      f"تا Snapshot نهایی {sum(a['status']=='sent' for a in alerts)} رسید هشدار جدید ثبت شده است؛ جزئیات و Message ID در final-evidence.json هستند. این رسیدها پذیرش بله را نشان می‌دهند، نه خواندن انسان.",'',
      'خلاصه deterministic ساعت ۱۷:۳۰ تهران فعال است. هفت روز نوبت‌های کامل و همه موارد حل‌نشده قدیمی، گیرنده همان نوبت، تاریخ داده، موعد، آخرین تلاش، علت، اقدام و وضعیت اصلاحیه را نشان می‌دهد. Snapshot و رسید مستقل هر بخش، پایدار و Idempotent هستند. اکنون هنوز موعد نخستین خلاصه جدید نرسیده است؛ ارسال واقعی آن تأیید نشده و پیش‌نمایش از کپی Ledger در manager-summary-preview-fa.txt محفوظ است.','',
      'خلاصه، Hold تصمیم مالک و سوابق ناکافی تاریخی را از خطای فنی جدا می‌شمارد. دو مورد نامطمئن خارج از بازه اصلی هم محفوظ‌اند: سرریز داده ۱۴۰۵/۰۸/۱۳ و رانندگان مکانیکی داده ۱۴۰۵/۰۷/۱۴ با legacy_artifact_unverified. هیچ‌کدام موفق تلقی یا بازفرستاده نشدند؛ جزئیات در پیش‌نمایش مدیر و پیوست Recovery Plan دیده می‌شود.','',
      '## جدول کامل هر نوبت و گیرنده','',
      'وجود داده در این جدول مربوط به Snapshot فعلی است؛ علت تاریخی فقط از Ledger و لاگ گرفته شده است. Hash و PDF قدیمی که نگه‌داری نشده‌اند با وضعیت نامعلوم مشخص‌اند؛ فایل فعلی جایگزین مدرک فایل تاریخی نشده است. جزئیات فایل، موعد اصلی، آخرین تلاش، Hash، Receipt، Message ID و اصلاحیه در recovery-audit-fa.xlsx و final-evidence.json آمده‌اند.','',
      '| تاریخ داده | Job | گیرنده | وضعیت فعلی | علت | داده موجود؟ | ارسال قابل انجام؟ | اقدام موردنیاز |','| --- | --- | --- | --- | --- | --- | --- | --- |']
    labels=dict(sent='ارسال با رسید',closed='بسته بدون ارسال',review='Hold تاریخی',future='موعد نرسیده',uncertain='نامطمئن',error='نیازمند بررسی')
    for r in rows:
        status=labels[r['status']]+(' / '+r['message_ids'] if r['message_ids'] else '')
        lines.append('| '+' | '.join(map(cell,[r['data_date'],r['job'],r['recipient']+' ('+r['recipient_id']+')',status,r['reason'],r['current_source'],'آماده؛ فقط با تأیید مالک' if r['safe_candidate'] else 'ارسال خودکار مجاز نیست' if r['status'] not in {'sent','future'} else 'نیازی به ارسال مجدد نیست' if r['status']=='sent' else 'در موعد خودش',r['action']]))+' |')
    lines+=['','## برنامه بازیابی آماده تأیید','',
      'دو PDF مستقل برای تعمیرات داده ۱۷ آبان (۱۳ رکورد) و ۱۸ آبان (۸ رکورد) ساخته و کامل اعتبارسنجی شده‌اند. هر کدام برای امامی 397185913 و پاکدل 514458396 آماده‌اند؛ در مجموع چهار عملیات پیشنهادی در recovery-plan.json با UUID و Hash ثابت ثبت شده است. همه approved=false و execution_status=not_sent هستند. بازگشایی نوبت قبلی یا تغییر Receipt اولیه بخشی از این برنامه نیست.','',
      '| داده | گیرندگان | PDF آماده بازبینی | SHA-256 |','| --- | --- | --- | --- |',
      *[f"| {p['date']} | امامی و پاکدل | [{Path(p['pdf']).name}]({Path(p['pdf']).as_posix()}) | {p['pdf_sha256']} |" for p in prepared], '',
      'ترتیب اجرا پس از تأیید مالک: تطبیق operation_id و گیرندگان؛ بازبینی مجوز و Hashهای همان فایل و Snapshot؛ بررسی دوباره Receipt برای نبود sent/sending/uncertain؛ رزرو هویت ارسال مستقل؛ ارسال با عنوان بازیابی و پیوند به original_due؛ ثبت رسید جدید و حفظ رسید اولیه. نتیجه نامطمئن یا قطع پس از رزرو، نیازمند بررسی مستقل است.','',
      'رانندگان و آهنگری ۱۵ و ۱۶ آبان شیت دقیق ندارند. سرریز ۱۶ آبان موجود نیست. تعمیرات ۱۶ آبان در معدن و محلی صفر رکورد دارد. روغن ۱۶ و ۱۷ آبان صفر ردیف دارد. عملکرد ۱۵ و ۱۶ آبان هر دو ۵۱/۵۲ دستگاه ثبت‌شده دارند؛ کد ۷۰۴ در شیت 8 و ردیف 27 ناقص است. این جزئیات وضعیت فعلی‌اند و از آنها علت تاریخی اختراع نشده است. هیچ‌کدام با داده آخرین شیت یا روز بعد بازسازی نشده‌اند.','',
      '## Sync، PDF و PermissionError','',
      'مقایسه اولیه تمام سلول‌ها، فرمول‌ها و Styleها نشان داد تغییر تاریخی وجود ندارد؛ ۲۱ رکورد هدف جدید در ۱۹ شیت اضافه شده‌اند. اختلاف ساختاری فقط از Selection/Scroll/انتخاب تب بود. محافظ فقط activeCell و sqref در Selection، و tabSelected و topLeftCell در SheetView را نادیده می‌گیرد. Zoom، Pane، RTL، ساختار و چاپ همچنان مقایسه می‌شوند و تغییر مبهم مسدود می‌شود. تست‌های تغییر تاریخی، چاپ، Freeze و Zoom همچنان fail-closed هستند.','',
      'Sync واقعی از baseline مالک 841da662... و Journal موجود عبور کرد؛ فایل معدن 78fe91ef... دقیقاً وارد شد و Hash نسخه محلی با معدن یکسان است. health_status=success و sync_healthy=true با پایان ۱۲:۱۰:۲۵ تهران ثبت شد؛ هیچ عملیات قدیمی بله بازپخش نشد.','',
      'اصلاح PrintArea مستقر در 28142e9 محفوظ است. فایل اولیه دفتر با Hash 93813868... رد شد؛ اصلاحیه مستقل 3f67e808... دوباره با تمام ۲۶ شرح اعتبارسنجی شد. رسید اولیه امامی 7316 و پاکدل 7318 و اصلاحیه‌های 7347 و 7348 بی‌تغییر باقی‌اند. برای صادقیان، رسید اولیه 7321 موجود است اما بایت و Hash اختصاصی فایل اولیه نگه‌داری نشده است؛ نقص آن فایل قطعی اعلام نمی‌شود. نیاز به بررسی نسخه دریافت‌شده یا تصمیم مستقل مالک درباره اصلاحیه، در Ledger اصلاحیه و Recovery Plan ثبت شده؛ هیچ اصلاحیه‌ای به او ارسال نشد.','',
      'علت قطعی PermissionError ساعت ۰۹ روز ۱۹ آبان از لاگ قدیمی قابل تعیین نیست. Logging امن مرحله، errno، winerror، نوع دسترسی و مسیر مجاز در خواندن منبع، Temp، Snapshot، Export و Validation اضافه شد؛ متن خام Exception و مسیرهای خارج از محدوده ثبت نمی‌شوند. هیچ ACL، تنظیم عمومی Excel یا مجوز ویندوز تغییر نکرد. در بررسی تازه عملکرد، بازکردن انحصاری WinError 32 داد؛ Snapshot پایدار رسمی با دو خواندن کامل و کنترل metadata/hash موفق شد. این رخداد فعلی در فایل دیگری، علت خطای تاریخی رانندگان محسوب نمی‌شود.','',
      '## استقرار و کنترل واقعی','',
      'تغییرات در checkout جدا تست و Commit شدند. Windows اجازه Disable-ScheduledTask نداد؛ این تلاش پیش از تغییر کد متوقف شد. Drain رسمی Runner با قفل مشترک Worker و ACK دارای Token اضافه و تست شد؛ اجرای جاری طبیعی پایان یافت. نصب هر Commit با نقطه کنترل مستقل انجام شد و Trigger اصلی Task و مجوز آن تغییر نکرد. نسخه فعال از نظر کد با Candidate تست‌شده یکسان است.','',
      f"Backup اصلی: {before['backup']}. Backup پیش و پس از Drain، کد و تنظیمات، SQLite Online Backup با integrity_check=ok، Excelهای مبدأ و Export Task محفوظ‌اند. Backup تکمیلی گیرندگان در recipient-followup-installed.json معرفی شده است.",'',
      '۲۴۴ تست Scheduler با منع کامل HTTP و صفر تلاش HTTP عبور کردند؛ ۳۱۳ رگرسیون AI روزانه، NET، OC/GR/AF، فرم‌های دستی، رانندگان۲ و Sync نیز بدون شکست و Skip عبور کردند. تست‌های تکمیلی: ۴۶ تحویل/رسید، ۱۰ خلاصه/حفظ رسید، ۱۸ Drain/Runner، ۴۶+۱۸ گیرنده تاریخی/دفتر، ۸۳ صف/عدم داده/Replay/عضویت/Drain و ۱۵ طبقه‌بندی خلاصه عبور کردند. شمار این مجموعه‌های هم‌پوشان با هم جمع نشده است. یک انتظار منسوخ «هشدار خطای تولید فوری» در اجرای اول پیدا و با سیاست ۱۵ دقیقه اصلاح شد؛ اجرای نهایی مجموعه Scheduler سبز است.','',
      'انتظار قدیمی آزمون زمان‌بندی PDF ادمین نیز با ساعت واقعی ۰۷:۴۵ و ساعات Preflight موجود هماهنگ شد و همان آزمون عبور کرد؛ تنظیمات واقعی تغییر نکردند. مجموعه‌های بالا آزمون کل مخزن یا اجرای واقعی همه عملیات OC/GR/AF نیستند. کد عملیاتی AI ورودی، فرم‌ها، NET و Work Orderها در این تغییر دست‌نخورده و قراردادهای منتخب آنها تست شده‌اند. فایل Excel ممیزی از نظر ساختار، تعداد ردیف، تاریخ‌ها و محتوا کنترل شد؛ بازکردن تعاملی آن در Office انجام نشده است.','',
      'Scheduler فعال است و Tick طبیعی نتیجه ۰ دارد. Gateway هنگام بررسی واقعاً متوقف بود؛ پس از اثبات نبود Worker و Supervisor فعال، با Python نصب‌شده Hermes و Supervisor رسمی شروع شد. Gateway با PID 11464 و پروفایل‌های default/admin/maintenance/net پاسخ می‌دهد؛ بله و تلگرام با writer_pid همین Worker، اتصال و پیشرفت Polling تازه دارند. Restart اجباری یا Kill انجام نشد.','',
      'تمام Receiptهای موجود قبل از نصب، شامل notice و report و office_artifact_receipts، عیناً حفظ شده‌اند؛ جداول مجوز نیز با Backup یکسان‌اند. هیچ پیام آزمایشی، گزارش تاریخی یا اصلاحیه واقعی در این مأموریت ارسال نشد؛ پیام‌های مدیریتی درخواست‌شده مستقل از گزارش‌ها ارسال شدند. هیچ Excel عملیاتی در تست یا با اصلاح دستی تغییر نکرد؛ داده تعمیرات از مسیر Sync واقعی و محافظت‌شده معدن منتقل شد. Hash منبع‌های گزارش دوباره در ۱۲:۴۰ تهران با Snapshotهای ممیزی تطبیق داده شد و تغییری نداشت.','',
      'Commitهای نصب‌شده:','','```text',commits,'```','','فایل‌های تغییرکرده و حذف‌شده:','','```text',changed,'```','',
      '## تصمیم‌های باقیمانده مالک','',
      '۱. تأیید یا رد چهار عملیات آماده تعمیرات برای داده‌های ۱۷ و ۱۸ آبان.','۲. بررسی نسخه اولیه 7321 صادقیان یا تصمیم صریح درباره ارسال اصلاحیه مستقل.','۳. اصلاح منبع موارد فاقد داده و سپس بررسی Backfill مستقل؛ رسید نامطمئن یا سابقه بدون گیرنده مشخص باید پیش از هر ارسال بررسی شود.','',
      'INDEPENDENT DAILY DELIVERY ACTIVE — UNIFIED 36H MISSING-DATA POLICY — MANAGER ALERTS COMPLETE — RECOVERY AUDITED',
      '','PARTIAL: اثبات محتوای فایل اولیه صادقیان؛ ارسال واقعی اولین خلاصه ۱۷:۳۰ هنوز سررسید نشده است.']
    (OUT/'report-fa.md').write_text('\n'.join(lines)+'\n','utf8')
    db.close();previous.close();oldauth.close()
    print(json.dumps(dict(captured=local(now.isoformat()),counts=dict(counts),future_occurrences=sum(o['status']=='future' for o in occurrences),recipient_rows=len(rows),ready_operations=len(proposals),report=str(OUT/'report-fa.md')),ensure_ascii=False))

if __name__=='__main__':main()
