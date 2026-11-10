"""Read-only operational audit; writes only new review artifacts in this folder."""
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import sqlite3
import sys

import jdatetime
sys.path.insert(0, str(Path(__file__).resolve().parents[3]))
from tools.authorization import AuthorizationStore
from tools.scheduler.runner import load_config, MISSING_EXPIRY_TASKS
from tools.scheduler.delivery_alert import _required_kind, _problems, _operators
from tools.scheduler.tasks import overflow_report_date

OUT = Path(__file__).resolve().parent
TZ = ZoneInfo('Asia/Tehran')
now = datetime.now(timezone.utc)
source = sqlite3.connect(Path('runtime/scheduler/runs.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
destination = sqlite3.connect(OUT / 'latest-ledger-snapshot.sqlite3')
source.backup(destination)
source.close()
destination.row_factory = sqlite3.Row
tables = {r['name']: [dict(v) for v in destination.execute('SELECT * FROM ' + r['name'])]
          for r in destination.execute("SELECT name FROM sqlite_master WHERE type='table'")}
destination.close()
latest = {'captured_at_utc': now.isoformat(), 'captured_at_tehran': now.astimezone(TZ).isoformat(), 'tables': tables}
(OUT / 'latest-evidence.json').write_text(json.dumps(latest, ensure_ascii=False, indent=2), encoding='utf-8')
store = AuthorizationStore()
_, _, jobs = load_config()
jobs = {j['id']: j for j in jobs}
names = {u: store.identity_label(u) for u in {str(r.get('recipient_id', r.get('operator_id', ''))) for rows in tables.values() for r in rows if 'recipient_id' in r or 'operator_id' in r}}
names.update({'387679249': 'صادق صادقیان', '654806764': 'سعید شایسته'})
titles = {
    'overflow_daily_test': 'سرریز دفتر',
    'overflow_daily_mechanical': 'سرریز مکانیکی',
    'driver_daily_office_supervisor': 'معایب رانندگان دفتر؛ مکانیکی و آهنگری',
    'driver_daily_mechanical': 'معایب مکانیکی رانندگان',
    'driver_daily_metalwork': 'معایب آهنگری رانندگان',
    'maintenance_daily_report': 'تعمیرات روزانه ماشین‌آلات',
    'oil_balance_daily_report': 'موازنه روغن',
    'machinery_performance_daily_report': 'عملکرد ماشین‌آلات',
}


def jd(day):
    return jdatetime.date.fromgregorian(date=day).strftime('%Y/%m/%d')


def local(stamp):
    if not stamp:
        return ''
    d = datetime.fromisoformat(stamp).astimezone(TZ)
    return jd(d.date()) + ' ' + d.strftime('%H:%M:%S')


def target(run):
    return overflow_report_date(datetime.fromisoformat(run['due']))


def table(headers, rows):
    def cell(value):
        return str(value if value is not None else '').replace('|', ' / ').replace('\n', '<br>')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] + ['| ' + ' | '.join(map(cell, r)) + ' |' for r in rows])


conn = sqlite3.connect((OUT / 'latest-ledger-snapshot.sqlite3').resolve().as_uri() + '?mode=ro', uri=True)
conn.row_factory = sqlite3.Row
classification = []
policies = []
for sid, job in jobs.items():
    if not job['enabled']:
        policies.append([sid, 'غیرفعال؛ صف فعال ندارد', 'ندارد', 'ندارد'])
        continue
    if job['task'] in MISSING_EXPIRY_TASKS:
        policy = 'صف دارد؛ نبود داده تا ۳۶ ساعت از موعد اصلی؛ سپس بسته می‌شود'
    elif job['task'] == 'metalwork_driver_daily':
        policy = 'صف دارد؛ ۳۶ ساعته نیست؛ افق بازیابی ۷ روز از موعد'
    elif job['task'] == 'machinery_performance_daily_report':
        policy = 'صف FIFO؛ پس از ۳۶ ساعت فقط در صورت آماده بودن روز جدیدتر از روز ناقص عبور می‌کند'
    else:
        policy = 'بازیابی ' + job['misfire']['policy']
    recipients = store.resolve_active_recipients(job['recipient_capability']).recipients
    policies.append([titles.get(sid, sid), policy, '، '.join(names.get(u, u) for u in recipients), '، '.join(names.get(u, u) + ' (' + u + ')' for u in _operators(store, job))])
    for run in tables['runs']:
        if run['schedule_id'] != sid or run['due'] < '2026-11-07' or run['status'] == 'succeeded':
            continue
        classification.append({'schedule_id': sid, 'target': target(run), 'run': run,
                               'required_kind': _required_kind(run),
                               'problems': _problems(conn, job, run, recipients, store)})
conn.close()
(OUT / 'latest-alert-classification.json').write_text(json.dumps(classification, ensure_ascii=False, indent=2), encoding='utf-8')

queue = []
closed = []
uncertain = []
for run in sorted(tables['runs'], key=lambda r: (r['due'], r['schedule_id'])):
    sid = run['schedule_id']
    if sid not in jobs or not jobs[sid]['enabled']:
        continue
    if run['status'] == 'uncertain':
        uncertain.append([titles.get(sid, sid), target(run), local(run['due']), run['error'], 'توقف بازفرستادن خودکار؛ نیازمند بررسی رسیدها'])
    if run['due'] < '2026-11-07':
        continue
    if run['status'] in {'waiting_for_data', 'retry_wait', 'partial', 'running'}:
        queue.append([titles[sid], target(run), local(run['due']), run['status'], run['error'], run['attempt'], local(run['next_attempt_at'])])
    elif run['status'] == 'no_data_final':
        closed.append([titles[sid], target(run), local(run['due']), local(run['finished']), run['error']])

alerts = []
for tab in ['source_notice_alert_receipt', 'delivery_alert_receipt']:
    for receipt in tables[tab]:
        if receipt['due'] < '2026-11-07':
            continue
        recipient = receipt.get('recipient_id')
        alerts.append([local(receipt['updated']), receipt['operator_id'], names.get(receipt['operator_id'], ''), receipt['schedule_id'], overflow_report_date(datetime.fromisoformat(receipt['due'])), names.get(recipient, '') if recipient else '', receipt.get('alert_kind', 'نبود داده'), receipt['status'], receipt['message_id'], receipt['error']])
alerts.sort(key=lambda r: r[0])

driver = []
for run in tables['runs']:
    if not run['schedule_id'].startswith('driver_daily') or target(run) != '1405/08/17':
        continue
    rs = [r for r in tables['receipts'] if r['schedule_id'] == run['schedule_id'] and r['due'] == run['due'] and r['delivery_kind'] == 'report']
    for receipt in rs:
        artifacts = [r for r in tables['office_artifact_receipts'] if r['schedule_id'] == run['schedule_id'] and r['due'] == run['due'] and r['recipient_id'] == receipt['recipient_id']]
        if artifacts:
            for artifact in artifacts:
                driver.append(['مکانیکی' if artifact['artifact_id'] == 'mechanical' else 'آهنگری ـ نسخه اولیه', names[artifact['recipient_id']], local(artifact['updated']), artifact['message_id'], artifact['status'], artifact['sha256']])
        else:
            driver.append([titles[run['schedule_id']], names[receipt['recipient_id']], local(receipt['updated']), receipt['message_id'], receipt['status'], ''])
for artifact in tables['office_artifact_receipts']:
    if artifact['schedule_id'].startswith('manual-driver-metalwork-correction-1405-08-17'):
        driver.append(['آهنگری ـ اصلاحیه مستقل', names[artifact['recipient_id']], local(artifact['updated']), artifact['message_id'], artifact['status'], artifact['sha256']])

today = []
for run in sorted(tables['runs'], key=lambda r: r['schedule_id']):
    if run['schedule_id'].startswith('driver_daily') and target(run) == '1405/08/18':
        rs = [r for r in tables['receipts'] if r['schedule_id'] == run['schedule_id'] and r['due'] == run['due'] and r['delivery_kind'] == 'report']
        parts = []
        for r in rs:
            artifacts = [a for a in tables['office_artifact_receipts'] if a['schedule_id'] == r['schedule_id'] and a['due'] == r['due'] and a['recipient_id'] == r['recipient_id']]
            mids = ','.join(a['message_id'] or '' for a in artifacts) if artifacts else r['message_id'] or ''
            parts.append(names.get(r['recipient_id'], r['recipient_id']) + ': ' + r['status'] + (' / ' + mids if mids else ''))
        today.append([titles[run['schedule_id']], run['status'], run['error'], local(run['finished']), '؛ '.join(parts)])

alert_counts = Counter((r[7] for r in alerts))
coverage = [
    ['سرریز دفتر و مکانیکی', 'اعلان نبود داده برای گیرنده تولید می‌شود؛ سپس هشدار مدیر از آن اعلان ساخته می‌شود.', 'یک‌بار برای هر گزارش/موعد/گیرنده؛ بسته‌شدن ۳۶ ساعته پیام نهایی مستقلی ایجاد نمی‌کند.'],
    ['رانندگان دفتر و مکانیکی', 'notify=False؛ notice_delivery_state=suppressed؛ _required_kind مقدار None می‌دهد.', 'انتظار برای داده و بسته‌شدن پس از ۳۶ ساعت می‌توانند بدون هشدار مدیر بمانند.'],
    ['رانندگان آهنگری', 'اعلان نبود داده فعال است؛ هشدار مدیر نیز یک‌بار ثبت می‌شود.', 'اعلان روزانه تکرار نمی‌شود؛ وضعیت قدیمی missing می‌تواند خطای فنی جدید را پشت رسید notice=sent پنهان کند.'],
    ['تعمیرات روزانه', 'اعلان نبود داده خاموش است؛ مسیر هشدار مدیر همان وضعیت suppressed را نادیده می‌گیرد.', 'یادآوری برای کرمعلی، شایسته و صادقیان مستقل است؛ این یادآوری، هشدار مدیر نیست.'],
    ['موازنه روغن', 'نبود داده با suppressed ثبت می‌شود؛ هشدار عمومی مدیر تولید نمی‌شود.', 'یادآوری هدایتی ساعت‌های ۱۱ و ۱۶ جای گزارش عدم ارسال به مدیر را نمی‌گیرد.'],
    ['عملکرد ماشین‌آلات', 'نبود داده suppressed است؛ هشدار خطای عملکرد نیز در _operators به هدایتی محدود می‌شود.', 'حساب 455740857 در مسیر اختصاصی هشدار عملکرد قرار ندارد؛ یادآوری هدایتی ساعت‌های ۱۴ و ۱۷ است.'],
]

parts = [
    '# ممیزی صف گزارش‌ها، اصلاحیه رانندگان و هشدار مدیر',
    'زمان ثبت آخرین وضعیت: **' + local(now.isoformat()) + ' تهران**. این ممیزی فقط‌خواندنی است؛ هیچ tick یا sender اجرا نشده، صف و تنظیمات تغییر نکرده و فایل منبع و رسیدها دست‌نخورده‌اند. فقط شواهد و این گزارش جدید نوشته شده‌اند. گزارش قبلی با زمان ۰۸:۲۵ محفوظ مانده است.',
    '## پاسخ مستقیم به سه مورد',
    '۱. سرریز از قلم نیفتاده است: هر دو مسیر سرریز صف دارند و در فهرست ۳۶ ساعته‌اند. ۳۶ ساعت مهلت انتظار برای داده مفقود است، نه تأخیر برنامه‌ریزی‌شده و نه تضمین ارسال فردا. روز جدید می‌تواند مستقل از روز قدیمی ارسال شود. تفاوت واقعی این است که آهنگری رانندگان سیاست ۷ روزه دارد و عملکرد از FIFO اختصاصی استفاده می‌کند.',
    '۲. «موعد ۱۷ آبان» و «داده ۱۷ آبان» دو چیز متفاوت‌اند. موعد ۱۷ مربوط به داده ۱۶ است و آن داده در رانندگان موجود نبود. داده ۱۷ روز ۱۸ آبان برای گیرندگان ارسال شده است؛ اصلاحیه آهنگری هم روز ۱۸ برای امامی و پاکدل ارسال شده و هش آن با فایل فعلی یکسان است. ستون‌های گزارش قبلی این تفاوت را به‌اندازه کافی روشن نمی‌کردند.',
    '۳. اشتراک هشدار حساب mojtaba با شناسه 455740857 فعال و تنها گیرنده هشدار عمومی است. مشکل، خاموش شدن کلی این اشتراک نیست: ۱۰ رسید هشدار نبود داده در روزهای ۱۶ و ۱۷ و ۱۰ رسید هشدار خطا/رفع مشکل در روز ۱۸ برای این حساب موجود است. ولی همه انواع عدم ارسال پوشش داده نمی‌شوند، پیام روزانه تکراری وجود ندارد و مسیر عملکرد به هدایتی می‌رود.',
    '## سیاست دقیق هر مسیر',
    table(['گزارش', 'سیاست صف', 'گیرندگان گزارش', 'گیرنده هشدار خطا'], policies),
    'هر شش مسیر عادیِ ۳۶ ساعته در تنظیمات replay با افق بازیابی ۷ روز دارند. شرط ۳۶ ساعته فقط نبود تاریخ/رکورد روز هدف را می‌بندد؛ خطاهای فنی، انتقال نامطمئن و رسیدهای sent/sending/uncertain با آن قاعده حذف نمی‌شوند. خطای فنی و بازفرستادن نیز محدودیت‌ها و افق بازیابی خود را دارند. وجود ردیف قدیمی failed در DB به معنی صف فعال نیست.',
    'مثال اثر FIFO عملکرد: گزارش داده ۱۶ آبان موعد ۱۷ آبان ساعت ۱۵ داشت و مهلت ۳۶ ساعت آن تا ۱۹ آبان ساعت ۰۳ بود. حتی پس از آماده شدن داده ۱۷، صف از روز ۱۶ پیش از این مرز عبور نکرد؛ در ۱۹ آبان ۰۳:۱۰ روز ناقص کنار گذاشته و داده ۱۷ ارسال شد. این رفتار متفاوت از سرریز است که روزهای جدید را مستقل بررسی می‌کند.',
    '## نوبت‌های فعلاً در صف یا در حال اجرا',
    table(['گزارش', 'تاریخ داده', 'موعد تهران', 'وضعیت', 'آخرین خطا', 'تلاش تمام‌شده', 'تلاش بعدی ثبت‌شده'], queue) if queue else 'در این بازه نوبت در صف ثبت نشده است.',
    'وضعیت running یعنی تلاش جدید شروع شده است؛ error و finished در آن ردیف ممکن است هنوز مربوط به تلاش قبلی باشند. این دو ستون به‌تنهایی نتیجه تلاش جاری نیستند.',
    '## نوبت‌های بسته‌شده و خارج از صف ارسال خودکار',
    table(['گزارش', 'تاریخ داده', 'موعد تهران', 'زمان بسته‌شدن تهران', 'علت نهایی'], closed),
    'بسته‌شدن هیچ‌گاه ارسال موفق نیست؛ پس از اصلاح داده، این نوبت‌ها خودبه‌خود باز نمی‌شوند. برای بازیابی بعدی باید تصمیم جداگانه و همراه با بررسی رسیدها گرفته شود.',
    '## نوبت‌های نامطمئن قدیمی',
    table(['گزارش', 'تاریخ داده', 'موعد تهران', 'علت', 'رفتار جاری'], uncertain),
    '## رفع ابهام رانندگان ۱۷ آبان و نسخه اصلاحی',
    table(['بخش/نسخه', 'گیرنده', 'زمان ثبت رسید تهران', 'شناسه پیام', 'وضعیت', 'SHA256 فایل'], driver),
    'علت نقص تاریخی آهنگری PrintArea ذخیره‌شده A1:D37 بود که ستون شرح E را حذف می‌کرد. اصلاح نصب‌شده محدوده چاپ را در کپی موقت مطابق ستون‌های انتخابی می‌سازد. پنج فایل تولیدی و اعتبارسنجی بررسی‌شده نسبت به commit 28142e9 تغییری ندارند. گزارش پذیرش قبلی، ۲۶ شرح در سه صفحه و سالم بودن فایل مکانیکی را ثبت کرده است. در این ممیزی، هش فایل اصلاحی و PDF تاریخی دوباره تطبیق داده شد؛ PDF تازه تولید یا ارسال نشد.',
    'اصلاحیه فقط به امامی و پاکدل فرستاده شده است. برای صادق صادقیان اصلاحیه مستقل ثبت نشده؛ رسید 7321 نسخه اولیه او را اثبات می‌کند. رسید حمل، به‌تنهایی محتوای فایل دریافت‌شده یا خواندن توسط انسان را ثابت نمی‌کند. برای مکانیکی اصلاحیه مستقلی ثبت نشده و شواهد پذیرش قبلی آن فایل را سالم دانسته‌اند.',
    '## شکاف‌های هشدار مدیر',
    table(['مسیر', 'رفتار فعلی', 'نتیجه برای انتظار مدیر'], coverage),
    'علت فنی شکاف اصلی: source_notice_alert فقط notice=sent را می‌بیند؛ تولیدکننده‌های چند گزارش notice را خاموش کرده‌اند؛ delivery_alert._required_kind نیز در suppressed بی‌درنگ None برمی‌گرداند. پس سکوت اعلان گیرنده، ناخواسته سکوت هشدار مدیر را هم ایجاد می‌کند. در این نوبت‌های جدید، baseline تاریخی علت حذف هشدار نبوده است.',
    'قاعده عمومی دیگر: خطای تازه معمولی پس از ۱۵ دقیقه مشاهده مستمر هشدار می‌گیرد، نتیجه uncertain فوری است، همان مشکل برای همان موعد تکرار روزانه ندارد و پیام رفع مشکل به وجود هشدار قبلی و رسید واقعی وابسته است. اسکن هشدار در پایان tick انجام می‌شود؛ تولید طولانی PDF می‌تواند زمان رسیدگی هشدار را عقب بیندازد.',
    'یک اشکال قابل بازتولید در طبقه‌بندی: نوبت‌های قدیمی آهنگری که قبلاً data_state=missing و notice=sent داشته‌اند، با خطای جدید PermissionError نیز required_kind=notice می‌گیرند؛ رسید قدیمی اعلان موفق باعث problems=[] می‌شود. این به معنی موفقیت فایل گزارش نیست و می‌تواند مانع هشدار خطای فنی تازه شود. خروجی تابع روی snapshot فقط‌خواندنی ساعت ۰۹:۰۷ در policy-and-classification.json ثبت شده است؛ latest-alert-classification.json وضعیت تازه‌تر را نشان می‌دهد.',
    '## رسیدهای واقعی هشدار حساب مدیر',
    table(['ثبت تهران', 'شناسه مدیر', 'نام مدیر', 'مسیر', 'تاریخ داده', 'فرد گزارش‌نگرفته', 'نوع', 'وضعیت', 'شناسه پیام', 'خطا'], alerts),
    'در روز ۱۶: 7242 تا 7245 برای سرریز و 7259 برای آهنگری. در روز ۱۷: 7277 تا 7280 برای سرریز و 7282 برای آهنگری. در روز ۱۸: هشدارهای 7304 تا 7308 و پیام‌های رفع مشکل 7313، 7314، 7322 تا 7324. دو هشدار اولیه preflight سرریز به علت TransportUnavailable شکست خورده‌اند؛ بعداً هشدار اصلی و رفع مشکل آن مسیرها رسید دارند. این‌ها رسید پذیرش بله‌اند؛ وضعیت خواندن در دسترس نیست.',
    '## رخداد تازه امروز؛ مستقل از اصلاح PrintArea',
    'از ۰۸:۵۵ در نوبت‌های قدیمی آهنگری و از ۰۹:۰۰ در سه مسیر رانندگانِ داده ۱۸ آبان PermissionError ثبت شد. اسکن مستقیم فقط‌خواندنی منبع در ۰۹:۰۹ موفق بود و همان هش فایل ساعت ۰۸:۲۵ را نشان داد. لاگ اجرایی فقط نام کلاس خطا را حفظ کرده و errno/winerror/مرحله/مسیر را ندارد؛ بنابراین تعیین قطعی قفل فایل، ACL یا مسیر موقت از شواهد فعلی ممکن نیست. این خطا را نمی‌توان به بازگشت مشکل PrintArea نسبت داد.',
    table(['مسیر امروز؛ داده ۱۸', 'آخرین وضعیت اجرا', 'آخرین خطا', 'آخرین finished ثبت‌شده', 'رسید گیرندگان'], today),
    'در snapshot ساعت ۰۹:۱۴ هر سه مسیر امروز succeeded شده‌اند: دفتر حدود ۰۹:۱۰، مکانیکی ۰۹:۱۲ و آهنگری ۰۹:۱۳. زمان‌بند خودکار آن‌ها را پیش از پایان مهلت هشدار ۱۵ دقیقه‌ای بازیابی کرده است؛ در نتیجه هشدار عدم ارسال امروز برای مدیر ثبت نشده است. این بازیابی نتیجه اجرای دستی این ممیزی نیست.',
    '## پیشنهاد برای اصلاح بعدی؛ در این نوبت اجرا نشده',
    '۱. سیاست صف را صریح و یکسان‌فهم کنیم: شش مسیر ۳۶ ساعته حفظ شوند یا با تصمیم مالک تغییر کنند؛ برای آهنگری بین مهلت ۳۶ ساعت و افق ۷ روز تصمیم روشن بگیریم؛ FIFO عملکرد جدا و با علت عبور از روز ناقص نمایش داده شود.',
    '۲. هشدار مدیر مستقیماً از تعهد ارسال گزارش و رسید هر گیرنده ساخته شود؛ به ارسال/عدم ارسال اعلان به گیرنده وابسته نباشد. برای پایان مهلت پیام نهایی مستقل و یک خلاصه روزانه از تمام موارد باز و بسته‌شده بدون ارسال تعریف شود؛ ساعت این خلاصه هنوز تصمیم‌گیری نشده است.',
    '۳. گیرنده هشدار مدیریتی عملکرد را از مسئول یادآوری تکمیل داده جدا تعریف کنیم؛ در حال حاضر هر دو به هدایتی می‌روند. افزودن مدیر به هشدار نیازمند تصمیم در مرحله اصلاح است؛ در این ممیزی مجوز یا اشتراکی تغییر نکرد.',
    '۴. خطای فنی جاری بر داده قدیمی missing اولویت داشته باشد؛ stale data_state نباید خطای تازه را پنهان کند. خطا با کد امن سیستم، مرحله و مسیر مجاز ثبت شود تا PermissionError مبهم نماند.',
    '۵. گزارش وضعیت سه تاریخ مستقل نشان دهد: تاریخ داده، موعد اصلی و زمان واقعی ارسال؛ و سه وضعیت مستقل: پذیرش فایل توسط بله، صحت محتوا و اصلاحیه. برای گزارش ۱۷، اصلاحیه فعلی حفظ شود و نیاز احتمالی به اصلاحیه صادقیان جدا بررسی شود؛ هیچ بازفرستادن کور انجام نشود.',
    '## شواهد و محدودیت',
    'منابع: snapshot سازگار SQLite از دفتر زنده؛ تنظیمات فعلی؛ کد صف و هشدار؛ نقش و هویت خصوصی رجیستری؛ شواهد پذیرش و اصلاحیه ۱۸ آبان؛ لاگ‌های زمان‌بند. فایل معدن و دریافت انسانی پیام‌ها مستقیماً بررسی نشده‌اند. علت دقیق PermissionError در لاگ تاریخی موجود نیست. هیچ تغییر کد، فایل منبع، صف، برنامه زمانی، دسترسی، ارسال یا restart انجام نشد.',
    'لنگرهای کد: tools/scheduler/runner.py:36 و :236 و :567؛ tools/fleet/machinery_performance/queue.py:57؛ tools/scheduler/delivery_alert.py:143 و :397 و :692؛ tools/scheduler/source_notice_alert.py:88؛ tools/scheduler/office_delivery.py:50؛ tools/scheduler/tasks.py:386؛ tools/fleet/maintenance_daily/delivery.py:57؛ tools/fleet/oil_balance/delivery.py:66؛ tools/fleet/machinery_performance/delivery.py:102.',
]
(OUT / 'audit-fa.md').write_text('\n\n'.join(parts) + '\n', encoding='utf-8')
summary = {'captured_at_tehran': latest['captured_at_tehran'], 'policies': policies, 'active_queue': queue, 'closed_without_report': closed, 'uncertain_legacy': uncertain, 'driver_17_receipts': driver, 'operator_alert_counts': dict(alert_counts), 'today_driver_status': today}
(OUT / 'summary.json').write_text(json.dumps(summary, ensure_ascii=False, indent=2), encoding='utf-8')
manifest = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in OUT.iterdir() if p.is_file() and p.name != 'manifest.json'}
(OUT / 'manifest.json').write_text(json.dumps(manifest, ensure_ascii=False, indent=2), encoding='utf-8')
print(json.dumps(summary, ensure_ascii=False))
