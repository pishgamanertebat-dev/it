# تحویل پیاده‌سازی ROC-L8

**READY FOR CONTROLLED ACTIVATION — نمایه نصب شده؛ Gateway عملیاتی فعال‌سازی نشده است.**

تاریخ: 2026-11-15، Asia/Tehran. PDFها، Scheduler، Sender، Excel/Sync، HRMS، داشبورد و دفتر تعمیرات در این کار بازنویسی/متوقف/بازارسال نشدند. فقط پایگاه نمایه اسناد فنی طبق دامنه مأموریت و پس از کنترل نصب تغییر کرد.

| پرسش مالک | موضوع | وضعیت | شاهد / حد پذیرش |
|---:|---|---|---|
| 1 | AGENTS مستقل | VERIFIED | ROC-L8/AGENTS.md با ۵ منبع، مدل و قرارداد نشست |
| 2 | پنج Document ID مستقل | VERIFIED | Manifest مجاز با SHA-256 و UNKNOWNهای صریح |
| 3 | فهرست کامل D1 | VERIFIED | ۳۳ بخش، تمام ۳۶۹ صفحه حساب‌رسی؛ ۳۶۹ فنی نیست |
| 4 | جداسازی L7/L8 در Search واقعی | VERIFIED | فیلتر page_models در Search/read/finish/Crop |
| 5 | ۶۳ شیت D4/D5 | VERIFIED | عنوان/شماره شیت و هویت از تصویر؛ پین‌ها فهرست جامع ندارند |
| 6 | بازیابی تصویری D1 | VERIFIED | ۶۵ رکورد نقشه، نسخه۴۴شیتی مستقل؛ دامنه مدل رعایت می‌شود |
| 7 | Parser Atlas Copco | VERIFIED | پروفایل atlas_bilingual، هندسه چرخیده و Refهای واقعی |
| 8 | استخراج D2 | VERIFIED | ۴۴۶ صفحه جدول، 5973 ردیف؛ ۱۰۲۳ صفحه حساب‌رسی |
| 9 | استخراج D3 | VERIFIED | ۶۴۱ صفحه سربرگ، 7264 ردیف؛ هشت جدول بدون قطعه صریح |
| 10 | پوشش و نقاط نامعلوم | PARTIAL | تمام سربرگ‌ها حساب‌رسی؛ PNهای نامعلوم و تأییدنشدن همه ردیف‌ها مستندند |
| 11 | دو کتاب بدون ادغام | VERIFIED | پیش‌فرض D2؛ D3 انتخاب صریح و UAT مقایسه مستقل |
| 12 | PN با/بدون فاصله | VERIFIED | حفظ صفر ابتدایی و نتیجه یکسان |
| 13 | Ref حرفی | VERIFIED | E01 و Refهای علامتی/دارای : و ,؛ PN نامعلوم ساخته نمی‌شود |
| 14 | پیوند تصویر/جدول | PARTIAL | شاهددار 368/418 مجموعه D2 و 583/619 مجموعه D3؛ بقیه بی‌دلیل متصل نشده‌اند |
| 15 | Manual/Parts/Render ابزارها | VERIFIED | مسیر واقعی ابزار + native image + Crop آزموده شد |
| 16 | Schema در پروفایل‌ها | PARTIAL | چهار فرآیند تازه VERIFIED؛ حافظه Gateway جاری UNKNOWN |
| 17 | Regression PC800/HD785-5 | PARTIAL | نمونه‌های معتبر و داده سالم؛ ۷ شکست سریال و یک Alias از مبنا باقی‌اند |
| 18 | سؤال فارسی Agent واقعی | VERIFIED | ۷ سناریو بدون پیام یا session DB عملیاتی |
| 19 | تحویل تصویر واقعی | VERIFIED | تصویر/Crop خوانا و Adapter ساختگی byte-identical؛ رسید کلاینت UNKNOWN |
| 20 | حفظ Production Index قدیمی | VERIFIED | هفت کتاب، meta، coverage، FTS و Digest ثابت |
| 21 | حفظ PDFها | VERIFIED | هش نهایی هر پنج فایل با ممیزی برابر |
| 22 | فعال‌سازی | PARTIAL | نمایه نصب شده؛ READY_FOR_ACTIVATION، Gateway reload/restart نشده |
| 23 | سریال/مدل/منابع باقی‌مانده | UNKNOWN | fit واقعی، Revisionهای نامشخص و منوال overhaul مستقل احراز نشده |
| 24 | Commit و Rollback | VERIFIED | شاخه مستقل، Commit receipt و بازگشت هش‌دار تمرین‌شده |
| 25 | مرجع دستگاه بعدی | VERIFIED | مرجع فارسی، چک‌لیست و قراردادهای واقعی پیاده‌سازی به‌روز شدند |

منبع اصلی قطعات D2 تنها پیش‌فرض است. تفاوت Ref4 در WAGON FRAME حفظ شد: `0147 1405 03` در PDF۱۲ D2 و `0147 1960 69` در PDF۳۳ D3. عدد داخل سند با قابلیت نصب یا سفارش روی دستگاه واقعی یکی نشده است.

جست‌وجوی Manual در نمونه‌ها ۹ تا۲۲ صفحه خواند، نه تمام ۳۶۹ صفحه. هر ۶۳ شیت مستقل تصویری و نسخه۴۴شیتی D1 قابل پیدا کردن و رندرند. آزمون B362 نشان داد مدل واقعاً صفحه و Crop را دید و اتصال را مطابق تصویر خواند؛ Metadata جای تصویر نشد.

نامزد نهایی در پایگاه تولیدی اسناد فنی نصب شد؛ integrity_check و حفظ هفت کتاب قدیمی تأیید شد. Backup کامل پیش از ROC و تمرین Rollback معتبرند. مسیر و هش دقیق در `ROC-L8-DEPLOYMENT-RECEIPT.json` است. کد و Schema چهار پروفایل در فرآیند تازه آزموده‌اند؛ نشست/Process جاری Gateway هنوز تأیید نشده و هیچ Restart یا Reload اجرا نشد. مجوز آزمون به مجوز Restart تعبیر نشده است.

وضعیت‌های قابل اعلام:

- ROC-L8 DOCUMENT PACKAGE COMPLETE — با محدودیت‌های ثبت‌شده منابع
- ROC-L8 MANUAL SEARCH READY
- ROC-L8 DUAL PARTBOOK INDEX READY — INDEXED_PARTIAL، هر نتیجه نیازمند تأیید ردیف
- ROC-L8 ELECTRICAL DIAGRAM SEARCH READY
- ROC-L8 TECHNICAL RENDERING READY — native inspection و Crop در Agent ایزوله تأیید شد
- DEVICE ONBOARDING REFERENCE UPDATED
- READY FOR CONTROLLED ACTIVATION

وضعیت کامل «LEGACY DEVICE REGRESSION PASSED» به‌صورت بدون قید اعلام نمی‌شود؛ عبارت دقیق «NO NEW REGRESSION IN TESTED TECHNICAL SURFACES؛ BASELINE FAILURES REMAIN» است. «ROC-L8 LIVE TECHNICAL TOOLS VERIFIED» نیز اعلام نمی‌شود.

مستندات همراه: MANUAL-INDEX-REPORT، DIAGRAM-INDEX-REPORT، PARTBOOK-COVERAGE، TOOL-INTEGRATION، TEST-RESULTS، REGRESSION-RESULTS، PERFORMANCE، KNOWN-LIMITATIONS، OWNER-UAT، DEPLOYMENT-AND-ROLLBACK و Manifest. شواهد قابل نسخه‌بندی در `ROC-L8-ACCEPTANCE-EVIDENCE.json` و Commitها در `ROC-L8-COMMITS.json` است؛ PDF، SQLite، PNG و transcript بزرگ در Git وارد نشده‌اند.
