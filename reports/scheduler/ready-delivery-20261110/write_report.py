"""Render final Persian evidence from definitive receipts and installation records."""
from pathlib import Path
from datetime import datetime
from zoneinfo import ZoneInfo
import json,subprocess

OUT=Path(__file__).resolve().parent;ROOT=OUT.parents[2]
result=json.loads((OUT/'delivery-result.json').read_text('utf8'))
installed=json.loads((OUT/'installed.json').read_text('utf8'))
acceptance=json.loads((OUT/'acceptance.json').read_text('utf8'))
production=json.loads((OUT/'production-verification.json').read_text('utf8'))
commit=subprocess.check_output(['git','rev-parse','HEAD'],cwd=ROOT,text=True).strip()
names={'397185913':'امامی','514458396':'هوشمند پاکدل'}
lines=['# نتیجه ارسال واقعی و اصلاح گزارش‌های تعمیرات ۱۷ و ۱۸ آبان',
       '', '**هر چهار عملیات ارسال و با رسید قطعی تطبیق داده شدند.** زمان‌های جدول تهران، ۱۹ آبان ۱۴۰۵ هستند. رسید `sent` پذیرش بله را ثابت می‌کند؛ مشاهده یا خواندن گیرنده از آن نتیجه نمی‌شود.',
       '', '| تاریخ داده | گیرنده | نتیجه | Message ID | زمان تهران | SHA256 |',
       '|---|---|---|---|---|---|']
for op in result['operations']:
    clock=datetime.fromisoformat(op['updated']).astimezone(ZoneInfo('Asia/Tehran')).strftime('%H:%M:%S')
    lines.append(f"| {op['target_date']} | {names[op['recipient_id']]} `{op['recipient_id']}` | {op['status']} | {op['message_id']} | {clock} | `{op['sha256']}` |")
lines += ['', 'شناسه‌های عملیات بازیابی عیناً حفظ شدند:', '']
for op in result['operations']:lines.append(f"- `{op['operation_id']}` → Message ID `{op['message_id']}`.")
lines += ['', '## علت دقیق و انتقال وضعیت', '',
'''۱. Hold را Scheduler عادی یا اسکریپت ممیزی نساخت. `reports/scheduler/repair-20261110/install_staged.py:125` در زمان استقرار، `hold_backlog()` را از `tools/scheduler/recovery_review.py` فراخواند. این طراحی در Commit `155edb3` اضافه شده بود؛ بعداً انتخاب گیرنده تاریخی در `660b3ce` اصلاح شد. شرط قدیمی فقط `original > now` را رد می‌کرد و هر موعد گذشتهٔ فاقد رسید را، بدون رعایت مهلت یا نوع خطا، تاریخی تلقی می‌کرد. بنابراین نوبت داده ۱۸ آبان، با attempt=1، دقیقاً 38.568593 ثانیه پس از موعد وارد Hold شد؛ نوبت داده ۱۷ آبان نیز پس از 86438.568593 ثانیه، یعنی ۲۴ ساعت و ۳۸ ثانیه، Hold گرفت. هر دو داخل مهلت ۳۶ ساعت بودند.

۲. نسخهٔ محلی `841da662...` در هر دو تاریخ صفر رکورد داشت. Snapshot معدن با Hash `78fe91ef...` در ساعت ۱۱:۲۸:۳۷ تهرانِ ۱۹ آبان، برای ۱۷ آبان ۱۳ رکورد و برای ۱۸ آبان ۸ رکورد داشت. کد قدیمی `build → scan → no_target_date_records → waiting_for_data` فقط نسخهٔ محلی را می‌دید؛ برای این ادعای فقدان داده، تازگی معدن/Sync را اثبات نمی‌کرد. نبود داده در این کپی قدیمی به وضعیت missing نوبت تبدیل شده بود. این شواهد زمان دقیق اولیهٔ آماده‌شدن معدن در روز قبل را ثابت نمی‌کند.

۳. Journal واقعی Sync، عملیات `95283601b5814785ab9734e46b86240b` را با وضعیت succeeded ثبت کرده است: `841da662... → 78fe91ef...`. پایان Sync موفق ساعت ۱۲:۱۰:۲۵ تهران است. رفع اختلاف Selection/Scroll در استقرار قبلی انجام شده بود؛ محافظ تاریخ، ساختار، چاپ، Pane و Zoom باقی ماند.

۴. Tickهای پس از Sync فعال بودند، اما `_claim_replay → held → return False` پیش از `_execute` و پیش از خواندن منبع اجرا می‌شد. attempt و finished هر دو نوبت تا شروع این مأموریت همان مقادیر پیش از Sync بود: برای ۱۷ آبان attempt=98 و آخرین پایان ۱۱:۵۲:۰۶؛ برای ۱۸ آبان attempt=1 و پایان ۱۲:۰۰:۰۴. بنابراین محتوا پس از Sync دوباره بررسی نشد. هیچ شرط رفع خودکار برای Hold ایجادشده وجود نداشت؛ این قفل تاریخی، پس از رفع مانع عملیاتی هم باقی مانده بود.

۵. در این مأموریت مجوز فقط برای چهار Operation ID موجود ثبت شد؛ نام/هویت private و approved و Capability دریافت هر گیرنده از Registry جاری بررسی شد. پیش از Upload، فایل و Snapshot دوباره Hash شدند و PDF از روی Snapshot خصوصی بازسازی شد: Hash مرجع عیناً برابر فایل آماده بود؛ کنترل محتوای همان PDF با چینش کامل، ۱۳ ردیف/۳ صفحه و ۸ ردیف/۲ صفحه را تأیید کرد. رسیدهای جاری و Hashهای ارسالی پیش از ارسال بررسی شدند. Sender رسمی بله استفاده شد؛ هیچ نتیجه uncertain رخ نداد.

۶. Run و Receipt تاریخی بازنویسی نشدند. مجوز، Claim، ارسال و رسید در `report_recovery_operation` و `report_recovery_event` و رسید مستقل هر Artifact ثبت شدند. پس از تطبیق کامل، فقط Hold دو نوبت مجاز به `recovered` تغییر کرد و approval_ref گرفت؛ نسخهٔ قبلی Hold در Audit محفوظ است. نوبت اصلی همچنان از ارسال مجدد منع می‌شود. Summary/Alert از رسید مکمل قطعی استفاده می‌کنند و این دو نوبت را دیگر منتظر تأیید مالک نشان نمی‌دهند. سایر Holdها تغییر نکردند.''',
       '', '## اصلاح نصب‌شده', '',
'''- SOURCE_UNAVAILABLE / SOURCE_STALE: خطای فنی منبع/تازگی، مستقل از فقدان داده؛ مهلت Replay فنی هفت‌روزه و Backoff موجود حفظ شده است. Timeout منبع نیز خطای منبع ثبت می‌شود، نه PDF Export.
- DATA_NOT_READY: ابتدا اعتبار Hash نسخهٔ محلی در زنجیره Baseline/Journal موجود اثبات می‌شود؛ برای اعلام فقدان تاریخ، تأیید موفق همان فایل معدن در پنج دقیقهٔ اخیر لازم است. مهلت داده دقیقاً از موعد اصلی +۳۶ ساعت محاسبه می‌شود.
- ARTIFACT_READY: PDF، Workbook، گزارش/چینش و Manifest با نام محتوایی و کنترل Hash نگه‌داری می‌شوند. تاریخ هدف، زمان آماده‌سازی و نسخهٔ Journal ثبت می‌شود. Preflight کامل همان تاریخ را نگه می‌دارد؛ نمونهٔ قدیمی جای تاریخ هدف منتشر نمی‌شود. کنترل ارسال به Snapshot و PDF پین‌شده متکی است و Share یا فایل جاری را دوباره باز نمی‌کند.
- نسخهٔ انتشار انتخاب‌شده بدون تغییر می‌ماند. نسخهٔ جدید منبع، فایل قبلی را خودکار جایگزین یا باطل نمی‌کند؛ اصلاحیه نیازمند بررسی مستقل و رسید جداست. نقص محتوا/Hash، `pdf_validation` قطعی و بدون Retry کور است. پاسخ فاقد Message ID نیز uncertain است.
- Hash پذیرفته‌شدهٔ جدید یا رویداد بازگشت اتصال Sync، Retry واجد شرایط را در Tick بعد بیدار می‌کند؛ Hold ایمنی و بسته‌شدن واقعی ۳۶ساعته آزاد نمی‌شوند. Migration دیگر موعد ۳۸ ثانیه‌ای یا Retry فنی داخل افق را تاریخی فرض نمی‌کند.
- تغییر محدود به قرارداد مشترک خطا/صف و مسیر آماده‌سازی تعمیرات است. فرم دستی، AI، NET و محافظ Sync با مسیرهای فعلی باقی مانده‌اند.''',
       '', '## تست و Production', '',
       f"Regression نهایی: **{acceptance['tests_run']} تست؛ {acceptance['tests_run']-acceptance['failures']-acceptance['errors']} موفق، {acceptance['failures']} شکست از قبل موجود در NET، {acceptance['errors']} Error، {acceptance['skipped']} Skip، {len(acceptance['external_attempts'])} تلاش شبکه بیرونی**. تمام تست‌های حوزهٔ تغییر عبور کردند. همان دو شکست با کد Production پیش از نصب در کلاس ۲۷تستی NET دوباره تولید شدند: انتظار قدیمیِ منو بدون car_wash، و AF وابسته به ترتیب اجرا. AF در اجرای دو تستِ جدا با نسخهٔ پایه و Candidate عبور می‌کند. NET تغییر نکرد و کل مجموعه سبز اعلام نمی‌شود. Registry و Transport جدا بودند؛ تست AF موجود یک کپی قدیمی را با مسیر منبع فعلی مقایسه می‌کند و برای اثبات آمادگی فایل‌های این مأموریت استفاده نشده است. تست‌های متمرکز تکمیلی برای نسخهٔ نهایی Wakeup/بسته‌شدن Hold نیز ۱۸ مورد موفق داشتند؛ تعدادها با هم جمع نشده‌اند. شناسهٔ دقیق تست‌ها و ماژول‌ها در acceptance.json و net-baseline-class.json و لاگ‌ها ثبت است.",
       '', 'یک اجرای اولیه، تست NativeMinimalTests را هم وارد کرد و پیش از پیدایش فرایند Export فرزند متوقف ماند. فقط Python همان تستِ جدا خاتمه یافت. این مورد از آزمون نرم‌افزاری نهایی کنار گذاشته شد و تأیید واقعی Office/COM اعلام نمی‌شود. یک انتظار قدیمی تست که موفقیت را با Message ID=None می‌پذیرفت، به Message ID قطعی اصلاح شد؛ تست مستقل پاسخ بدون ID، uncertain را تأیید می‌کند. این موارد سبزِ کاذب گزارش نشده‌اند.',
       '', f"Commit مستقل Sender بازیابی: `7b1f5de`. Commit اصلاح معماری: `{installed['code_commit']}`.",
       '', f"Backup نصب: `{installed['backup']}`. نصب با Drain رسمی و قفل Worker انجام شد؛ فایل‌های نصب‌شده با Candidate تست‌شده برابرند. integrity_check={installed['integrity']}. Runها، تمام Receiptها و Holdهای خارج از دو نوبت مجاز قبل و بعد نصب برابرند. نصب هیچ Upload یا Write به Workbook انجام نداد. Gateway Restart نشد؛ Scheduler در Tickهای بعد کد تازه را بار می‌کند.",
       '', f"Tick طبیعی پس از نصب در `2026-11-10T12:14:01.814588+00:00`، یعنی ۱۵:۴۴:۰۱ تهران، جدول Wakeup را با Hash پذیرفته‌شدهٔ `78fe91ef...` ثبت کرد. خواندن فقط‌خواندنی نهایی، کد نصب‌شده را برابر Candidate و هر چهار رسید را sent تأیید کرد. Gateway با PID {production['gateway']['pid']} در وضعیت {production['gateway']['phase']} و Heartbeat تازه است. نبود drain-request و integrity_check=ok تأیید شدند. لاگ عادی Scheduler نیز رفع وضعیت تحویل دو نوبت را با همین چهار Message ID ثبت کرد.",
       '', '## سندهای قابل بررسی', '',
       '- [چهار رسید قطعی و Operation IDها](delivery-result.json)',
       '- [مجوز محدود مالک](execution-authorization.json)',
       '- [اعتبارسنجی پیش از ارسال](pre-send-validation.json)',
       '- [Trace منبع، Journal، Hold و نصب قبلی](root-cause-trace.json)',
       '- [نتیجه تست‌ها](acceptance.json)',
       '- [Hash فایل‌های نصب‌شده و تطبیق Holdها](installed.json)',
       '- [وضعیت Production پس از نصب](production-verification.json)',
       '', '**هیچ گزارش ۱۵ یا ۱۶ آبان و هیچ فایل دیگری در مسیر ارسال این مأموریت فرستاده نشد.**',
       '', 'FOUR MAINTENANCE REPORT DELIVERIES RECONCILED — READY ARTIFACTS NO LONGER BLOCKED BY TRANSIENT MINE CONNECTIVITY', '']
(OUT/'report-fa.md').write_text('\n'.join(lines),'utf8')
print(OUT/'report-fa.md')
