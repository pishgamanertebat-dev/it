from build_documents import *

write('05-ARCHITECTURE-BLUEPRINT.md','معماری مستقل، فناوری، تازگی و پایداری',r'''
## تصمیم پیشنهادی و مرزهای اجرا

PROPOSED برای شروع توسعه: HTML5،Tailwind build محلی،Vanilla ES Modules،Vite،Chart.js محلی؛Python/FastAPI در venv مستقل. این انتخاب‌ها تصویب یا نصب عملیاتی نیستند. تمام فایل‌های این مأموریت صرفاً سند/شاهد هستند؛رابط/سرور/port ایجاد نشده است.

محل آینده E:/ManagementDashboard است. محل این گزارش داخل reports پروژه صرفاً fallback مجوز filesystem است و ادغام معماری محسوب نمی‌شود. ساختار آینده:

~~~text
ManagementDashboard/
  frontend/src/{app,components,pages,api,state,i18n}/
  backend/dashboard/{api,auth,adapters,projections,cache,audit}/
  contracts/{openapi,read-models,schema-fingerprints}/
  tests/{fixtures,adapters,contracts,security,ui}/
  runtime/{snapshots,cache,audit}/
  releases/   backups/   planning/
~~~

~~~mermaid
flowchart LR
  Browser["مرورگر RTL / شبکه خصوصی"] --> TLS["HTTPS خصوصی + Session"]
  TLS --> API["Dashboard API / field permissions"]
  API --> Cache["versioned read models"]
  Collector["bounded snapshot worker"] --> Cache
  Collector --> KA["KomatsoAI read adapters"]
  Collector --> HA["HRMS read adapters"]
  KA --> Ledger["receipts / runs / journals"]
  KA --> Health["Gateway / Sync state"]
  HA --> HR["personnel revision / job / manifest"]
  Artifacts["immutable authorized artifacts"] --> API
  Domains["Existing Scheduler / Writer / HRMS engine"] --> Ledger
  Domains --> HR
~~~

Browser فقط API را می‌بیند. مسیر خام server،credential،Bale API وSMB در client وجود ندارد. API از داده محلی از پیش آماده می‌خواند؛refresh مرورگر باعث خواندن دوباره منبع زنده نمی‌شود.

## انتخاب Frontend

| گزینه | ارزیابی این پروژه | ریسک نگهداری/مهاجرت |
|---|---|---|
| Vanilla ES Modules | برای ۸ صفحه عمدتاً خواندنی،فیلتر/جدول/جزئیات کافی؛moduleهای mount/unmount و state واحد کوچک | lifecycle،event cleanup وstate باید قرارداد داشته باشد؛از global mutable state وDOM duplication پرهیز |
| React | state وcomponent/form ابزار غنی دارد؛برای فرم‌های پیچیده چندمرحله‌ای آینده می‌تواند مفید باشد | نیاز فعلی الزامی ایجاد نمی‌کند؛هزینه runtime/build وآموزش باید در برابر سود واقعی سنجیده شود |
| Preact | جایگزین component-based سبک‌تر با مدل نزدیک React؛مهاجرت مرحله‌ای ممکن | compatibility کامل همه ابزارها فرض نشود؛قبل از انتخاب آزمایش component/form لازم است |

این مقایسه قضاوت طراحی بر اساس read-heavy بودن دامنه است،نه benchmark اندازه‌گیری‌شده. [React state](https://react.dev/learn/managing-state) و [Preact](https://preactjs.com/guide/v10/getting-started/) قابلیت‌های گزینه‌ها را شرح می‌دهند. مهاجرت فقط وقتی فرم‌های concurrent،shared state متعدد یاduplicate UI واقعی مانع نگهداری شدند؛page shell وAPI contract مستقل بمانند تا فقط view layer عوض شود.

Componentها: AppShell،Sidebar،DateRange،MetricCard،StatusBadge،SourceStamp،ReportTable،ArtifactList،RecipientGrid،EvidenceDrawer،ErrorPanel،EmptyState،StaleBanner،Pagination،ChartPanel. state:session،route،filters،selectedOccurrence،queryCache؛URL فیلتر قابل bookmark وAbortController درخواست قدیمی را لغو کند. templates ثابت؛متن منبع با textContent.

[Tailwind Vite](https://tailwindcss.com/docs/installation/using-vite) تولید CSS محلی را پشتیبانی می‌کند؛[Chart.js](https://www.chartjs.org/docs/latest/getting-started/integration.html) با JS ساده/ES Modules قابل استفاده است. فونت فارسی دارای مجوز،CSS،chart،آیکون وcalendar dependency همراه release بسته‌بندی شوند؛CDN وtelemetry وابسته به اینترنت در runtime نباشند.

Node موجود 22.23.1 با حد فعلی [Vite](https://vite.dev/guide/) 22.12+ سازگار است؛نسخه دقیق dependency در lockfile زمان ساخت pin شود. static build خروجی deployment است؛vite dev/preview سرور دائمی تولید نیست. ساخت reproducible روی محیط توسعه مجاز وartifact checksum به سرور منتقل شود.

## Backend و Adapterها

FastAPI برای OpenAPI typed contracts،validation،field projection وdependency auth مناسب است؛Flask نیز شدنی است ولیvalidation/contract بیشتری دستی می‌شود. نبود FastAPI/Uvicorn فعلی مانع طراحی نیست؛نصب در venv داشبورد کار فاز آینده است. یک process API ویک snapshot worker برای MVP پیشنهاد می‌شود؛افزایش worker قبل از اندازه‌گیری توجیه ندارد. [FastAPI deployment](https://fastapi.tiangolo.com/deployment/concepts/) اثر lifecycle،HTTPS وmemory هر worker را توضیح می‌دهد.

| Adapter آینده | مسئولیت محدود | reuse / شکاف |
|---|---|---|
| ScheduleRegistryReader | هشت job/cron/start_date +hash | YAML safe parse؛سیاست schedule/version به تاریخچه متصل شود |
| DeliveryLedgerReader | snapshot SQL وtupleهای原 occurrence/receipt/artifact/recovery/correction | constructors runner/Store وensure_schema import نشوند؛projector خواندنی رسمی از قواعد موجود استخراج/پوشش داده شود |
| SourceHealthReader | Sync guard،Gateway heartbeat،HR bot health | allowlist schema/version؛فایل partial/old→unknown |
| EntryAuditReader | فقط status/operation/hash،بدون متن خصوصی | Draft موفق باWriter commit جدا؛write helper فراخوانی نشود |
| HRPersonnelReader | revision واحد وprojection as_of | pure personnel_display.projection قابل reuse روی snapshot؛directory اتصال پیش‌فرض وexport دارایside effect باید interface خواندنی رسمی بگیرند |
| HRProcessingReader | jobs/result→manifest→daily/monthly/publication | pipeline.run محاسبه/نوشتن دارد وGET نیست؛read projection فقط |
| ArtifactResolver | id→allowlist manifest→path/hash/permission | path ورودی کاربر،glob arbitrary وregenerate ممنوع |
| FleetReadReader | اختیاری بعدMVP؛NET/OC/GR/AF وfreshness | داده مشتق/سال۱۴۰۵ وnamespace work-order ازdaily report جدا |

هیچ adapter،Bale sender،Writer،HRMS calculate یاmigration را import/execute نمی‌کند. کدهایpure اگرباapplication import ترکیب شده‌اند،ابتدا قرارداد خواندن مشترک درمنبع طراحی/آزمون شود؛فعلاًschema-based read رویsnapshot باacceptanceهایهمان semantics. duplicating payroll/expiry/retry ممنوع.

## SQLite وExcel

SQLite: URI mode=ro،query_only،timeout کوتاه،Online Backup صفحه‌ای به حافظه/فایل خصوصی داشبورد،deadline،سپس transaction منبع بسته. schema fingerprint ووجود ستون‌ها قبل ازquery بررسی؛schema ناشناخته partial/unknown. WAL همراه source توسطSQLite دیده می‌شود؛raw copy DB بدونWAL یاcheckpoint اجباری ممنوع. [Python backup](https://docs.python.org/3.11/library/sqlite3.html) رابط اینsnapshot را مستند می‌کند.

رابط readonly درحساب جدا ممکن است بهsidecar قابل‌خواندن نیاز داشته باشد؛اگرمشابهHRMS اینممیزی fail شد،fallback رسمی owner-produced immutable snapshot است؛immutable=1 رویlive منبع ممنوع. source→dashboard readonly ACL پیش ازMVP-1 آزموده شود؛این ممیزیACL را تغییر نداده است.

Excel: backend request هیچ COM/export/recalc ندارد. snapshot منتشرشده درمنبع یاstable-reader هماهنگ باlock Writer/Sync،hash،parser-version،data-date وcheck-time می‌خواهد. TTL منبع را ازmtime استنتاج نکنید. درartifact lookup ازPDF نگهداری‌شده استفاده ودرنبود آن unavailable برگردانید؛ساخت PDF جدید با نام «فایل تاریخی» ممنوع.

## Refresh وcache

| رویکرد | ارزیابی | تصمیم |
|---|---|---|
| Polling | قطع/retry ساده،حجم کوچک،مناسب یک مدیر | MVP باETag؛tab مخفی کند/متوقف؛overlap ممنوع |
| SSE | یک‌طرفه وreconnect برایnotification مناسب؛proxy/session باlong connection نیاز تست | بعداًاگرlatency نیاز واقعی شد؛فقطcache version event |
| WebSocket | stateful اتصال دوطرفه،heartbeat/reconnect پیچیده‌تر | نیازMVP ندارد |

PROPOSED budget اولیه،نیازمند benchmark: UI هر۶۰ثانیه؛health cache۳۰ثانیه،ledger۶۰ثانیه،Sync۶۰ثانیه متناسب۲دقیقه،HR jobs۶۰ثانیه،personnel۵دقیقه یاrevision-change،Excelحداقل۵دقیقه/event وهرگز درrequest. snapshot concurrency=1،connect timeout۲ثانیه،حدکلcollector۱۰ثانیه،bounded SQL cursor/page؛snapshot صف‌مانده برایهمانمنبعcoalesce شود. این اعداد نتیجهاندازه‌گیریبارتولیدنیستند.

هرفهرست snapshot_version دارد؛pagination همانversion راpin می‌کند. invalidation باrevision/schema/hash/updated watermarks؛mtime تنهاhint است. auth revoke بلافاصله درهرrequest وdownload بررسی می‌شود،حتیاگرdata cache تازه باشد.

read model تازه ابتداcomplete وvalidate سپسatomic pointer publish؛آخریننسخهسالم رویrestart بازیابی. اگرrefreshfail شد،data قبلی باage/reason/stale برگردد؛بدوننسخهسالم503. API هرsource observed_at خودش راحفظ می‌کند وmixed snapshot وانمود نمی‌کند.

## تاریخ و ساعت واحد

Timestamp واقعی ISO8601 offsetدار؛کلیدdue درUTC؛date عملیاتی Jalali YYYY/MM/DD string باcalendar=persian. DateRange به‌طورپیش‌فرض **تاریخداده** است؛فیلترموعد نام مستقل دارد. targetازoriginaldue درAsia/Tehran یکروزتقویمی قبل گرفته می‌شود؛ازretry/created_at استنتاجنشود.

Backend ازzoneinfo باtzdata بسته‌بندی‌شده وconverter pin‌شده jdatetime استفاده می‌کند. Windows ممکن استIANA داده نداشته باشد؛[zoneinfo](https://docs.python.org/3.11/library/zoneinfo.html) fallback tzdata دارد. HRMS calendar قانون۱۴۰۵/Nowruz را خودش دارد؛نتایج آنراrecalculate نکنید.

Frontend نمایشباIntl.DateTimeFormat('fa-IR-u-ca-persian',{timeZone:'Asia/Tehran'})؛ورودی شمسی native date نیست وبهvalidation backend فرستاده می‌شود. برایdate picker،jalaali-js pin‌شده گزینهconvert است؛[محدوده ورفتار](https://github.com/jalaali/jalaali-js) بایدباbackend درسال‌های پشتیبانی‌شده cross-check شود. محدودهMVP۱۴۰۵؛سال خارج ازقرارداد422. localeمرورگر تاریخ معدن راعوض نمی‌کند. ID/hash وtimestampدرRTL داخلbdi/dir=ltr.

مرزها: ۲۳:۵۹/۰۰:۰۰تهران؛اسفندکبیسه؛پایانماه/سال؛DueهایUTCروزقبل؛DSTتاریخیایران؛۳۶ساعت دقیق و۷روز دقیق؛ساعتScheduleعوض‌شده؛روزگزارش۱۷باdue۱۸؛مرخصی/شیفتشب کهروزکارکردآنباخروجفردایکی‌نیست.

## ظرفیت،backup وخرابی

هدفپذیرش پیشنهادی: p95 پاسخcache کمتر۵۰۰ms برای۵client؛source lock wait بیشتراز۲s نباشد؛peakRAM مجموع API/collector کمتر۵۱۲MiB درآزمونMVP؛page size<=100،bodyفهرست<=1MiB،پنجرهتاریخ<=۹۲روز. اینbudgetها سقفپیشنهادی‌اند وپسازbenchmarkبازبینی می‌شوند؛هیچسنجشruntimeسرویسی دراینممیزی انجامنشده است.

PDF/Excel/HR processing درrequest ممنوع تاباExcel موجود،Gateway وScheduler رقابت ایجادنشود. circuit breaker برایSMB/source repeated failures؛داشبورداصلاًSMBراscanنکند. healthread خودش side effect نداشته باشد.

Backup آینده: config/secrets باACL ونسخهامن؛dashboard auth/audit جدا ازrebuildable cache؛cacheprovenance وpublished manifests؛schema/versionrelease. backupازDBعملیاتیمسئولیتدامنهخودشاست،نهdashboard. restoreدرمسیرجدا،hash/integrity/schema وpermissioncheck؛sessionهایقدیمیinvalidate. cacheپاک‌شده→rebuild محدود،auditپاک‌نشود. RPO/RTO وretention نیازتصمیممالک دارد.

آلارمداخلیUIبرایsnapshotfailure/schema drift/stale/unauthorized file؛هیچارسالBaleاضافیپیشنهادیااجرانشده است. قطعمعدن→degraded+last_verified_at؛HRMSخاموش→healthofflineدرآستانهمصوب،نتیجهتاریخیهمچنانباtimestampموجود؛backendقطع→UIconnectionlost وbadgeقدیمی،بخشHRدرpersistent browser cacheذخیرهنشود.
''')

write('06-UI-UX-PRD.md','نیازمندی محصول و تجربه کاربری فارسی',r'''
## مسئله و کاربران

مدیر باید تاریخ داده را انتخاب کند وبداند چه گزارش/فایلی برای چه کسی با چه مدرکی پذیرفته شده،چرا موردی متوقف است وچه اقدامی مجاز است. یکرنگ سبز کلی یاخطاهایقدیمی تکراری پاسخ نیست. نسخهشخصی manager-only است؛HR ووحدتهابعداً باfield scope اضافه می‌شوند.

MVP-0 mock UI مستقل؛MVP-1 اولیننسخهاستفادهعملیاتی باصفحات A/B/D وdetail G محدود؛صفحهC درنسخهاول فقطread-only case list ساده ونسخهکاملMVP-3. HRMSدرMVP-2؛پرسنلF فقططراحی. H صرفاًتنظیمشخصی. disabled future navigation با برچسب «در مرحله بعد»؛فرم یاaction ظاهراًفعال بدونAPIنسازید.

## قواعد قابل پذیرش UX

- تمامصفحات RTL،lang=fa،فونت محلی،اعدادزمان/تاریخخوانا؛hash/ID/timestamp باLTR وکپی صحیح.
- navigation ثابت؛عنوانصفحه،بازهتاریخداده،zoneتهران وsource/refresh timestamp همواره دردید.
- statusمتن+icon+color؛رنگتنهاحامل معنا نیست. unknown خاکستری باعلت؛uncertain زرد وبدوندکمهretry؛closed archival خنثی.
- شمارنده‌های «نوبت»،«فایل»،«ارسال فایل به گیرنده» برچسب مستقل دارند؛reminder/notice ازreport جدا.
- snapshotتاریخیباتازگیsourceامروز بازتفسیرنشود. closed-ownerبه‌صورتپیش‌فرض ازactive issues خارج اماقابلجست‌وجو باشد.
- loading،verified empty،source unavailable،partial،stale وpermission denied شش حالت مستقل؛empty فقطبعدquery موفق.
- Drawer جزئیاتترتیب علت→شواهد→اثربرگزارش→اقداممجاز→رسید/نسخه؛rawlog یاstacktrace نمایش داده نشود.
- جدولserver-paginated؛موبایل۳۶۰pxاسکرولبخشجدول یاcardsummary وDrawerتمام‌صفحه؛دسکتاپ۱۴۴۰px.
- keyboard navigation،focus trap/return،aria-label،contrast مناسب هردوتم؛نمودارحتماًجدولمتناظر دارد.
- تغییرtheme/ستونها درlocalStorage فقطترجیحات غیرحساس؛هیچHRdata/session/token آنجا نباشد.

## جریان‌های اصلی

۱. ورود →نمایکلیمنابع→انتخاب روزشمسی→فهرست۸Job واجدscope→گسترشartifact→گیرندگان→رسید. درروزتاریخیjobغیرفعالقدیمی نیزفقطاگرثبتوجودداشتهباشد ظاهرشود.

۲. موردنیازمندبررسی→caseجزئیات→علتواقعی+evidence→اقداممتنیمجاز؛درMVPهیچارسال،resolveیاپوشاندنمسئلهرخنمی‌دهد.

۳. HRMS →دورهمحاسبه→runنسخهثابت→known/final/provisional وissues؛بدونثبتتأییداداریازرویظاهر.

۴. قطعbackend →هماننمایآخر بااعلانقطعوعمرsnapshot؛پسازlogin/permissionchangeدوبارهscopevalidated؛اطلاعاتحساسنقشقبلی درDOMباقی نماند.

تعریف دقیقصفحات،sourceها وپذیرش در[Page Specs](06-PAGE-SPECS.md). هیچ داراییگرافیکی،وب‌سایتیاprototypeعملیاتی دراینممیزی ساختهنشده است.
''')

pages=[
('A','نمای کلی مدیریت','MVP-1','overview','K-SCHED,K-SYNC,K-GATEWAY,H-HEALTH',
 '[سایدبار] [تاریخ داده / تهران / آخرین refresh]\n[ارسال با رسید] [فایل آماده] [نیاز بررسی] [خطای فعال]\n[نوبت‌ها: کامل/ناقص/نامعلوم] [Bale / Gateway / Sync / HRMS]\n[موارد مهم امروز] [پیوند گزارش‌های همان تاریخ]',
 'MetricCard,HealthStrip,SourceStamp,CasePreview,CountLegend',
 'تعدادsuccess ازdelivery exact+receipt؛unknown جدا؛همانعددباquery B قابل تطبیق؛HRMS آخرینموفقglobal درنبودjobs برابرnull.',
 'اگرsource unavailable شدcardصفرنشود؛stalebadge وlast_verified_at؛کلیکعددهمانfilters راحفظکند.'),
('B','گزارش‌های روزانه','MVP-1','reports','K-SCHEDULE,K-SCHED,K-ART-MAINT,K-ART-PERF',
 '[تاریخ داده از/تا] [Job] [نوع/Status/گیرنده] [فقط اقدام] [بسته‌ها]\n[نوبت | تاریخ داده | موعد تهران | فایل | گیرندگان | علت | بررسی]\n  > [artifact mechanical] > [گیرنده A:receipt / گیرنده B:pending]\n[صفحه بعد] [زمان snapshot]\n[Drawer:source/pdf/version/attempts/recovery/correction/evidence]',
 'DateRange,ReportTable,ArtifactList,RecipientGrid,Timeline,EvidenceDrawer',
 'کلیدOccurrence ثابت؛موعد/data_date جدا؛office=دوPDF؛notice واردcount گزارش نشود؛hash/ID null باunknown.',
 'fixture یکJob×دوartifact×دوگیرنده عدد۱/۲/۴؛partial→all-sent transition فقطباreceipt؛بسته۱۵/۱۶آبانactiveنباشد.'),
('C','مرکز عملیات','MVP-1 basic / MVP-3 full','operations','K-SCHED,K-SYNC,K-ENTRY,K-MAINT-ENTRY,H-OUTPUT',
 '[مشکل فعال / شدت / تاریخ / دامنه]\n[شرح] → [شواهد] → [اثر] → [اقدام پیشنهادی] → [وضعیت]\n[فیلتر انسانی/فنی/داده/PDF/اصلاحیه] [بسته‌ها جدا]',
 'CaseList,SeverityBadge,EvidenceDrawer,AllowedNextStep',
 'casekey دامنه+occurrence یاsync identity+cause؛attemptهاییکتعارض جدیدissueنسازند؛ownerclosedازactiveخارج.',
 'D23باlocal/minehashوlast_seen بیاید؛متفرقهباسابقهقدیمیجدا؛هیچresolve/retryendpointفراخوانینشود.'),
('D','سلامت سیستم','MVP-1','system/health + sync/status','K-GATEWAY,K-CRON,K-SYNC,H-HEALTH,K-SCHED',
 '[Gateway running] [Bale connected] [Telegram retrying]\n[Scheduler last recorded run / registry evidence]\n[Sync:overall + exact-file guards + last good]\n[HR worker heartbeat] [source age / unknown measurements]',
 'HealthCard,HeartbeatAge,SourceGuard,ReadFailureBanner',
 'online=heartbeatتازه+state/PIDمعتبر؛degraded=retrying/partial/guardblocked؛offlineفقطexplicitdead/agepolicy؛نرسیدنه‌دلیلreadfailure=unknown.',
 'heartbeatمعتبرBale باعثسبزشیدنTelegram/Syncنشود؛cronexit0باhealthpartial سبزنشود.'),
('E','HRMS','MVP-2','hrms/overview,processing,rules,personnel','H-PERSONNEL,H-BOT,H-OUTPUT,H-RULE',
 '[دوره شمسی] [run/version] [revision پرسنل]\n[کل/فعال/بدون گروه] [Import] [processing/form/approval]\n[known vs provisional vs final minutes] [unresolved count]\n[توزیع گروه] [افراد نیاز بررسی] [نسخه قوانین / پوشش ماه]',
 'PeriodPicker,RunSelector,RuleTag,PersonnelSummary,IssueTable,TotalsPanel',
 'readiness/calculation/HRapproval/receiptجدا؛میانrunهاsumنشود؛permissionfieldmask؛canonicalcountبدونDBnull.',
 'G08implementedوG09دستهruleمصرف‌شدهبیاید؛G09تاریخیimportباcurrentcalculatedقاطینشود؛HRviewerبدونحق aggregateحقوقفیلدنگیرد.'),
('F','مدیریت پرسنل آینده','design only / MVP-4','future service-layer commands','H-PERSONNEL',
 '[جست‌وجو / تاریخ مبنا] [پروفایل وhistory]\n[طرح افزودن/اصلاح سمت/نام/گروه تاریخ‌دار]\n[پیش‌نمایش تفاوت + نسخه] [تأیید scoped] [operation result]\n[غیرفعال‌سازی:نیاز قرارداد سرویس]',
 'PersonnelSearch,Profile,RevisionDiff,EffectiveRange,Confirmation',
 'هیچفرمwriteفعالدرMVP0-3؛آیندهprepare/approveهمانسرویسوbase_revision/policyhash؛identityeditعمومینداریم.',
 'stale/concurrentreviewردشود؛requestidempotencyیکrevision؛scopeHR وfieldaccessسروراجبارشود.'),
('G','تاریخچه و Audit','MVP-1 report detail / MVP-3 full','audit','K-SCHED,K-ENTRY,K-MAINT-ENTRY,K-FLEET,H-PERSONNEL,H-BOT',
 '[تاریخ / دامنه / رخداد / شناسه مجاز]\n[رویداد | نتیجه | نسخه | ارتباط با اصل | evidence]\n[recovery / correction / AI commit / source guard / personnel revision]\n[متن خصوصی وsecretها نمایش داده نمی‌شوند]',
 'AuditTable,EventType,VersionLink,RedactedEvidence',
 'sourceauditباdashboardaccessauditجدا؛draft_textبهAPIنیاید؛id lineageحفظ.',
 'نام/کدملی/بلهchattext/tokenدرlogresponseنباشد؛roleمکانیکیHRauditنگیرد.'),
('H','تنظیمات شخصی','MVP-0 local preferences','preferences local / authenticated session','dashboard-only',
 '[روشن/تیره/سیستم] [اعداد/قالب تاریخ] [ستون‌های جدول]\n[Refresh UI دربازه مجاز / source frequency ثابت]\n[timezone عملیات:Asia/Tehran فقط‌خواندنی]',
 'ThemeSwitch,TablePreferences,RefreshSelector,LocalePreview',
 'تنظیمUIبهscheduler/HRMS/Gatewayمنتقلنشود؛refreshUIکمترازminserverباعثsourcepollاضافی نشود.',
 'localsettingsهیچدادهحساسندارد؛browserTZتاریخداده/موعدرانمی‌لغزاند.')
]
body='## مشخصات مشترک\n\nتمام صفحه‌ها شش حالت loading/empty/partial/stale/unavailable/forbidden دارند. empty state فقط query سالم با count=0 است. Wireframeها سند هستند و به داده Production متصل نشده‌اند.\n\n'
for code,name,mvp,endpoint,source,wire,components,mapping,accept in pages:
    body+=f"## صفحه {code} — {name}\n\nمرحله: {mvp}. API پیشنهادی: {endpoint}. Source IDs: {source}.\n\n~~~text\n{wire}\n~~~\n\nComponentها: {components}.\n\nSource mapping و محاسبه نما: {mapping}\n\nمعیار پذیرش: {accept}\n\n"
write('06-PAGE-SPECS.md','Wireframe متنی و قرارداد هر صفحه A تا H',body)

write('07-SECURITY-AND-ACCESS.md','امنیت شخصی، دسترسی خصوصی و RBAC آینده',r'''
## نسخه شخصی

PROPOSED: مدیر با مرورگر واردHTTPS خصوصی می‌شود؛reverse proxy فقطرویIP شبکهخصوصی/VPNموردتأییدlisten کند وFastAPI فقطloopback. مسیر شبکهخانه/WireGuard/روترتاVMهنوزUNKNOWN است؛IP یاportفرضی به‌عنوانمسیرآمادهتحویلنمی‌دهیم. Public hosting وpublic DNS لازم نیست؛گواهیداخلی معتبرمورداعتمادclientلازم است. هیچlistener،Firewallrule،VPNpeerیاaccountدراینممیزی ایجادنشده است.

~~~mermaid
flowchart LR
  Home["مرورگر مدیر"] --> VPN["WireGuard peer / private routed network"]
  VPN --> Gate["Firewall allowlist + HTTPS"]
  Gate --> Auth["Dashboard login + session + permission"]
  Auth --> API["read API / field projection"]
  API --> RO["read-only snapshots"]
~~~

[WireGuard](https://www.wireguard.com/quickstart/) tunnel/peer/route راپوشش می‌دهد؛هویتکاربرورتبهدسترسیبرنامهجداست. TailscaleفعلیمهمانجایاثباتWireGuardمدیرنیست. HTTPS/authحتیدرLAN/VPNپیشنهادپایه‌است.

احرازهویتانتخابیآینده: یکحسابmanagerمحلیباpasswordhashاستانداردوTOTPمحلییاIdPخصوصیموجوددرصورتاثباتدسترسی. secretsدرACLمحدودserver؛هرگزfrontend/envbundle. credential جدیدالانساختهنشود. Session opaque وسمتserver،cookie Secure/HttpOnly/SameSite=Lax،rotationپسازlogin/تغییرنقش،logoutrevoke. پیشنهادtimeout:۳۰دقیقهidle،۸ساعتabsolute؛نیازتصویبمالک. CSRFبرایlogin/logout/هرwriteآینده،rate-limitlogin،originallowlist،CORSsame-origin؛bearerدرlocalStorageنداریم. مبنای[OWASP session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html).

service accountداشبوردفقطreadمنابعمجاز،writeفقطruntime/dashboard؛بدوندسترسیwriteبهFunction/DB/source/Gatewayconfig/token. چونmode=roبرایHRMSدراینمحیطfailشد،قبلproductionبایدowner-producedimmutableexportیاreadonlyinterfaceقابل‌دسترسفراهمشود؛نقش/ACLاصلیگسترده‌نمی‌شود.

## ماتریس آینده

اینماتریس **پیشنهاد** است وgrantفعلیهیچفردنیست. صفحاتودادهومجوزعملیاتهرسهسمتserverفیلترمی‌شوند. نقشmanagerدرنسخهشخصیدادهحساسHRراپسازتصمیمصریحscopeمی‌بیند؛مقادیرحقوقیکهفعلاًمنبعندارندنمایشندارند.

| نقش | صفحات | داده وfield scope | عملیات آینده |
|---|---|---|---|
| مدیر اصلی | A–H طبقمرحله | کلmetadataعملیاتی؛HRaggregate/نتایجمجاز؛کدملیپیش‌فرضmask؛secretهرگز | commandمحدودبامجوزدامنهوتأیید؛نهshell/configآزاد |
| منابع انسانی | E,F,G(HR),H؛AخلاصهHR | پرونده/کارکردscopeمصوب؛نام/تردد/مرخصیضروری؛کدملیفقطpermissionجدا | personnelprepare/approve،uploadمحاسبه/تأییددرقراردادجدا؛کنترلpayrollقانوننداردمگرمصوب |
| مدیر ماشین‌آلات | A,B,C,D,Gعملیاتی,H | fleet/reportهایscope؛هیچpersonnel/HRsalary | recovery/correctionrequestفقطبعدمجوزقابلاثبات |
| مکانیکی | B,C,G,Hبخشخود | artifactmechanical/گزارش‌هایgrantشده؛گیرندگانبدوناطلاعاتاضافی | فرم‌هایهماندامنهباWriter وgrantمستقل |
| آهنگری | B,C,G,Hبخشخود | scheduledreceiveفعلیمعادلgrantdownloadهمهفایل‌هانیست؛filescopeصریحلازم | فعلاًread؛writeفقطباcontract+grantمستقل |
| مشاهده‌گر | صفحه‌هایreadمجاز | aggregate وartifactصریحallowlisted؛HRپیش‌فرضdeny | هیچdomaincommand |

Permissions granular: reports.read،reports.artifacts.download،operations.read،system.health.read،audit.operational.read،hrms.overview.read،hrms.personnel.read،hrms.attendance.read،hrms.identity.sensitive.read،hrms.outputs.download؛writepermissionsبعداًنام‌گذاری/تصویبمی‌شوند. scopeبخش/منبع/فرد/دورههردرخواستبررسیشود. denydefault،fieldallowlistوobjectauthorizationبرایURLمستقیمالزامی. menuمخفیامنیتسرورنیست.

## فایل،log وprivacy

artifact_idopaqueبهcatalogمجازresolve؛کاربرpath/UNC/URLنمی‌دهد. canonicalrealpathوreparsepointcheck،نسخه/hash+permissionدرهرdownload،Content-Disposition attachment،nosniff،no-storeبرایHR؛404برایartifactناموجود/نامجازطبقضدenumerationpolicy،410برایفایلباشناسهقدیمیِشناخته‌شدهاماازبین‌رفته،درنبوداطمینانsource503. downloadهیچفایلیراgenerate/downloadازBaleنمی‌کند.

sourceevidenceبهAPIrawstacktrace،directorylisting،absoluteprivatepath،botfile_id،privatechattext،نامپدر/کدملی/حقوقنمی‌دهد. redactionپیشازlog/cache؛auditفقطactorref،permission،objectref،decision،time،requestid/resultcode. HRexportنیازpermissionجدا+auditدارد. cache/auth/auditنقش‌هاجدا؛sharedpubliccacheبرایHRنداریم.

XSS: sourceproseراHTMLنکنید؛CSPself برایscript/style/font،بدونinlineunsafe؛dependency/fonthashrelease. webhook/externalURLدرreadAPIوجودنداردتاSSRFساختهنشود. Host/originمحدود،session revoke باهرpermissionchange؛downloadsignedURLاگربعداًآمدبایدکوتاهوعینیوابستهبهactor/versionباشد.

## write آینده

فقطAPIرسمیدامنه،expectedversion،scopedactor،validation،concurrency،idempotency،auditوقابل‌اثباتبودننتیجه. عملیاتنامطمئنخودکارretryنمی‌شود. تأییدUIمجوزاضافهازمدل/متنآزادایجادنمی‌کند. rollbackاثرمالی/ارسالاغلب«رویدادجبرانیجدید»است،نهحذفreceiptیاrevision. جزئیاتدر[عملیاتآینده](09-FUTURE-WRITE-OPERATIONS.md).
''')

write('09-FUTURE-WRITE-OPERATIONS.md','امکان‌سنجی عملیات مدیریتی آینده، بدون اجرا',r'''
## طبقه‌بندی

۱=سرویس موجود قابل reuse؛۲=read/transport adapter لازم؛۳=service layer مشترک لازم؛۴=permission/audit تازه؛۵=ناایمن یاخارجدامنهفعلی. ترکیبدسته‌ها براییکعملیاتممکن است. هیچcommandزیرپیاده‌سازی/اجرانشده است.

| عملیات | دسته | backend/callsite موجود | شرط معماری ونتیجه |
|---|---|---|---|
| مشاهده PDF معتبر | ۲ | pinned/manifest +artifact receipt؛فایل‌هایقدیمیگاهیtemporary | GETidopaque؛authorization/hash/path؛نبودفایل410/unknown؛بازسازیندارد |
| مشاهده Receipt | ۲ | receipts/office_artifact_receipts/recovery_operation | projectionmasked؛status+ID+scope؛humanreadunknown |
| بررسی گزارش جاافتاده | ۲،۳ | misfire/recovery review وledger | dry-readdiff؛بدونclaim/tick/hold_backlog؛اگرexpectedsetتاریخیمفقودunknown |
| تأیید Recovery | ۱،۳،۴ | scheduler/recovery_delivery.py وreport_recovery_* | exactoperation/date/recipient/source/PDFhashallowlist،approvalref،preflight،receiptidempotency؛هرگزgenerictick |
| درخواست Correction | ۳،۴ | report_correction_review وsender مستقلتاریخی | reason+originalversion+candidatehash+approval؛اصلreceiptمحفوظ؛requestباsendجدا |
| مشاهده Sync | ۲ | mine-file-sync state +journals | readonly؛cronexitجایhealthنیست |
| بررسی مجدد منبع | ۳،۴ | domainfreshness/preflightخواندنممکناستsideeffectداشتهباشد | commandboundedمستقل؛نهSyncrun/SMBscanازGET؛نتیجهchecktime+sourceversion |
| تنظیم عملیاتی Gateway/Scheduler | ۵ برایMVP؛۴ برایآینده محدود | فایل/CLIهایعملیاتی | noarbitraryconfig/shell؛typedwhitelistproposalباimpactdiff وauthorizationمستقل |
| افزودن پرسنل | ۱،۲،۴ | PersonnelService.prepare/preview/approve | actorازweb→domainmapping؛request_key،base_revision،۳۰دقیقهexpiry،policyhash،همانtransaction |
| اصلاح سمت/نام محدود | ۱،۲،۴ | edit/rename contracts | personnel_idهدفثابت؛national_id/codeعمومیممنوع؛freshreview+approve |
| گروه تاریخ‌دار | ۱،۲،۴ | operation=transfer | بازه/مجموعهگروهکامل؛هم‌زمانی؛revisionنو؛محاسبهگذشتهاجرا/بازنویسینشود |
| غیرفعال‌سازی | ۳،۴ | inactive/ignoreمدلوجوددارد،عمومیserviceعملیاتندارد | employment/processing/authorizationسهتعریفجدا؛تاریخاثر،proof وreactivationcontractابتداتصویب |
| بررسی مغایرت HR | ۲،۳،۴ | outputs/review/identity/source_conflicts | observationباHRdecisionجدا؛resolutionبایدversion-boundدرengineورودیثبتشود |
| مدیریت Import | ۱،۳،۴ | hrms_bale.pipeline.run وingestion | sourceSHA/BIFF/scope/size؛transport-independentrequestservice؛duplicate/conflictnoautowinner |
| محاسبه مجاز | ۱،۳،۴ | batch.run_batch | commandisolatedoutputنسخهجدید،queue/cancellation/recovery،nooverwrites؛GETمحاسبهندارد |
| مشاهده/تأیید خروجی | ۲ برایمشاهده؛۳،۴ برایتأیید | publication_decisionreadinessموجود | approvalworkflow/result_versionجدیدلازم؛ready≠HRapproved≠published≠sent |
| مجوز بله/کاربر | ۱،۳،۴ | Store.access،PersonnelService.role،Komatsoauthstore | owner-only؛هرauthorityمستقل؛webroleبه‌تنهاییبلهgrantندهد |

## الگوی command آینده

prepare(actor,scope,payload,expected_version,idempotency_key) → immutableproposal+diff+expires+approvalbinding → confirm(actor,token,expectedhash) → existingauthorizedservice → durableoperationresult → channel/file/writeevidence.

result باید committed/not_applied/partial/uncertainراصریحبرگرداند؛خطایreceiptپسازcommit باعثcommitدوبارهنشود. همانUUID/fingerprint/transactionمرجعاصلدامنهreuseشود. Action APIداشبوردازwrite مستقیمExcel/SQLiteکاملاًمستقلاست.

نمونهمفهومیfutureendpointها: POST/operations/recovery-proposals وPOST/proposals/{id}/confirm؛POST/hrms/personnel/proposals وPOST/hrms/personnel/proposals/{id}/confirm. این‌هادرOpenAPIفازreadوجودندارند؛نصب/routeکردنآن‌هاقبلMVP-4ممنوع. mockUIنبایدبهسرویسواقعیforwardشود.

اصلاحیه/RecoveryمثلاsuccessپسازsendقابلRollbackبهمعنایپس‌گرفتنفایلازکاربرنیست؛ثبتcorrection/decisionبعدی روشجبرانی‌است. پروندهpersonnelباrevisionجبرانیrollbackمی‌شود؛revisionقبلیپاکنمی‌شود. conflictنسخهیاscopeدرconfirm→409/403؛unknownoutcome→202/uncertainopreference وhumanreview،نهsuccessساختگی.
''')
print('Design documents written')
