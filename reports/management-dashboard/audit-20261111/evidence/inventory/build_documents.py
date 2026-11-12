"""Documentation renderer. Writes only this audit package; never imports domain apps."""
from pathlib import Path
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
import json
import textwrap

ROOT=Path(__file__).resolve().parents[2]
EV=ROOT/'evidence'
def read(name):return json.loads((EV/name).read_text('utf-8-sig'))
NOW=datetime.now(timezone.utc)
KHEAD=read('sanitized/installed-code.json')['komatso_head']
META=dict(schema_version='management-dashboard-audit-1',compiled_at_utc=NOW.isoformat(),
          compiled_at_tehran=NOW.astimezone(ZoneInfo('Asia/Tehran')).isoformat(),
          operational_date='1405/08/20',komatso_reviewed_commit=KHEAD,
          komatso_installed_commit='component-specific; entry deployment 39892553; gateway code e7208cd0',
          hrms_reviewed_commit=None,hrms_reason='not a git repository; use code-index SHA256',
          sources=['E:/KomatsoAI','E:/HRMS','E:/Function','C:/Users/win-10/AppData/Local/hermes'],
          live_hrms_database_access='SQLITE_CANTOPEN; code schema only',
          confidence='mixed: VERIFIED / PARTIAL / UNKNOWN / PROPOSED per finding',
          source_observation_policy='per-evidence timestamps; not a cross-system atomic snapshot')
(ROOT/'AUDIT-METADATA.json').write_text(json.dumps(META,ensure_ascii=False,indent=2),encoding='utf8')
def write(name,title,body):
    header=f"""# {title}

زمان تدوین UTC: {META['compiled_at_utc']}؛ تهران: {META['compiled_at_tehran']}؛ روز عملیاتی: ۱۴۰۵/۰۸/۲۰.

Commit بررسی‌شده KomatsoAI: {KHEAD}. نصب: جزءبه‌جزء؛ Entry برابر manifest نصب 39892553، Hermes گزارش code e7208cd0/version 0.21.4؛ برابری کل فرایند با HEAD اثبات نشده است. HRMS مخزن Git نیست؛ SHA256 کد در evidence/inventory/code-index.json مرجع این ممیزی است.

Source: E:/KomatsoAI، E:/HRMS، E:/Function و Hermes محلی. Schema: SQLite به‌تفکیک منبع، rule=attendance-rule-2، bundle=hrms-policy-bundle-1. سطح اطمینان: هر ادعا با VERIFIED/PARTIAL/UNKNOWN/PROPOSED. آخرین مشاهده معتبر: timestamp همان evidence، عمدتاً 08:55–09:03 UTC؛ زمان تدوین جای آن نیست. دسترسی: پنج SQLite KomatsoAI خوانده شدند؛ دو SQLite HRMS با SQLITE_CANTOPEN در این محیط قابل خواندن نبودند. این مجموعه عکس لحظه‌ای هم‌زمان از همه منابع نیست.

[فهرست و نقشه اصلی](DASHBOARD-MASTER-PLAN.md) · [شواهد و محدودیت‌ها](14-SOURCE-EVIDENCE-INDEX.md)

"""
    (ROOT/name).write_text(header+textwrap.dedent(body).strip()+'\n',encoding='utf8')

write('01-AS-IS-INFRASTRUCTURE.md','وضعیت موجود و ظرفیت میزبان',r'''
## نتیجه قابل اتکا

VERIFIED: مسیرهای سه پروژه/منبع وجود دارند. Windows 10 Enterprise 22H2، build 19045، محصول VMware Virtual Platform، ۸ پردازنده منطقی و حدود ۱۹ GiB RAM قابل مشاهده است. در 08:55:46 UTC حدود ۱۰٫۶ GiB حافظه آزاد، C حدود ۱۰۴٫۵ GiB و E حدود ۱۴۶٫۳ GiB فضای آزاد داشتند. این نمونه ظرفیت اولیه برای API سبک را محتمل می‌کند؛ ظرفیت تضمین‌شده زیر بار و نسخه ESXi از داخل مهمان اثبات نشده‌اند.

منبع: [مشاهده WinAPI و Registry](evidence/inventory/infrastructure-native.json). WMI/CIM با HRESULT 0x80041003 رد شد؛ از WinAPI و Registry فقط‌خواندنی استفاده شد. CPU ثبت‌شده Xeon E5-2680 v4 است؛ بار CPU لحظه‌ای قابل اتکایی جمع‌آوری نشد. CPU تجمعی فرایند معادل درصد بار جاری نیست.

## موجودی اجرایی

| موضوع | KomatsoAI | HRMS |
|---|---|---|
| ساختار | tools/fleet، tools/scheduler، tools/authorization، integrations/hermes، settings، data، runtime، work_orders، reports | hrms، rules، config تاریخی، data، integrations/bale، runtime، output، reports، bale_output |
| ورودی | Hermes plugins؛ runner --tick؛ توابع گزارش مستقل | hrms/cli.py → batch.run_batch؛ integrations/bale/run.py → hrms_bale/cli.py و bot.py |
| Runtime | Python 3.11.15؛ SQLite 3.53.1 در مفسر ممیزی؛ Hermes محیط جدا دارد | pyvenv.cfg نسخه 3.11.15؛ نسخه SQLite فرایند HRMS جدا اندازه‌گیری نشد |
| dependency | APScheduler 3.11.3، openpyxl 3.1.5، PyMuPDF 1.28.0، requests 2.34.2، PyYAML 6.0.3، jdatetime 5.3.0 | pyproject و requirements: xlrd 2.0.1، openpyxl 3.1.5، et_xmlfile 2.0.0؛ نسخه نصب همه بسته‌ها مستقلاً شمارش نشد |
| تنظیم | settings/schedules.yaml و JSONهای entry؛ secret در env موجود، خوانده/کپی نشد | rules/policy.json و rules/*؛ integrations/bale/.local/settings.json و env، محتوای حساس کپی نشد |
| DB | runtime/scheduler/runs.sqlite3؛ reports/telegram_usage/telegram_users.db؛ data/fleet/db/fleet_ops.db؛ دو audit.sqlite3 | data/personnel/personnel.sqlite3 و integrations/bale/.local/bot.sqlite3؛ انبارهای ورودی/خروجی فایل‌محور نیز وجود دارند |
| log | runtime/scheduler/scheduler.log و rotation؛ Hermes logs و state | integrations/bale/.local/bot.log، stderr.log، supervisor.log؛ reports اجرای مستقل |
| artifact | runtime/scheduler؛ reports؛ work_orders؛ فایل‌های موقت بسیاری تضمین ماندگاری ندارند | runtime/bale/<job>/reports و output؛ bale_output آرشیو مشتق‌شده حاوی PII |
| backup | backup/، نصب‌های staged و activation backup، snapshotهای Writer، رسیدها مستقل | backups/، revisions تغییرناپذیر، registry_snapshots، manifest هر اجرا؛ آرشیو خروجی جای backup canonical نیست |
| release/rollback | commit، staged hash، drain/worker lock در انتشارهای قبلی؛ این ممیزی اجرا نکرد | manifest/hash و backup مستقل؛ چون Git نیست rollback مبتنی بر فایل مصوب و بازگردانی آزموده‌شده است |
| تست | tools/scheduler/test_*.py، repairs و report/work-order tests | tests/ و integrations/bale/tests؛ شرح نتایج تاریخی در برنامه تست |

Dependencyهای audit از [runtime](evidence/inventory/python-runtime.json) و کد/مسیرها از [code index](evidence/inventory/code-index.json) آمده‌اند. FastAPI/Uvicorn در .venv KomatsoAI مشاهده نشدند؛ نصب آینده باید در محیط مستقل داشبورد باشد.

## سرویس‌ها، taskها و شبکه

VERIFIED: gateway_state در 08:59 UTC، Gateway=running، Bale=connected، Telegram=retrying با telegram_connect_error و profileهای default/admin/maintenance/net را ثبت کرده است. PID=36740 در فهرست فرایندها نیز دیده شد؛ این مدرک مشاهده/خواندن فایل توسط کاربر بله نیست. HRMS heartbeat تازه، worker_alive=true و process PID=17764 دارد. manifest نصب اولیه HRMS با تعدادی از فایل‌های جاری متفاوت است؛ نسخه نصب اولیه را نسخه فعلی کل سامانه اعلام نمی‌کنیم.

PARTIAL: cron نصب‌شده Hermes یک mine-file-sync فعال با شناسه 3ccb658aed3c، هر دو دقیقه و no_agent=true دارد. last_status عمومی cron جای health_status تفصیلی Sync را نمی‌گیرد. Scheduler KomatsoAI هر دقیقه طبق install.ps1 تعریف شده و due/started تازه در Ledger دارد؛ query زنده Task Scheduler از این محیط با «The system cannot find the path specified» موفق نشد. ثبت و وضعیت واقعی تمام taskها UNKNOWN است. فایل XML ذخیره HRMS لاگین کاربر + تأخیر یک دقیقه و pythonw/autostart.py را نشان می‌دهد، ولی ثبت فعلی task را اثبات نمی‌کند.

VERIFIED: IP مهمان 192.168.8.238، Tailscale 100.85.193.3 و رابط Tailscale فعال مشاهده شد؛ رابط محلی WireGuard مشاهده نشد. این نبودن، WireGuard روی روتر/سایت دیگر را رد نمی‌کند. مسیر end-to-end خانه مدیر → VPN → سرور UNKNOWN و آزموده نشده است. کلید و config VPN خوانده نشد.

Firewall هر سه profile روشن و BlockInbound/AllowOutbound است. اجازه خاص یک پورت داشبورد اثبات نشده است. listenerهای 135،445،3389،5040،5357،7680 و پورت‌های سیستمی/loopback در [فهرست](evidence/inventory/network-processes.json) ثبت‌اند. listener روی 0.0.0.0 اثبات دسترسی عمومی اینترنت نیست؛ مسیر/NAT آزموده نشده است. هیچ پورت پیشنهادی رزرو یا باز نشده است.

منابع: [Gateway](evidence/sanitized/gateway-facts.json)، [HRMS](evidence/sanitized/hrms-runtime.json)، [cron](evidence/sanitized/cron-facts.json)، [Firewall](evidence/inventory/firewall-profiles.txt).

## وابستگی سرویس‌ها

~~~mermaid
flowchart LR
  Mine["SMB معدن"] --> Sync["Hermes cron / mine_file_sync"]
  Sync --> Function["E:/Function"]
  Function --> Reports["KomatsoAI report generators"]
  Scheduler["Windows task / runner tick"] --> Reports
  Reports --> Bale["Bale transport"]
  Scheduler --> Ledger["Scheduler ledger"]
  Hermes["Hermes Gateway / profiles"] --> Entry["Existing authorized Writer"]
  Entry --> Function
  Entry --> Journals["Entry journals"]
  Wisman["Wisman upload"] --> HRBot["Independent HRMS Bale bot"]
  Registry["Versioned personnel + dated rules"] --> HRCore["HRMS batch/engine"]
  HRBot --> HRCore
  HRCore --> Outputs["Immutable run outputs + manifests"]
  HRBot --> HRReceipts["HRMS outbox receipts"]
~~~

## امکان استقرار آینده

PROPOSED: سرویس مستقل با یک worker API، snapshot worker با concurrency=1 و static build محلی؛ هیچ import از Gateway runner یا transport نداشته باشد. سرویس دائمی/launch agent مستقل برای start/restart لازم است؛ Task دقیقه‌ای گزارش برای Web API مناسب نیست. Windows Service یا task اختصاصی با supervisor مستقل پس از طراحی حساب و آزمون startup انتخاب شود. داخل process Hermes اجرا نشود.

Node 22.23.1 و npm.cmd 10.9.8 موجودند؛ npm.ps1 به execution policy برخورد کرد و هیچ policy تغییر نکرد. Vite صرفاً build tool است. نسخه runtime سرور از browser و dev-server مستقل می‌ماند. تأیید نهایی ظرفیت منوط به آزمون روی داده جدا، سنجش p95 و peak RAM و تأیید دسترسی/HTTPS است.
''')

sched=read('sanitized/scheduler-facts.json')
titles={
'overflow_daily_test':'سرریز دفتر','overflow_daily_mechanical':'سرریز مکانیکی',
'driver_daily_office_supervisor':'رانندگان دفتر، دو بخش','driver_daily_mechanical':'معایب مکانیکی',
'driver_daily_metalwork':'معایب آهنگری','maintenance_daily_report':'تعمیرات ماشین‌آلات',
'oil_balance_daily_report':'موازنه روغن','machinery_performance_daily_report':'عملکرد ماشین‌آلات'}
source_names={
'overflow_daily_test':'سرریز روزانه.xlsx','overflow_daily_mechanical':'سرریز روزانه.xlsx',
'driver_daily_office_supervisor':'گزارش روزانه رانندگان2.xlsx','driver_daily_mechanical':'گزارش روزانه رانندگان2.xlsx',
'driver_daily_metalwork':'گزارش روزانه رانندگان2.xlsx','maintenance_daily_report':'تعمیرات 1405.xlsx',
'oil_balance_daily_report':'موازنه روغن مصرفی.xlsx','machinery_performance_daily_report':'عملکرد ماشین آلات.xlsx'}
job_rows=[]
for j in sched['jobs']:
    rc='؛ '.join(r['user_id']+' ('+r['role']+')' for r in j['eligible_recipients'])
    fmt='تصویر، page:N' if 'overflow' in j['id'] else 'دو PDF: mechanical/metalwork' if j['task']=='driver_daily' else 'یک PDF'
    job_rows.append(f"| {j['id']} | {titles[j['id']]} | E:/Function/{source_names[j['id']]} | {j['trigger']['hour']:02}:{j['trigger']['minute']:02} | {rc} | {fmt} |")
job_table='\n'.join(job_rows)
write('02-KOMATSOAI-AUDIT.md','ممیزی KomatsoAI، هشت Job و قرارداد تحویل',r'''
## Registry فعلی، هویت و scope

VERIFIED: settings/schedules.yaml نسخه ۱ دقیقاً هشت schedule فعال دارد. این جدول از YAML فعلی و join خواندنی registry مجوزها استخراج شده است. گیرندگان زیر **وضعیت فعلی** دارند؛ برای موعد قدیمی مدرک تعهد همان موعد از receipts/hold/event لازم است. داشبورد نباید نقش امروز را به گذشته تعمیم دهد.

| Job ID | عنوان | Workbook منبع | ساعت تهران | شناسه و نقش فعلی | خروجی |
|---|---|---|---|---|---|
'''+job_table+r'''

برای همه: cron روزانه، timezone=Asia/Tehran، تاریخ داده = روز تقویمی پیش از due اصلی؛ retry زمان تاریخ را تغییر نمی‌دهد. replay.policy=replay، horizon_days=7. نبود داده معتبر تا original_due+36h فرصت دارد؛ در مرز دقیق بسته می‌شود و خودکار باز نمی‌شود. این دو افق سیاست اجرایی‌اند؛ نگهداری داشبورد تصمیم مستقل است. بازه شروع موازنه و عملکرد 2026-11-03 است و برای قبل از فعال‌سازی تعهد فرضی ساخته نمی‌شود.

Scope منابع و capability دقیق هر Job در [scheduler facts](evidence/sanitized/scheduler-facts.json) است. مکانیکی علاوه بر subscription، read capability مربوطه را در tasks._mechanical_push بازبینی می‌کند. آهنگری scheduled-only با on-demand file read برابر نیست. مجوز receive فعلی اجازه دانلود همه فایل‌ها از داشبورد نیست.

## قرارداد تولید و آمادگی هر خانواده

| خانواده | تاریخ/Schema واقعی | آماده/ناقص | مدرک PDF/فایل | callsite اصلی |
|---|---|---|---|---|
| سرریز دفتر و مکانیکی | overflow/report.py؛ تاریخ دقیق شیت درخواست، بدون fallback به روز قبلی | نبود همان روز → date_missing؛ خطای خواندن/قفل منبع جدا | یک یا چند تصویر؛ دفتر artifact page:N دارد؛ مسیر مکانیکی فقط رسید تجمیعی و فهرست IDها دارد | tasks.py:overflow و mechanical_overflow؛ office_delivery.deliver |
| رانندگان دفتر | driver_report.load_driver؛ تاریخ در سطر اول، ستون‌های یکتای سطر دوم | شیت هم‌تاریخ تکراری/ستون مبهم → خطا؛ شرح خالی در روز معتبر الزاماً missing day نیست | دو PDF مستقل؛ هر بخش validation محتوا/تاریخ/هویت دارد؛ سالمِ بخش دیگر مستقل می‌فرستد | integrations/hermes/function_domain/driver_report.py و scheduler/office_delivery.py |
| رانندگان مکانیکی/آهنگری | همان Drivers2 و section مشخص؛ date exact | بدون ردیف/شیت یا شرح، معنای اختصاصی منبع حفظ شود | یک PDF همان بخش؛ receipt عمومی ممکن است ID داشته باشد ولی hash ساختاریافته ندارد | tasks._mechanical_push و metalwork_driver_daily |
| تعمیرات | maintenance_daily/report.py و layout.json؛ ستون‌های تاریخ، کد مکانیزم، مکانیک، شرح، قطعات؛ نگاشت هر شیت صریح | فعالیت واقعی همان روز لازم؛ date-only کافی نیست؛ DATA_NOT_READY فقط accepted journal head + بررسی موفق exact-file معدن در پنج دقیقه | prepared.py مدل ARTIFACT_READY با workbook/PDF/report/layout/manifest محتوامحور دارد؛ pointer جاری در مسیر cache در این نمونه پیدا نشد | maintenance_daily/prepared.py:source_proof، confirm_missing_source، prepare؛ delivery.deliver |
| موازنه روغن | oil_balance/report.py:scan؛ تاریخ/سطرهای معتبر و ارزیابی محدود فرمول‌ها | date_missing جدا از خرابی source/formula؛ سلول blank با صفر یکی نیست | PDF با hash منبع و validation؛ hashها در log، receipt عمومی فایل را به‌تنهایی version نمی‌کند | oil_balance/report.py، render.py، delivery.py |
| عملکرد | machinery_performance/report.py؛ سال ۱۴۰۵، ستون‌های تاریخ؛ mode=monthly، comparison=average_gt_limit و fuel_only | readiness از مقدار ثبت‌شده روز هدف؛ missing data مستقل؛ FIFO اختصاصی ندارد | runtime/scheduler/machinery-performance/<date>/report.json+report.pdf به صورت pinned؛ manifest مرز انتشار است | machinery_performance/delivery.py:pinned، prepare، deliver |

فایل Drivers2 از «تعمیرات 1405.xlsx» مستقل است؛ موفقیت ثبت شرح رانندگان، ورود به دفتر تعمیرات را اثبات نمی‌کند. metadata پنج workbook بدون ذخیره سلول‌ها در [workbook inventory](evidence/inventory/workbook-metadata.json) است. اندازه‌ها حدود ۱٫۰۷MB،۸۰۷KB،۱۹۱KB،۱۶KB،۱۰۶KB هستند؛ بازکردن workbook با ۳۲۱ یا ۲۰۸ شیت در هر refresh طراحی مناسبی نیست.

## Ledger، رسید و خطای تاریخی

VERIFIED: runs=285، receipts=469 و office_artifact_receipts=242 در snapshot اولیه. این تعداد سوابق است، نه تعداد ارسال‌های موفق امروز. PK واقعی runs=(schedule_id,due)، receipts=(schedule_id,due,recipient_id,delivery_kind)، office_artifact_receipts=(schedule_id,due,recipient_id,artifact_id). schema کامل و ستون‌ها در [database schemas](evidence/inventory/database-schemas.json) ثبت‌اند.

Run موفق به‌تنهایی proof ارسال نیست. از ۳۸۳ ردیف report با status=sent، ۲۵۷ ردیف Message ID غیر NULL دارند؛ ۱۲۶ ردیف فاقد آن‌اند. بسیاری از aggregateهای دفتر با receipt فایل قابل تکمیل‌اند؛ این ۱۲۶ را خودکار «ارسال‌نشده» یا «نامطمئن» اعلام نکنید. یک notice از ۴۹ notice sent نیز ID ندارد. چهار receipt report در وضعیت uncertain و بدون ID هستند. هیچ office artifact در وضعیت sent و بدون ID مشاهده نشد.

Query شمارش runهای succeeded فاقد رسید عمومی دارای ID صرفاً **هشدار کیفیت** است: artifact receipt یا recovery می‌تواند proof مستقل بدهد. بعضی scheduleهای تاریخی مثل repairs_daily/metalwork_daily دیگر فعال نیستند. نسبت‌دادن آن‌ها به هشت Job فعلی بدون قرارداد migration ممنوع است.

## سه سطح شمارش و کلیدهای پیشنهادی

1. Occurrence: tuple(project, schedule_id, original_due_utc). از due اصلی، نه retry/finished/date امروز. در تغییر ساعت schedule، رکورد قبلی همان روز مقدم است؛ due جدید دوم برای همان تعهد نسازید.
2. Logical artifact: occurrence_id + artifact_kind (mechanical،metalwork،page:N یا report). نسخه فایل با sha256 و revision_id جداست؛ نبود hash برابر hash فرضی نیست.
3. Recipient delivery: artifact_version_id + recipient_ref + delivery_kind + channel. تلاش‌ها زیر همین هویت‌اند؛ retry ارسال جدید در شمارنده موفقیت نسازد. receiptهای تجمیعی قدیمی با proof_scope=recipient_aggregate نمایش داده شوند و به تعداد PDF یا page حدس زده نشوند.

مثال مصنوعی: یک occurrence دفتر × دو PDF × دو گیرنده = ۱ نوبت،۲ فایل منطقی و۴ ارسال فایل. اجرای همان روزِ Job مکانیکی occurrence جداست، حتی اگر محتوای PDF مشترک باشد. recovery و correction زیر original_occurrence پیوند دارند؛ به‌عنوان Job برنامه‌ریزی‌شده جدید شمرده نمی‌شوند. رکورد نسخه/تلاش برای audit جدا شمارش می‌شود.

## Retry، recovery، correction و alert

Backoff کد misfire.py: ۶۰،۱۲۰،۳۰۰،۶۰۰،۹۰۰ ثانیه؛ سقف هفت‌روزه برای خطای فنی. invalid_source، driver_pdf_content و pdf_validation terminal هستند؛ Retry کور برای نقص محتوا مجاز نیست. uncertain و sending وقفه‌خورده به بررسی رسید نیاز دارند. notice و report هویت مستقل دارند؛ ارسال موفق یادآوری هیچ گزارش را موفق نمی‌کند.

Recovery: report_recovery_hold اصل suppression را حتی بعد recovered نگه می‌دارد؛ عملیات مستقل در report_recovery_operation/event است. زنجیره سه جدول operation→receipts→office_artifact_receipts با ID/hash/approval_ref باید برابر باشد. correction در report_correction_review وضعیت بررسی دارد؛ sent در آن بدون رسید ارسال مستقل، proof کامل نسخه اصلاحی نیست.

Alert: delivery_failure_watch/case/receipt برای خطای حل‌نشده پس از ۱۵ دقیقه، uncertain فوری و expiry؛ completion_reminder_receipts مستقل از delivery گزارش. snapshot مدیریتی ۱۷:۳۰ (manager_daily_snapshot/receipt) هفت روز کامل + همه مسائل حل‌نشده تاریخی را نگه می‌دارد؛ آن را snapshot امروز یا فهرست همه داده‌های روز معرفی نکنید. بازه‌های preflight و reminders در YAML با job PDF برابر نیستند.

## یافته‌های فعلی و سناریوهای تاریخی

| موضوع | نتیجه و اطمینان | مدرک/اثر |
|---|---|---|
| بازیابی ۱۷ و۱۸ آبان | VERIFIED، چهار زنجیره receipt تطبیق دارد | IDهای 7417,7418,7419,7420؛ دو hash مستقل؛ ردیف اصل حفظ و suppression برقرار؛ مشاهده انسانی UNKNOWN |
| ۱۵ و۱۶ آبان | VERIFIED، owner_history.py آن‌ها را خارج پیگیری عملیاتی می‌داند | آرشیو باقی؛ صفحه عملیات آن‌ها را active incident یا تعهد ارسال تازه نسازد |
| اصلاحیه دفتر | VERIFIED، reviewهای sent + رسید مستقل 7347/7348 با hash 3f67e808... وجود دارد | original receipt محفوظ؛ محتوای فرستاده‌شده نخست از این اصلاحیه بازسازی نشود |
| نسخه اولیه آهنگری گیرنده اختصاصی | PARTIAL، needs_content_review باقی است | payload/hash اختصاصی پیام 7321 نگهداری نشده؛ نقص محتوای آن قطعی اعلام نشده |
| تعارض متفرقه | VERIFIED به‌عنوان سابقه | آخرین مشاهده ساختار قدیمی 2026-11-10T07:15:12Z؛ هنوز در failure journal هست، ولی علت جاری همین نیست |
| تعارض 705/D23 | VERIFIED جاری در آخرین scan | MAINTENANCE_SYNC_HISTORICAL_CELL_CONFLICT؛ local 78fe91ef... در برابر mine c1ff0dde...؛ هر دو snapshot نگهداری شده‌اند؛ رفع نشده |
| Sync | VERIFIED degraded/partial در آخرین scan کامل؛ هنگام نمونه بعدی attempt_running | source_enumerated=true و outage=false با errors=1؛ سلامت کلی را سبز نکنید؛ source-outage قدیمی هنوز در field تاریخی وجود دارد |
| تعمیرات داده ۱۹ آبان، موعد۲۰ آبان۱۲:۰۰ | VERIFIED در snapshot scheduler، retry_wait/source_stale | نبود داده همان روز از این وضعیت قابل اثبات نیست؛ آخرین تلاش 08:49Z و retry بعدی 09:04Z |
| ARTIFACT_READY | VERIFIED در کد، activation کلیِ داده آماده امروز UNKNOWN | cache maintenance-ready در خواندن این ممیزی pointer نداشت؛ report recovery از artifactهای مستقل استفاده کرده است |
| queue/FIFO | VERIFIED نبود صف FIFO اختصاصی در مسیر عملکرد/برنامه مشترک فعلی | هر occurrence در misfire/replay plan مستقل بررسی می‌شود؛ آزمون مجدد زیر load در این ممیزی اجرا نشد |
| AI Entry | VERIFIED کد نصب‌شده با manifest 39892553 برابر است؛ اتصال Bale تازه | تأیید گزارش برای بررسی؛ تأیید متن مجوز یک‌بارمصرف ذخیره همان نسخه/hash و اجرای ai_commit؛ confirm_write قدیمی رد می‌شود |
| پیام AI و ذخیره | PARTIAL | ۱ Draft=EDIT و۴۳ edit succeeded در DB رانندگان هست؛ این اعداد نشان نمی‌دهند این Draft نوشته شده؛ متن خصوصی وارد evidence نشده است |

منابع: [facts](evidence/sanitized/scheduler-facts.json)، [رسیدهای مستقل](evidence/sanitized/independent-artifact-receipts.json)، [Sync](evidence/sanitized/sync-facts.json)، [Journal](evidence/sanitized/entry-journal-summary.json)، [نصب Entry](evidence/sanitized/entry-install-comparison.json).

## سایر ماژول‌ها و آمادگی اتصال

| ماژول | منبع موجود | استفاده dashboard و محدودیت |
|---|---|---|
| fleet faults/history | fleet_ops.db:daily_fault_reports،issue_mentions،issue_state_current و provenance ingest_sources | آداپتور read-only ممکن؛ داده مشتق‌شده import شده، منبع زنده امروز نیست؛ سال عملیاتی و تازگی الزامی |
| maintenance history | maintenance_events و identity mapping | ثبت اقدام، نتیجه تعمیر/رفع علت را اثبات نمی‌کند |
| service/PM | service_pm_snapshots،service_shift_hours،service_year_imports | سرویس/ساعت از خرابی و تعمیر مستقل نمایش داده شود؛ remaining_hours اثبات تعویض روغن نیست |
| NET/OC/GR/AF | service_work_orders/items/users/dispatch/delivery_attempts و work_order_final_copy؛ channels/bale | read adapter برای شماره،state،revision_of،رسید؛ این namespace از scheduler PDF جداست |
| manual entry | repairs/entry_service.py و maintenance_service.py → edits+backup+atomic replace | جزییات operation،before/after hash و uncertainty خوانده شود؛ هیچ commit از dashboard MVP وجود ندارد |
| AI entry | entry_extract/entry_draft/entry_draft_store/entry_ai_bale و shared Writer | source spans و ambiguity حفظ؛ UI/confirm امروز با قرارداد قدیمی دو تأیید مستقل ذخیره اشتباه نشود |
| access/roles | auth_user_roles،auth_role_capabilities،channel_users و extension migrations | استفاده برای توضیح source scope؛ نقش dashboard مستقلاً تعریف شود؛ broad admin بودن مجوز تازه‌ای ایجاد نمی‌کند |

برای همه خانواده‌ها Recovery/Correction خارج برنامه به تصمیم/شناسه/hash محدود احتیاج دارد؛ دکمه اجرا در MVP ممنوع و فقط اقدام پیشنهادی نمایش داده می‌شود.
''')

write('03-HRMS-AUDIT.md','ممیزی HRMS، نسخه قوانین و مدیریت پرسنل',r'''
## مرز حقیقت

VERIFIED از کد و مسیرها: HRMS موتور مستقل است؛ pipeline حمل‌ونقل را از run_batch جدا کرده و ورودی/قانون/خروجی را نسخه‌دار می‌کند. موتور منطق payroll تازه‌ای از dashboard دریافت نمی‌کند. **G08 اکنون implemented است و G09 جاری الزاماً import ویسمن نیست.** طرح مبتنی بر وضعیت قدیمی pending/import-only اشتباه خواهد بود.

PARTIAL: دو DB زنده HRMS در این sandbox با mode=ro و Online Backup به حافظه، SQLITE_CANTOPEN دادند. علت دقیق OS/WAL/ACL اثبات نشده است؛ absent بودن sidecar به‌تنهایی تشخیص قطعی نیست. immutable=1 روی منبع زنده، کپی خام DB یا ساختن shm برای دورزدن محدودیت انجام نشد. Schema HRMS از DDL کد نصب‌شده استخراج و فقط در حافظه ساخته شد؛ این، تأیید schema زنده/شمارش canonical نیست.

## مسیر منابع و پردازش

Wisman .xls واقعی → source_intake/validation/ingestion → snapshot منابع → personnel.engine_sources و rules/policy.json → rulebook تاریخ‌دار + shift_selection → engine.calculate و سیاست‌های حضور/جمعه/مرخصی → daily/monthly results → publication_decision → exporter و manifest. در بله: hrms_bale.bot/Store → pipeline.run → همان batch؛ outbox رسید حمل جدا دارد.

| منبع | Schema/هویت | مرجع و محدودیت |
|---|---|---|
| personnel.sqlite3 | metadata؛ roles/role_audit؛ revisions(id,parent_id,token,actor,people BLOB,assignments BLOB,extra_rules,at_utc,...)؛ proposals(token,request_key,base_revision,policy_sha256,expires_utc,committed_revision,...) | نسخه تغییرناپذیر canonical؛ جدول نرمال‌شده «همه پرسنل جاری» وجود ندارد؛ projection از BLOB نسخه انتخاب‌شده ساخته می‌شود |
| people.xlsx و assignments.csv | نام/کد/سمت/گروه/وضعیت و انتساب تاریخ‌دار | مبنای تاریخی؛ _check_legacy هش را کنترل می‌کند؛ آمار جاری از خواندن این دو فایل به‌تنهایی معتبر نیست |
| registry_snapshots/<hash>/... | registry_receipt.json: schema hrms-personnel-snapshot-1،revision و SHA همه فایل‌ها | snapshot ثابت برای engine؛ آخرین folder از روی mtime با head canonical برابر فرض نشود |
| rules/policy.json | hrms-policy-bundle-1؛ references به rules،catalog،attendance v9،shift-selection v2،source-validation v5 | منبع پیش‌فرض جاری؛ config/ به replay تاریخی تعلق دارد |
| rules/rule_versions.csv | group،version،effective_from/to،state،rule_file،personnel_ids،approval/evidence | specificity فردی بر عمومی مقدم است؛ pending fallback برای گروه‌های بدون scope مصوب همچنان وجود دارد |
| داده HR | shift_plan.csv،overrides.csv،leave_approvals.csv و تصمیم‌های تاریخ‌دار | missing/نامعتبر/گروه مبهم حدس زده نمی‌شود؛ schema و columnهای خواننده منبع است |
| Wisman | داده تردد و status، source calculations و observation state | صفر،blank،invalid،unknown جدا؛ مجموع source همان تأیید حقوق نیست |
| output per run | daily_results.json،monthly_results.json،batch_summary.json،ingestion_snapshot.json،source_conflicts.json،manifest.json و Excel | مرجع محاسبه همان content_id و نسخه قواعد؛ داده یک اجرا با قواعد امروز دوباره تفسیر نشود |
| HRMS bot DB | users،inbox،jobs،outbox،audit،personnel_ui_sessions/events | job.state=ready / manifest.state=complete با outbox.sent و HR approval مستقل‌اند |
| bale_output | کپی مشتق از job/receipt و hash منبع | آرشیو قابل مشاهده است، authority جدید محاسبه یا receipt نیست؛ حاوی PII و خارج از static root |

جزئیات [code-only schemas](evidence/inventory/hrms-code-schemas.json) و [rule catalog](evidence/sanitized/rule-catalog.json).

## G01 تا G09: نسخه فعلی و تاریخ مؤثر

این جدول قرارداد موجود در کد/JSON است؛ معادل تأیید صحت همه پرونده‌های واقعی نیست. Scope شخصی در کاتالوگ و extra_rules نسخه personnel باید همراه نسخه resolve شود.

| گروه | نسخه جاری قابل انتخاب | از چه تاریخ | رفتار مهم قابل نمایش، بدون محاسبه dashboard |
|---|---|---|---|
| G01 | G01-7.0.0 | 1405/07/01 | شب۱۹ تا۰۴، بدون استراحت؛ deadline۱۸:۵۰؛ فقط ورود strictly قبل۱۸:۰۰ برای اضافه‌کار قبل۱۹؛ کسری مستقل از اضافه‌حضور؛ بعد۰۴ جبران کسری نیست |
| G02 | مهر1.2.0؛ آبان1.3.0 | 1405/08/01 برای نسخه جدید | حضور۰۷–۱۹،استراحت۱۳:۳۰–۱۴:۳۰،دیرکرد/خروج زود از نخستین دقیقه؛ قبل۰۷ و بعد۱۹ طبق قانون اضافه‌کار |
| G03 | مهر6.0.0؛ آبان7.0.0 | 1405/08/01 برای نسخه جدید | ۱۹–۰۶ روز بعد؛ پایه۴۸۰دقیقه،deadline۱۹،cutoffخروج۰۳:۳۰؛ استراحت آبان۰۰–۰۳؛ کسری مستقل؛ اضافه‌حضور نمایشی با اضافه‌کار قابل پرداخت یکی نیست |
| G04 | G04-1.0.0 | 1405/07/01 | روز۰۶:۴۰–۱۸:۴۰؛ استراحت۱۲:۳۰–۱۳:۳۰؛ exceptions شب با assignment/shift/rule جدا بررسی شوند |
| G05 | مهر1.3.0 + استثنای شخصی یک‌روز1.3.1؛ آبان1.4.0 | 1405/08/01 عمومی جدید | deadline۰۶:۵۰،required_exit۱۸:۵۰؛ خروج strictly بعد۱۹:۱۵،کل حضور پس از۱۹ را اضافه‌کار می‌کند؛ دقیق۱۹:۱۵ صفر؛ قبل شیفت صفر |
| G06 | G06-2.0.0 | 1405/07/01 | ۰۷–۱۹،استراحت۱۲:۳۰–۱۳:۳۰؛ اضافه‌کار بعد۱۹ وقتی بیشتر۱۰دقیقه است،کل مدت مجاز؛ قبل۰۷ صفر |
| G07 | مهر2.0.0؛ آبان2.1.0 | 1405/08/01 جدید | deadline۰۶:۴۵؛ استراحت۱۲:۳۰–۱۳:۳۰؛ آستانه strictly بعد۱۹:۱۵،کل مدت پس از۱۹؛ پیش از شیفت اضافه‌کار ندارد |
| G08 | G08-1.2.1 implemented،scope۹ کد | 1405/07/01 | paid_break،کارکرد ثابت۵۷۰ دقیقه روز معتبر،کنترل کسری۰۸–۱۹؛ استراحت ثابت۱۳:۳۰–۱۵؛ اضافه‌حضور نمایشی قبل۰۸/بعد۱۹ و payable overtime=0؛ full absence/leave رفتار base-day مستقل دارد |
| G09 | خانواده7 برای مهر،8.0.0/8.0.1/8.1.0 برای آبان؛ fallback عمومی pending | 1405/08/01 برای خانواده8 | پروفایل استاندارد۰۸–۱۶ و پنجشنبه۱۲؛ مرخصی ماهانه۹۶۰ دقیقه برای scope مربوط؛ پروفایل دیگر پنج‌روز/سهمیه صفر/آستانهstrict>۱۵ جدا؛ جمعه آبان actual_presence ضریب۱؛ تعطیل رسمی خارج استثنا ممکن است باز بماند |

**G09 تاریخی و جاری را جدا نگه دارید:** engine.py شاخه wisman_authorized_import و total import را همچنان برای نسخه‌های تاریخی پشتیبانی می‌کند؛ g09_overtime.py منبع مجاز را اعتبارسنجی می‌کند. JSONهای جاری خانواده8، overtime_policy=before_and_after_shift دارند؛ موتور از بازه‌های معتبر حضور محاسبه می‌کند و اضافه‌کار ویسمن را روی آن جمع نمی‌کند. مرجع هر مقدار باید rule_version و audit kind همان daily result باشد. مقایسه G09 با مقدار source در نسخه محاسباتی جاری هم‌معنی تطبیق مستقل نسخه import تاریخی نیست.

Rule catalogs فقط عمومی نیستند: personnel.revisions.extra_rules در export به snapshot merge می‌شود؛ بدون head revision نمی‌توان نسخه مؤثر همه افراد را از rules folder تنها تعیین کرد. جدول بالا roadmap موتور است، نه وضعیت محاسبات همه کارکنان.

## شواهد اجرا و محدودیت آزمون

VERIFIED: سه manifest ذخیره‌شده جدید در درخت runtime/bale، کد engine با ۴۰/۴۰ SHA فعلی برابر دارند؛ آخرین نمونه 2026-11-08T13:32:16Z،دوره1405/07،۳۰رکورد،یک فرم و state=complete است. hr_approval_state=not_recorded. این نمونه یک scope محدود است و چون DB jobs خوانده نشد، «آخرین پردازش واقعی همه کاربران بله» یا آخرین تحویل را اثبات نمی‌کند.

مطالعه کد نشان می‌دهد تمام GROUPS implemented=True و G08 paid_break path فعال است؛ آزمون‌های test_g08_accounting/deduction و test_g09_authorized/all_fridays و تاریخ‌دار موجودند. در این ممیزی هیچ suite اجرایی HRMS یا محاسبه مجدد انجام نشد. نتیجه تاریخی G03: focused۸۲ و Bale۹۳ موفق؛ full baseline۱۰۶۲ با۲failure،۹error،۱skip،candidate کامل نشده. این شاهد تاریخی را suite سبز امروز معرفی نکنید. قواعد آبان۱۱/۱۰ در پوشه موجودند؛ این سه خروجی مهر، اجرای آن نسخه‌های آبان را اثبات نمی‌کنند.

## KPIهای مدیریتی و حد دسترسی

| KPI | منبع معتبر پیشنهادی | وضعیت موجود | قاعده نمایش و حساسیت |
|---|---|---|---|
| کل/فعال/غیرفعال/بدون گروه | PersonnelService.directory/projection از یک revision و as_of | تابع موجود،live count UNKNOWN | unique person key؛ alias/code متعدد یک فرد تکرار نشود؛ status پردازش از استخدام فعال جدا؛ فقط manager/HR |
| توزیع گروه | assignments همین revision و بازه مؤثر | موجود؛ adapter لازم | گروه ترکیبی چند membership دارد؛ جمع membership با تعداد افراد برابر لازم نیست |
| import ماه | jobs/result + ingestion_snapshot/source states | pipeline موجود،DB وضعیت جاری UNKNOWN | exact period/content_id؛ ingest accepted با کامل‌بودن ماه یکی نیست |
| محاسبه/آخرین موفق | jobs.state + manifest.state/finished_at | موجود در منابع؛global live UNKNOWN | latest واقعی فقط با jobs lineage؛ latest folder آزمایشی را وارد شمارنده نکنید |
| errors/conflicts | source_conflicts،assignment_issues،identity_conflicts،decision_issues،input_accounting و review topics | خروجی موجود | یک علت مشترک چند issue خام را duplicate action نشمارد؛ PII صرفاً role مجاز |
| فرم‌ها | manifest.forms_generated،outputs و page_manifest با SHA | موجود | عدد فرم با employee و count دریافت‌کنندگان برابر نیست |
| day/night/shifts | daily_results group/shift و batch_summary.shifts | موجود یا projection،فرمول جدید نیاز نیست | inferred shift ممنوع؛ ambiguous=null؛ as_of/rule release همراه |
| work/OT/deduction/leave | monthly_results known_* و final_* و publication | موجود،بعضی مقادیر null | confirmed/known/provisional/final را جدا؛ جمع ناقص صفر نشود؛ HR حساس |
| تأیید و نهایی‌سازی | publication.publication_state،hr_approval.state و result version | readiness موجود،workflow تأیید مالی عمومی تکمیل نیست | complete/form بی‌برچسب،HR approved نمی‌شود |

هیچ endpoint عمومی حقوق،کدملی،نام پدر،تردد خام یا فایل کامل پرسنل را برنمی‌گرداند. نمودار manager فقط aggregateهای مجاز و تعداد unresolved مستقل دارد.

## عملیات پرسنلی موجود و نیاز آینده

| عملیات | Handler/Service | permission/transaction/audit | امکان API آینده |
|---|---|---|---|
| افزودن و assignment | PersonnelUI.handle → PersonnelService.prepare(operation=add/assignment) → approve | roles فعال مستقل؛ BEGIN IMMEDIATE؛ base_revision/policy hash؛ proposal۳۰دقیقه؛ revisions immutable و token/request_key | service موجود؛adapter هویت وب و binding تأیید لازم |
| اصلاح سمت | operation=edit؛payload فقط personnel_id/job_title | همان transaction و revision audit؛ expected version | reuse سرویس پس از بررسی actor mapping |
| اصلاح نام | UI action identity → operation=rename | payload نام/نام‌خانوادگی + کد هدف ثابت؛ عمومی تغییر code/national_id نیست | موجود در کد جاری؛ متن قدیمی README ممکن است این capability را ذکر نکند |
| گروه تاریخ‌دار | operation=transfer | groups کامل،effective range،validation انتساب و policy؛ تأیید همان actor | service موجود؛دو endpoint prepare/confirm آینده |
| مشاهده تاریخچه | directory و revisions | authorize و legacy-hash؛ projection read-only | read adapter ممکن؛directory() از sqlite3.connect پیش‌فرض استفاده می‌کند؛ برای حساب dashboard read-only interface رسمی لازم |
| غیرفعال‌سازی فرد | operation عمومی disable در validate_operation وجود ندارد | inactive/ignore در مدل/منابع هست،اما handler عمومی تأییدشده اثبات نشد | نیاز service contract؛با role(active=false) اشتباه نشود،آن مجوز کاربر را قطع می‌کند |
| شناسه/مجوز بله | Store.access و PersonnelService.role مستقل | owner-only و audit/role_audit؛ upload access ≠ personnel manager | تغییر حساب و نقش در MVP خارج؛RBAC تازه بدون ادغام خودکار |
| import/محاسبه/تأیید خروجی | pipeline خدمت مستقل دارد؛publication readiness جدا | ورودی hash/snapshot،outbox idempotency؛تأیید نهایی HR عمومی هنوز طراحی لازم دارد | پیش از write API،command service هویت‌دار و audit/approval لازم |

**خواندن امن:** PersonnelService.export با نام خواندن، snapshot directory ایجاد می‌کند؛ Store constructor schema/owner را می‌نویسد. آن‌ها را از request GET داشبورد فراخوانی نکنید. directory با role canonical و read connection جدیدِ mode=ro/همان projection باید boundary مشخص پیدا کند. قانون یا employee identity را در adapter بازتفسیر نکنید.

## مغایرت مستندات با کد

فایل pending_decisions هنوز برخی پرسش‌های قبلی مثل G05 exit grace و بخشی از جمعه G09 را open معرفی می‌کند،درحالی‌که کاتالوگ/قانون جدید بخشی را صریح حفظ/حل کرده است. README قدیمی و G08 scope «هشت نفر» نیز با کاتالوگ۹کدی جاری یکسان نیستند. این‌ها ریسک drift مستندات‌اند؛ خودکار issue مالی برای همه نسازید. Source of Truth محاسبه،نسخه انتخاب‌شده همان اجراست؛ وضعیت اداری open decision نیاز reconciliation مستند در دامنه HRMS دارد و در این ممیزی ویرایش نشد.
''')

if __name__=='__main__':
    print('Core audit documents written inside',ROOT)
