# اتصال ابزارها و هویت منابع

وضعیت: **VERIFIED در فرآیند تازه و آزمون ایزوله؛ UNKNOWN در حافظه Gateway عملیاتی جاری**.

| لایه | تغییر |
|---|---|
| manual_models | ROC-L8، ROC L8، ROCL8 و L830 به پوشه مجاز |
| technical_documents | شناسه ثابت Manifest، انتخاب D1/D4/D5، هش منبع، کنترل مدل و شیت تصویری |
| manual_evidence_probe | انتخاب محدود موضوع، فیلتر صفحه مدل، حفظ document_id، عدم fallback خودکار در ROC |
| manual_worker_batch | درخواست bound و حفظ document_id در read/render پایان |
| Parser/Registry/Resolver | دو کتاب مستقل، D2 پیش‌فرض، D3 تکمیلی با انتخاب صریح؛ Reader قدیمی با book_ids فقط D2 را می‌بیند |
| partbook_lookup | item_ref حرفی/علامتی، PN بدون فاصله، تأیید صحیح PDF چرخیده، منبع و وضعیت انطباق |
| Tool Schema | enum شناسه سند/کتاب، پارامترهای قدیمی محفوظ، inspect_images اختیاری |
| Renderer / Crop | مجازبودن Manifest، هش منبع، صفحه و Crop معتبر، نام یکتای سند و نشست |
| مدل دیداری | همان PNG معتبر با قرارداد _multimodal موجود Hermes؛ fallback بدون ادعای مشاهده |
| Media | فیلتر و Adapter موجود بدون افزایش مجوز؛ انتقال ساختگیِ byte-identical آزموده شد |

هیچ ابزار فایل عمومی یا مجوز تازه به Hermes داده نشد. pdf_path دلخواه، Document ID نامجاز، سند دستگاه دیگر، صفحه L7 برای L8، نشست خارجی و Crop نامعتبر رد می‌شوند. finish به request_id و نشست retrieve وابسته است و تعویض Document ID را نمی‌پذیرد.

اسناد: D1=`roc-l8-trainee-9852960001`، D2=`roc-l8-parts-9853647820d`، D3=`roc-l8-lf-parts-9853678620`، D4=`roc-l8-cabin-9840042409`، D5=`roc-l8-electrical-9840042418`.

همه Loaderهای موجود به کد مرجع اشاره می‌کنند. Schema در فرآیند تازه پروفایل‌های default، maintenance، admin و net بارگذاری شد. این مشاهده به‌معنی به‌روزرسانی objectهای Python و Schema نشست‌های موجود Gateway نیست. Reload رسمی نصب‌شده، بازکشی همه وابستگی‌ها و Schema نشست قبلی را تضمین نمی‌کند؛ Restart بدون مجوز مستقل اجرا نشد.

نسخه مبنا: پروژه HEAD `ae0466e16478e6e0d43bdd6629bb947cf444e3f2`، Git 2.54.0.windows.1، Python پروژه 3.11.15، PyMuPDF 1.28.0. کد نصب‌شده Hermes در دیسک `e7208cd01179d21494b7f46478cd7db85481ed90` بود. ممیزی قبلی HEAD کد را ثبت نکرده بود؛ تطابق دقیق Commit زمان ممیزی UNKNOWN است، ولی قراردادهای موردنیاز دوباره محدود و اجرایی بررسی شدند.
