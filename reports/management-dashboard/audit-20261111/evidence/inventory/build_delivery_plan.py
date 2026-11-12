from build_documents import *

write('10-TEST-AND-ACCEPTANCE-PLAN.md','برنامه آزمون و معیار پذیرش قابل سنجش',r'''
## آنچه این ممیزی آزموده و آنچه اجرا نکرده است

VERIFIED در این مأموریت: استخراج پنج SQLite با اتصال readonly و Online Backup در حافظه؛quick_check نسخه حافظه=ok؛مقایسه receipt chain چهار recovery؛مقایسه hash نصب Entry باکد جاری؛خواندن JSON/schema/catalog و metadata محدود Excel. هیچ پردازش HRMS،تست native Office،transport واقعی،Replay،Backfill یا Write به منبع انجام نشد.

تست نهایی این بسته مستندات: parse JSON/YAML،حل تمام refهایOpenAPI،سازگاری نمونه‌های کامل باschema،وجود تمام deliverableها،لینک‌های نسبی،source-ID mapping،پوشش۸Job/۸صفحه/۹گروه،برابری hash کدهای audit‌شده باپایان،اسکن الگوهایsecret و self-audit. خروجی validation.json همراه evidence index است. این‌ها جای acceptance سرویس ساخته‌نشده را نمی‌گیرند.

## شاهدهای آزمون قبلی

| شاهد ذخیره‌شده | نتیجه ثبت‌شده | حد استناد |
|---|---|---|
| runtime/two-step-entry-20261111/acceptance.json | ۱۶۱،صفر failure/error/skip وبدونnetworkattempt | آزمون تاریخی مسیر Entry؛در اینممیزی دوباره اجرا نشد |
| ready-delivery-20261110/acceptance.json وreport-fa | ۳۵۱/۳۵۳،دوfailure پایه NET؛nativeOffice تأییدنشد | کلsuite سبز نیست؛شاهدبودن فایل امروز تأیید محتوا/نصب جدید همه مسیرها نیست |
| HRMS G03_CUTOFF_20261108 | focused۸۲ وBale۹۳ موفق؛baseline۱۰۶۲ با۲failure/۹error/۱skip؛candidateکامل نشده | قواعد بعدی آبان/همهپرونده‌های واقعی را تأییدنمی‌کند |
| تست‌های G08/G09،personnel،misfire/receipt immutability | source آزمون موجود وhash ثبت شده | وجود تست معادل اجرایموفق امروز نیست |

[فهرست hash ونتایج تاریخی](evidence/test-references/test-inventory.json).

## محیط آزمون آینده

repo/venv مستقل داشبورد؛fixtureهای مصنوعی،DB جدا باهرschema تاریخی،copies immutable بدوننام/کدملی/متن خصوصی. transport،Writer وHRMS mutation درtest process deny شوند؛هر network بیرونی fail test. SQLite writer مصنوعی برایconcurrency؛Excel مصنوعی/کپی مجاز بامنفذ write فقطtemp. آزمون nativeOfficeصرفاًدرمسیرجدا وباworkerمهارشده،نهworkbook جاری. هیچ fixture از متن بله بهمدل واقعی نمی‌رود.

## ماتریس correctness

| ID | setup/ورودی | انتظار دقیق |
|---|---|---|
| DC01 | run=succeeded،هیچreceipt | status unknown/proof run_only؛success count=0 قابل اثبات؛unsent قطعی ادعا نشود |
| DC02 | sent+ID exact recipient/artifact | sent_with_receipt؛human_read=unknown؛۱delivery |
| DC03 | office۱occurrence،۲PDF،۲recipient | counts=۱/۲/۴؛بدوندوبرابرشمردن aggregate |
| DC04 | فقط۳از۴delivery sent | primary partial،sent_count=۳،pending=۱؛occurrence complete=false |
| DC05 | generic sent بدونID،دوartifactreceiptقطعی | artifactهاsuccess؛receiptgenericفاقدIDبه‌تنهاییخطایجدیدنباشد |
| DC06 | uncertain یاsending interrupted | noauto-retry؛rawevidenceحفظ؛nextstep بررسی |
| DC07 | recovery operation/receipt/artifact/hash/approvalهمهبرابر | originaloccurrenceیکبار+recoveredbadge؛chainعدم‌تطبیق→unknown/conflict |
| DC08 | correction اولیهsent ونسخهجدیدsent | اصلID/hashمحفوظ؛revision جدا؛humanreadهنوزunknown |
| DC09 | داده۱۷آبان،due۱۸آبان،retry۲۰آبان | filter data_date۱۷؛موعداصلی۱۸؛attempt۲۰؛سهتاریخجابه‌جانشوند |
| DC10 | acceptedsource+fresh exact-file no rows | waitingتاdue+36h؛درمرزclosed_expiry؛دادهبعدیrestoreخودکارندارد |
| DC11 | local stale ومعدنقطع | source_stale/unavailable؛DATA_NOT_READY/اتهامعدم‌ثبتنباشد |
| DC12 | artifactready معتبر،shareقطع | readyfactباقی؛downloadنسخهپین‌شدهباhash؛منبعliveبازنشود |
| DC13 | pinnedPDFhash/content mismatch | artifact invalid/terminal؛no blindretry/exportfallback |
| DC14 | مالک۱۵/۱۶آبانclosed،receiptنامطمئنقدیمی | activecaseصفر برایآنclosedcase؛archiveباproofuncertaintyمحفوظ |
| DC15 | recipientroleامروزافزوده،سابقهتعهدناقص | تاریخیexpectedunknown؛نوبتمفقودبرایگیرندهجدیدنساز |
| DC16 | schemaقدیمیnotice/reportبدونتفکیک | parserنسخه‌دار؛نامعلومsafe؛legacy noticeموفقگزارشموفقنشود |
| DC17 | چندصفحهسرریزمکانیکیفقطIDتجمیعی | proofscopeaggregate؛تعداد/هویتartifactتاریخیunknown،صفحهجعلیساختهنشود |
| DC18 | schedulerclockتغییر،dueقبلیهمانروز | occurrenceاصلیحفظ؛یکتعهدجدیدنه |
| DC19 | missingdate درjobبعدازstart_date | فقطروزهایواقعاًفعال؛قبلstartdate تعهدجعلیندارد |
| DC20 | slotreminder sent،reportpending | reportsuccess=0؛notice/remindercountجدا |

## HRMS

| ID | setup | انتظار |
|---|---|---|
| HR01 | G01–G09 fixtures باexactrule/revision | valueهایAPIباخروجیموتوربرابر؛هیچفرمولدرadapter/browser |
| HR02 | G08۱.۲.۱ وfixtureقدیمیpending | جاریimplemented،تاریخیهمانوضعیتpin‌شده؛fixedwork570 وnonpayableextraازنتیجهصرفاًنمایش |
| HR03 | G09historicalauthorized وcurrent8calculated | authorityجدا؛Wismanباcurrentمحاسبهجمعنشود؛ازنامگروهفرمولحدسنزن |
| HR04 | مهر/آبانboundary وpersonal scopedrule | انتخابازmanifestهمانrun؛catalogامروزگذشتهراتغییرندهد |
| HR05 | personactive/inactive/ignore،aliasوmixedgroups | uniquepeople وmembershipcountجدا؛statusپردازش≠استخدام |
| HR06 | jobsready/outboxuncertain/HRapprovalnot_recorded | processingcomplete،deliveryuncertain،HRتأییدنشدهنمایشمجزا |
| HR07 | نقصpunch/unknownstatus/zero/blank | nullمعنایمجهول؛جمعknownباfinalقاطینشود؛صفرصریححفظ |
| HR08 | sameperson/periodدرچندrun وduplicatedupload | selectedversionمشخص؛sumacrossrunsممنوع |
| HR09 | liveDBunavailableاماoldmanifestموجود | canonicalpeoplecount=null؛آخرینپردازشglobalنامعلوم؛savedrunباtimestampمجاز |
| HR10 | DBreadconstructor/exportsideeffect | اجرایGETنبایدفایلبسازد،roleupsertکندیامحاسبهکند؛filewriteguardfailtest |

## امنیت

SEC01 تمامURLهابدونsession→401؛SEC02 نقشنامجاز/APIمستقیم→403/404بدونfieldleak؛SEC03 expired/revokedcookieرد؛SEC04 rolechangeباcachewarm/downloadURLهمچنانdeny؛SEC05 pathtraversal/UNC/reparse/objectIDدیگررد؛SEC06 secret/PIIدرHTML،JSON،log،source-mapوبدونمجوزdownloadنباشد؛SEC07 writer/networkexportsدرMVPfail؛SEC08 XSSsourceprose inert؛SEC09 sessioncookie/CSP/CSRFlogin/logout؛SEC10 permission-scopeدرETag/cachekey؛SEC11 rate/pagination/filterinjection؛SEC12 browserpersistentcacheبدونHRdata.

## پایداری وکارایی

REL01 قطعمعدن/Syncpartial→lastgood+stale؛REL02 Gatewayقطع/TelegramretryingباBaleجدا؛REL03 Excellocked→unknownوnoCOM؛REL04 SQLiteconcurrentwrites→consistentsnapshotونبودlockطولانی؛REL05 snapshotbudget/timeout→lastgood یا503؛REL06 backendrestart→atomiclastvalidcache،auditمحفوظ؛REL07 cursorرویsnapshotold→409؛REL08 malformedJSON/schemaunknown→unknownنهempty؛REL09 HRMSخاموشاماoutputsقدیمی→healthجدا؛REL10 rawWALcopyهرگزخواندهنشود؛REL11 ۵client و۱۰۰rowpage درbudgetپیشنهادیp95/RAM؛REL12 noforegroundSMB/fullExcelread.

زمان: TZ01 نیمه‌شبتهرانبابرانیtimezoneآمریکا؛TZ02 آخرماه/اسفندکبیسه؛TZ03 ۳۶ساعتدقیق/۷روزدقیق؛TZ04 dueUTCدرروزمختلف؛TZ05 شیفتشبروزخروجمتفاوت؛TZ06 تاریخخارج۱۴۰۵ردصریحبدونfallback.

## UX ودروازهتحویل

UX01 PersianRTL وبیدیID؛UX02 Jalalifilterbookmark؛UX03 دوتم+contrast؛UX04 keyboard/focusdrawer؛UX05 موبایل۳۶۰/دسکتاپ۱۴۴۰؛UX06 largepaginationباستونfixedlabel؛UX07 unknown/unavailable/stale/emptyکاملاًمتمایز؛UX08 اعدادunitدار۱/۲/۴؛UX09 receipt≠humanreadمتنواضح؛UX10 noactionbuttonدرreadMVP.

قبلMVP-1: همهDC/SEC/REL/TZ/UXمربوطموفق،هیچproductionwrite/networksend،مقایسهread-onlyfixture+نمونهواقعیمجازبافقطreceiptmetadata،تأییدمالکمشاهدهUI،آزمونstartup/shutdownonlyسرویسداشبوردجدا. قبلMVP-2 HR01–10+liveDBreadinterface+HRfieldsapproval. nativeOfficeوfullsuiteهایاصلیاگرخارجدامنه‌اندصریحnot_runبمانند؛تعدادهایتاریخیدوبارهجمعنشوند.
''')

write('11-IMPLEMENTATION-ROADMAP.md','برنامه اجرا از MVP-0 تا چندکاربره',r'''
## نقطه شروع

اولین تسک در شروع توسعه، R0-REFRESH است: commit/hash/schema/registry/receipt/freshness نسبت به این ممیزی دوباره بررسی شود؛سپس MVP0-01 ساخت shell مستقل با fixture مصنوعی. اجرای این roadmap اکنون مجاز نیست؛این مأموریت صرفاً طراحی است.

| تسک | ورودی/خروجی قابل تحویل | وابستگی | پیچیدگی / خطر | معیار پذیرش |
|---|---|---|---|---|
| R0-REFRESH | diff اینpacket باکد/Schema/وظایفروزساخت؛ثبتdelta وdecisionها | اسنادresume وevidence | متوسط / drift | نوبت/authorityتازه؛unknownهاصریح |
| MVP0-01 | repoمستقل،frontend/Vite/Tailwind،lockfile،RTL/sidebar/theme | R0 | کم / محیطbuild | offlinebuild وبدونCDN |
| MVP0-02 | moduleهایcomponents/pages/state/api-client باfixtureهای۱/۲/۴ | MVP0-01 | متوسط / state | A/B/D/H،detailG،stale/unknown UX |
| MVP0-03 | dateinput/format،tablepagination،keyboard/mobile | MVP0-02 | متوسط / تاریخRTL | TZ/UXموفق؛صفراتصالsource |
| MVP1-01 | backend/venv/Auth/session/field permissions مستقل | MVP0 contracts | متوسط / access | SEC01–12؛MVPmanager-only |
| MVP1-02 | DeliveryLedgerReader+legacy fingerprint+readmodelprojector | refreshedKschema | زیاد / رسیدناقص | DC01–20؛historicalrecipientunknownمحفوظ |
| MVP1-03 | SourceHealthReader/Sync/Gateway +atomiccache | MVP1-01 | متوسط / stale | تفکیکpartial/attempt_running/outage؛RELtests |
| MVP1-04 | ArtifactResolver+recovery/correctionlineage | MVP1-02 | زیاد / wrongversion | فقطhashمجاز؛410گم‌شده؛noexport |
| MVP1-05 | اتصالA/B/D+casebasic+Gdetail؛polledAPI | MVP1-01–04 | متوسط / UIcount | اولینخروجیعملیاتی؛date/recipient/proofقابلپیگیری |
| MVP1-06 | benchmark،startup/backup/rollback،UATشخصیمدیر | MVP1-05 | متوسط / VMco-location | سقفمنابع وnoimpact؛استقرارفقطبعدتأییدمحدوده |
| MVP2-01 | HRreadonlysnapshot/interface،revisionprojection،joblineage | MVP1 ورفعHreadgap | زیاد / PII/WAL | canonicalcountواقعی،fieldscope،noexportsideeffect |
| MVP2-02 | HRProcessingReader+RuleReleaseview+E | MVP2-01 | زیاد / قانونتاریخی | HR01–10؛noformula؛noHRapprovalfake |
| MVP2-03 | HRformdownloadمجاز+privacyUAT | MVP2-02 | متوسط / فایلحساس | SECfield/download،hash/provenance |
| MVP3-01 | casecenterکامل、dedupcauses、auditfilter/history | MVP1 وMVP2 | متوسط / staleopenissues | ownerclosed/journalattemptdedup؛C/Gکامل |
| MVP4-01 | typedcommandservicecontracts/domains | readversionsstable | زیاد / sideeffects | [write matrix]؛permission/version/idempotency/uncertain |
| MVP4-02 | scopedrecovery/correctionprepare-confirm | MVP4-01 | زیاد / غیرقابلبرگشتبودنsend | exactallowlist+رسیداصلی؛nocrossscope |
| MVP4-03 | HRpersonnel/importcommands+result-boundapproval | MVP4-01 وHRcontracts | زیاد / payroll/identity | concurrentstale409؛revisionaudit؛rollbackcompensation |
| MVP5-01 | RBACچندنقش،HR/unitonboarding،scope/field/data/download | securitymatrixapproved | زیاد / exposure | objectaccesscrossrole باcachewarm؛revocation |

## بسته نسخه‌ها،استقرار وRollback

| نسخه | دامنه دقیق / API | روش استقرار آینده | Rollback | سطح خطر وپیش‌نیاز |
|---|---|---|---|---|
| MVP-0 | پوستهA/B/D/H،mockC/G؛F/Eدرطراحی؛APIواقعیندارد | staticartifactمحلیبدونsource/VPN/portجدید | بازگشتبهstaticreleaseقبل | کم؛R0 وbuildمحلی |
| MVP-1 | managerAuth،/overview،/reports/detail/deliveries/artifacts،/content،/sync/status،/system/health،/me؛Cbasic/Gdetail | سرویسجداreadonly،HTTPSخصوصیپسازgate،یکAPIworker/collector؛readonlyACLیاsnapshotرسمی | stopفقطdashboard،atomicreleasepointerقبل،cachecompatible؛sourceهرگزrestoreنشود | متوسط؛snapshot/proof/auth/VMtest |
| MVP-2 | E،/hrms/* وfilepermissionمخصوص | adapterflagoff→fixtureacceptance→readonlyproductiondata؛browserdataHRno-store | HRfeatureflagoff+releaseقبل؛source/HDbبدونتغییر | متوسط-زیاد؛canonicalrevision/jobsreadگپحلشود |
| MVP-3 | C/Gکامل،/operations،/audit،advancedfilters | readmodel/scopeنسخهجدیدباcachemigrationدرstorageخود | برگشتschemacacheقابل‌بازسازی؛auditappendمحفوظ | متوسط؛cause/closedpolicyverifiable |
| MVP-4 | فقطcommandهایصریحتصویب‌شده،Fعملیاتی | هرcommandجدا،dry-readproposal،محدودallowlist،kill-switchفقطdomainaction | غیرفعالکردنactionAPI؛outcomeهایcommit/sentراunwindنکنید؛compensationجدید | زیاد؛authorization/Audit/confirm/receiptقبولشده |
| MVP-5 | چندکاربرهHR ووحدتها | rolloutنقش‌به‌نقشباfield/objectmatrix؛accountsessionجدا | revokeدسترسیجدید/خاموشfeature؛historicalauditحذفنشود | زیاد؛privacy وrevocationendtoend |

طولزمان روز/هفتهارائه نشدهچونbenchmark/تیم/زیرساختشبکههنوزمعیننیست. پیچیدگینسبیرویproofمفقود،legacySchema،HRrevisionوجریانwriteاست،نهتعدادصفحاتUI. Reactپیش‌نیازهیچMVPنیست؛درMVP-4اگرcomplexstateواقعیایجابکردADRبازبینی‌شود.

معیارخروجMVP-1: مدیرروزشمسیراانتخابکند،نوبت/فایل/گیرنده،علت،ID/hashموجود،Recovery/Correction وخطایSyncراببیند؛هیچsendیاwriteازآنرخندهد. ایناولیننسخهقابل‌استفادهاست،نهMVP-0mock.
''')

risks=[
('R01','WAL/live DB read ناممکن یاsnapshot ناسازگار','اثبات‌شده برایدسترسی H؛احتمالخطرفیزیکی درطراحی','زیاد','SQLITE_CANTOPEN دوDBHRMS','readonly interface/Online Backup رسمی؛raw copy ممنوع','schema/read failure+snapshot age','last good stale یاunknown؛رفعinterface درمنبع','OPEN'),
('R02','قفل/رقابت Excel با Writer/Sync','اثبات‌شده سابقه نزدیک','متوسط-زیاد','آخرینfault_syncfailure 11/11 lock؛Excelprocessوجوددارد','no live Excel per refresh؛lock-aware snapshots','reader error/journal mismatch','snapshot قبلی باbadge؛دست‌کاریlock ممنوع','MITIGATION DESIGNED'),
('R03','قطع SMB وlocal stale به‌اشتباه missingdata','اثبات‌شده نوسان اتصال','زیاد','Sync recovery/outage history وsource_stale امروز','exact-file source proof؛محدودنبود داده۳۶h ازفنیجدا','health/status+guard/accepted hash','نمایشlastgood؛human review؛replayخودکارdashboardندارد','OPEN'),
('R04','تعارض تاریخی705/D23','اثبات‌شده جاری','زیاد','Sync guard +failurejournal local78fe91/minec1ff0d','fail-closed؛هر دوسnapshotحفظ','identity/reason/last_seen','review مستقل مالک؛اینممیزیاصلاحنکرد','OPEN'),
('R05','میراث متفرقه درلیست خطایجدید تکرار شود','اثبات‌شده سابقه،نمایشغلططراحی‌محتمل','متوسط','failure rows قدیمی+guard فعلی متفاوت','case dedup وlatest guard/closed disposition','مقایسهactive identity باlast scan','تاریخچهجدابدونپاک‌کردنjournal','MITIGATION DESIGNED'),
('R06','sent/run success بدون proofقابل دفاع','اثبات‌شده','زیاد','126genericreport sent فاقدID؛artifactproofممکن','artifact/recipientproofscope؛unknown support','proof-gap query+reconciliation','بررسیسوابق؛نهresendکور','OPEN'),
('R07','تاریخچهexpected recipients ناقص','اثبات‌شده محدودیتschema','زیاد','current registry وlegacyreceipts فاقدfreeze کامل','future owner-domain snapshot؛عدمbackfillحدسی','recipient_set_provenance=unknown','نمایشdenominatorunknown؛scopedecisionمستقل','OPEN'),
('R08','receipt/hash artifact قدیمی درlog/temporaryازبینبرود','اثبات‌شده مورد7321؛عموممحتمل','زیاد','correction needs_content_review وcachetemporary','retained immutable artifact registryبرایآینده','retained/hash/content flags','410/unknown؛generateنسخهجعلی‌ندارد','OPEN'),
('R09','uncertain resend تکراری','اثبات‌شده uncertainty،تکرارفرضی','زیاد','4reportuncertain؛state immutable logic','noauto-retry؛operationallowlist وidempotency','exact receipt/fingerprint','بررسیانسانی؛اصلreceiptمحفوظ','MITIGATION DESIGNED'),
('R10','HRMS source/rule drift','اثبات‌شده','زیاد','G08implemented/G09calculated برخلافیادداشتقدیمی؛pending_decisionsstale','version-bound result+rules/source hash','manifest/code/catalog diff','refresh audit؛domainauthority review','OPEN'),
('R11','اطلاعات ناقصHRMS بهجمع صفر/approved تبدیل شود','اثبات‌شده قابلیتnull؛نمایشغلطفرضی','زیاد','publicationnot_recorded؛incomplete/known/final fields','noformula؛nullوprovisionalجدا','HR fixture وcontract validations','رفعsource/HRdecisionدردامنه؛نتیجهجدید','MITIGATION DESIGNED'),
('R12','افشایHRdata/کدملی/حقوق درAPI/cache/log','محتمل طراحی','زیاد','DBBLOB/outputs حاویPII؛فعلاًگپیندیده‌ایم','field/object RBAC،no-store،ACL،redaction','negative roletests+payload scans','revoke/sessionrotation+incident audit','MITIGATION DESIGNED'),
('R13','VPNبه‌تنهاییمجوزمدیریت تلقی شود','محتمل طراحی؛مسیرواقعیUNKNOWN','زیاد','Tailscaleinterface؛WireGuardend-to-endاثباتنشد','HTTPS+login+permission مستقل','VPNclientبدونsessionباید401بگیرد','مسیرخصوصی/credentialrevokeطبقrunbook','OPEN'),
('R14','فشارVM/ExcelUIexport','محتمل','متوسط-زیاد','۸vCPU/۱۹GiB وExcel/Hermesهم‌مکان؛benchmarkنداریم','singleworker،cache،boundedreads،noCOM','p95/RAM/I/O/lock wait','خاموشفقطdashboard؛lastgoodsourceبدونتغییر','BENCHMARK REQUIRED'),
('R15','cache درrestart گم یاmixed snapshot منتشر شود','فرضی طراحی','متوسط','سرویسهنوزساختهنشده','atomicversionpublish+lastvalidpersist+per-sourcewatermark','crash-injection/partialwrite','discardincomplete؛rebuild محدود؛auditحفظ','MITIGATION DESIGNED'),
('R16','CDN/اینترنتbuild/runtime dependency','محتمل','متوسط','networkprivateوrequirementsآینده','vendoredrelease/font/chart+lockfile؛runtimeoffline','offlineUI acceptance','rollbackbuiltartifact؛بدونinstallتولید','MITIGATION DESIGNED'),
('R17','تاریخUTC/Jalali وdue/retry قاطی شود','محتمل بااثرعملیاتی','زیاد','ledgerUTC،گزارشJalali،Asia/Tehran','datecontract+converterpin+fixedzone','TZfixtures وoriginaldue assertions','projectioncorrectversion؛receiptاصلحذفنشود','MITIGATION DESIGNED'),
('R18','constructor یاGET بهمنبع بنویسد','اثبات‌شده درAPIهایمنبع،استفادهنادرستمحتمل','زیاد','Store.__init__ DDL/upsert؛PersonnelService.export creates snapshots','no app constructors؛readonly SQL/pureprojectioninterface','filesystem/DBwrite guard tests','stopdashboard؛audit source mutation if any','MITIGATION DESIGNED'),
('R19','نصب/کدلودشده وکدرویdiskیکسانفرضشود','اثبات‌شده محدودیت','متوسط-زیاد','manifest اولیهHRMSبعضیhashهافرق؛loaded memoryhashUnknown','component deploymentmanifest+PIDstart+provenance','drift job+code hash','مصوبهreleaseجدید؛اینممیزیrestartندارد','OPEN'),
('R20','تغییرpermission باcachewarm باعثدسترسیقدیمی شود','محتمل','زیاد','futureRBAC چندنقش','authrecheckeveryrequest/download؛cachekeyscopeversion','SEC04/10 directURL','invalidate session/cache role mapping','MITIGATION DESIGNED'),
('R21','open_decisionsتاریخی بهتعهدمالیجدیدتبدیلشود','اثبات‌شده drift محتوا؛اثرطراحی‌محتمل','زیاد','G05grace/G09Friday درpendingfile باreleaseجدیدهم‌پوشانی','source matrix وdecisionversion؛review نهautoapply','comparedecisioncatalog/results','رفعناسازگاریفقطدردامنهHRMSباتصمیممستقل','OPEN')]
table='\n'.join('| '+' | '.join(r)+' |' for r in risks)
write('12-RISK-REGISTER.md','دفتر ریسک با شاهد و مسیر بازیابی',r'''
احتمال این جدول کیفی است و نرخ آماری حادثه اندازه‌گیری نشده است. «اثبات‌شده» به شاهد امروز/سابقه مشخص اشاره دارد؛«محتمل» ارزیابی طراحی و«فرضی» سناریوی آزمون است. OPEN به معنی درخواست اجرای رفع در اینممیزی نیست.

| Risk ID | شرح | احتمال/نوع شاهد | اثر | شواهد | پیشگیری | تشخیص | بازیابی | وضعیت |
|---|---|---|---|---|---|---|---|---|
'''+table+r'''

مرجع شواهد: [source matrix](04-SOURCE-OF-TRUTH-MATRIX.md)،[source index](14-SOURCE-EVIDENCE-INDEX.md)،[Gateway/Sync](evidence/sanitized/sync-facts.json) و[HR runtime](evidence/sanitized/hrms-runtime.json).
''')

questions=[
('Q01','مالک/شبکه','مسیر خانه مدیر تا VPN وVM کجاست؟ WireGuard رویروتر است یامهمان؟','طراحی منطقی آماده؛IP/route/TLS ناشناخته','پیشMVP1 deploy'),
('Q02','مالک','نام خصوصیسایت،گواهی مورداعتماد،حسابmanager وTOTP/IdP کدام است؟','فقطشبکهخصوصی+احرازهویت مستقل','پیشAuth activation'),
('Q03','مالک/HR','manager دقیقاًکدام داده‌هایشخصی/کارکرد را می‌بیند؟کدملی وحقوق چهscopeدارد؟','mask/deny حساس؛aggregateاولیه','پیشMVP2'),
('Q04','مالک','مدت نگهداریhistory/cache/artifact/accessaudit،RPO/RTO چقدر باشد؟','retentionنامحدود ادعا نمی‌شود؛۳۶h/۷روزنهretention','پیشbackup/deploy'),
('Q05','HRMS maintainer','چگونهH-PERSONNEL/H-BOT درحسابread-onlyبدونside effectخوانده شوند؟','unknown/null live count؛صادرکنندهرسمیimmutableیاreadAPI','پیشMVP2؛مانعMVP0/1نیست'),
('Q06','مالکعملیات','مرجع رفع705/D23 کدام تغییر مصوب است؟','fail-closed وهر دوinputمحفوظ؛رفعخارجدامنه','پیشرفعSync،نهپیشساختUI'),
('Q07','مالکعملیات','نسخهاولیهآهنگری7321 نیاز correction دارد؟','needs_content_review؛نهdefectقطعی/نهsendتازه','مستقلازساختMVP'),
('Q08','maintainer KomatsoAI','ثبتimmutable artifact/recipientset برایآینده دردامنهچهcontractیخواهدداشت؟','گذشتهحدسنزن؛expectedunknown؛APInullable','پیشبهبودکیفیتproofتاریخچه'),
('Q09','مالک/HR','pending_decisions بازمانده باreleaseهایجدید چگونهreconcile شود؟','سندقدیمیauthorityمحاسبهجاری نیست؛caseautoایجادنشود','پیشبستنHRissuesمربوط'),
('Q10','مالک','اولینrollout manager-only تاچهحدPDFdownloadداشتهباشد؟','metadataاول؛downloadفقطgrant/file/hashواقعی','پیشMVP1UAT'),
('Q11','مالک/HR','تعریف رسمیactiveemployee وغیرفعال‌سازی employment/processing/access چیست؟','directoryprocessingباemploymentمعادلنیست','پیشMVP4deactivation'),
('Q12','زیرساخت','TaskSchedulerregistration،ESXi hostversion وbenchmark بارواقعی کدام‌اند؟','کد/فایلXMLودادهledgerاثباتقسمتی؛تماموظایفUNKNOWN','پیشdeploy'),
('Q13','مالک','ترتیبگسترش HR/unitها وdomain actionها چیست؟','MVP0→1→2→3؛write/multiuserبعدازقبولread-only','پیشMVP4/5')]
write('13-OPEN-QUESTIONS.md','پرسش‌های باز، مسئول و نقطه تصمیم',r'''
برای تولید این بسته هیچ پاسخ اضافه‌ای لازم نبود؛موارد زیر دروازه اجرای آینده‌اند وپرسش مکرر مجوز برای ادامه ممیزی نیستند. unknown فنی را با تصمیم مالک مخلوط نکنید.

| ID | مسئول | پرسش یا نیازمندی | فرض طراحی فعلی | زمان تصمیم |
|---|---|---|---|---|
'''+ '\n'.join('| '+' | '.join(q)+' |' for q in questions)+r'''

سؤالات مربوط بهپرسنل واقعی،مرخصی،تعطیل رسمییااصلاحمنبع درفرآیندHRMS ومجوزدامنه پاسخداده می‌شوند؛داشبوردتصمیم‌گیرحقوقیخودکارنیست.
''')
print('Test/roadmap/risk/question documents written')
