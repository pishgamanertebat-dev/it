from build_documents import *
import os
import hashlib

required=[
('DASHBOARD-MASTER-PLAN.md','نقشه اصلی و نقطه ورود'),
('RESUME-PROJECT-LATER.md','شروع دوباره پس از گذشت زمان'),
('CHANGELOG-AND-DECISIONS.md','تاریخچه تصمیم‌ها و وضعیت تصویب'),
('00-EXECUTIVE-SUMMARY-FA.md','پاسخ عملی به ۱۳ پرسش مالک'),
('01-AS-IS-INFRASTRUCTURE.md','زیرساخت،runtime،شبکه و محدودیت دسترسی'),
('02-KOMATSOAI-AUDIT.md','هشت Job،receipt،Sync،Entry و سایر ماژول‌ها'),
('03-HRMS-AUDIT.md','HRMS،G01–G09،پرسنل و عملیات'),
('04-DATA-SOURCE-CATALOG.md','فهرست منابع و روش خواندن امن'),
('04-DATA-DICTIONARY.json','۲۶ منبع وschema ستون‌های۸۴جدول'),
('04-SOURCE-OF-TRUTH-MATRIX.md','منطق قطعی وضعیت‌ها و اولویت تعارض'),
('05-ARCHITECTURE-BLUEPRINT.md','معماری،تاریخ،refresh،عملکرد وbackup'),
('06-UI-UX-PRD.md','نیازمندی‌های محصول و جریان کار'),
('06-PAGE-SPECS.md','wireframe وپذیرش صفحات A تا H'),
('07-SECURITY-AND-ACCESS.md','شبکه خصوصی،Auth،RBAC وprivacy'),
('08-API-CONTRACT.md','۱۷Endpoint وقرارداد رفتاری'),
('08-OPENAPI-DRAFT.yaml','OpenAPI3.1،۳۶Schema،design-only'),
('09-FUTURE-WRITE-OPERATIONS.md','طبقه‌بندی وشرایط commandهای آینده'),
('10-TEST-AND-ACCEPTANCE-PLAN.md','fixture/testهاینتیجه‌محور ونتایجتاریخی'),
('11-IMPLEMENTATION-ROADMAP.md','تسک‌ها،MVPها،deploy/rollback وپیچیدگی'),
('12-RISK-REGISTER.md','۲۱ریسک باevidence،پیشگیری وrecovery'),
('13-OPEN-QUESTIONS.md','دروازه‌های تصمیم مالک/زیرساخت/HR'),
('14-SOURCE-EVIDENCE-INDEX.md','شاخص شواهد،کد،hash وtimestamp'),
('15-SELF-AUDIT.md','کنترل کیفیت،پوشش ومواردPARTIAL/UNKNOWN')]
index='\n'.join(f"- [{title}]({name})" for name,title in required if name!='DASHBOARD-MASTER-PLAN.md')

write('DASHBOARD-MASTER-PLAN.md','نقشه اصلی داشبورد مدیریتی KomatsoAI و HRMS',r'''
## تصمیم قابل شروع

این بسته مرجع **ممیزی فقط‌خواندنی و طراحی ساخت** برای شرکت صنعتی معدنی پیشگامان ارتباط هشت بهشت است. ساخت dashboard هنوز شروع نشده است. هدف، دید تاریخ‌محور و قابل دفاع از گزارش‌ها،فایل‌ها،گیرندگان،تازگی منابع و وضعیت HRMS است؛موتورهای موجود مرجع محاسبه و اجرا می‌مانند.

نسخه نخست برای مدیر و شبکه خصوصی طراحی شده است. طرح منتخب پیشنهادی: HTML/Tailwind/Vanilla ES Modules/Vite/Chart.js با asset محلی،FastAPI وadapterهای مستقل،cache نسخه‌دار وsession سمت server. پروژه آینده در E:/ManagementDashboard مستقل از دو دامنه قرار می‌گیرد. این اسناد به دلیل محدوده filesystem در E:/KomatsoAI/reports/management-dashboard/audit-20261111 ذخیره شده‌اند؛این محل،تصمیم ادغام محصول نیست.

## واقعیت‌هایی که طراحی را تعیین می‌کنند

۱. هشت Job فعال از Registry فعلی وگیرندگان واجد شرایط استخراج شد. زمان‌هایتهران: سرریز۰۷:۴۵،رانندگان۰۹:۰۰،تعمیرات۱۲:۰۰،موازنه/عملکرد۱۵:۰۰. تاریخ داده روز قبل due اصلی است. نبود داده۳۶ساعت وReplayفنی۷روز دو سیاست مستقل‌اند.

۲. موفقیت Run،پذیرش بله،اعتبار محتوای PDF و مشاهده انسانی چهار ادعای جدا هستند. data model باید occurrence،artifact وrecipient delivery را جدا بشمارد. legacy proof گاهی ناکافی است؛unknown بخشی از قرارداد محصول است.

۳. چهار recovery باIDهای7417–7420 واصلاحیه‌هایدفتر7347/7348 شواهد مستقل دارند. ۱۵/۱۶آبان خارج پیگیری مالک‌اند. نیاز بررسی محتوا برای پیام اولیه7321 باقی است. receiptهای تاریخی پاک/بازنویسی نشده‌اند.

۴. Sync اکنون guard تعارض705/D23 دارد؛آخرینscan کامل partial بوده وlocal/mine version متفاوت‌اند. این وضعیت،نبود گزارش۱۹آبان را اثبات نمی‌کند؛تعمیرات درsnapshot scheduler بهsource_stale/retry_wait رسیده است.

۵. G08 اکنون implemented و۹کد scope دارد. G09 جاری ازrules خانواده8 وپروفایل تاریخ‌دار محاسبه می‌کند؛importWisman در نسخه‌های تاریخی باقی است. هرmetric باید ازresult همانrun/rule_version بیاید؛داشبورد هیچ فرمول حقوقی تازه‌ای ندارد.

۶. canonical personnel درrevisionهای SQLite وBLOB جفت people/assignments است؛خواندن people.xlsx فعلی به‌تنهایی authority نیست. live read دو SQLite HRMS دراینمحیطباSQLITE_CANTOPEN ممکن نشد؛schema آن‌ها ازکد وoutcomeهایذخیره‌شده جدا علامت‌گذاری شده است.

۷. ظرفیت اولیه VM سبک مناسب به نظر می‌رسد:۸logicalCPU و۱۹GiB RAM باحدود۱۰٫۶GiB آزاد درنمونه. مسیرVPNمدیر/ثبتزنده taskها/بارآینده UNKNOWN است؛هیچپورتیاخدمتی راه‌اندازی نشد.

این‌ها snapshot زمان ممیزی‌اند؛شروع چندماه بعد به R0-REFRESH نیاز دارد،نه اعتماد خودکار بهایننسخه.

## Source of Truthهای اصلی

| موضوع | مرجع | شرط استفاده |
|---|---|---|
| schedule/موعد | settings/schedules.yaml وruns اصلی | current registry را تاریخچه کامل ندانید |
| پذیرش فایل بله | office_artifact_receipts یاreceipts report باID وproof_scope | دقیقاً همانartifact/recipient/version؛human_read unknown |
| recovery/correction | chain مستقل درoperation/receipt/artifact وreview linkage | اصلreceipt حفظ؛نوبت دوباره شمرده نشود |
| سلامتSync | mine-file-sync.json،guard وjournal hash |cronexit،mtime یاlastsuccessقدیمی کافی نیست |
| ذخیرهEntry | edits.operation/status/before/after وWriter outcome |Draft/textok یاپیام،اثباتcommit نیست |
| personnel | personnel.sqlite3 revision +as_of projection |livehead/readonlyinterface بایدقابل‌خواندن باشد |
| HRMS اعداد | manifest/content_id+daily/monthly/publication همانrun |known/provisional/final وapproval جدا |
| HRMS تحویل | bot.sqlite3 outbox receipt |ready/complete/form بی‌برچسب،sent یاapproved نیست |

ماتریس کامل وضعیت وpriority در [04](04-SOURCE-OF-TRUTH-MATRIX.md) قراردادی است که backend/UI باید رعایت کنند.

## تصمیم‌های مالک و پیشنهادهای این ممیزی

تصمیم‌های صریح مأموریت: readonly audit،حفظ production،عدم ساخت سرویس/نصب/ارسال/محاسبه مجدد؛هدف مدیرمحور،پیش‌بینی HR/RBAC آینده،استقلال موتورها،استقرار داخلی و پرهیز ازReact اجباری/CDN. تصمیم‌های تاریخی ۱۵/۱۶آبان وEntryجدید تنهادرحد sourcecode/evidence حفظ شده‌اند؛اینممیزی مجوز عملیاتی تازه‌ای ازآن‌ها استخراج نکرده است.

انتخاب FastAPI/Vanilla/polling،TTLها،بودجه منابع،طرحAuth وترتیب جزئی rollout پیشنهاد فنی‌اند. IP/port/certificate،حسابAuth،HRfieldscope،retention/RPO/RTO وactionهایآینده هنوز نیازتصمیم دارند. [Decision log](CHANGELOG-AND-DECISIONS.md) جایفرضیاتوتصویب‌ها را مشخص می‌کند.

## دامنه مرحله اول و آینده

MVP-0: shell فارسی RTL،theme،date،componentها وMock بدونsource. MVP-1: اولین نسخه عملیاتی،Authمدیر،A/B/D،caseهایخواندنیساده وGdetail؛گزارش روزشمسی،artifact/recipient،علت،رسید،recovery/correction،Sync/Gateway وrefreshکنترل‌شده. هیچwrite بهدامنه،ارسال یااجرایRetry ندارد.

MVP-2: HRMSreadonly باrevision/joblineage وscopeداده؛MVP-3: مرکزعملیات/historyکامل؛MVP-4: فقطcommandهایتصویب‌شده ازserviceهایاصلی؛MVP-5: چندنقش باobject/fieldpermission. React فقطدرصورتنیازواقعیstateفرم‌ها بازبینی می‌شود.

## وابستگی ودروازه‌های شروع

قبلMVP-0: R0-REFRESH،repoمستقل،lockfileمحلی وfixture. قبلMVP-1: interfaceامنreadledger،projectorproof،artifactresolver،Auth/TLS وprivate route،schema/versiontest وcapacitybudget. قبلMVP-2: رفعgapخواندنHDBباinterface/snapshotرسمی،canonicalrevision،jobslineage وHRprivacy.

ریسک‌هایاصلی: legacyreceiptناقص،WAL/Excellock،driftقانون/مستندات،Syncconflict،بازسازیPDFتاریخی،fieldleak وpermissioncache،تداخلVM وUTC/Jalali. sourcewrite/send/restart برایکاهش اینریسک‌ها دراینممیزی انجامنشده است.

## فهرست کامل تحویل

'''+index+r'''

فایل‌های evidence: inventory شاملschema،hash،WinAPI/network،metadataworkbook وcollectorهایaudit است؛sanitized فقطfact/aggregate/metadata مجاز؛test-references فقطreference ونتیجه تاریخی. هیچDBخام،workbookپرسنل یاprivatechattext اینجا ذخیره نشده است.

## روش شروع در آینده

ابتدا [Resume](RESUME-PROJECT-LATER.md)،بعدExecutive/SourceMatrix/OpenAPI وRoadmap. تسک R0-REFRESH،سپس MVP0-01. قراردادهایunknownوproofscope را ازروز اولUI/API نگه دارید تاconnectشدنمنبع باعث تغییرمعنایمحصولنشود.

## وضعیت نهایی این مأموریت

- DASHBOARD MASTER AUDIT COMPLETE — بسته مستندات کامل؛پوشش شواهد زنده به‌صراحت PARTIAL/UNKNOWN درموارد دسترسی.
- KOMATSOAI & HRMS SOURCE CONTRACTS DOCUMENTED — live schema دوDB HRMS تأیید نشده؛DDL کد جداست.
- SECURE LOCAL ARCHITECTURE DEFINED — طرح آماده؛مسیرVPN/Auth/TLS وdeploy هنوز اجرا/تأییدعملیاتی نشده‌اند.
- IMPLEMENTATION BLUEPRINT READY — شروعMVP0 وMVP1 بدونحدس دربارهsemantics؛گپ‌ها ودروازه‌ها مشخص‌اند.
- PRODUCTION UNCHANGED — فعالیت اینممیزی فقطread وwrite همینپوشه است؛backgroundهایعادیسامانه متوقفنشده‌اند وادعایثباتتمامstateزندهدرطولممیزی نداریم.
''')

write('00-EXECUTIVE-SUMMARY-FA.md','جمع‌بندی مدیریتی و پاسخ به۱۳پرسش',r'''
## نتیجه اصلی

ساخت داشبورد داخلی با رابط ساده فارسی و API مستقل از نظر معماری امکان‌پذیر است و ظرفیت اولیه VM برای یک سرویس سبک مناسب به نظر می‌رسد. استفاده عملیاتی هنوز به آزمون بار،احراز هویت،HTTPS خصوصی واثبات مسیر شبکه نیاز دارد. ساخت داشبورد در این مأموریت انجام نشده است.

۱. **هاست جدید لازم است؟** الزام فنی به هاست عمومی نداریم. VM فعلی۸پردازنده منطقی و۱۹GiB RAM دارد؛درمشاهدهحدود۱۰٫۶GiB آزادبود. این feasibility اولیه است؛SLA/بارواقعی وroute مدیر تأیید نشده‌اند.

۲. **HTML/Tailwind/Vanilla کافی است؟** برایMVPخواندنی کافی است. ESModules،componentهای مشترک وstate محدود برنامه‌ریزی شده‌اند. Reactمزیت اجباری فعلی ندارد؛Preact/React درصورت پیچیدگیواقعی فرم‌هایآینده بازبینی می‌شوند. Vite فقطbuild؛همهassetها محلی.

۳. **KomatsoAI چه چیز آماده نمایش دارد؟** registry هرهشتJob،موعد/data date،Run/receipt،فایل‌های دفتر،گیرندگان،Recovery/Correction،guardهایSync،healthGateway/Bale/Telegram وmetadataEntry. endpointداشبورد فعلاًوجودندارد؛adapter/projection لازم است. دادهfleet/NET قابلگسترش است ولیفازاولreadreportمحورمی‌ماند.

۴. **HRMS چه چیز آماده دارد وچه چیز ناقص است؟** کد/خروجی‌هاینسخه‌دارگروه‌ها،daily/monthly metrics،publication/readiness،شواهدImport،فرم‌ها وserviceپرسنلی موجودند. G08فعال است؛G09جاریباrules خانواده8 محاسبه می‌کند وimportویسمن تاریخی است. canonicalcounts/jobs جاریبه‌دلیلSQLITE_CANTOPEN اینمحیطتأییدنشدند. workflowعمومی تأیید نهایی مالی وهمهعملیاتغیرفعال‌سازی هنوزسرویس/قراردادآینده می‌خواهند.

۵. **داده تاریخ/گیرنده برایهمه گزارش‌ها کافی است؟** براینمایقابل‌استفادهبله؛برایاثبات کامل همهartifactهایهمهتاریخ‌هاخیر. برخیreceiptهای قدیمیID/hash/recipientsetکاملندارند. نسخهجدید بایدunknown/proofscopeنشان دهد؛فایل یاارسال تازهبرایپرکردنhistoryنسازد.

۶. **Source of Truth مشخص است؟** بله،ماتریس رسمی برایstatusهایارسال،Sync،HRMS وEntry نوشته شد. run.succeeded یاoutboxready باreceipt قطعی برابر نیست؛receipt نیز خواندن توسطانسانرااثبات نمی‌کند.

۷. **Adapter وEndpoint لازم چیست؟** Ledger/Registry،SourceHealth،ArtifactResolver ونسخهبعدHRPersonnel/HRProcessing. MVP-1 از/overview،/reports وdetail/deliveries/artifacts،/artifacts/{id}/content،/sync/status،/system/health و/me استفاده می‌کند. مجموعطرح۱۷Endpointخواندنی و۳۶Schema دارد؛هیچکدامنصبنشده‌اند.

۸. **خطرها چیست؟** Excellock،WAL/readpermission،legacyreceiptناقص،Syncconflict،تغییرقانون،PII،تداخلVM وتاریخ. مسئلهفعلیقابلاثباتSync: شیت705/سلولD23؛تعمیراتdueامروزدرsnapshot source_stale بوده است. اصلاحمنبعیاretryازdashboardپیش‌فرضنداریم.

۹. **دسترسی شخصی WireGuard چگونه باشد؟** tunnelخصوصی→Firewallallowlist→HTTPSخصوصی→login/session/permissionمستقل→APIloopback. رویاینVMرابطTailscaleمشاهدهشد؛WireGuardend-to-endمدیرهنوزاثبات نشده است. VPNبه‌تنهاییgrantمدیریت نیست.

۱۰. **ورود منابع انسانی چه می‌خواهد؟** RBACصفحه/داده/فیلد/عملیات،sessionمستقل،maskاطلاعاتهویتی،downloadمجوزدار،audit،revocation/cacheinvalidation وapprovalهایresult-bound. دسترسیمستقیم بهسورسوفایل‌ها بهکاربرلازم نیست.

۱۱. **اولینMVP دقیقاً چیست؟** MVP-0mockRTL/theme/date/components؛اولیننسخهعملیاتیMVP-1 باAنمایکلی،Bگزارش‌ها،Dسلامت،Ccaseساده،GdetailوHترجیحات. تاریخداده/موعد،دوPDFدفتر،گیرندگان ورسید،علتخطا،recovery/correction،staleوunknownحتماًهستند.

۱۲. **چه تصمیم‌هایی باز است؟** مسیرVPN/TLS/Auth،scopeاطلاعاتHR،retention/RPO/RTO،رفعinterfaceخواندنیHRMS،artifactretention/recipienthistoricalproof،تصمیممستقل705/D23 وreviewپیام7321. فهرستمسئول/زماندر[پرسش‌ها](13-OPEN-QUESTIONS.md) است؛تصمیم‌هایداده‌ایباز مانعساختMockنیستند.

۱۳. **سهماه بعد ازکجا شروع کنیم؟** از[RESUME-PROJECT-LATER](RESUME-PROJECT-LATER.md)،سپسR0-REFRESH در[Roadmap](11-IMPLEMENTATION-ROADMAP.md)،بعدMVP0-01. commit/schema/rules/registry/recipients/health/freshnessهمهتازهبررسیشوند؛اینsnapshotراcurrentفرضنکنید.

دراینممیزیcodeProduction،Excel/DB/receiptاصلی،مجوز،TaskScheduler،Firewall وVPNتغییرنکرد؛هیچارسال،Restart،نصبdependencyیاخدمتیساختهنشد. کنترلپایانی ومواردتأییدنشد‌ه در[Self-audit](15-SELF-AUDIT.md) و[Source index](14-SOURCE-EVIDENCE-INDEX.md) است.
''')

write('RESUME-PROJECT-LATER.md','دستور شروع پروژه پس از گذشت زمان',r'''
## ترتیب مطالعه

۱. DASHBOARD-MASTER-PLAN وExecutive؛۲. Source-of-Truth Matrix ودوAudit؛۳. Data Dictionary/OpenAPI؛۴. Security/Test/Roadmap؛۵. OpenQuestions/DecisionLog. تماماسنادازMasterlinkدارند. اینبسته snapshot۱۴۰۵/۰۸/۲۰است؛مقادیرسلامت/گیرنده/نسخهقانونبرایروزشروعاعتبارخودکارندارند.

## R0-REFRESH: بدون تغییر Production

۱. تاریخUTC/تهران،مسیرواقعیE:/KomatsoAI،E:/HRMS،E:/Function وHermesراverify؛AGENTSروز را بخوانید. scopeمجوزروزساخت/استقرار راازاینpacketاستنتاجنکنید.

۲. KomatsoAI HEADممیزی=75b712cd531bc08aed566014e9a1d7e2e0d15e69. gitstatus/log وdiffفقطخواندنیبااینcommit؛نامrepository/branchعوضشدههمثبتشود. SHAفایل‌هایindex رادوبارهبگیرید. generatedreportهاباsourceکداشتباهنشوند. commitبررسی‌شده راinstalledکلprocessفرضنکنید.

~~~powershell
git -C E:\KomatsoAI status --short
git -C E:\KomatsoAI diff --name-status 75b712cd531bc08aed566014e9a1d7e2e0d15e69 -- tools/scheduler tools/fleet integrations/hermes settings
~~~

۳. HRMSGitندارد؛ازhashهایcode-index وsource-files،rules/policy.json،catalog،scopedruleها،manifestنصب/آخرینrun استفادهکنید. hashکدengine باoutputsمقایسه؛inputs/rulesتاریخ‌دارنسخهروز را جدا بخوانید. configتاریخیبامرجعفعالrulesاشتباهنشود.

۴. schemaدوباره: K-SCHEDتمامruns/receipts/artifacts/recovery/holds/closures/corrections/alerts؛K-AUTHversion/extensions/registration؛fleetworkorder/finalcopy؛entrydraft/edit/journal؛H-PERSONNELrevision/proposal وH-BOTjobs/outbox. user_versionصفرهممی‌تواندschemaبزرگیمتفاوتداشتهباشد؛DDLfingerprint/columns/PKلازم است.

۵. HRreadonlyreadمشابهSQLITE_CANTOPENشد،همانمحدودیتثبت؛immutableرویlive/rawDBcopy/ساختshm/restartبرایبازکردنقفلنکنید. ازowner-producedpublishedsnapshotیاinterfaceخواندنیجدااستفادهکنید؛تولیدexportازGETsideeffectدارد.

۶. Registryفعلیراازsettings/schedules.yaml وcron/jobs.jsonبخوانید؛۸Jobبودنرابه‌عنوانfactقدیمیبدانید. timezone/start_date/misfire/recipientscopeراverify؛expectedsetگذشتهازرسید/رزروتاریخیاست،نهgrantامروز. تغییرساعتنبایدdueدومبسازد.

۷. samplesجدیدreceipt/sent-without-ID/officeartifact/recovery/correction/ownercloseddateراcheck؛عملیات7417–7420historicalreceiptهستند،مجوزتکرارنیستند. حفظمرزnotice/report وhumanreadunknownالزامی.

۸. Gateway/HRhealth+PIDidentity،Syncstate/guard+lastcomplete،fileretained/hash،WAL/lock،resources/listeners/Firewall/private routeرابه‌روزکنید. دیگر705/D23 یاTelegramretryingراخطایقطعیروزآیندهفرضنکنید. بررسیWireGuardخانهتاVMازمالکمسیرصحیحمی‌خواهد؛کلیدخوانده/ذخیرهنشود.

۹. تغییرهادرCHANGELOG/ADRجدید وdelta.jsonذخیره؛برایناشناختهstatusUNKNOWNباsource/date. تست‌هایقراردادراابتدابافیxtureجدااجراکنید؛sourceproductionنهtesttargetاست ونهمحلتولیدfixtureحساس.

## قابل استفاده بدون ممیزی مجدد

اصولاستقلالموتورها،سهواحدشمارش،receipt≠humanread،unknown/null،fieldRBAC،noCDN،preserveraw/history وnoimplicitwriteطولعمرطراحیبالادارند. Wireframeساختاریوtestscenarioهاقابلreuseاند. فناوری/نسخهdependency واندازهcache/TTLنیازبازبینیهزینهودسترسی‌دارند.

## نیازمند Freshness Check

هرcommit/loadeddeployment،DDL،rowcount،فعال‌بودنJob،recipient/role،release rule/personnelrevision،statehealth/sync،retainedfile،path/SMBroute،port/firewall/certificate،runtime/dependency وآخرینخروجیواقعی بایدتازهاثباتشود. اسمفایلقدیمییاگزارشسبزگذشته،حقیقتامروزنیست.

## اولین تسک توسعه بعد ازR0

MVP0-01: ساختrepositoryمستقلdashboardباmockfixtureوbuildمحلی،بدوناتصالبهproduction؛سپسMVP1-01Auth وMVP1-02snapshot/projector. HRMSمستقلبرایMVP2می‌آید؛هیچفرمولاضافه‌کار/کسرکاردرUI/APIنویسی نشود. همهدروازه‌هایdeployرویکاندیدreviewable وtestشدهبررسیشوند؛اینممیزیخودمجوزdeployنیست.
''')

decisions=[
('D01','2026-11-11','CONFIRMED_USER_SCOPE','فقطread audit/design؛فقطپوشهگزارشwrite','خطقرمزصریحمأموریت','ساختسرویس/نصب/ارسال/restartردشده'),
('D02','2026-11-11','AUTHORIZED_FALLBACK','اسناددرreports/management-dashboard؛محصولآیندهE:/ManagementDashboard','workspacewriteمحدود؛کاربرfallbackمجازکرده','ساختالانخارجworkspaceیاادغاممحصولردشده'),
('D03','2026-11-11','PROPOSED','VanillaESModules/Tailwind/Vite/Chart.js','readheavy۸صفحه،بدوننیازاثبات‌شدهReact','Reactالزامیرد؛Preactگزینهآینده'),
('D04','2026-11-11','PROPOSED','FastAPIمستقل+snapshotworker+cache','typedcontract/جداسازیdomain/readload','داخلHermes/HRMSprocessرد؛Flaskممکناماهرآداپترمستقل'),
('D05','2026-11-11','PROPOSED','Polling۶۰ثانیهUI؛sourceTTLمتناسبهزینه','سادهبرایmanager؛sourceI/OازUIجدا','WebSocketبی‌نیاز؛SSEبهتعویق'),
('D06','2026-11-11','VERIFIED_CONSTRAINT','receipt-artifact-recipientproof وسهواحدشمارش','legacyrun/sentنمونهناکافی؛officeدوPDF','سبزکردنباrun.succeededرد'),
('D07','2026-11-11','VERIFIED_FINDING','G08implemented؛G09currentcalculated/version-dependent','کد،catalogوtestsreferenceامروز','یادداشتقدیمیpending/import-onlyردبراینسخهجاری'),
('D08','2026-11-11','PARTIAL_ACCESS','HRliveDBunavailable→code-onlyschema/countunknown','SQLITE_CANTOPEN؛عدم‌دورزدنWAL/ACL','rawcopy/immutablelive/sidecarcreationرد'),
('D09','2026-11-10','VERIFIED_EXISTING_POLICY','۱۵/۱۶آبانخارجپیگیری؛چهارrecoveryمستقل','owner_history.py وreceiptchain۴عملیات','ارسال/بازگشاییخودکاررد؛اینممیزیgrantتازهندارد'),
('D10','2026-11-11','VERIFIED_EXISTING_WORKFLOW','Entry:تأییدگزارش→review،تأییدمتن→commitهماننسخه','installedmanifest39892553وکدبرابر','confirm_writeقدیمیرد؛قراردادقدیمیبهامروزتعمیمندارد'),
('D11','2026-11-11','PROPOSED','privateHTTPS+login مستقلVPN،manager-onlyMVP','شبکهخصوصیبهمعنیمجوزappنیست','publichostingاجبارندارد؛VPN-onlyauthرد'),
('D12','2026-11-11','OPEN_OWNER_DECISION','retention/RPO/RTO/HRscope/Authroute','scopeامنیتوبکاپنهاییبهنیازمالکبسته‌است','عددزمانیغیرمستند/retention۷روزرد'),
('D13','2026-11-11','PROPOSED','MVP0→1operationalread→2HR→3cases→4actions→5roles','کاهشریسکproof/sideeffectsقبلwrite','شروعازaction/تغییرقانون/payrollرد')]
write('CHANGELOG-AND-DECISIONS.md','تاریخچه و ثبت تصمیم‌های معماری',r'''
وضعیت PROPOSED تصمیم تصویب‌نشده این ممیزی است؛VERIFIED_EXISTING_POLICY تنها وجود سیاست قبلی را از کد/شاهد تأیید می‌کند و مجوزاجرای دوباره نیست. اینpacket اولینversion مستندات باschema audit-1 است.

| ID | تاریخ | وضعیت | تصمیم | دلیل/منشأ | جایگزین یا حدردشده |
|---|---|---|---|---|---|
'''+ '\n'.join('| '+' | '.join(d)+' |' for d in decisions)+r'''

درشروعآیندهتغییرتصمیمباADRجدید+دلیل+اثر+statusثبتشود؛ایننسخهتاریخینبایدبازنویسیبی‌مدرکشود. رخدادproductionعادیطولممیزیبه‌عنوانتغییراینpacketثبتنمی‌شود.
''')

code=read('inventory/code-index.json')
selected=['tools/scheduler/runner.py','tools/scheduler/misfire.py','tools/scheduler/office_receipts.py','tools/scheduler/office_delivery.py','tools/scheduler/recovery_review.py','tools/scheduler/recovery_delivery.py','tools/scheduler/daily_summary.py','tools/scheduler/delivery_alert.py','tools/scheduler/owner_history.py','tools/scheduler/report_errors.py','tools/authorization/store.py','tools/fleet/maintenance_daily/prepared.py','tools/fleet/maintenance_daily/delivery.py','tools/fleet/maintenance_daily/report.py','tools/fleet/oil_balance/report.py','tools/fleet/machinery_performance/delivery.py','tools/fleet/repairs/entry_ai_bale.py','tools/fleet/repairs/entry_draft.py','tools/fleet/repairs/entry_service.py','tools/fleet/repairs/sync_service.py','tools/fleet/repairs/maintenance_sync.py','hrms/personnel.py','hrms/personnel_display.py','hrms/engine.py','hrms/g09_overtime.py','hrms/rulebook.py','hrms/publication.py','hrms/policy_store.py','hrms/batch.py','hrms/cli.py','hrms/export.py','hrms_bale/store.py','hrms_bale/pipeline.py','hrms_bale/personnel_ui.py']
code_rows=[]
for row in code:
    p=row['path'].replace('\\','/')
    if any(p.endswith('/'+s) for s in selected):
        anchors=row.get('anchors',[])[:6]
        symbols='؛ '.join(a['symbol']+':'+str(a['line']) for a in anchors)
        try:
            link=os.path.relpath(row['path'],ROOT).replace('\\','/')
            label=f"[{p}]({link})"
        except ValueError:label=p
        code_rows.append(f"| {label} | {row['sha256'][:16]}… | {symbols} |")
evidence_rows=[]
for folder in ['inventory','sanitized','test-references']:
    for p in sorted((EV/folder).glob('*')):
        if not p.is_file() or p.suffix not in ['.json','.txt']:continue
        h=hashlib.sha256(p.read_bytes()).hexdigest()
        rel=p.relative_to(ROOT).as_posix()
        evidence_rows.append(f"| [{p.name}]({rel}) | {h[:16]}… | {folder} |")
write('14-SOURCE-EVIDENCE-INDEX.md','شاخص شواهد و منشأ ادعاها',r'''
## روش وکیفیت

خواندن filesystem/code/metadata،Online Backupبهحافظهباreadonlyconnection،aggregateهایبدونمتن/پرسنل،allowlistJSON،بررسیhash/receiptchain. کداپلیکیشنimport/executeنشد؛فقطکتابخانه‌هایتحلیلدرمفسرپروژهبا-B استفاده شدند. scriptهایauditداخلهمینپوشهقابل‌بازبینی‌اند؛اجرایمجددآن‌هاشواهداینsnapshotراoverwriteمی‌کند،پسبرایauditجدیدپوشهتاریخدارجدا بسازید.

VERIFIED=شاهد مستقیم مشخص،CODE_VERIFIED=ساختار/رفتار کد خوانده‌شده،PARTIAL=بخشی ازlineage یاactivation ناقص،UNKNOWN=خواندن/اثباتممکننشده،PROPOSED=طراحیآینده. نتیجهتستذخیره‌شدهhistorical است. SchemaversionهایSQLiteدرinventoryواقعی‌اند؛schemaهیچDBمنبعmigrateنشده است.

## ادعاهای اصلی → شاهد

| ادعا | شاهد مستقیم | محدودیت |
|---|---|---|
| هشت Job فعال وگیرندگان واجدشرایط | scheduler-facts +hashYAML +joinثبتapproved/private | current recipient set؛گذشتهfreezeکاملنیست |
| چهار recovery7417–7420 | scheduler-facts.recoveries receipt_chain_matches=1 |پذیرشکانال؛نهخواندنِانسان/نمایشnativePDF |
| correctionدفتر7347/7348 وreview7321 | independent-artifact-receipts +corrections |فایلنخستاختصاصی7321hashندارد |
| تعارض705/D23 | sync-facts guard +entry-journal-summary last_seen/hash |منبعSMBدراینممیزیدوبارهخواندهنشد |
| G08فعال/G09جاری/versionهایآبان | rule-contracts/catalog +models/engine/rulebook |liveheadextra_rulesهمهافرادتأییدنشد |
| liveHDBقابل‌خواندننیست | database-schemas error SQLITE_CANTOPEN |علتقطعیOS/WALتشخیص داده نشد |
| HRنتیجهذخیره‌شده وکدبرابر۴۰/۴۰ | hrms-saved-runs+output-summary |آخرینjobواقعیglobalبدونDBاثباتنشد |
| Entryنصبفعلیباmanifestبرابر | entry-install-comparison+installed-code |تستخصوصی/commitزندهتازهنداشتیم |
| hardware/network | infrastructure-native،network-processes،firewall |بارpeak،TaskSchedulerزنده،ESXiversion وrouteخانهUNKNOWN |

## فایل‌های Evidence

hashکاملکددرcode-index وhashفایل‌هادرartifact-inventoryپایانیثبتمی‌شود. جدولزیرموجودیدرزمانتدوینشاخصاست؛validation/self-auditپایانیبعدازاینمرحلهاضافهمی‌شوند.

| فایل | SHA256 prefix | گروه |
|---|---|---|
'''+ '\n'.join(evidence_rows)+r'''

## نقاط کلیدی کد وLine Anchor

شماره خطوط متعلق بهSHAثبت‌شده‌اند. code-index.json همهsymbolها/خطوط را دارد؛براینسخهآیندهخطثابتراauthorityمطلقندانید. لینک‌هایEdriveنسبی‌اند؛مسیرHermes رویCدرindexماشین‌خوانثبتاست.

| مسیر | SHA256 prefix | نمونهsymbol:line |
|---|---|---|
'''+ '\n'.join(code_rows)+r'''

## منابع رسمی بیرونی بررسی‌شده

مستندات بیرونی فقط برای تصمیم فناوری/ایمنی خواندن استفاده شدند و جایگزین شاهد محیط کاربر نیستند:

- [SQLite WAL](https://sqlite.org/wal.html)،[Online Backup](https://www.sqlite.org/backup.html)،[Python sqlite3](https://docs.python.org/3.11/library/sqlite3.html)
- [Vite](https://vite.dev/guide/)،[Static build](https://vite.dev/guide/static-deploy)،[Tailwind Vite](https://tailwindcss.com/docs/installation/using-vite)،[Chart.js](https://www.chartjs.org/docs/latest/getting-started/integration.html)
- [FastAPI deployment](https://fastapi.tiangolo.com/deployment/concepts/)،[React state](https://react.dev/learn/managing-state)،[Preact guide](https://preactjs.com/guide/v10/getting-started/)
- [zoneinfo](https://docs.python.org/3.11/library/zoneinfo.html)،[Intl.DateTimeFormat](https://developer.mozilla.org/en-US/docs/Web/JavaScript/Reference/Global_Objects/Intl/DateTimeFormat)،[jalaali-js](https://github.com/jalaali/jalaali-js)
- [WireGuard](https://www.wireguard.com/quickstart/)،[OWASP Session](https://cheatsheetseries.owasp.org/cheatsheets/Session_Management_Cheat_Sheet.html)

## محدودیت‌های صریح

هیچline-by-lineتطبیقتمامWordpolicyباهمهپرونده‌ها انجامنشد؛هیچtestsuiteاصلییاسرویسجدیدrunنشد؛هیچnativeExcel/PDFrender جدید انجامنشد؛محاسبهکارکردروینتیجهعملیاتیاجرانشد؛خواندندادهحساسبهمدلواقعییاپیامبیرونیصورتنگرفت. فایل‌هایliveزمانممیزیباbackgroundعادیسامانهتغییرمی‌کنند؛اینpacketادعایglobalatomicstateندارد.

readWMI/TaskSchedulerناموفقبه‌صراحتثبتشده؛روش‌هایجایگزینفقطreadonlyبودند وpermission/systemconfigتغییرنکرد. هیچSecret/DBخام/HRrecordخامکپی نشده؛schemaوhashاطلاعاتحساسخامنیستند،اماoperationalrecipientIDsداخلیبایدبراینسخهعمومیredactشوند.
''')

self_rows=[
('۱','تمام۸Jobفعال','VERIFIED','YAML+jobreceipientfacts؛قراردادهرخانوادهدر02'),
('۲','هرstatusمنبعومنطققطعی','VERIFIED DESIGN / PARTIAL HISTORY','04matrix؛legacyproofناقصunknown'),
('۳','Occurrence/Artifact/Recipient','VERIFIED DESIGN','کلید،aggregation،۱/۲/۴،proofscope'),
('۴','استقلالمحاسباتHRMS','VERIFIED','هیچمحاسبهاجرا/تعریفنشده؛outputprojection'),
('۵','G08/G09ازکد','CODE VERIFIED / TESTS HISTORICAL','فعال‌بودنکدوقاعدهتأیید؛suiteتازهنداشتیم'),
('۶','حفاظتاطلاعات','VERIFIED COLLECTION / FUTURE TESTS','rawDB/HRchatمنتقلنشد؛scanنهایی+RBACplan'),
('۷','معماریبدونReact','VERIFIED DESIGN','Vanilla/React/Preacttradeoffs وmigrationgate'),
('۸','استقرارداخلیبدونهاستعمومی','PARTIAL FEASIBILITY','hardwareواقعی؛capacitydeploy/routeهنوزآزمودهنشده'),
('۹','WireGuardوAuthمستقل','VERIFIED DESIGN / UNKNOWN ROUTE','طرحsecurity؛WireGuardend-to-endنامعلوم'),
('۱۰','همهصفحاتبهsource/gapوصل','VERIFIED DESIGN','A–Hsource/component/wireframe/acceptance'),
('۱۱','عملیاتwriteفقطآینده','VERIFIED','جدول09؛OpenAPIهمهGET؛هیچservice ساختهنشد'),
('۱۲','TestPlanنتیجه‌محور','VERIFIED DESIGN / NOT RUN PRODUCT','DC/HR/SEC/REL/TZ/UXباانتظاردقیق؛dashboardنداریم'),
('۱۳','قابلیتادامهچندماهبعد','VERIFIED DESIGN','Master/Resume/Decision/Roadmap+hashbaseline'),
('۱۴','ریسک/تصمیمبازپنهاننیست','VERIFIED','۲۱risk و۱۳question باowner/gate'),
('۱۵','هرادعایمهمشاهد','VERIFIED WITH EXPLICIT GAPS','14codeindex+26sourcecatalog؛unknownهاصریح'),
('۱۶','عدمproductionmutation/send/service','VERIFIED AUDIT SCOPE','فقطreadوwriteپوشهاسناد؛sourcehashنهایی+actionlog'),
('اضافی','liveHRDBschema/count','UNKNOWN','SQLITE_CANTOPEN؛DDLکدجایlivecountنیست'),
('اضافی','تمامartifactهایقدیمی/hashهایPDF','PARTIAL','retention/recipientproofهمهتاریخ‌هاکاملنیست'),
('اضافی','Windows task registrations/ESXi hostversion','UNKNOWN','queryزندهناموفق؛savedXML/code/ledgerفقطشاهدبخشی'),
('اضافی','nativeOffice/HRfinalapproval/UAT','NOT RUN / UNKNOWN','اینممیزیreadonlyاست؛ظاهرformapprovalنیست')]
coverage=[
('۰','حدودreadonly','Master/14/actionlog'),
('۱','موجودیوزیرساخت','01+inventory'),
('۲','KomatsoAI۸Job وDelivery','02+04matrix+schedulerfacts'),
('۳','HRMSگروه/پرسنل/handlers','03+rulecontracts+codeschemas'),
('۴','Catalog/Dictionary/SoT','04 سهفایل'),
('۵','Architecture/Refresh','05'),
('۶','A–HWireframes','06دوفرایل'),
('۷','تاریخوزمان','05datecontract+10TZtests'),
('۸','Security/RBAC','07'),
('۹','API','08دوفرایل+examples'),
('۱۰','Futureoperations','09'),
('۱۱','Performance/Backup','05capacity+11deploy/rollback'),
('۱۲','Test/Acceptance','10'),
('۱۳','MVP0–5','11'),
('۱۴','Reuse/resume','Master+Resume+Decisions'),
('۱۵','Risks','12'),
('۱۶','Filepackage','Masterindex+artifactinventory'),
('۱۷','Selfaudit','15+validation')]
write('15-SELF-AUDIT.md','کنترل کیفیت خود ممیزی و مرز اطمینان',r'''
بسته اسناد کامل شده است؛این به معنی اثبات کامل تمام داده‌های زنده یا اجرای dashboard نیست. جدول وضعیت‌ها تعهدی برای رفع خارج‌دامنه ایجاد نمی‌کند.

| بند | کنترل | نتیجه | شاهد/حد |
|---|---|---|---|
'''+ '\n'.join('| '+' | '.join(r)+' |' for r in self_rows)+r'''

## ردیابی همه فازهای درخواست

| فاز | موضوع | خروجی |
|---|---|---|
'''+ '\n'.join('| '+' | '.join(r)+' |' for r in coverage)+r'''

## کنترل فنی بسته

نتیجه ماشین‌خوان نهایی در [validation.json](evidence/inventory/validation.json) قرار دارد. لینک‌ها،JSON/YAML،OpenAPIrefs وrequiredfiles بایدpassشوند؛وجودPartial/Unknownیافتهعمداًfailureکنترلکیفیتنیست. اگرsourcecodeدرطولممیزیعوضشدهباشد،code_driftدرvalidationبایدنمایان‌شود،نهسبزپوشاندهشود.

فهرست نهایی فایل‌ها وSHA256 در [artifact-inventory.json](evidence/inventory/artifact-inventory.json) است؛فهرست خودش وvalidationبرایاجتنابازhashچرخه‌ای ازselfhashخارج‌اند. [audit-actions.json](evidence/inventory/audit-actions.json) مرزفعالیترا ثبت می‌کند.
''')
print('Master/index/resume/executive/self-audit written')
