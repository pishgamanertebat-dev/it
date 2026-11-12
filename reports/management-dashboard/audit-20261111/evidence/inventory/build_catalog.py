from build_documents import *
import sqlite3

DB=read('inventory/database-schemas.json')
SCHEMAS=read('inventory/hrms-code-schemas.json')
sources=[]
def add(id,name,owner,path,kind,entities,authority,read_method,confidentiality='internal',roles=None,frequency='event-driven',history='نسخه/تاریخ معتبر منبع حفظ شود',quality='PARTIAL',cost='bounded',snapshot='read-only memory snapshot',modified=None,freshness='source watermark and observed_at',example=None,api=None):
    sources.append(dict(source_id=id,name_fa=name,owner=owner,path=path,type=kind,entities=entities,
        authority=authority,safe_read=read_method,confidentiality=confidentiality,roles=roles or ['manager'],
        change_frequency=frequency,last_modified_utc=modified,snapshot=snapshot,lock_risks='قفل یا WAL ناممکن: unknown/stale؛ bypass ممنوع',
        performance=cost,quality=quality,historical_limits=history,freshness=freshness,
        anonymous_example=example or {'id':'SYNTHETIC-001','state':'unknown'},api_mapping=api or []))
for key,fa in [('K-SCHED','دفتر نوبت‌ها و رسیدها'),('K-AUTH','هویت و مجوزهای بله'),('K-FLEET','داده مشتق ناوگان و NET'),('K-ENTRY','Journal رانندگان و AI Draft'),('K-MAINT-ENTRY','Journal تعمیرات و Sync guard')]:
    d=DB[key]
    add(key,fa,'KomatsoAI',d['path'],'SQLite',list(d['tables']),
        'canonical per entity؛ منبع Excel و transport اثبات جدا دارند',
        'sqlite URI mode=ro؛query_only؛Online Backup محدود به حافظه؛schema/SELECT روی snapshot',
        confidentiality='restricted' if key in ['K-AUTH','K-ENTRY','K-MAINT-ENTRY'] else 'internal',
        frequency='minute / event',history='تغییر schema تاریخی؛عدم انتساب recipient فعلی به گذشته؛سال fleet باید صریح باشد',
        quality='VERIFIED schema and selected aggregates',cost='فایل کوچک/متوسط؛به ازای request تکرار نشود',
        modified=d.get('modified_utc'),api=['reports','audit','system/health'] if key=='K-SCHED' else ['read adapter only'])
add('K-SCHEDULE','تنظیم برنامه جاری','KomatsoAI','E:/KomatsoAI/settings/schedules.yaml','YAML',['schedules','reminders','preflight'],'current registry; not historical schedule archive','safe_load با schema محدود و file hash؛no runner import',frequency='operator change',quality='VERIFIED',snapshot='bytes + sha256',api=['overview','reports'])
add('K-SYNC','وضعیت تفصیلی Sync معدن','Hermes','C:/Users/win-10/AppData/Local/hermes/state/mine-file-sync.json','JSON',['health','last_run','guards','recovery'],'health_status + exact-file guard؛cron exit code کافی نیست','read atomic JSON؛schema_version=3/reliability=1؛field allowlist',frequency='2 minutes',quality='VERIFIED partial/guard conflict',snapshot='sanitized selected fields',api=['sync/status'])
add('K-CRON','Registry و وضعیت cron','Hermes','C:/Users/win-10/AppData/Local/hermes/cron/jobs.json','JSON',['mine-file-sync job'],'schedule/dispatch metadata only','select fields؛prompt/credential/URL حذف',frequency='2 minutes',quality='VERIFIED',api=['system/health'])
add('K-GATEWAY','Heartbeat Gateway و کانال‌ها','Hermes','C:/Users/win-10/AppData/Local/hermes/gateway_state.json','JSON',['gateway','platforms','served_profiles'],'writer pid/start identity + heartbeat؛state file ممکن stale باشد','allowlist JSON؛هویت process و age',frequency='heartbeat',quality='VERIFIED at observation',api=['system/health'])
workbooks=read('inventory/workbook-metadata.json')['sources']
for idx,d in enumerate(workbooks):
    add(['K-X-OVERFLOW','K-X-DRIVERS','K-X-MAINT','K-X-OIL','K-X-PERF'][idx],Path(d['path']).name,'KomatsoAI',d['path'],'Excel',
        [['dated sheet','overflow rows'],['dated sheet','machine code','mechanical','metalwork'],['date','mechanism','mechanic','action','parts'],['date','oil balance quantities'],['date','machine','fuel','hours','monthly averages']][idx],
        'raw operational source؛file hash alone freshness یا delivery را اثبات نمی‌کند',
        'برای API فقط snapshot/cache مصوب و parser همان domain؛no COM/live read per request',
        confidentiality='restricted' if idx==2 else 'internal',frequency='mine Sync + authorized Writer',
        quality='VERIFIED bytes observed; business completeness not audited for every cell',
        snapshot='audit: two equal reads in memory؛future: coordinated immutable snapshot',
        modified=d.get('modified_utc'),freshness='accepted journal head + exact-file mine check؛برای سایر منابع watermark وابسته به parser',
        cost=f"{d.get('bytes')} bytes; {d.get('sheet_count')} sheets",api=['report source detail'])
add('K-ART-MAINT','انتشار پین‌شده تعمیرات','KomatsoAI','E:/KomatsoAI/runtime/scheduler/maintenance-ready/<target>/','JSON+PDF+Excel',['ready pointer','content addressed manifest','source/report/layout/PDF'],
    'published version proof only after byte/content checks','read immutable files by allowlisted manifest؛no production prepare() invocation',
    quality='VERIFIED code; no current pointer observed',snapshot='content-addressed files',history='later source version correction review؛old version immutable',api=['reports/{id}/artifacts'])
add('K-ART-PERF','انتشار پین‌شده عملکرد','KomatsoAI','E:/KomatsoAI/runtime/scheduler/machinery-performance/<target>/','JSON+PDF',['report.json','report.pdf'],
    'manifest publication boundary','target/mode/comparison/PDF SHA validation from existing publication contract',
    quality='VERIFIED code and folder presence; all retained PDFs not content-audited',api=['artifacts/{id}/content'])
add('K-LOG','لاگ گزارش و Sync','KomatsoAI/Hermes','runtime/scheduler/scheduler.log + Hermes logs/mine-file-sync*','Log',['diagnostic events','source/pdf hashes'],
    'secondary evidence؛rotation/format gaps؛not receipt authority','bounded tail/structured event extractor؛redaction before persistence',
    frequency='event',quality='PARTIAL',snapshot='redacted records only',history='no parsing of dates/IDs from arbitrary prose without versioned parser',api=['audit'])
for key,fa in [('H-PERSONNEL','بانک نسخه‌دار پرسنل'),('H-BOT','صف/رسید مستقل بله HRMS')]:
    d=DB[key]
    add(key,fa,'HRMS',d['path'],'SQLite',[t['name'] for t in SCHEMAS[key]['tables']],
        'canonical personnel revision' if key=='H-PERSONNEL' else 'canonical inbox/job/outbox transport metadata',
        'mode=ro + Online Backup؛این ممیزی SQLITE_CANTOPEN؛constructor سرویس ممنوع',
        confidentiality='hr-sensitive',roles=['manager','hr'],quality='UNKNOWN live schema/count; CODE_VERIFIED DDL',
        modified=d.get('modified_utc'),api=['hrms/personnel'] if key=='H-PERSONNEL' else ['hrms/processing'])
add('H-RULE','قواعد تاریخ‌دار و policy bundle','HRMS','E:/HRMS/rules/policy.json + rule_versions.csv + rules-*.json','JSON+CSV',['group','release','effective range','scope','policy'],
    'effective rule selected for run؛extra_rules revision نیز دخیل است','read referenced files + sha؛engine rule_for semantics reused؛no calculation',
    confidentiality='internal',roles=['manager','hr'],quality='VERIFIED files; effective all-person scope PARTIAL',
    snapshot='run pinned input bundle',history='config historical؛pending_decisions ممکن stale باشد؛specificity مقدم',api=['hrms/rules'])
add('H-REG-SNAPSHOT','نسخه ثابت ورودی پرسنل','HRMS','E:/HRMS/data/personnel/registry_snapshots/<hash>/','Excel+CSV+JSON',['registry_receipt','people','assignments','extra_rules'],
    'revision-pinned input؛latest folder canonical head نیست','receipt file checks + immutable read؛export() در GET فراخوانی نشود',
    confidentiality='hr-sensitive',roles=['manager','hr'],quality='PARTIAL',snapshot='immutable generation',api=['hrms/personnel'])
add('H-HR-INPUT','انتساب،شیفت و تصمیم‌های HR','HRMS','E:/HRMS/data/hr/{assignments,shift_plan,overrides,leave_approvals}.csv','CSV',['effective assignments','shift','override','leave approval'],
    'per-run pinned inputs؛assignments legacy از canonical revision جداست','read pinned copy/hash؛provenance و date scope حفظ',
    confidentiality='hr-sensitive',roles=['manager','hr'],quality='VERIFIED code mapping',api=['hrms/processing/{id}'])
add('H-WISMAN','منابع تردد ویسمن','HRMS','E:/HRMS/data/input + per-job data/wisman','XLS+JSON',['raw punches','source calculations','ingestion receipts','conflicts'],
    'raw evidence؛HRMS authority dependent on rule version','BIFF validation؛reuse ingest receipts؛parse latest source in API ممنوع',
    confidentiality='hr-sensitive',roles=['manager','hr'],quality='PARTIAL saved run evidence',snapshot='source SHA + ingestion_snapshot',api=['hrms/processing/{id}'])
add('H-OUTPUT','خروجی محاسبه و فرم همان اجرا','HRMS','E:/HRMS/runtime/bale/<job>/reports/<period>/<run>/ + output','JSON+Excel',['manifest','daily_results','monthly_results','publication','conflicts','forms'],
    'calculation output for exact run؛not HR approval/transport proof','job lineage + manifest content_id/hash؛read immutable results؛field projection',
    confidentiality='hr-sensitive',roles=['manager','hr'],quality='VERIFIED selected saved artifacts; live job lineage UNKNOWN',
    snapshot='immutable run generation',history='هرگز جمع across runs/people تکراری؛آخرین نتیجه بدون version decision انتخاب نشود',api=['hrms/overview','hrms/processing'])
add('H-HEALTH','سلامت ربات مستقل HRMS','HRMS','E:/HRMS/integrations/bale/.local/{health,process}.json','JSON',['worker heartbeat','pid','state'],
    'fresh process/worker evidence only','allowlist read؛no run.py check network call',frequency='poll',quality='VERIFIED',api=['system/health'])
add('H-ARCHIVE','آرشیو خروجی قابل مشاهده','HRMS','E:/HRMS/bale_output','Files+JSON',['copied source','response workbook','delivery status'],
    'derived copy؛job/outbox remains authority','read existing authorized file by source receipt hash؛no regeneration',
    confidentiality='hr-sensitive',roles=['manager','hr'],quality='PARTIAL inventory/code',snapshot='source hashes required',api=['authorized artifacts only'])

tables={}
for key,d in DB.items():
    if 'tables' in d:
        tables[key]=dict(provenance='live_readonly_memory_snapshot',user_version=d.get('user_version'),schema_version=d.get('schema_version'),tables=d['tables'])
    elif key in SCHEMAS:
        db=sqlite3.connect(':memory:');db.row_factory=sqlite3.Row
        out={}
        for t in SCHEMAS[key]['tables']:
            if t['sql'] and t['sql'].startswith('CREATE TABLE') and not t['name'].startswith('sqlite_'):
                db.execute(t['sql'])
                out[t['name']]=dict(columns=[dict(x) for x in db.execute('PRAGMA table_info("'+t['name']+'")')],count=None,ddl=t['sql'])
        tables[key]=dict(provenance='installed_code_ddl_not_live_schema',tables=out)
        db.close()
dictionary=dict({**META,'source_roots':META['sources'],'sources':sources},database_dictionary=tables,
    normalized_contract_version='dashboard-read-model-1',null_policy='unknown/invalid/missing distinct; never convert to zero',
    examples_are_synthetic=True,source_credentials_included=False)
(ROOT/'04-DATA-DICTIONARY.json').write_text(json.dumps(dictionary,ensure_ascii=False,indent=2),encoding='utf8')
table='\n'.join('| '+s['source_id']+' | '+s['name_fa']+' | '+s['type']+' | '+s['authority']+' | '+s['quality']+' |' for s in sources)
write('04-DATA-SOURCE-CATALOG.md','کاتالوگ منابع داده و قرارداد خواندن',r'''
## فهرست عملیاتی

هر Source ID ثابت است؛ filepath در API به کاربر فرستاده نمی‌شود. مشخصات کامل هر منبع شامل entityها،authority،تناوب،آخرین تغییر،روش خواندن/snapshot،قفل،هزینه،محرمانگی،نقش‌ها،تازگی،مثال مصنوعی و API mapping در [Data Dictionary](04-DATA-DICTIONARY.json) قرار دارد. Schema ستون/نوع/PK همه جدول‌های مشاهده‌شده همان فایل است؛ schemaهای HRMS به‌صراحت code-only هستند.

| Source ID | عنوان | نوع | authority | سطح مشاهده |
|---|---|---|---|---|
'''+table+r'''

## Primary key و رابطه‌ها

K-SCHED: runs↔receipts از schedule_id/due؛receipt عمومی recipient/delivery_kind دارد؛office_artifact_receipts روی همان occurrence/recipient با artifact_id مستقل. Recovery روی original_schedule/original_due و receipt_schedule پیوند می‌خورد. correction_review همیشه artifact_id/recipient می‌خواهد؛ زمان ارسال correction مستقل می‌تواند due دیگری داشته باشد و نباید جای موعد اصلی بنشیند.

K-ENTRY/K-MAINT-ENTRY: edits.operation و fingerprint؛draft.id→draft_events.draft؛hash/version متنی با before/after hash workbook برابر نیست. Sync failures،receipt ارسال یا edit موفق نیستند. fleet_ops روابط id و ingest_source_id را نگه می‌دارد؛raw_excel_rows خط مرجع است،issue classification مشتق و ناقص است.

H-PERSONNEL: revisions.id نسل canonical؛parent_id تاریخچه؛proposals.token و request_key idempotency. people و assignments BLOB با یک revision جفت می‌مانند؛کدملی/کد بله join key داشبورد عمومی نیست. H-BOT: inbox.update_id→jobs.update_id(unique)؛outbox.job_id+event_key→transport receipt. result محتوا JSON است و schema تاریخی باید تشخیص داده شود.

H-OUTPUT: run_id/content_id + period + ingestion snapshot + rule bundle + personnel revision. یک فرد می‌تواند در چند run/منبع/شیفت دیده شود؛join صرف personnel_id/month جمع تکراری می‌سازد. یک «run انتخاب‌شده» یا policy صریح supersession لازم است.

## امن‌بودن خواندن

برای پنج DB KomatsoAI connection URI mode=ro و query_only استفاده و Online Backup به حافظه گرفته شد؛هیچ DB خام در این گزارش ذخیره نشد. snapshot هر DB سازگار است ولی بین چند DB transaction مشترک نداریم؛observed_at مستقل ضروری است. timeout/budget باعث stale/unknown می‌شود،نه تغییر journal mode یا حذف WAL.

برای HRMS نتیجه اتصال unavailable است؛از code-only schema نمی‌توان count جاری ساخت. اگر برای حساب read-only به ساخت sidecar نیاز باشد،owner باید interface/snapshot انتشارشده رسمی را در پروژه منبع فراهم کند؛dashboard اجازه نوشتن directory اصلی نمی‌گیرد. immutable فقط برای فایل ثابتِ منتشرشده مجاز است. [رفتار WAL خواندنی](https://sqlite.org/wal.html) و [Online Backup](https://www.sqlite.org/backup.html) این محدودیت را توضیح می‌دهند.

Excel audit به دو خواندن بایتی برابر در حافظه محدود بود؛نگهداری raw workbook یا مشاهده همه سلول‌ها انجام نشد. تولید API آینده باید snapshot با provenance و سیاست lock موجود داشته باشد؛از equality دو read،تازگی معدن یا کامل‌بودن تاریخ را نتیجه نگیرد. قالب خام،فرمول،blank،zero،invalid،مقدار محاسبه‌شده و cached Excel result جدا هستند.

## watermark و خطای کیفیت

mtime زمان داده نیست. API چهار زمان جدا می‌دهد: source_modified_at،source_observed_at،last_verified_at،served_at؛همچنین target_data_date و original_due. validity=verified|partial|unknown و freshness=fresh|stale|unknown دو محورند. دلیل unavailable هم‌چون lock/schema mismatch گزارش می‌شود؛برای مصدر محرمانه فقط code عمومی.

نمونه مصنوعی همه‌جا SYNTHETIC-001 است؛این مجموعه هیچ raw پیام خصوصی،raw HR row،توکن،کلید VPN یا DB پرسنل ندارد. Operational recipient IDهای برنامه به درخواست مالک در قرارداد schedule ثبت‌اند؛نسخه عمومی بیرون شرکت باید آن فایل‌ها را نیز redact کند.
''')

write('04-SOURCE-OF-TRUTH-MATRIX.md','ماتریس رسمی حقیقت، اولویت تعارض و وضعیت‌ها',r'''
## چند محور مستقل

وضعیت باید از منبع اصلی **همان رخداد تاریخی** بیاید. فیلدهای مستقل:

source_state=available|unavailable|stale|unknown؛data_state=ready|missing|unknown؛artifact_state=ready|invalid|missing|unknown؛delivery_state=sent_with_receipt|partial|pending|uncertain|unknown؛disposition=active|held|closed_owner|closed_expiry؛recovery_state و correction_state مستقل؛freshness روی observation است.

sent اثبات پذیرش کانال است؛human_read همیشه unknown مگر evidence رسمی جدا موجود شود. Source امروز قطع باشد،receipt قطعی گذشته باطل نمی‌شود. file جدید جای فایل فرستاده‌شده نمی‌نشیند.

## authority هر نمایش

| نما | منبع اول | پشتیبان/تعارض | رفتار قطعی |
|---|---|---|---|
| موعد/attempt/status موتور | runs.original due/attempt/started/finished | registry جاری فقط برای آینده؛notice و log مستقل | run success به delivery ارتقا نمی‌دهد |
| ارسال فایل دفتر | office_artifact_receipts exact key + sent + nonempty message_id | generic receipt قدیمی از scope پایین‌تر | artifact نامطمئن مانع all-artifacts complete است |
| ارسال تک‌فایل عمومی | receipts.kind=report,status=sent,ID معتبر | hash ممکن null؛log فقط corroboration | proof_scope=recipient_aggregate یاsingle_artifact مطابق قرارداد job؛bytes_verified جدا |
| چند صفحه مکانیکی سرریز | ID list عمومی با قرارداد tasks | تعداد/هویت همه pageها تاریخی در schema کامل نیست | acceptance از IDهای ثبت‌شده معلوم؛all-artifact completeness ممکن unknown |
| recovery | operation+approval_ref+receipt+artifact+ID/hash برابر | hold recovered فقط disposition است | recovery success بدون join کامل unknown |
| correction | correction_review + receipt مستقل +hash و linkage ثبت‌شده | status sent در review به‌تنهایی کافی نیست | نسخه اصل محفوظ؛correction_state جدا |
| منبع تعمیرات | accepted baseline/journal head + exact-file freshness guard | mtime یاcron success کافی نیست | source_stale با data_missing یکی نیست |
| Sync کلی | state.health_status/sync_healthy+last completed scan+guards | cron exit code وlast_success صرفاًcontext |partial/guard blocked→degraded |
| AI ذخیره | edits succeeded + operation/before-after/result + workbook evidence | Draft/text_ok/فرستادن پیام کافی نیست | saved ادعا فقط با journal commit متناظر |
| HRMS محاسبه | pinned daily/monthly output +manifest content_id/rule version | latest config کد تاریخی را عوض نمی‌کند | known/final/null عین همان نتیجه |
| HRMS پرسنل | revisions head +as_of projection | people.xlsx legacy یاآخرینsnapshot folder کافی نیست | canonical count unavailable→null |
| HRMS import/export | inbox/job/result +output hash وoutbox receipt |manifest complete یافایل آرشیو≠sent | محاسبه،تولیدفرم،کانال وHR approval مجزا |
| HRMS تأیید | publication.hr_approval برایهمانresult_version |ready_for_approval وform clean≠approved |not_recorded معادل pending/approved حدسی نیست |
| health | timestamp+PID identity+state |وجود PID قدیمی/mtime تنها ناکافی |unknown اگر evidence ناکافی؛stale اگرheartbeat گذشته |

## منطق نمایش Statusهای خواسته‌شده

هر predicate با projector_version=dashboard-read-model-1 و evidence refs ثبت شود؛transition اجرایی تازه‌ای در dashboard رخ نمی‌دهد.

| status API / فارسی | predicate قابل اثبات | authority | اقدام/اثر شمارش |
|---|---|---|---|
| sent_with_receipt / ارسال با رسید | exact delivery sent +ID غیرخالی؛در artifact مسیرشناخته‌شده همه obligations ثبت‌شده لازم | artifact/generic receipt طبقproof_scope |successful delivery count؛human-read unknown |
| partial / ارسال جزئی | دست‌کم یک obligation قطعی sent ویکی unsent؛expected set تاریخی معلوم |artifact receipts/recipient reservations |occurrence incomplete؛count هرsuccess مستقل |
| ready / آماده |manifest+hash+validation برایtarget وهنوزpending recipient |retained artifact +receipts |no sent count |
| waiting_for_data / منتظر داده |missing code معتبر ازsource پذیرفته‌شده؛پیشdeadline |run.data_state +freshness proof |due+36h؛عدمfreshness→source_stale |
| source_unavailable / منبع قطع |source_unavailable explicit/timeout طبقه‌بندی‌شده تازه |source reader/Sync+run |فنی؛۷روزreplay domain؛dashboard اجرا نمی‌کند |
| technical_error / خطای فنی |error code معتبر وهنوزرفع‌نشده؛unknown exception class جزئیات عمومی دارد |run/guard/preflight |دیگرfailure قدیمی باsuccess بعدی فعال نشود |
| closed_without_send / بسته |closed_owner یاmissing_data_closures/no_data_final برایهمانdue |owner_history/closure |attention=false؛delivery history محفوظ |
| awaiting_review / بررسی انسانی |hold held یاcase unresolved نیازتصمیم |recovery hold/review |هیچدکمهsend درMVP |
| recovered / بازیابی |join recovery باchain کامل |operation+۲receipt tables |نوبت۱بار؛delivery نسخه recovery جدا |
| uncertain / نامطمئن |explicit uncertain؛sending stale باschema/قانون وقفه معتبر؛conflicting receipt |receipt اصلی |retry خودکار/ارسال تازه ممنوع |
| correction_required / اصلاحیه لازم |review status باز وscopeمشخص؛نه صرف وجودنسخهجدید منبع |correction_review |alert مستقل؛اصلsent محفوظ |
| correction_sent / اصلاحیه ارسال |review/lineage+receiptنسخهجدید وID/hash |receiptمستقل |revision delivery؛original occurrence تکرارنشود |
| not_due / موعد نرسیده |occurrence محاسبه‌شده ازschedule معتبر وdue>now؛scopeبعدفعال‌سازی |versioned registry proposal |نهpending overdue؛عدمخلق تاریخچه قدیمی |
| stale / قدیمی |age>TTL یاbackend refresh ناموفق باlast good version |cache +source watermark |badge رویتمامvalueهایقدیمی؛fact گذشته حفظ |
| unknown / نامعلوم |schema/expected recipient/ID/hash/lineage ناکافی یاread failed بدونcache |explicit evidence gap |null/count bounds؛هرگزempty success یاunsent قطعی نشود |

## ترتیب حل تعارض

۱. identity رخداد،version وrole scope را resolve کنید؛schema ناشناخته به unknown می‌رود.۲. evidence قوی receipt برایهمانversion را مقدم برaggregate run بخوانید.۳. recovery/correction را تنها باlineage به اصل وصل کنید.۴. disposition مالک/expiry تعیین می‌کند چه چیزی active action است؛closed_owner اثرunknown/uncertain تاریخی را پاک نمی‌کند.۵. source/data/artifact امروز فقط تعهد فعلی را توضیح می‌دهد.۶. freshness overlay آخر اعمال شود.

primary_status برایکارت رخداد: اگرowner closed است closed_without_send باbadgeهایreceipt/recovery باقی؛درactiveها uncertain→correction_required→awaiting_review→partial→all_required_sent(recovered/correction_sent/sent_with_receipt)→artifact_invalid/technical_error→ready→source_unavailable/source_stale→waiting_for_data→not_due→unknown. هیچprimary label محورهای دیگر را حذف نمی‌کند. اگرتمامreceipts قطعی‌اند ولیrun failed است،ارسال‌هاموفق‌اند وengine_consistency_warning جدا ثبت می‌شود.

تعارض sent وuncertain رویidentity/version یکسان بدونprecedence رسمی→unknown_conflict با نمایشهر دو evidence؛یکی را بر اساسزمان آخرینlog انتخاب نکنید. Aggregate current-role recipients به‌گذشته join نمی‌شود. dashboard فقط normalizer قراردادمصوب است وRetry/expiry/Payroll را ازنو اجرا نمی‌کند.
''')
print('Catalog sources',len(sources),'dictionary tables',sum(len(x['tables']) for x in tables.values()))
