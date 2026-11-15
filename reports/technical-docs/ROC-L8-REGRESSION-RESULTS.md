# Regression دستگاه‌های موجود

**VERIFIED برای حفظ داده و نمونه‌های قبلی؛ PARTIAL برای سبزبودن کل تست‌های تاریخی.**

هفت کتاب قدیمی از ابتدای کار تا نامزد اول، ساخت کتاب دوم و نصب نهایی با Digest و رکورد books/coverage مقایسه شدند. PC800، HD785-5، HD785-7 B1/B2، HD465-7R، WA600 و PC1250SP تغییر ناخواسته ندارند. ۲۰ کلید meta قدیمی و ۳۸۴۸ ردیف figures_fts قدیمی نیز عیناً حفظ شدند؛ integrity_check پایگاه نهایی ok است.

Manualهای موجود، رندر کامل و Crop، قراردادهای قدیمی item، مدل و MEDIA آزموده شدند. PNهای PC800 و HD785-5 از PDF بازخوانی شدند. Agent واقعی PC800، منبع PC800-8 را از هویت درخواستی PC800-8R تفکیک کرد و fit قطعی ادعا نکرد.

مجموعه ۷۰ تست Part Book موجود اجرا شد: ۶۳ موفق و ۷ شکست. هر هفت شکست با کد دست‌نخورده HEAD مبنا ae0466e نیز عیناً بازتولید شد؛ درباره انتظار فیلتر حذف ردیف‌های خارج سریال هستند، درحالی‌که کد جاری آن‌ها را با mismatch نگه می‌دارد. این سیاست در مأموریت ROC تغییر نکرد:

- HD785-5: closed_serial_range_filters_without_dropping_the_raw_note
- HD785-7 B1: abbreviated_serial_range، engine_serial، machine_serial_split
- PC1250SP: applicability_ranges_and_suffix_ar
- PC800: closed_range_wide_page_and_blank_pn
- WA600: serial_split_keeps_both_quantities_and_source_ranges

مجموعه shared_technical با محیط وابستگی صحیح: ۶ موفق و یک شکست قدیمی Alias (انتظار نبودن 800). همان شکست با Plugin دست‌نخورده HEAD نیز بازتولید شد. اجرای اولیه با Python پروژه بدون مسیرهای وابستگی Hermes دو ImportError داشت؛ با مفسر/مسیر صحیح رفع شد و خطای محصول جدید گزارش نمی‌شود. Loader default همچنین هشدار اسکن `.pytest_cache` با Access denied داد؛ Schema موردنیاز با وجود آن بارگذاری و تأیید شد.

هیچ‌یک از این شکست‌های مبنا برای سبزکردن ظاهری تست‌ها اصلاح یا انتظارشان ضعیف نشد. عبارت دقیق پذیرش: **NO NEW REGRESSION IN TESTED TECHNICAL SURFACES؛ BASELINE FAILURES REMAIN**.
