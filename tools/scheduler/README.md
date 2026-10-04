# زمان‌بندی ارسال‌های بله

فایل اصلی: `E:\KomatsoAI\settings\schedules.yaml`.
job موجود سرریز `overflow_daily_test` هر روز ساعت **09:00 Asia/Tehran** اجرا می‌شود؛ گیرنده از Role فعال `office_supervisor` و `reports.overflow.daily_receive` resolve می‌شود. برای تغییر سرپرست فقط assignment در Authorization Store را تغییر دهید. هیچ recipient ID در job سرریز مجاز نیست.
صفر holder، چند holder، identity تأییدنشده یا store خراب/در دسترس نبودن، ارسال را با وضعیت `skipped` و علت روشن متوقف می‌کند. انتخاب خودکار یک نفر انجام نمی‌شود. generator deterministic سرریز موجود reuse می‌شود؛ Agent/LLM ندارد. تاریخ درخواستی دقیقاً روز قبل موعد به وقت تهران است؛ اجرای 1405/07/13 گزارش 1405/07/12 را درخواست می‌کند. روز ناموجود به آخرین شیت fallback نمی‌کند.
دستور دستی نیز registration تأییدشده و `reports.overflow.read` می‌خواهد. منبع worker ثابت `E:\Function\سرریز روزانه.xlsx` است. [schema و مدیریت assignment](../authorization/README.md).
گزارش‌های تعمیرات و تنظیمات مستقل قبلی آن‌ها در این Phase تغییر نکرده‌اند.

## گزارش تعمیرات

نوع کار `repairs` فایل `E:\Function\گزارش روزانه رانندگان2.xlsx` را در هر اجرا دوباره می‌خواند و بزرگ‌ترین تاریخ معتبر در سطر اول شیت‌ها را انتخاب می‌کند. ترتیب شیت‌ها یا نام تب تعیین‌کننده نیست. تاریخ تکراری/مبهم باعث توقف می‌شود تا گزارش اشتباه ارسال نشود.

خروجی PDF با Excel نصب‌شده و فقط از همان شیت، با حفظ محدودهٔ چاپ، اندازهٔ صفحه، قلم‌ها و صفحه‌بندی اصلی ساخته می‌شود. یک کپی بایتی موقت از فایل استفاده می‌شود و اصل اکسل ذخیره یا تغییر نمی‌کند. caption همیشه آخرین روز ثبت‌شده را اعلام می‌کند و اگر با امروز برابر نباشد هشدار می‌دهد.
پارامتر `section` بخش را تعیین می‌کند: `mechanical` برای شرح معایب مکانیکی و `metalwork` برای شرح معایب آهنگری. در هر PDF فقط ردیف، نوع دستگاه، کد جدید و ستون معایب انتخابی نمایش داده می‌شود. تمام ردیف‌های جدول حفظ می‌شوند؛ ستون‌های دیگر فقط در کپی موقت Excel از چاپ کنار گذاشته می‌شوند. ستون‌ها از عنوان سطر دوم شناسایی می‌شوند؛ ستون ناموجود یا مبهم باعث خطا می‌شود و هرگز گزارش کامل جایگزین نمی‌شود. پیش‌فرض مکانیکی است.

```yaml
  - id: repairs_daily
    enabled: true
    task: repairs
    recipient: "654806764"
    trigger:
      type: cron
      hour: 8
      minute: 0
    params:
      section: mechanical
  - id: metalwork_daily
    enabled: true
    task: repairs
    recipient: "455740857"
    trigger:
      type: cron
      hour: 8
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
      hour: 9
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
- جبران تأخیر تا ۱۵ دقیقه است. برای چند موعد ازدست‌رفته فقط آخرین مورد معتبر بررسی می‌شود؛ قدیمی‌ترها ارسال نمی‌شوند.
- سابقهٔ SQLite در `runtime/scheduler/runs.sqlite3` با کلید یکتای `(id, due)` پیش از ارسال ثبت می‌شود. اجرای مجدد همان موعد یا ری‌استارت باعث ارسال دوباره نمی‌شود.
- در قطع ناگهانی یا شکست شبکه، نتیجه ممکن است نامشخص باشد. برنامه برای جلوگیری از تصویر تکراری، همان موعد را خودکار تکرار نمی‌کند. `failed` و `running` باقی‌مانده نیاز به بررسی دارند؛ موعد روز بعد مستقل اجرا می‌شود. تضمین تحویل دقیقاً یک‌باره در API بله وجود ندارد.
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
