# نصب و بازگشت ROC-L8

وضعیت: **INDEX INSTALLED — READY FOR CONTROLLED ACTIVATION**. Gateway نه Restart و نه Reload شده است. مجوز کلی آزمون، به‌عنوان مجوز Restart سرویس تفسیر نشد.

شاخه: `technical-docs/roc-l8-onboarding-20261115`؛ HEAD مبنا: `ae0466e16478e6e0d43bdd6629bb947cf444e3f2`.

پایگاه فنی تولیدی: `runtime/partbook/partbook_index.sqlite`؛ Candidate نهایی: `runtime/roc-l8-onboarding/acceptance-final.sqlite`. SQLiteهای نامزد/Backup و PDFها در Git نیستند. ساخت هر کتاب ابتدا در فایل جدا با Backup قبلی و تراکنش انجام می‌شود؛ فقط پس از integrity_check و Digest مقایسه‌شده، فایل به‌شکل اتمیک جایگزین می‌شود.

Backup کامل پیش از ROC: `E:\KomatsoAI\runtime\roc-l8-onboarding\production-before-ROC-20261115T130855.sqlite`؛ SHA-256: `64815c762c81875d3c761058aad4902cf2d3984c036926f5b3acf3e4751a2ef9`. رسید جاری و هش نصب نهایی در `runtime/roc-l8-onboarding/deployment-receipt.json` مرجع دقیق هستند؛ hash قدیمی را برای نصب جدید حدس نزنید. در اصلاح نمایه، Backup میانی نیز ثبت و اصل Backup پیش از ROC حفظ شد.

تمرین Rollback روی کپی جدا، با بازگشت همه داده‌ها و Digest قدیمی موفق بود. بازگشت واقعی فقط اگر پایگاه پس از نصب تغییر نکرده باشد پذیرفته می‌شود:

```powershell
& E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\tools\deploy_roc_l8_index.py --rollback E:\KomatsoAI\runtime\roc-l8-onboarding\deployment-receipt.json
# پس از هماهنگی بازگشت، همان فرمان با --apply
```

فرمان اول فقط اعتبارسنجی است. اگر هش فایل جاری با installed_sha256 برابر نباشد، بازگشت می‌ایستد تا داده جدید بازنویسی نشود. برای خود کد، Commitهای همین شاخه در `ROC-L8-COMMITS.json` ثبت می‌شوند؛ revert باید به‌ترتیب معکوس و با حفظ کارهای بعدی انجام شود.

AGENTS ریشه از قبل gitignored بود. فقط دو افزودن مسیریابی انجام شد؛ patch در `ROC-L8-ROOT-AGENTS.patch` و Backup/هش در `runtime/roc-l8-onboarding/root-agents-receipt.json` است. کل AGENTS قدیمی به Git وارد نشد. پیش از معکوس‌کردن patch، هش فعلی کنترل شود. AGENTS مستقل ROC در بسته versioned است.

فعال‌سازی مجاز بعدی:

1. Commit/هش کد، شاخه، Schemaهای تازه و رسید نصب بررسی شود؛ هیچ diff نامرتبطی وارد نشود.
2. طبق روش موجود supervisor، با مجوز مستقل، Gateway در زمان کنترل‌شده به فرآیند تازه برسد؛ فرآیند موازی ساخته نشود. مفسر lifecycle همان venv خود Hermes است، نه venv پروژه.
3. Schema و منبع ROC در Process واقعی و نشست تازه کنترل شود؛ نشست قدیمی ممکن است Schema cache قدیمی داشته باشد.
4. Manual، D2، LF صریح، D4/X50، D5/B362 همراه مشاهده تصویر، و PC800/HD785-5 Smoke Test شوند.
5. فقط به شناسه آزمایشی مجاز بله تصویر ارسال شود و Message ID/رسید واقعی ثبت شود. سپس، در صورت موفقیت همان Process، عبارت LIVE VERIFIED مجاز است.

قابلیت رسمی reload-plugins کد نصب‌شده، بازکشی همه ماژول‌های Python واردشده و Schema نشست موجود را تضمین نمی‌کند؛ به همین دلیل در این مأموریت برای اعلام فعال‌سازی استفاده نشد. Scheduler، Senderها، Excel، Sync، HRMS، داشبورد و ثبت تعمیرات متوقف/بازسازی/بازارسال نشده‌اند.
