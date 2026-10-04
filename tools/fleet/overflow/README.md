# سرریز روزانه

ارسال خودکار هر روز ساعت 09:00 `Asia/Tehran` در job موجود `settings/schedules.yaml` تعریف شده است. گزارش ارسالی دقیقاً مربوط به روز قبل موعد به وقت تهران است و در نبود همان تاریخ هیچ گزارش جایگزینی ارسال نمی‌شود. گیرنده در هر اجرا از holder فعال نقش `office_supervisor` و capability `reports.overflow.daily_receive` resolve می‌شود. راهنما: [Authorization](../../authorization/README.md).

گزارش مستقل پایتون برای نمایش مستقیم جدول اکسل در گفتگوی خصوصی بله؛ بدون فراخوانی مدل زبانی.

- `سرریز` یا `سرریز روزانه`: بزرگ‌ترین تاریخ کامل داخل سطر اول شیت‌ها.
- `سرریز 1405/06/14`: فقط همان تاریخ؛ ارقام فارسی و عربی نیز پذیرفته می‌شوند.
- تاریخ نامعتبر، تاریخ ناموجود و تاریخ تکراری پیام روشن می‌دهند و به ایجنت نمی‌روند.
- ستون‌ها، ردیف‌های تکراری دستگاه، صفرها، خانه‌های خالی و جمع ثبت‌شده عیناً حفظ می‌شوند. واحدی که در فایل نیامده به گزارش اضافه نمی‌شود.
- شیت تجمیعی بدون تاریخ روزانه وارد گزارش نمی‌شود. تاریخ‌های متعارض شیت‌های قدیمی باید در فایل منبع اصلاح شوند.

منبع پیش‌فرض: `E:\Function\سرریز روزانه.xlsx`. فایل در هر درخواست دوباره خوانده می‌شود؛ تغییرات ذخیره‌شده در همان فایل فوراً دیده می‌شوند. فایل اصلی تغییر نمی‌کند. مسیر worker بله و ارسال روزانه ثابت و scoped است؛ `FLEET_OVERFLOW_SOURCE` آن را تغییر نمی‌دهد. گزینه منبع CLI فقط برای توسعه محلی/fixture است. این بخش دریافت و جایگزینی اکسل از پیام‌های بله را انجام نمی‌دهد.

backend بله ابتدا identity واقعی private DM، registration تأییدشده و `reports.overflow.read` را بررسی می‌کند. درخواست denied پیش از routing به Agent/admin متوقف می‌شود و generator اجرا نمی‌شود. مجوز پیش از تولید و ارسال دوباره بررسی می‌شود؛ menu جای authorization را نمی‌گیرد. متن پیام هویت محسوب نمی‌شود.

خروجی تصویر جدول راست‌به‌چپ است؛ گزارش‌های بلند به صفحات ۱۶ ردیفی تقسیم می‌شوند. وابستگی‌ها `openpyxl` و `PyMuPDF` هستند که در محیط پروژه موجودند. پردازش در subprocess محیط پروژه با مهلت ۹۰ ثانیه انجام می‌شود.

```powershell
& .\.venv\Scripts\python.exe -X utf8 -m tools.fleet.overflow.report --output-dir artifacts/overflow-preview
& .\.venv\Scripts\python.exe -X utf8 -m tools.fleet.overflow.report --date 1405/06/14
& .\.venv\Scripts\python.exe -X utf8 -m unittest tools.fleet.overflow.test_report -v
& .\.venv\Scripts\python.exe -X utf8 integrations/hermes/deploy_overflow_authorization.py
```

استقرار canonical bridge از هر twin runtime backup timestamped می‌گیرد و hash را بررسی می‌کند. بارگذاری با native plugin reload انجام می‌شود و نیازی به restart Gateway ندارد. آزمون ارسال با آداپتر ساختگی انجام می‌شود و پیامی به کاربران واقعی نمی‌فرستد.
