# PAKDEL DAILY REPORTS + FINAL WORK ORDER COPY

**READY — PAKDEL DAILY REPORTS AND WORK ORDER COPIES ACTIVE**

استقرار: 2026-10-08. فعال‌سازی outbox در ساعت **10:38:40 Asia/Tehran**؛ اتصال Gateway پس از فعال‌سازی اولیه در حدود **10:40:13** و پس از اصلاح caption در **10:53** دوباره تأیید شد. هیچ حکم ساختگی در production ساخته نشد و هیچ PDF آزمایشی به سرویسکار یا کاربر واقعی ارسال نشد.

## هویت و مجوز

داده زنده با شرح درخواست تطابق داشت.

| شناسه | نام تأییدشده | Roleهای قبل | Roleهای بعد | Profile قبل/بعد |
|---|---|---|---|---|
| 397185913 | امامی | business_admin، office_supervisor، maintenance_daily_report_recipient | همان قبلی‌ها | admin / admin |
| 514458396 | هوشمند پاکدل | business_admin، maintenance_daily_report_recipient | قبلی‌ها + office_daily_report_recipient + work_order_final_copy_recipient | admin / admin |
| 641220453 | حمید هدایتی | net_manager | بدون تغییر | net / net |
| 1294822197 | پوریا آسترکی | net_manager_deputy | بدون تغییر | net / net |

| Role جدید؛ هر دو بدون profile mapping | تنها Capabilityهای آن |
|---|---|
| office_daily_report_recipient | reports.overflow.daily_receive، reports.driver_daily.daily_receive |
| work_order_final_copy_recipient | work_orders.final_copy.receive |

resource رونوشت ثابت و برابر work_orders.final_approved_copy است. دو نقش جدید فقط به پاکدل تخصیص یافتند. امامی قابلیت دریافت دفتر را از نقش قبلی خود حفظ کرد. business_admin مجوز دریافت رونوشت نیست؛ delivery engine استثنای hardcoded برای شناسه پاکدل ندارد.

تمام assignmentهای قبلی، profile mapping و domain authority زنده با snapshot قبل برابرند. هیچ مجوز جدیدی به دیگر کاربران داده نشد. پاکدل office_supervisor یا NET manager نشد؛ مجوز create/assign/approve/send حکم، ثبت شرح خرابی یا ویرایش Excel به او اضافه نشد. Function scope و business_admin → admin قبلی حفظ شدند.

## تمام گزارش‌های فعال امامی

فقط سه Schedule فعال برای امامی وجود داشتند؛ Schedule ساعت ۱۰ دو PDF دارد. گزارش فعال دیگری برای او یافت نشد.

| گزارش / Schedule دقیق | گیرندگان قبلی | گیرندگان جدید | ساعت تهران |
|---|---|---|---|
| مکانیکی / driver_daily_office_supervisor | 397185913 | 397185913، 514458396 | 10:00 |
| آهنگری / driver_daily_office_supervisor | 397185913 | 397185913، 514458396 | 10:00 |
| سرریز دفتر / overflow_daily_test | 397185913 | 397185913، 514458396 | 09:00 |
| تعمیرات / maintenance_daily_report | 397185913، 514458396 | همان دو نفر | 09:00 |

| Schedule | تاریخ هدف | منبع | Artifact | Capability دریافت |
|---|---|---|---|---|
| driver_daily_office_supervisor | روز تقویمی دقیق قبل از occurrence به وقت تهران | E:\Function\گزارش روزانه رانندگان2.xlsx؛ یک snapshot منبع در هر تولید | دو PDF مستقل مکانیکی و آهنگری، همان build_driver_pdf فعلی | reports.driver_daily.daily_receive |
| overflow_daily_test | روز تقویمی دقیق قبل از occurrence به وقت تهران | E:\Function\سرریز روزانه.xlsx | تصاویر PNG و caption فعلی؛ PDF موازی ساخته نمی‌شود | reports.overflow.daily_receive |
| maintenance_daily_report | روز تقویمی دقیق قبل از occurrence به وقت تهران | E:\Function\تعمیرات 1405.xlsx و layout موجود | PDF ترکیبی فعلی تعمیرات | reports.maintenance.daily_receive |

هر سه Schedule سیاست replay با horizon هفت‌روزه دارند. نبود تاریخ هدف، notice مستقل گیرنده و waiting_for_data ایجاد می‌کند؛ فایل روز قدیمی جایگزین نمی‌شود. ظهور دیرهنگام داده در همان horizon، actual report را برای گیرندگان مجاز ارسال می‌کند. receipt notice مستقل از report است. تعمیرات با no_target_date_records همان رفتار موجود را حفظ کرده است.

گزارش‌های دفتر امروز پیش از استقرار برای امامی موفق شده بودند. ledger موفق بازنشانی یا دستی بازپخش نشد. نوبت روزانه بعدی دفتر **2026-10-09 ساعت 09:00 و 10:00 تهران** است و تاریخ هدف **2026-10-08 / 1405/07/16** خواهد بود.

سایر مسیرهای فعال بدون تغییر:

| Schedule | گیرندگان قبل و بعد | ساعت تهران |
|---|---|---|
| overflow_daily_mechanical | 1732374823، 654806764 | 09:00 |
| driver_daily_mechanical | 1732374823، 654806764 | 10:00 |
| driver_daily_metalwork | 387679249 | 09:00 |

repairs_daily و metalwork_daily غیرفعال ماندند. هیچ Job جدید یا مسیر تولید موازی ایجاد نشد. mine-file-sync و سیاست latest_only آن تغییر نکردند.

## Fan-out و receipt دفتر

در YAML فقط recipient_role دو Schedule دفتر به capability دریافت متناظر تغییر کرد. نام، ساعت، trigger، تاریخ هدف و catch-up ثابت ماندند. مسیر legacy برای سازگاری باقی است؛ production از capability استفاده می‌کند.

همان generator موجود فقط یک بار در هر تلاش تولید می‌کند و خروجی بین گیرندگان reuse می‌شود. جدول افزوده office_artifact_receipts در runtime/scheduler/runs.sqlite3 کلید زیر دارد:

schedule_id + due + recipient_id + artifact_id

artifact_id دو PDF برابر mechanical و metalwork است؛ تصاویر سرریز page:0 و صفحات بعدی‌اند. status، message_id و SHA-256 هر artifact ثبت می‌شود. شکست PDF دوم یا گیرنده دیگر، artifact موفق را تکرار نمی‌کند. sending مانده پس از crash به uncertain تبدیل می‌شود و pending دیگر قابل retry می‌ماند. receiptهای legacy sent/uncertain محفوظ‌اند.

approved/private بودن هویت و capability پیش از هر upload دوباره resolve می‌شود. قطعی موقت دیتابیس مجوزها، خطای قابل retry است و با revocation واقعی اشتباه گرفته نمی‌شود. aggregate ledger قبلی برای Delivery Exception Alert باقی است و scan هشدار پیش از drain رونوشت اجرا می‌شود.

## Hook نهایی AF / OC / GR

Hook هر سه نوع در tools/fleet/work_orders/core/delivery.py داخل send_work_order مشترک است:

1. final_copy.prepare حدود خط 150، bytes همان PDF نهایی را پیش از transport اصلی با SHA-256 در runtime/work_order_copies/artifacts/<sha256>.pdf نگه می‌دارد.
2. ارسال سرویسکار از همان فایل نگه‌داری‌شده، با نام اصلی PDF و caption قبلی سرویسکار انجام می‌شود؛ renderer دوم یا بازسازی workbook وجود ندارد.
3. فقط پس از موفقیت واقعی transport و UPDATE به SENT، final_copy.enqueue حدود خط 184 در همان transaction مربوط به SENT اجرا و سپس commit می‌شود.
4. drain مستقل رونوشت در tick Scheduler پس از گزارش‌ها و scan هشدارها اجرا می‌شود؛ Job جدید ایجاد نشده است.

پیاده‌سازی outbox: tools/fleet/work_orders/core/final_copy.py. Eligibility صادرکننده از نقش NET manager/deputy، capabilityهای واقعی و created_by/approved_by همان رکورد بررسی می‌شود. هیچ شناسه مدیر در engine hardcode نشده است.

DRAFT، FILE_READY، Preview، Review، ASSIGNED، APPROVED-only و ارسال ناموفق یا نامطمئن به سرویسکار، رونوشت قابل ارسال ایجاد نمی‌کنند.

جدول work_order_final_copy در fleet_ops.db کلید زیر دارد:

work_order_id + artifact_sha256 + recipient_id + delivery_kind

delivery_kind برابر final_approved_copy است. caption از نوع واقعی registry، شماره و تاریخ رکورد، ماشین‌های items و هویت صادرکننده ساخته می‌شود؛ نام سرویسکار از dispatch.staff_name همان حکم و در نبود آن از service_staff خوانده می‌شود؛ از نام فایل حدس زده نمی‌شود و در همان document message ثبت می‌شود.

نمونه PDF نهایی واقعی هر سه نوع پیش از تغییر موجود و معتبر بود. حذف PDF اصلی یا تغییر workbook بعدی باعث renderer جدید نمی‌شود؛ bytes فایل نگه‌داری‌شده مرجع ارسال رونوشت است.

Outbox فقط از transition آینده به SENT پر می‌شود. SENT تاریخی، callback تکراری یا restart backfill ایجاد نمی‌کند. در بررسی زنده پس از استقرار، تعداد رونوشت صفر بود و تمام ردیف‌های تاریخی service_work_orders، items، owner، service_staff، roster، dispatch و conversation با backup قبل عیناً برابر بودند.

## استقلال و retry

شکست رونوشت هیچ UPDATE به وضعیت حکم اصلی انجام نمی‌دهد؛ ارسال سرویسکار یا archive AF/GR را فراخوانی نمی‌کند. Ownership، staff acknowledgement، NET manager/deputy parity و state machine قبلی حفظ شده‌اند.

خطای قطعی pre-send یا رد صریح Bale، failed و قابل retry با backoff فعلی 60/120/300/600/900 ثانیه است. نتیجه نامطمئن یا sending مانده بیش از ۹۰۰ ثانیه uncertain می‌شود و blind resend ندارد. claim اتمیک outbox و primary key مانع duplicate هستند. hash نامعتبر یا artifact غیرقابل اعتماد blocked می‌شود و از workbook بازسازی نمی‌شود.

## تست‌ها و محدودیت اثبات

تست‌ها با fixture، SQLite موقت و fake Bale اجرا شدند. runner پذیرش، نوشتن در DBهای production، workbook منبع و transport واقعی را ممنوع می‌کند. تست native archive فقط فایل موقت خودش را استفاده می‌کند.

| مجموعه | نتیجه |
|---|---|
| Authorization، NET، Scheduler، no-data، maintenance و قابلیت‌های جدید | 245 تست PASS؛ بدون skip |
| Work Order regression | 256 تست پوشش داده شد؛ 255 ابتدا PASS؛ یک fixture قدیمی archive اصلاح و همان تست در rerun PASS شد؛ خطای باقی‌مانده صفر |
| همان rerun + تمام تست‌های final copy | 17 تست PASS؛ بدون skip |
| Scheduler پس از حفظ ترتیب scan هشدار | 92 تست PASS؛ بدون skip |
| final copy پس از خواندن نام دقیق سرویسکار از dispatch | 17 تست PASS؛ بدون skip |
| supervisor و external-supervisor restart در محیط dummy | 10 تست PASS؛ بدون skip |

fixtureهای قدیمی مکانیکی/آهنگری با API فعلی missing_result و sections ناسازگار بودند؛ failure آنها روی canonical بدون تغییر نیز تأیید شد و فقط fixtureها هماهنگ شدند. assertionهای Schedule مطابق تغییر مجاز role → capability اصلاح شدند. یک تست lifecycle با workbook جعلی نیز archive را mock نمی‌کرد؛ fixture اصلاح شد. منطق production گزارش‌های مدیران مکانیکی یا آهنگری تغییر نکرد.

| تست الزامی درخواست | پوشش |
|---|---|
| 1، 2 | هر دو PDF برای هر دو گیرنده؛ یک generation و bytes یکسان |
| 3 | سرریز برای هر دو، بدون duplicate |
| 4 | maintenance assignment قبلی و receiptهای sent زنده محفوظ؛ تست‌های تعمیرات موجود |
| 5، 6 | notice مستقل و actual report پس از late-data برای هر دو |
| 7، 8 | شکست گیرنده و PDF دوم؛ فقط artifact ناموفق retry |
| 9 | revocation بین uploadها و پیش از document رونوشت |
| 10 | Jobهای غیرفعال ثابت؛ maintenance همچنان فقط یک Schedule |
| 11، 12، 13 | AF/OC/GR فقط بعد از SENT؛ NET deputy نیز پوشش دارد |
| 14 | حالت‌های غیرنهایی و main failed/uncertain بدون رونوشت |
| 15 | تطابق bytes و hash سرویسکار/رونوشت؛ حذف اصل بدون renderer جدید |
| 16 | copy failure با حفظ SENT و بدون staff resend/archive |
| 17 | once، callback، restart، stale sending و historical SENT |
| 18 | capability دریافت بدون create/approve/assign/send یا staff acknowledgement |
| 19 | حفظ Role/Profile در fixture و مقایسه تمام assignment/profile/domain rows زنده |
| 20 | مجموعه‌های regression بالا؛ failure باقی‌مانده صفر |

هنوز حکم ساختگی production برای اثبات end-to-end صادر نشده است. اولین receipt واقعی باید با اولین ارسال عملیاتی آینده بررسی شود؛ فعال‌بودن کد، مجوز، outbox، Scheduler و Gateway تأیید شده است.

## Backup و سلامت

پیش از mutation، backup آنلاین معتبر این سه SQLite تهیه شد؛ درست پیش از deploy نیز backup تازه ثبت شد: telegram_users.db، fleet_ops.db و runs.sqlite3. Integrity هر سه ok و FK هر سه بدون خطا بود؛ پس از استقرار نیز تکرار شد.

پوشه audit:

E:\KomatsoAI\runtime\pakdel-delivery\20261008T064224Z

این پوشه شامل backup اولیه، code snapshot، manifest-before، recipient-matrix، authorization-before/after، immediate-predeploy، assignment-backups، deployment.json و gateway-before/after است. hash فایل‌های rollback با backup معتبر و hash کد deployشده با canonical مطابقت دارد.

۳۰ workbook منبع fingerprint شدند و پس از deploy hash آنها ثابت بود. زمان آخرین تغییر دو archive اصلی پیش از شروع این کار است. تمام receiptهای sent تعمیرات در snapshot اولیه با همان status/message_id حفظ شدند.

تغییرات از قبل موجود Delivery Exception Alert حفظ شدند. moduleهای alert، Gateway supervisor، mine-file-sync و Excelهای منبع تغییر نکردند. Gateway دو بار restart رسمی با drain تمیز داشت (فعال‌سازی اولیه و بارگذاری اصلاح caption)؛ launchers و Hermes code SHA/version ثابت ماندند:

0.21.4 / 2945ced6c60a4d7ea669d7c0ac8cd6a6d5c1928b

Gateway جدید از control socket با answering_pid=15588 و supervisor=external تأیید شد؛ Bale و Telegram connected، session_store=ok و Profileهای default/admin/maintenance/net برقرار بودند. Scheduler پس از deploy نتیجه 0 داشت و همان Task هر دقیقه با IgnoreNew اجرا می‌شود.

## اولین حکم واقعی آینده

دستور زیر فقط SQLite را read-only می‌خواند و hash PDF نگه‌داری‌شده را بررسی می‌کند؛ حکم یا فایل ارسال نمی‌کند:

~~~powershell
E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\runtime\pakdel-delivery\verify_future.py
~~~

برای شماره مشخص، --number "شماره واقعی حکم" اضافه شود.

نشانه موفقیت: work_order_status=SENT، recipient_id=514458396، copy_status=sent، message_id ثبت‌شده و pinned_hash_verified=true. pending تا tick بعدی طبیعی است؛ failed طبق backoff retry می‌شود و uncertain بدون evidence مجدداً ارسال نمی‌شود.

## Rollback محدود

پس از بررسی سلامت Gateway و نبود کار در جریان، از E:\KomatsoAI اجرا شود:

~~~powershell
E:\KomatsoAI\.venv\Scripts\python.exe -m integrations.hermes.deploy_pakdel_delivery rollback
~~~

این فرمان فقط دو نقش افزوده پاکدل را غیرفعال، marker فعال‌سازی رونوشت را حذف و کد قبلی canonical را برمی‌گرداند. خود اسکریپت Gateway را قطع نمی‌کند؛ پس از rollback کد Work Order، restart رسمی کنترل‌شده لازم است. Receiptها، artifactها و داده‌های عملیاتی جدید حذف یا با DB تاریخی جایگزین نمی‌شوند. Roleها و Profileهای قبلی حفظ می‌شوند.

**READY — PAKDEL DAILY REPORTS AND WORK ORDER COPIES ACTIVE**
