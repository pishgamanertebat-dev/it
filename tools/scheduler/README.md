# زمان‌بندی ارسال‌های بله

فایل اصلی: `E:\KomatsoAI\settings\schedules.yaml`.
job موجود سرریز `overflow_daily_test` هر روز ساعت **12:00 Asia/Tehran** اجرا می‌شود؛ گیرنده از Role فعال `office_supervisor` و `reports.overflow.daily_receive` resolve می‌شود. برای تغییر سرپرست فقط assignment در Authorization Store را تغییر دهید. هیچ recipient ID در job سرریز مجاز نیست.
صفر holder، چند holder، identity تأییدنشده یا store خراب/در دسترس نبودن، ارسال را با وضعیت `skipped` و علت روشن متوقف می‌کند. انتخاب خودکار یک نفر انجام نمی‌شود. generator deterministic سرریز موجود reuse می‌شود؛ Agent/LLM ندارد. تاریخ درخواستی دقیقاً روز قبل موعد به وقت تهران است؛ اجرای 1405/07/13 گزارش 1405/07/12 را درخواست می‌کند. روز ناموجود به آخرین شیت fallback نمی‌کند.
دستور دستی نیز registration تأییدشده و `reports.overflow.read` می‌خواهد. منبع worker ثابت `E:\Function\سرریز روزانه.xlsx` است. [schema و مدیریت assignment](../authorization/README.md).
زمان‌بندی مشترک همه گیرندگان و پروفایل‌ها به وقت تهران: سرریزها و تعمیرات ۱۴۰۵ ساعت ۱۲:۰۰؛ گزارش روزانه رانندگان شامل مکانیکی و آهنگری ساعت ۰۹:۰۰. ارسال‌های مستقیم قدیمی همچنان غیرفعال‌اند. با تغییر ساعت، سابقه و رسیدهای همان روز حفظ می‌شوند تا گزارش یا اعلان دوباره ارسال نشود؛ موعدهای باز با شناسه و تاریخ اصلی پیگیری می‌شوند.

## گزارش تعمیرات

نوع کار `repairs` فایل `E:\Function\گزارش روزانه رانندگان2.xlsx` را در هر اجرا دوباره می‌خواند و بزرگ‌ترین تاریخ معتبر در سطر اول شیت‌ها را انتخاب می‌کند. ترتیب شیت‌ها یا نام تب تعیین‌کننده نیست. تاریخ تکراری/مبهم باعث توقف می‌شود تا گزارش اشتباه ارسال نشود.

خروجی PDF با Excel نصب‌شده و فقط از همان شیت، با حفظ محدودهٔ چاپ، اندازهٔ صفحه، قلم‌ها و صفحه‌بندی اصلی ساخته می‌شود. یک کپی بایتی موقت از فایل استفاده می‌شود و اصل اکسل ذخیره یا تغییر نمی‌کند. caption همیشه آخرین روز ثبت‌شده را اعلام می‌کند و اگر با امروز برابر نباشد هشدار می‌دهد.
پارامتر `section` بخش را تعیین می‌کند: `mechanical` برای شرح معایب مکانیکی و `metalwork` برای شرح معایب آهنگری. در هر PDF فقط ردیف، نوع دستگاه، کد جدید و ستون معایب انتخابی نمایش داده می‌شود. تمام ردیف‌های جدول حفظ می‌شوند؛ ستون‌های دیگر فقط در کپی موقت Excel از چاپ کنار گذاشته می‌شوند. ستون‌ها از عنوان سطر دوم شناسایی می‌شوند؛ ستون ناموجود یا مبهم باعث خطا می‌شود و هرگز گزارش کامل جایگزین نمی‌شود. پیش‌فرض مکانیکی است.

```yaml
  - id: repairs_daily
    enabled: false
    task: repairs
    recipient: "654806764"
    trigger:
      type: cron
      hour: 9
      minute: 0
    params:
      section: mechanical
  - id: metalwork_daily
    enabled: false
    task: repairs
    recipient: "455740857"
    trigger:
      type: cron
      hour: 9
      minute: 0
    params:
      section: metalwork
```

مسیر اختیاری با `params.source` (مسیر کامل) یا متغیر محیطی `FLEET_REPAIRS_SOURCE` قابل تغییر است.
برای پیش‌نمایش بدون ارسال: `python -X utf8 -m tools.fleet.repairs.report --section mechanical --output-dir artifacts/repairs-preview` با پایتون پروژه؛ برای آهنگری `--section metalwork` بدهید.
آزمون Excel در حساب واقعی زمان‌بند با اسکریپت `tools/scheduler/verify_repairs_service.ps1` انجام می‌شود؛ فقط PDF محلی می‌سازد و وظیفهٔ موقت را حذف می‌کند. نیازمند مجوز ثبت وظیفهٔ ویندوز است.
اگر پروفایل سیستمی پوشهٔ Desktop نداشته باشد، `prepare_excel_service.ps1` با دسترسی مدیر دو پوشهٔ خالی `C:\Windows\System32\config\systemprofile\Desktop` و `C:\Windows\SysWOW64\config\systemprofile\Desktop` را آماده و آزمون را اجرا می‌کند. پوشه‌های ساخته‌شده در `runtime/scheduler/excel-service-directories.json` ثبت می‌شوند؛ رجیستری و مجوزها تغییر نمی‌کنند.
تبدیل PDF مهلت ۲۴۰ ثانیه دارد. در پایان فقط فرایند Excel ساخته‌شده برای همان تبدیل، با بررسی PID و زمان ایجاد، پاک‌سازی می‌شود؛ نشست‌های دیگر Excel بسته نمی‌شوند.

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
source day returns `waiting_for_data`, sends one Persian text notice to each
currently authorized recipient, and keeps bounded source retries active for the
existing seven-day horizon. Latest dates come from each domain parser, never file
names. A driver day with a valid exact-date sheet and no defects remains a valid
report; maintenance requires actual target-date activity rows.

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
