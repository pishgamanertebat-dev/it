# ROC-L8 — گزارش پیش از Restart عملیاتی

**نتیجه: CONDITIONAL-GO برای فعال‌سازی کنترل‌شده در پنجره بعدی؛ Restart اکنون مجاز نیست.**

تاریخ: ۲۰۲۶/۱۱/۱۵، منطقه زمانی Asia/Tehran. آغاز مشاهده این ممیزی: ۱۴:۱۴؛ آخرین ثبت زنده و کنترل ۴۱ اثرانگشت: **۱۴:۴۱:۱۲ تهران** (`2026-11-15T11:11:12.867483+00:00`). زمان تولید گزارش: `2026-11-15T14:48:21.355067+03:30`. نتایج صف و اتصال‌های باز، نمونه‌برداری با زمان مستقل هستند و جای Preflight لحظه آخر را نمی‌گیرند.

فقط مرحله اول انجام شد. هیچ Restart، Reload، ارسال پیام، Retry، اجرای گزارش Scheduled، اصلاح تنظیمات، بازسازی یا جایگزینی نمایه انجام نشد. مجوز جدید Restart صادر نشده است. تصمیم حاضر مجوز اجرا یا ادعای LIVE_VERIFIED نیست.

## ۱. وضعیت فعلی Gateway

| مورد | یافته جاری و شاهد |
|---|---|
| Gateway پاسخ‌گو | PID **53564**؛ پاسخ مستقیم `identify/status` از Named Pipe، نه صرفاً فایل وضعیت |
| شروع Process | ۲۰۲۶/۱۱/۱۱ ساعت **۱۶:۴۷:۱۳٫۴۵۴۹۱۶ تهران**؛ UTC: `2026-11-11T13:17:13.454916Z` |
| وضعیت | `running`؛ `restart_requested=false`؛ Session Store برابر `ok` |
| کار فعال در آخرین پاسخ | `active_agents=0`؛ این فیلد در کد نصب‌شده مجموع Chat/Cron/API/Deferred را حساب می‌کند |
| شبکه | بله و تلگرام `connected`، بدون `needs_attention`؛ Auth واقعی و ارسال آزمایشی تازه انجام نشد |
| پروفایل‌های میزبانی‌شده | `default`, `admin`, `maintenance`, `net` |
| Supervisor واقعی | PID **59268**؛ state=`running`، child launcher=**25488**، generation=`53262bd6b4ef4b35ad6803ffcf4d94a5`، attempt=1 |
| Process موازی | Gateway مستقل دوم در Snapshot سیستم دیده نشد |

زنجیره واقعی Processها: **53328 → 59268 → 25488 → 53564**. PIDهای 53328 و 25488 Launcher محیط مجازی‌اند؛ 59268 Supervisor و 53564 Worker اصلی‌اند. وجود Base Python و venv در این زنجیره چهار Gateway محسوب نمی‌شود. Parent اولیه 53328 برابر 39500 ثبت شده و هنگام بررسی دیگر زنده نبود؛ مرجع فعلی بازیابی، Supervisor 59268 است.

`active_work=null` به‌تنهایی معیار خالی‌بودن صف نیست: کد فقط هنگام draining جزئیات آن را منتشر می‌کند. نتیجه بی‌کاری از شمارنده زنده، Leaseها، Routing، Delivery Ledger، وضعیت Cron و Processها به دست آمده است. Snapshotها اتمیک و هم‌زمان با هم نیستند؛ فاصله بررسی تا اجرا باید با Gate تازه پوشش داده شود.

Gateway تنها میزبان اسناد فنی نیست: مسیریابی و مجوز کاربران، بله/تلگرام، ابزارهای تعمیرات و NET، ابزارهای فنی و Cron پروفایل‌ها در همین Process‌اند. Restart آن بر دریافت و پاسخ این چهار پروفایل و Sync داخلی وقفه می‌اندازد. Scheduler گزارش‌ها Task مستقل است و نباید Restart شود.

## ۲. وضعیت Git و نسخه نصب‌شده

شاخه فعلی: `technical-docs/roc-l8-onboarding-20261115`. HEAD پروژه: `ebfb190fa92bb78c94733ad41eb801cf5972bbec`.

| Commit | نقش |
|---|---|
| `9bf3bb01510f6a33a9cfbfac06053dae55132350` | پیاده‌سازی ROC، دو کتاب Atlas و شواهد تصویری |
| `af4ca6954e942a650182fa36fbbf4c2448abe7c4` | پذیرش، محدودیت‌ها و تحویل کنترل‌شده |
| `ebfb190fa92bb78c94733ad41eb801cf5972bbec` | اصلاح هویت Revision؛ پسوند 20D مدرک Revision مستقل نیست |
| `ae0466e16478e6e0d43bdd6629bb947cf444e3f2` | مبنای پیش از ROC |

پیش از تولید این دو گزارش، خروجی `git status --porcelain` پروژه خالی و diff ثبت‌شده صفر بود. Git برای یک پوشه نامرتبط `work_orders/oil_change/1405-08/manager_preview_mos4ziv3` هشدار Access denied داد؛ بنابراین خواندن محتوای همه فایل‌های ignored پروژه ادعا نمی‌شود. مسیرها و Hashهای مرتبط با این فعال‌سازی جداگانه کنترل شدند. دو گزارش PRE-RESTART در این مأموریت ساخته شدند و Commit جدیدی انجام نشد. چون قانون موجود `*.md` گزارش جدید را پنهان می‌کرد، فقط یک استثنای دقیق برای همین گزارش به `.gitignore` افزوده شد تا هر دو خروجی در Git قابل‌Review باشند؛ این تنها تغییر فایل tracked است و تنظیم عملیاتی نیست.

نصب Hermes: `C:\Users\win-10\AppData\Local\hermes\hermes-agent`. نسخه Core **0.21.4**؛ HEAD نصب و Code SHA اعلام‌شده در Process زنده هر دو **`e7208cd01179d21494b7f46478cd7db85481ed90`** هستند. تغییر ثبت‌نشده در فایل‌های tracked نصب دیده نشد؛ untrackedهای نصب در این دستور شمارش نشدند. این SHA مربوط به Core Hermes است و نباید با Commit پروژه KomatsoAI یکی گرفته شود.

مفسر رسمی Lifecycle: `C:\Users\win-10\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe`؛ `pyvenv.cfg`، CPython **3.11.15** از مسیر Base زیر را نشان می‌دهد:

`C:\Users\win-10\AppData\Roaming\uv\python\cpython-3.11.15-windows-x86_64-none\python.exe`

مفسر پروژه `E:\KomatsoAI\.venv\Scripts\python.exe` برای آزمون/خواندن PDF استفاده شد، نه برای Lifecycle. فرمان موجود `hermes` نیز به `C:\Users\win-10\AppData\Local\hermes\bin\hermes.exe` حل می‌شود؛ در مرحله دوم بهتر است مفسر نصب‌شده به‌طور صریح به کار رود.

تمام **25 فایل** نام‌برده در `ROC-L8-COMMITS.json` با Hash جاری برابرند. اصلاح سوم نیز در همین Receipt لحاظ شده است. جمعاً **۴۱ فایل محافظت‌شده** شامل PDF، کد، رجیستری، AGENTS، تنظیم برنامه، Launcher، نمایه و Backup در کنترل نهایی بدون تغییر بودند. Hash چهار Config پروفایل در `final-check.json` ثبت شد؛ هیچ Config و Startup/Network تغییری نکرد.

## ۳. وضعیت بارگذاری Plugin و Lifecycle

فایل Canonical Plugin:

`E:\KomatsoAI\integrations\hermes\plugins\komatso-technical-docs\__init__.py`

| پروفایل | Loader نصب‌شده | نتیجه بارگذاری تازه و محلی |
|---|---|---|
| default | `C:\Users\win-10\AppData\Local\hermes\plugins\komatso-technical-docs\__init__.py` | هر چهار کنترل Schema موفق |
| admin | `C:\Users\win-10\AppData\Local\hermes\profiles\admin\plugins\komatso-technical-docs\__init__.py` | هر چهار کنترل Schema موفق |
| maintenance | `C:\Users\win-10\AppData\Local\hermes\profiles\maintenance\plugins\komatso-technical-docs\__init__.py` | هر چهار کنترل Schema موفق |
| net | `C:\Users\win-10\AppData\Local\hermes\profiles\net\plugins\komatso-technical-docs\__init__.py` | هر چهار کنترل Schema موفق |

هر چهار Loader یکسان‌اند و از طریق `exec(compile(...))` فایل Canonical پروژه را می‌خوانند؛ کپی قدیمی جدا از Plugin در این مسیرها پیدا نشد. `document_id` و enum واقعی اسناد، `inspect_images`، `book_id` شامل D3 و `document_id` در Crop در بارگذاری محلی تازه تأیید شدند. `komatso-technical-docs` در Configها فعال است و تنظیم مدل `gpt-6.1-sol` با Provider `openai-codex` ثبت شده است. این آزمون API مدل یا Agent عملیاتی ایجاد نکرد.

**وضعیت Schema در حافظه Gateway فعلی همچنان UNKNOWN است.** Process از ۱۱ نوامبر و پیش از Commitهای ROC زنده است؛ کنترل خواندنی شناخته‌شده `identify/status` فهرست اشیای ماژول یا Schema نشست‌های Cache را ارائه نمی‌دهد. خواندن دیسک، فایل Prompt یا موفقیت Unit Test به‌عنوان اثبات بارگذاری ROC در همین Worker معرفی نمی‌شود.

روش رسمی موجود، `hermes_cli.gateway_windows.restart()` است: Lifecycle lock → درخواست توقف پایدار Supervisor → Planned-stop marker و Drain → احراز غیبت Worker قبلی → Start زیر Supervisor یکتا → کنترل آمادگی. کد PID را با زمان ایجاد تطبیق می‌دهد و اگر Supervisor متوقف نشود از ساخت Process رقیب امتناع می‌کند. لاگ قبلی ۱۱ نوامبر، توقف intentional و راه‌اندازی همین نسل با سلامت Worker 53564 را نشان می‌دهد؛ در این مرحله Restart آزمایشی انجام نشد.

نکته حیاتی: CLI ویندوز Drain را حداکثر **۳۰ ثانیه** محدود می‌کند و پس از آن می‌تواند Processهای هدف را Force terminate کند؛ فرمان رسمی تضمین نمی‌کند که کار طولانی نامحدود منتظر بماند. به همین دلیل آغاز با کار فعال ممنوع و Quiet gate لحظه اجرا ضروری است.

`start()` همچنین `reconcile_scheduled_task()` را فراخوانی می‌کند و در صورت Drift می‌تواند Task/Launcher را بازثبت کند. XML زنده و Template فعلی **قابل‌خواندن، قابل‌Parse و بدون Drift** بودند؛ بنابراین در شرایط ثبت‌شده نیازی به تغییر Task نیست. مرحله دوم باید همین نتیجه را دوباره احراز کند؛ Drift تازه به معنی توقف و ارزیابی دوباره است. هیچ Reconcile یا نصب Task در این ممیزی فراخوانی نشد.

Task `Hermes_Gateway` در Snapshot حالت `Ready` داشت؛ این با Supervisor جداشده فعال سازگار است و نشانه خاموشی Worker نیست. Task `KomatsoAI Schedules` مستقل، تکرار یک‌دقیقه‌ای، `IgnoreNew` و مفسر پروژه دارد. یادداشت قدیمی `windows-gateway-supervisor-v0.21.4.md` به‌تنهایی مرجع Lifecycle امروز نیست؛ کد نصب‌شده و XML واقعی بررسی شدند.

## ۴. وضعیت PDFها، AGENTS و رجیستری‌ها

| سند | Document ID واقعی | صفحات | کنترل جاری |
|---|---|---:|---|
| D1 | `roc-l8-trainee-9852960001` | 369 | تطابق SHA-256 و تعداد صفحه |
| D2 | `roc-l8-parts-9853647820d` | 1023 | تطابق SHA-256 و تعداد صفحه |
| D3 | `roc-l8-lf-parts-9853678620` | 1440 | تطابق SHA-256 و تعداد صفحه |
| D4 | `roc-l8-cabin-9840042409` | 16 | تطابق SHA-256 و تعداد صفحه |
| D5 | `roc-l8-electrical-9840042418` | 47 | تطابق SHA-256 و تعداد صفحه |

مسیر و Hash واقعی پنج PDF:

- D1: `ROC-L8/ROC-L8_ShopManual.pdf`؛ SHA-256: `1216b841239be1eda0b0f5cd4c051bdf66c6024a62aabef5c7d47ec7a6ba766f`
- D2: `ROC-L8/ROC-L8_PartBook.pdf`؛ SHA-256: `0749517569bb35de78d9898e47987b9bee60c5dd7b26104638a522a16806bc56`
- D3: `ROC-L8/ilide.info-roc-l8-30-lf-2011-model-9853-6786-20-pdf-pr_89055a09f0f869abf3be39c9e81fca1c.pdf`؛ SHA-256: `ed143535a1f1f46e651a7186a988e24b85772fb702e102ed88ee9d69b7637cb5`
- D4: `ROC-L8/ilide.info-9840-0424-09-electrical-diagram-cabin-pr_f7a76ce65c3567cd4f68a2c4edf4b85e.pdf`؛ SHA-256: `cf9bdc7db23bb145995b6edae515f50f3248ff7cbc23e235f440e0a8cd646c1a`
- D5: `ROC-L8/ilide.info-9840-0424-18-electrical-diagram-pr_0a0b062345b5137ccb3f8d7935015f42.pdf`؛ SHA-256: `e32701757674d981742d862730ea9c97698ed2e95785a571adb288ed25090af9`

`ROC-L8/document_manifest.json` با پنج فایل اصلی، `manual_sections.json` با **33 بخش**، `diagram_index.json` با **128 رکورد**، `tools/manual_models.json` و `tools/fleet/partbook_fleet_registry.json` کنترل شدند. از ۱۲۸ رکورد نقشه، ۶۳ شیت مستقل D4/D5 و ۶۵ رکورد داخل D1 هستند؛ نسخه ۴۴شیتی داخل D1 با نسخه ۴۷شیتی D5 ادغام نشده است.

AGENTS ریشه از قبل ignored است؛ Hash آن **`05be817cfdf8b138db8db13753caa2349c31e72155c6ba4518443953b3b8bac2`** با Receipt برابر است. افزودن ROC و گزینه هشتم مسیریابی حاضرند؛ AGENTS مستقل ROC نیز با Hash Commit برابر است. `git apply --reverse --check reports/technical-docs/ROC-L8-ROOT-AGENTS.patch` بدون اعمال تغییر موفق شد. Backup ریشه و Hash آن ثبت شد؛ بازگشت بعدی نباید کل AGENTS قدیمی را روی کارهای تازه بنویسد.

محدودیت‌های صفحه در آزمون تازه بررسی شدند: صفحات اختصاصی L7/L740/L740CR رد می‌شوند؛ هیدرولیک L8 به صفحات ۳۱۲–۳۱۵ محدود می‌شود، PLC L830 محدوده ۲۹۲–۳۰۳ دارد و صفحه FILEMARKET شماره ۳۶۹ از پاسخ فنی حذف شده است. D1 سند آموزشی چندمدلی است؛ وجود آن به معنای منوال کامل تعمیر اساسی یا شناسایی موتور واقعی نیست.

## ۵. وضعیت دو Part Book و اتصال‌های SQLite

نمایه واقعی: `runtime/partbook/partbook_index.sqlite`، اندازه **65,740,800 بایت**؛ SHA-256:

`6b18429dffeca3d20df5e192cd6a46923bb38207f1d58be97ad1579a231d3a81`

این Hash با Receipt نصب، Candidate نهایی و رسید Commit برابر است؛ Receipt داخل runtime و نسخه گزارش نیز یکسان‌اند. `integrity_check=ok`، journal mode برابر **DELETE** و `user_version=0` است. نبود شماره مهاجرت در `user_version` به‌تنهایی معیار Schema نیست؛ جدول‌ها، رکورد books، Digestها و قرارداد Reader جدا کنترل شدند.

| کتاب | هویت / پیش‌فرض | صفحات / ردیف | نتیجه |
|---|---|---|---|
| `ROC-L8-D2` | ROC L8، پیش‌فرض مالک؛ `default_search_enabled=true` | ۱۰۲۳ / ۵۹۷۳ | Digest شاخص و Atlas با Candidate پذیرفته‌شده برابر |
| `ROC-L8-LF-D3` | ROC L8(30)LF مستقل؛ پیش‌فرض عمومی خاموش | ۱۴۴۰ / ۷۲۶۴ | Digest مستقل برابر؛ انتخاب صریح یا Alias LF مجاز |

هر دو از Parser `atlas_bilingual` و نسخه استخراج `atlas-bilingual-1` استفاده می‌کنند. Source PDF و SHA رکورد books با Manifest یکسان‌اند. Serial range و Revision کلی ناشناخته مانده‌اند؛ شماره روی جلد به سریال تبدیل نشده است.

WAL، SHM و rollback-journal در Snapshot نمایه وجود نداشتند. Reader عملیاتی `partbook_lookup.connect()` پایگاه را با `mode=ro` باز می‌کند. بازکردن خواندنی Windows با `FILE_SHARE_READ` موفق بود؛ در آن لحظه Handle نویسنده متعارض وجود نداشت. فهرست Resourceهای Windows نیز در **۱۴:۳۹:۰۱ تهران** با `RmGetList` خالی بود؛ هیچ API توقف/Restart مربوط به Restart Manager فراخوانی نشد. این دو بررسی، وضعیت همان لحظه را ثابت می‌کنند، نه قفل دائمی در برابر Writer آینده. اتصال‌های همین ممیزی با context manager بسته شدند.

هیچ migration، rebuild یا جایگزینی جدید برای فعال‌سازی لازم نیست. اگر هنگام اجرای مرحله دوم Hash، Journal mode، Sidecar یا Writer عوض شد، حتی با مجوز قبلی نباید نمایه قدیمی جایگزین شود.

## ۶. وضعیت سایر دستگاه‌ها و شواهد آمادگی

هفت کتاب حفظ‌شده: `HD465-7R`, `HD785-5`, `HD785-7-B1`, `HD785-7-B2`, `PC1250SP-8R`, `PC800-8`, `WA600-6-2010`. رکورد books، Digest منطقی هر کتاب و coverage آن با مبنا/Backup برابر است. **۲۰ کلید meta** و **۳۸۴۸ ردیف figures_fts قدیمی** نیز در خواندن جاری عیناً حفظ شده‌اند.

در این مرحله مجموعه متمرکز `tools/test_roc_l8.py` دوباره اجرا شد: **۲۷ آزمون، صفر شکست و صفر Error، ۲۴٫۵۴۲ ثانیه**. پایگاه آزمون `runtime/roc-l8-onboarding/acceptance-final.sqlite` بود و Hash آن با Production برابر است. آزمون‌ها شامل این موارد بودند:

- Feed، Compressor، هیدرولیک L8، PLC، محدوده مدل و رد صفحات نامرتبط؛ سؤال خارج پوشش بدون شاهد ساختگی.
- PN فاصله‌دار و بدون فاصله با صفر ابتدایی، Ref حرفی E01، Ref علامتی، See page و جدول ادامه‌دار.
- پیش‌فرض صرفاً D2؛ انتخاب صریح LF؛ Ref 4 در WAGON FRAME دو کتاب: PN `0147 1405 03` در D2/PDF12 در برابر `0147 1960 69` در D3/PDF33؛ تأیید از PDF و عدم ادعای fit.
- Document ID، اتصال retrieve/finish به همان سند و نشست، D4/X50، D5/B362، استقلال ۴۴ و۴۷ شیت، رندر واقعی و Crop، قرارداد تصویر و رد مسیر/سند نامعتبر.
- Manualهای PC800 و HD785-5؛ بازخوانی PN `209-01-42232` در PC800 و `6212-11-1103` در HD785-5 از PDF واقعی.

هشدار عمدی `text-fixture/non-vision` مربوط به آزمون fallback موفق است؛ اثبات دیدن تصویر توسط مدل واقعی نیست. این مرحله هیچ سؤال تازه‌ای به مدل واقعی نفرستاد و Agent تازه در Worker عملیاتی ایجاد نکرد.

شواهد قبلی که خوانده شدند اما دوباره کامل اجرا نشدند: ۸۲ آزمون متمرکز موفق، هفت سناریوی Agent واقعی ایزوله، و Regression قطعات با ۶۳ موفق از ۷۰ آزمون. **هفت شکست سریال و یک شکست Alias** قبلاً روی مبنای دست‌نخورده بازتولید شده‌اند؛ نام کامل آن‌ها در `legacy-baseline-failures.json` و `shared-technical-regression.json` ثبت است. این شکست‌ها رفع‌شده یا آزمون‌های امروز معرفی نمی‌شوند و شکست تازه احتمالی نیز نباید به آن‌ها نسبت داده شود. Regression عملیاتی پس از Restart همچنان PENDING است.

## ۷. Scheduler، ارسال‌های جاری و جلوگیری از تکرار

مرجع زنده `settings/schedules.yaml` است. `tools.scheduler.runner --check` با خروجی صفر و بدون Tick/Send اجرا شد؛ هشت برنامه فعال فعلی:

| زمان تهران | Schedule ID |
|---|---|
| ۰۷:۴۵ | `overflow_daily_test`, `overflow_daily_mechanical` |
| ۰۹:۰۰ | `driver_daily_office_supervisor`, `driver_daily_mechanical`, `driver_daily_metalwork` |
| ۱۲:۰۰ | `maintenance_daily_report` |
| ۱۵:۰۰ | `oil_balance_daily_report`, `machinery_performance_daily_report` |

Preflight ساعتی در ۶،۷،۸،۱۱،۱۴؛ یادآور مکانیک ۸ و۱۵؛ تعمیرات ۱۱ و۱۵؛ روغن ۱۱ و۱۶؛ عملکرد ۱۴ و۱۷ فعال‌اند. ارسال خلاصه مدیریتی و Final-copy outbox نیز از Tick مستقل Scheduler عبور می‌کنند؛ این‌ها با Restart Gateway دوباره ساخته یا Trigger نمی‌شوند. همه گزارش‌های بالا policy=`replay` و horizon هفت‌روزه دارند؛ مهلت مستقل کمبود داده **۳۶ ساعت از original due** در کد جاری حفظ شده است.

در Snapshot حدود ۱۴:۳۲، Ledger گزارش‌ها `integrity_check=ok` بود: runs=321، receipts=532، artifact receipts=280. در آخرین کنترل ۱۴:۴۱، شمار `running/sending/attempting` در **هر سه جدول صفر** بود. کلیدهای یکتای occurrence، گیرنده/نوع و گیرنده/Artifact سالم‌اند و duplicate key صفر است. Task Scheduler با `IgnoreNew` و `worker.lock` اجرای هم‌زمان Tick را محدود می‌کند. `_claim_replay()`، وضعیت `sending` باقی‌مانده را `uncertain` می‌کند؛ `office_receipts.record()` رسید `sent/uncertain` را حفظ می‌کند و مسیر Office این دو را دوباره نمی‌فرستد. Exactly-once برای خطای مبهم شبکه ادعا نمی‌شود.

صف Gateway در چهار state.db جدا بررسی شد: **۲۵ + ۲ + ۳ obligation همگی delivered**؛ admin جدول obligation نداشت. هیچ obligation در pending/attempting/failed، هیچ turn lease و هیچ resume_pending در Routing مشاهده نشد. quick_check چهار پایگاه ok و integrity کامل state.db اصلی ok بود. پوشه `pending_messages` خالی بود. بنابراین در نمونه‌برداری، بازیابی پاسخ معوق شناخته‌شده‌ای برای Startup وجود نداشت. خود Hermes قابلیت redelivery و auto-resume دارد؛ اگر لحظه آخر چنین ردیفی پیدا شود، این نتیجه معتبر نیست و باید متوقف شد.

تنها Cron تعریف‌شده Gateway، **mine-file-sync** با شناسه `3ccb658aed3c`، هر دو دقیقه، `no_agent=true` و مقصد `local` است. پروفایل‌های دیگر Job تعریف‌شده نداشتند. Snapshot اجراهای Cron: ۱۰۰۰ رکورد completed و ۱۸۸ incident resolved. Claimها و completed-occurrence از اجرای همان موعد جلوگیری می‌کنند؛ رجیستری سیاست external misfire این Sync را `latest_only` تعیین کرده است. ممکن است موعد تازه/آخرین Sync پس از Startup اجرا شود؛ ادعای «هیچ Jobی در Startup اجرا نمی‌شود» درست نیست. هیچ Job گزارش قدیمی داخل رجیستری Gateway مشاهده نشد. Sync طبق آخرین State بررسی‌شده موفق، worker پایان‌یافته و `sync_healthy=true` بود.

موارد تاریخی جدا از ارسال جاری:

- چهار receipt و سه artifact receipt با `uncertain` و Message ID خالی وجود دارند؛ رسید موفق محسوب نمی‌شوند. occurrenceهای مربوط hold دارند و بازفرستادن آن‌ها مجاز نیست.
- سه waiting قدیمی تعمیرات/روغن با hold=`held/recovered` محفوظ‌اند. تابع `recovery_review.held()` هر دو وضعیت را می‌بندد، حتی اگر وضعیت قدیمی runs هنوز waiting باشد. دو Job قدیمی حذف‌شده `repairs_daily/metalwork_daily` در برنامه فعال نیستند.
- روغنِ موعد **۱۴ نوامبر ساعت ۱۵** هنوز `waiting_for_data/date_missing` است؛ Retry طبیعی حدود هر۱۵ دقیقه در Scheduler مستقل جریان دارد. این کار در این مأموریت Trigger نشد و نباید موفق/ارسال‌شده معرفی شود.
- یک `report_preflight_runs.running` از **۱۸ اکتبر** مانده است؛ زمان `checked` قدیمی و Process متناظر فعالی دیده نشد. این رکورد، ارسال جاری نیست؛ پاک یا اصلاح نشد.
- ۱۴۳ receipt تجمیعی `sent` فاقد Message ID مستقیم‌اند: ۱۴۲ گزارش و یک notice تاریخی. ۱۴۲ مورد دارای دست‌کم یک artifact receipt با Message ID هستند؛ این پیوند به‌تنهایی اثبات کامل‌بودن تمام اجزای هر گزارش نیست. notice قدیمی مدرک ID مستقیم ندارد. وضعیت‌های تاریخی بازنویسی نشدند و از شمار raw sent برای اثبات تحویل کامل استفاده نشد.

در زمان Snapshot، گزارش‌های صبح و ۱۲ امروز succeeded بودند؛ گزارش‌های **۱۵ امروز هنوز موعد نرسیده بودند**. وضعیت پس از ساعت۱۵ از این گزارش قابل استنتاج نیست. هیچ ارسال تکراری ناشی از این ممیزی ایجاد نشد؛ نبود تکرار در تمام تاریخ یا آینده از شمار Snapshot نتیجه گرفته نمی‌شود.

## ۸. خطرات Restart

| خطر | کنترل لازم |
|---|---|
| قطع پاسخ فنی/NET یا دریافت بله/تلگرام | Quiet gate واقعی؛ active work صفر؛ Drain رسمی؛ نشست تازه پس از بالا آمدن |
| پایان اجباری پس از ۳۰ ثانیه | شروع فقط در بی‌کاری؛ پایان Sync/کار API نیز کنترل شود؛ در شکست، علت ثبت و از تکرار کور جلوگیری شود |
| اجرای پاسخ معوق یا Cron پس از Startup | Ledger/Resume/Claim تازه بررسی شود؛ تنها آخرین Sync مجاز طبیعی ادامه یابد |
| تداخل با گزارش‌های۱۵ و Retry روغن | فعال‌سازی به پس از آرام‌شدن این پنجره موکول شود؛ Scheduler و Queue آن تغییر نکند |
| تغییر خودکار Task توسط CLI | مقایسه XML لحظه آخر؛ Drift تازه مساوی توقف |
| خطای شبکه موجود قبل از Restart | خطاهای ۱۴:۰۲–۱۴:۰۴ در baseline ثبت است؛ بازیابی polling در ۱۴:۰۳:۵۸ بله و ۱۴:۰۴:۳۱ تلگرام رخ داده؛ بعداً با زمان/PID جدید مقایسه شود |
| Schema Cache قدیمی | فقط Process و نشست تازه و فراخوانی واقعی ابزار ملاک پذیرش است |
| تغییر Index توسط فرآیند دیگر | Hash، Sidecar، Writer و Receipt تازه؛ ممنوعیت restore کور |

لاگ Supervisor در ۱۲ نوامبر `startup_timeout` تاریخی دارد؛ این سابقه پنهان نشد. پاسخ زنده فعلی سالم است، اما فایل Supervisor و وجود PID به‌تنهایی اثبات سلامت تمام قابلیت‌ها نیست. وقفه یا پایداری آینده اندازه‌گیری نشده است.

## ۹. سازگاری کد و نمایه

کد جاری با Schema نصب‌شده، دو book_id، Atlas metadata و Document IDها در آزمون تازه سازگار است. Hash Candidate آزموده‌شده عین Production است؛ Digest منطقی هر دو کتاب و هفت کتاب قدیمی نیز تأیید شد. تغییر Schema در Startup لازم نیست و نباید فعال‌سازی را به Build یا Migration تبدیل کرد.

سازگاری ترکیب **Backend قدیمی + نمایه جدید** در این مرحله به‌طور جامع آزموده نشده و برای Rollback فرض نمی‌شود. در صورت نیاز به بازگشت کامل، Gateway هدف باید متوقف و جفت هم‌خوان کد/رجیستری/AGENTS/نمایه آماده شود؛ سپس Startup و Regression دوباره انجام شود. اگر داده تازه مانع restore نمایه شد، بازگشت ترکیبی نامطمئن نیز مجاز نیست.

## ۱۰. اعتبار Backup و Rollback مستقل

| جزء | وضعیت و مسیر |
|---|---|
| Backup کامل پیش از ROC | `E:\KomatsoAI\runtime\roc-l8-onboarding\production-before-ROC-20261115T130855.sqlite`؛ Hash و integrity جاری معتبر؛ دقیقاً هفت کتاب قدیمی |
| SHA-256 Backup کامل | `64815c762c81875d3c761058aad4902cf2d3984c036926f5b3acf3e4751a2ef9` |
| Backup بلافاصله پیش از نصب نهایی | `E:\KomatsoAI\runtime\roc-l8-onboarding\production-before-ROC-20261115T132603.sqlite`؛ Hash=`8c6fd4170b2fce73b69f6263bfbd4750c05dcb95cd8886c51643ba914a0abccc`؛ هر۹ کتاب، قبل از اصلاح نهایی |
| Candidate نهایی | `E:\KomatsoAI\runtime\roc-l8-onboarding\acceptance-final.sqlite`؛ Hash و integrity معتبر و برابر Production |
| Root AGENTS | Backup مستقل، Hash جاری مطابق Receipt؛ reverse patch check موفق بدون Apply |
| کد و رجیستری | سه Commit روشن؛ بازگشت معکوس ebfb190، af4ca69، 9bf3bb0 پس از بررسی کارهای بعدی |

`before_sha256` رسید نهایی برابر `51cc20eb...` مربوط به نصب میانی است و با SHA Backup کامل پیش از ROC تفاوت دارد؛ این تفاوت خرابی Backup نیست. Receipt نهایی عمداً هم Backup کامل و هم Backup بلافاصله قبل از اصلاح را نگه داشته است. مسیر بازگشت کامل، فیلد `backup` و Hash مخصوص آن است.

دستور زیر **فقط اعتبارسنجی** شد و موفق بود؛ `--apply` اجرا نشد:

```powershell
& E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\tools\deploy_roc_l8_index.py --rollback E:\KomatsoAI\runtime\roc-l8-onboarding\deployment-receipt.json
```

Receipt، تمرین قبلی Rollback روی کپی جدا را VERIFIED ثبت کرده است؛ این مرحله Hash، integrity، داده قدیمی و Guard خود دستور را دوباره کنترل کرد و تمرین جایگزینی دیگری نساخت. ابزار Rollback اگر Hash جاری با `installed_sha256` فرق کند یا Backup عوض شده باشد، پیش از Apply می‌ایستد. خود شاخه Rollback ابزار، حفظ Writer/اتصال‌های زنده را به‌جای هماهنگی اپراتور تضمین نمی‌کند؛ توقف Gateway هدف و احراز نبود Writer/Sidecar نیز لازم است.

| سناریوی شکست مرحله دوم | واکنش ازپیش‌تعریف‌شده |
|---|---|
| Worker بالا نیاید | لاگ Supervisor/stdio، مرحله شکست و Exit ثبت شود؛ فقط یک تلاش برنامه‌ریزی‌شده؛ از ساخت Gateway موازی یا Restart پیاپی خودداری؛ اگر crash loop ادامه یافت توقف رسمی فقط هدف برای حفظ شواهد |
| Plugin Import/ثبت نشود | مسیر Loader، Canonical، traceback و permissions با baseline مقایسه شود؛ Core/Network/Task بی‌دلیل تغییر نکند؛ در صورت نیاز بسته قبلی هم‌خوان بازگردد |
| فقط ROC خراب، دستگاه‌های قبلی سالم | ادعای LIVE_VERIFIED رد شود؛ نیاز به Rollback مشترک ارزیابی شود؛ SQLite قدیمی خودکار restore نشود؛ محدودکردن/برگرداندن مسیر ROC تنها با diff قابل‌بررسی و بدون حذف قابلیت دیگر |
| Regression جدید جدی در PC800/HD785-5 | پذیرش متوقف؛ بازگشت بسته هم‌خوان با حفظ کارهای بعدی و آزمون مجدد؛ به baseline سریال/Alias نسبت داده نشود |
| Backend قدیمی Schema را نفهمد | ترکیب نامطمئن بالا نیاید؛ نمایه و کد/رجیستری هم‌نسخه در حالت متوقف آماده شوند |
| نمایه بعد از نصب تغییر کرده باشد | Guard باید STOP بدهد؛ بدون review داده تازه، Backup قدیمی جایگزین نشود؛ در صورت نامشخص‌بودن ایمنی، BLOCKED |
| Gateway از قبل مشکل داشته باشد | PID/نسخه، خطاهای شبکه و startup_timeout ثبت‌شده با خطای تازه و Timestamp پس از Restart تفکیک شوند |

کد، رجیستری، AGENTS و SQLite چهار دامنه مستقل بازگشت‌اند. Commit revert قرار نیست فایل‌های ignored، همه AGENTS یا داده تازه SQLite را خودکار مدیریت کند. هیچ `git reset`، revert، patch معکوس یا Restore تولیدی اجرا نشد.

## ۱۱. آزمون‌های لازم پس از Restart

این جدول برنامه مرحله دوم است؛ نتیجه همه ردیف‌ها اکنون **PENDING** است. مدل تنظیم‌شده فعلی `gpt-6.1-sol` است؛ مدل واقعاً انتخاب‌شده، نام ابزار، Document ID، صفحه، Trace و خطا باید هنگام اجرا ثبت شود.

| Test ID | درخواست / انتظار قابل‌پذیرش | مسیر شاهد |
|---|---|---|
| LIVE-BOOT | PID تازه و یکتا، Supervisor سالم، چهار پروفایل و شبکه/Auth/Routing قبلی | CIM، control زنده، لاگ بازه جدید و XML/Config ثابت |
| LIVE-SCHEMA | سه ابزار در نشست Agent تازه؛ Schema شامل Document ID، D3 و inspect_images | Trace فراخوانی واقعی؛ صرف Schema محلی یا Process کافی نیست |
| ROC-FEED | سؤال فارسی درباره Feed؛ شاهد مدل معتبر و بدون انتقال L7 | maintenance_manual_evidence؛ D1، صفحه واقعی و محدوده مجاز |
| ROC-HYD | هیدرولیک L8، رد شاهد اختصاصی L7 | D1، محدوده۳۱۲–۳۱۵؛ ثبت صفحات استفاده‌شده |
| ROC-D2 | PN `0147 1405 03` و `0147140503`، تأیید PDF و عدم fit قطعی | maintenance_partbook_lookup؛ D2، جدول۱۲ و شکل۱۱ |
| ROC-LF | درخواست ROC L8(30)LF و Ref4 WAGON FRAME | D3، جدول۳۳ و شکل۳۲؛ مقایسه مستقل با D2 |
| ROC-D4 | X50 / برق کابین | D4 شناسه واقعی؛ شیت۲/۴ مطابق شاهد، نه ID حدسی D4 |
| ROC-D5 | B362 / نقشه اصلی | D5، شیت۳۰/۴۷؛ مشاهده native واقعی و Crop؛ حفظ نسخه۴۴شیتی D1 |
| ROC-UNKNOWN | تلرانس/گشتاور Overhaul فاقد منبع کافی | نبود عددسازی، بیان کمبود منوال موتور/هویت واقعی |
| REG-PC800 | Manual و PN معتبر PC800 | هویت PC800-8 در برابر درخواست PC800-8R جدا؛ PDF و تصویر قدیمی |
| REG-HD7855 | سؤال retarder معتبر و PN شاهددار | Manual HD785-5؛ عدم اختلاط مدل |
| REG-OTHER | در صورت امکان مدل سوم و Routing قبلی | برای نمونه HD785-7؛ نتیجه واقعی ثبت شود، از قبل PASS فرض نشود |
| BALE-UAT | متن، تصویر و Crop خوانا با ID سند/صفحه درست | فقط مقصد صریح مجاز؛ Message ID، فایل/تصویر دریافتی و تأیید انسانی جدا |
| LIVE-AFTER | سلامت نمایه و PDFها، عدم خطای تازه/نشانه ارسال تکراری | Hash و integrity، Ledger و لاگ فقط در بازه مشاهده‌شده |

برای Smoke در Worker واقعی، مسیر قابل‌فهم این است: پیام آزمایشی مجاز از DM تست → مسیریابی Registry/Auth موجود → نشست تازه Agent → ابزار Technical Docs → MEDIA همان نشست → Adapter بله. قبل از هر پیام باید مقصد و مجوز مشخص باشد. Loader تازه در CLI یا Import محلی، جانشین اثبات ابزارهای همین Worker نیست؛ اگر فقط آن مسیر قابل‌آزمون بود، نتیجه محدود و پذیرش LIVE_PARTIAL/UNKNOWN ثبت شود. هیچ endpoint تازه، Hook آزمایشی یا مجوز جدیدی برای تحمیل آزمون ساخته نشود.

## ۱۲. آمادگی محیط تست بله

Adapter بله متصل، Loader و ابزارها نصب و مسیر تصویر/Crop محلی در آزمون تازه موفق‌اند. آزمون Adapter ساختگی قبلی صرفاً مسیر Bytes را اثبات کرده است. **شناسه مقصد تست و مجوز ارسال به آن برای این مأموریت داده نشده است**؛ شماره‌های موجود در Allowlist یا تاریخچه، خودکار مقصد تست مجاز نیستند.

بنابراین `BALE_REAL_DELIVERY=PENDING`، Message ID واقعی این مأموریت **ندارد** و دریافت انسانی **تأیید نشده** است. در مرحله دوم نیز تا تعیین مقصد مجاز هیچ پیام آزمایشی ارسال نشود. نبود این مقصد مانع ادعای پذیرش کامل تحویل خواهد بود، نه دلیلی برای انتخاب یک گیرنده عملیاتی. قبول API، تحویل فایل به حساب و خوانده‌شدن انسانی باید جدا گزارش شوند.

## ۱۳. موانع، محدودیت‌ها و بدهی باقی‌مانده

مانع سخت در فایل‌ها، Hash، integrity، Loader نصب‌شده یا روش Lifecycle مشاهده نشد. شرایط بازِ اجرایی: مجوز مستقل مالک، عبور از پنجره۱۵، Gate تازه و مقصد تست بله برای پذیرش تحویل. اگر یکی از کنترل‌های حیاتی Gate تازه نامعلوم یا ناموفق شد، تصمیم همان لحظه **NO-GO** است.

محدودیت منبع و محصول: سریال/موتور/آپشن/Revision واقعی UNKNOWN؛ منوال جامع Overhaul مستقل اثبات نشده؛ ۱۰۲ ردیف D2 و۷۰۰ ردیف D3 PN نمادی/ناخوانا یا مرجع‌محور دارند؛ بخشی از Refها چاپ نشده‌اند. پیوندهای تصویر کامل نیستند (۳۶۸ از۴۱۸ مجموعه D2 و۵۸۳ از۶۱۹ D3 شاهد پیوند دارند). هر۶۳ شیت عنوان/شماره تأیید دارد، اما همه پین‌ها و سیم‌ها فهرست نشده‌اند. نتیجه Index فقط routing است و هر پاسخ دقیق نیازمند بازخوانی PDF/پیکسل همان منبع است. محدودیت دیدن تصویر به Provider/مدل وابسته است. هفت شکست سریال و یک Alias قدیمی باقی‌اند.

Runtime evidence و PDF/SQLiteهای ignored به Git اضافه نشدند. فایل‌های گزارش قابل‌Review هستند؛ Hash شواهد در JSON همراه ثبت است. هیچ پاکسازی یا اصلاح خارج از این ممیزی انجام نشد.

## ۱۴. توصیه زمان امن و Preflight لحظه آخر

Restart به پنجره نامزد **امروز ۱۵:۱۰ تا۱۵:۴۵ تهران، پس از آرام‌شدن اجرای ساعت۱۵ و یادآورهای آن** موکول شود؛ این زمان رزرو، تضمین بی‌کاری یا مجوز اجرا نیست. اگر بررسی واقعی تا آن زمان کامل نشد یا کار جاری بود، نوبت بعدی خارج از یادآور۱۶/۱۷ انتخاب شود. Sync دو‌دقیقه‌ای و Retry روغن۱۵دقیقه‌ای یعنی صرف انتخاب ساعت کافی نیست؛ بلافاصله بعد از اتمام Sync موفق و در نبود Claim/ارسال فعال اقدام شود. Scheduler مستقل حتی در این فاصله ادامه دارد و نباید برای این کار Pause یا Restart شود.

پس از مجوز مالک، و پیش از هر اثر عملیاتی:

1. PID/زمان شروع، Supervisor، Process موازی و پاسخ control تازه ثبت شوند؛ active work، obligation، resume_pending، Lease و Queue صفر/بسته باشند.
2. Ledger گزارش‌ها، Artifactها، Final-copy/Job جاری و موعدهای تنظیم جاری دوباره خوانده شوند؛ هیچ Trigger/Retry برای «بررسی» اجرا نشود.
3. پایان Sync، نبود fire_claim زنده و نبود Worker باقی‌مانده احراز شود؛ در صورت race یا ناشناخته‌بودن، صبر/NO-GO.
4. HEAD، وضعیت Git، Hashهای41گانه، Configهای چهار پروفایل، Loader، AGENTS، Receipt و Backup دوباره برابر باشند؛ XML Task بدون Drift و نمایه بدون Writer/Sidecar تازه باشد.
5. فقط فرمان رسمی زیر پس از عبور Gate استفاده شود؛ اینجا صرفاً ثبت شده و **اجرا نشده** است:

```powershell
& C:\Users\win-10\AppData\Local\hermes\hermes-agent\venv\Scripts\python.exe -m hermes_cli.main gateway restart
```

فقط Gateway هدف و Supervisor آن در دامنه‌اند. Windows/ESXi، سرویس‌های دیگر، Scheduler مستقل، Startup/Network و تنظیم Task نباید تغییر کنند. اگر شرایط به‌شکل اساسی تغییر کرد، مجوز قبلی تأیید خودکار برای شرایط جدید نیست.

## ۱۵. نتیجه صریح و تحویل به مالک

**CONDITIONAL-GO — بسته ROC روی دیسک و نمایه، برای فعال‌سازی کنترل‌شده آماده است؛ Restart اکنون مجاز نیست.** روش رسمی شناخته‌شده، Task بدون Drift، Hashها و Backup معتبر، کد/نمایه سازگار و صف قابل‌بررسی هستند. توصیه، عبور از پنجره۱۵ و اجرای Gate تازه پس از مجوز مستقل است؛ GO بدون قید برای لحظه‌ای در آینده صادر نمی‌شود.

مرحله اول در همین نقطه متوقف است. هیچ نتیجه POST-RESTART، تحویل واقعی بله یا سلامت بلندمدت اعلام نشده و فایل‌های POST این مأموریت ساخته نشدند. **مالک برای آغاز مرحله دوم باید مجوز صریح و مستقل Restart همین Gateway را اعلام کند؛ برای آزمون واقعی بله نیز شناسه و مجوز مقصد تست لازم است.**

### شواهد همراه

خلاصه ماشینی: `reports/technical-docs/ROC-L8-PRE-RESTART-GATE.json`. شواهد خواندنی این مأموریت در `runtime/roc-l8-pre-restart-20261115/` قرار دارند؛ Hash هر اثر در فیلد evidence خلاصه ماشینی آمده است:

- `static.json`: Git، ۲۵ Hash انتشار، پنج PDF، Digest هر۹ کتاب، Backup و journal mode.
- `processes.json`, `control.json`, `final-check.json`: زنجیره Process، Task، هویت زنده و کنترل نهایی۴۱فایل.
- `lifecycle.json`, `gateway-task.xml`, `gateway-task-template.xml`: کنترل خواندنی Lifecycle و نبود Drift.
- `runtime.json`, `details.json`, `queues.json`, `pending-messages.json`: Integrity، وضعیت صف، Hold، baseline تاریخی و پروفایل‌ها؛ بدون محتوای پیام خصوصی.
- `open-handles.json`: فهرست خواندنی استفاده‌کنندگان نمایه؛ هیچ Shutdown/Restart API اجرا نشده است.
- `focused-tests.log`, `schedule-check.log`: ۲۷ آزمون تازه و بررسی برنامه بدون اجرای Job.

برای فرامین خواندنی CIM/Task/Named Pipe/Resource Inventory که sandbox مانع شده بود، اجرای خواندنی خارج از sandbox استفاده شد. ExecutionPolicy فقط در یک Process بررسی اعمال شد؛ تنظیم پایدار Windows تغییر نکرد. هیچ rejection نهاییِ حل‌نشده از بازبینی خودکار باقی نماند.
