# زمان‌بندی ارسال‌های بله

فایل اصلی: `E:\KomatsoAI\settings\schedules.yaml`.
دو مسیر سرریز هر روز ساعت **07:45 Asia/Tehran** اجرا می‌شوند؛ گیرندگان از Capability همان Job تعیین می‌شوند. شناسه Job و موعد اصلی، هویت رسید را حفظ می‌کنند.
صفر holder، چند holder، identity تأییدنشده یا store خراب/در دسترس نبودن، ارسال را با وضعیت `skipped` و علت روشن متوقف می‌کند. انتخاب خودکار یک نفر انجام نمی‌شود. generator deterministic سرریز موجود reuse می‌شود؛ Agent/LLM ندارد. تاریخ درخواستی دقیقاً روز قبل موعد به وقت تهران است؛ اجرای 1405/07/13 گزارش 1405/07/12 را درخواست می‌کند. روز ناموجود به آخرین شیت fallback نمی‌کند.
دستور دستی نیز registration تأییدشده و `reports.overflow.read` می‌خواهد. منبع worker ثابت `E:\Function\سرریز روزانه.xlsx` است. [schema و مدیریت assignment](../authorization/README.md).
ساعت تهران: سرریز ۰۷:۴۵، رانندگان و مکانیکی و آهنگری ۰۹:۰۰، تعمیرات ۱۲:۰۰، موازنه روغن و عملکرد ۱۵:۰۰. همه هشت مسیر برای نبود داده مهلت مستقل ۳۶ ساعت از موعد اصلی دارند؛ افق Replay خطای فنی هفت روز است. عملکرد صف اختصاصی FIFO ندارد. بسته‌شدن نبود داده در missing_data_closures ثبت می‌شود و بازگشایی خودکار ندارد.

هشدار مدیر از رسید خود گزارش تعیین می‌شود و به اعلان گیرنده وابسته نیست. خطای حل‌نشده پس از ۱۵ دقیقه، نتیجه نامطمئن فوری و پایان مهلت با Case مستقل هشدار می‌گیرند. خلاصه ۱۷:۳۰ شامل هفت روز ارسال کامل و همه موارد حل‌نشده قدیمی است؛ Snapshot، Hash و رسید هر بخش مستقل و پایدارند. manager_daily_snapshot و manager_daily_receipt مسیر Retry خلاصه را از گزارش جدا می‌کنند. هشدار عملکرد برای دارندگان reports.delivery_failure_receive و مسئول تکمیل داده محفوظ است.

نوبت‌های تاریخیِ report_recovery_hold تا تصمیم مستقل مالک ارسال نمی‌شوند. این Hold به نوبت‌های آینده اضافه نمی‌شود، Receipt را بازنویسی نمی‌کند و وضعیت قبلی و مهلت ۳۶ ساعت را برای بررسی حفظ می‌کند. موارد تحت تصمیم انسانی به‌اشتباه نبود داده یا شکست فنی تازه محسوب نمی‌شوند. report_correction_review وضعیت اصلاحیه را جدا از رسید اولیه نگه می‌دارد.

## گزارش تعمیرات

مسیرهای فعال `driver_daily_mechanical` و `driver_daily_metalwork` فایل `E:\Function\گزارش روزانه رانندگان2.xlsx` را فقط برای تاریخ روز قبلِ موعد اصلی می‌خوانند. دو Job قدیمی و Handler اختصاصی `repairs` حذف شده‌اند؛ Ledger و رسیدهای تاریخی حفظ می‌شوند.

خروجی PDF با Excel نصب‌شده و فقط از همان شیت در کپی بایتی موقت ساخته می‌شود. محدودهٔ چاپ از ستون‌های بخش انتخابی و آخرین ردیف معتبر تعیین می‌شود؛ محدودهٔ ذخیره‌شدهٔ ناقص مبنا نیست. قلم خوانا، عرض یک صفحه، ارتفاع آزاد، سرستون تکرارشونده و ردیف‌های ادامه برای شرح بلند فقط در همان کپی تنظیم می‌شوند. اصل اکسل و تنظیمات سایر شیت‌ها ذخیره یا تغییر نمی‌کنند. caption مسیر آخرین روز ثبت‌شده را اعلام می‌کند و اگر با امروز برابر نباشد هشدار می‌دهد.
پارامتر `section` بخش را تعیین می‌کند: `mechanical` برای شرح معایب مکانیکی و `metalwork` برای شرح معایب آهنگری. در هر PDF فقط ردیف، نوع دستگاه، کد جدید و ستون معایب انتخابی نمایش داده می‌شود. تمام ردیف‌های جدول حفظ می‌شوند؛ ستون‌های دیگر فقط در کپی موقت Excel از چاپ کنار گذاشته می‌شوند. ستون‌ها از عنوان سطر دوم شناسایی می‌شوند؛ ستون ناموجود یا مبهم باعث خطا می‌شود و هرگز گزارش کامل جایگزین نمی‌شود. پیش‌فرض مکانیکی است.

مسیر روزانه، مسیر آخرین شیت و Preflight قرارداد مشترک `validate_driver_pdf` دارند. عنوان بخش، تاریخ، هویت و متن کامل هر سلول شرح با همان Snapshot رندر تطبیق داده می‌شوند؛ شرح تکراری برای هر دستگاه جداگانه کنترل می‌شود. استخراج گلیف در فرایند ایزوله انجام می‌شود و خروج ناموفق یا نتیجهٔ نامطمئن اجازهٔ ارسال نمی‌دهد. خطای `driver_pdf_content` با تاریخ، شیت، بخش و محل نقص ثبت می‌شود و Retry خودکار ندارد. در ارسال دفتر، فایل سالمِ بخش دیگر مستقل ارسال می‌شود و رسید هر گیرنده/فایل حفظ می‌شود. نبود شرح از اکسل تشخیص داده می‌شود و اعلان معتبرِ بدون شرح مجاز است. اصلاحیهٔ حادثه یک ارسال مستقل با رسید و Hash مستقل است؛ رسید قبلی بازنشانی نمی‌شود.


## تغییر تنظیمات

فایل UTF-8 در هر tick دوباره خوانده می‌شود؛ restart لازم نیست. job سرریز همان شناسه قبلی را حفظ می‌کند تا ledger جلوگیری از تکرار معتبر بماند:

```yaml
  - id: overflow_daily_test
    enabled: true
    task: overflow
    recipient_role: office_supervisor
    timezone: Asia/Tehran
    trigger:
      type: cron
      hour: 12
      minute: 0
    params: {}
```

timezone این job صریح است و به timezone ویندوز یا jobهای دیگر وابسته نیست. برای توقف `enabled: false` کافی است. برای تغییر گیرنده فقط Role assignment را تغییر دهید؛ job اضافی نسازید. پارامترهای schedule سرریز باید خالی باشند؛ runner تاریخ شمسی روز قبل را محاسبه و به worker می‌دهد. منابع/پارامتر دلخواه و recipient ثابت برای overflow پذیرفته نمی‌شوند.

## اجرا و کنترل

دستورات از ریشهٔ پروژه در PowerShell:

```powershell
& .\.venv\Scripts\python.exe -m pip install -r tools/scheduler/requirements.txt
& .\.venv\Scripts\python.exe -X utf8 -m tools.scheduler.runner --check
& .\.venv\Scripts\python.exe -X utf8 -m tools.scheduler.runner --status
& .\.venv\Scripts\python.exe -X utf8 -m tools.scheduler.probe
powershell.exe -NoProfile -ExecutionPolicy Bypass -File .\tools\scheduler\install.ps1
```

`--check` فقط اعتبارسنجی و موعد بعدی را نشان می‌دهد. `probe` تولید تصویر و اتصال `getMe` را بررسی می‌کند و **پیامی نمی‌فرستد**.
`--tick` موعدهای رسیده را واقعاً ارسال می‌کند؛ فرمان اجرای دستی آزمایشی نیست.
نصاب وظیفهٔ `KomatsoAI Schedules` را با دسترسی محدود کاربر جاری و S4U ثبت می‌کند تا بدون ورود کاربر هم اجرا شود؛ ثبت وظیفه ممکن است دسترسی مدیر بخواهد.
اجرای پس‌زمینه هر دقیقه مستقل از ربات و ترمینال انجام می‌شود. برای نصب مجددِ همان وظیفه `-Replace` بدهید؛ XML قبلی در `backup/scheduler` ذخیره می‌شود.

## رفتار عملیاتی

- APScheduler 3 محاسبات cron/date را انجام می‌دهد و Windows Task Scheduler اجرای پایدار هر دقیقه را بر عهده دارد؛ daemon جداگانه‌ای لازم نیست.
- سرور باید روشن و دارای اتصال اینترنت باشد. اجرا در اولین بررسی پس از موعد شروع می‌شود؛ تولید و ارسال تصویر چند ثانیه زمان می‌برد.
- job بدون `misfire` همان رفتار قبلی را دارد: جبران تا ۱۵ دقیقه و فقط آخرین موعد داخل آن پنجره.
- گزارش‌های انسانی `misfire.policy: replay` و `horizon_days` (پیش‌فرض ۷) دارند. due اصلی حفظ می‌شود و تاریخ گزارش از همان due حساب می‌شود، نه از ساعت بازیابی. موعد قدیمی‌تر از افق، و سوراخ‌های قبل از آخرین سابقه، خودکار ارسال نمی‌شوند.
- `latest_only` فقط آخرین موعد داخل پنجره را اجرا می‌کند. `skip_missed` فقط اگر همان موعد هنوز داخل ۹۰ ثانیه باشد. `external_misfire` برای `mine-file-sync` سیاست `latest_only` است؛ این scheduler آن job را اجرا نمی‌کند و هر تیک دو دقیقه‌ای را replay نمی‌کند.
- سابقه در `runtime/scheduler/runs.sqlite3` با کلید `(schedule_id, due)` است. رسید هر گیرنده کلید `(schedule_id, due, recipient_id)` دارد. وضعیت `succeeded` و `uncertain` دوباره ارسال نمی‌شوند. قطع قطعی بله `retry_wait` می‌ماند و با فاصلهٔ ۱، ۲، ۵، ۱۰ و سپس ۱۵ دقیقه دوباره تلاش می‌شود. timeout بعد از شروع ارسال `uncertain` است و کورکورانه تکرار نمی‌شود. نبودن تاریخ دقیق `no_data` است و به گزارش قدیمی‌تر fallback نمی‌شود؛ تا پایان افق، اگر منبع دیر برسد، همان due دوباره خوانده می‌شود.
- خطای یک ارسال مانع اجراهای بعدی نمی‌شود. فایل تنظیمات نامعتبر کل بررسی را متوقف می‌کند و پس از اصلاح در بررسی بعدی دوباره خوانده می‌شود.
- لاگ چرخشی: `runtime/scheduler/scheduler.log`. وضعیت ویندوز: `Get-ScheduledTaskInfo -TaskName 'KomatsoAI Schedules'`.
- توکن در YAML ذخیره نمی‌شود؛ از `BALE_BOT_TOKEN` محیط یا `.env` موجود Hermes خوانده می‌شود. اتصال مستقیم مطابق تنظیم فعلی آداپتر بله است.
- منبع worker سرریز همان مسیر ثابت اکسل فعلی است؛ override محیطی آن را تغییر نمی‌دهد.
- شناسهٔ یک برنامه را برای کار دیگری بازیافت نکنید و پایگاه سابقه را پاک نکنید؛ این کار ممکن است ارسال تکراری بسازد.

## افزودن نوع کار جدید

گیرنده سرریز فقط از assignment نقش resolve می‌شود. زمان/تاریخ محتوا را در YAML تنظیم کنید. **نوع گزارش واقعاً جدید** یک تابع اجرا و یک تابع اعتبارسنجی در `tasks.py` می‌خواهد و به `TASKS` اضافه می‌شود؛ هستهٔ زمان‌بندی و نصب ویندوز تغییر نمی‌کنند. YAML امکان اجرای دستور یا import دلخواه ندارد.

## آزمون

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m unittest tools.scheduler.test_runner tools.fleet.overflow.test_report tools.fleet.repairs.test_report -v
```

آزمون‌ها از گیرندهٔ ساختگی استفاده می‌کنند و پیام واقعی نمی‌فرستند.


## Exact-day no-data contract

All six enabled deterministic daily report families use the exact previous Tehran
calendar day of their original due. No older report substitutes for it. A missing
source day returns `waiting_for_data`. For office/mechanical overflow and driver
reports, maintenance and oil balance, missing files expire **36 hours after their
original scheduled due**, including exactly at that boundary. They become
`no_data_final` with `missing_date_expired`; later uploads of that expired day are
not sent automatically. Subsequent days keep their own due dates and retries: a
ready day sends normally, while a missing day waits for its own 36-hour deadline.
The seven-day downtime replay horizon remains unchanged. Delivery/technical
failures, partial or uncertain sends, and existing receipts are not expired by
this missing-file rule. Metalwork and machinery-performance queue policies remain
unchanged. Latest dates come from each domain parser, never file
names. A driver day with a valid exact-date sheet and no defects remains a valid
report; maintenance requires actual target-date activity rows.

Driver-report completion reminders run at 08:00 and 15:00 Asia/Tehran, only for
the active mechanical report recipients (currently Shayesteh and Karamali).
Each slot reads the exact previous Tehran day independently of PDF receipts.
A missing day or a completely blank mechanical section gets one reminder per
recipient per slot; an existing mechanical entry suppresses that slot. An empty
section is described as empty, without claiming that a machine has a defect.
The afternoon slot rereads the source, so filling it stops the second reminder.
The workbook has no explicit completion marker: partially filled sections cannot
be classified from blank device cells alone. Unreadable or invalid workbooks are
technical failures, never proof of missing entries. Definite transport failures
retry within the slot's hour; uncertain sends remain closed. Reminder decisions
and receipts persist separately from PDF delivery.

Office and mechanical driver-report jobs suppress their old missing-day messages
at 09:00. Emami and Pakdel receive the actual driver PDFs without file-completion
reminders. Metalwork and overflow missing-data notices keep their existing
recipient policies. Maintenance completion reminders run at 11:00 and 15:00 for
the explicit approved private identities Karamali, Shayesteh and Sadegh Sadeghian.
Their active mechanical/metalwork report eligibility is rechecked before each
message. These reminders grant no workbook access or maintenance PDF subscription.
The exact previous-day maintenance scan must contain an activity row; a date-only
template or an older date does not satisfy it. Source errors never become a
completion accusation. Office maintenance missing-day messages at 12:00 are
suppressed; actual PDF delivery and its recipients retain their existing behavior.
Both reminder families share receipt handling with distinct schedule/slot keys.
Overflow delivery for all current recipients is now
07:45; driver PDFs remain at 09:00 and maintenance remains at 12:00.
Overflow preflight retains its one-hour window (06:45 to 07:45). Existing original
dues and report receipts remain authoritative when a delivery clock changes.

Receipts use `(schedule_id, due, recipient_id, delivery_kind)` with independent
`notice` and `report` identities. `pending`, `sending`, `sent`, `failed`, `uncertain`
and `revoked` track each delivery. Legacy receipts migrate as reports; a legacy
missing-day PDF can only be reclassified with occurrence-specific evidence.
`notice_delivery_state` distinguishes notice transport retry or uncertainty while
`data_state=missing` keeps the occurrence waiting. An uncertain notice is never
resent and does not block a later actual report. An uncertain report is never
blindly resent. Current recipients are resolved again before each artifact send.

Successful notices never derive report success. After an exact report is delivered,
only report receipts determine terminal success. Expired missing-data occurrences
become `no_data_final`; sent notices remain closed. A report whose source is ready
but whose transport is unavailable uses `retry_wait`/`partial`. Recipient report
receipts are reserved before generation so a crash after the first fanout send
cannot incorrectly close unsent recipients. Disabled schedules and external
high-frequency jobs keep their existing behavior.

Fixture verification:
`python -m unittest tools.scheduler.test_no_data tools.scheduler.test_misfire`
## Maintenance publication versions and source state

Maintenance delivery keeps three independent states:

* `source_unavailable` / `source_stale`: infrastructure or an unverified local
  version. No missing-date claim is made. The existing seven-day technical
  replay horizon and backoff remain in effect.
* `waiting_for_data` with `no_target_date_records`: the accepted local journal
  head was read and the exact mine file was successfully checked within five
  minutes. The target date genuinely has no activity in that verified version.
  Its deadline remains exactly the original occurrence plus 36 hours.
* `ARTIFACT_READY`: a complete exact-date PDF and source workbook are pinned in
  `runtime/scheduler/maintenance-ready/<date>/`. Content-addressed PDF, workbook,
  report/layout JSON and manifest record the preparation time and accepted
  journal version. Delivery validates those retained bytes and current recipient
  authorization/receipts; it does not reopen the mine or current business file.

Preflight retains a complete target-date artifact. Older layout samples do not
become the target report. A later Sync outage cannot turn a ready artifact into
missing data. A later source version does not overwrite the selected publication
version: corrections require independent review and receipts. Corrupt PDF content
is terminal (`pdf_validation`); uncertain delivery is never blindly retried.

A new accepted exact-file Sync hash advances `report_source_wakeup` and makes
eligible maintenance retries due on the next tick. Safety holds and terminal
36-hour closures remain intact. Deployment backlog migration excludes the full
36-hour window and technical retries still within their configured horizon.

Explicit historical recovery uses `recovery_delivery` with exact operation,
recipient, date and hash approval. `report_recovery_operation` and append-only
`report_recovery_event` supplement the original run and receipts. Only after all
approved recipients have matching sent operation, report and artifact receipts
may that specific historical hold become `recovered`. The historical occurrence
stays suppressed to prevent another send. Management summaries reconcile those
supplemental receipts without rewriting history.

