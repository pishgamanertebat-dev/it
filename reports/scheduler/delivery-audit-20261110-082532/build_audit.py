"""Render the frozen, read-only delivery audit. Does not call any sender."""
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo
import hashlib
import json
import math
import sqlite3

import jdatetime
from openpyxl import Workbook, load_workbook
from openpyxl.styles import Alignment, Font, PatternFill
from openpyxl.utils import get_column_letter

OUT = Path(__file__).resolve().parent
E = json.loads((OUT / 'evidence.json').read_text(encoding='utf-8'))
T = E['tables']
TZ = ZoneInfo('Asia/Tehran')
ASOF = datetime.fromisoformat(E['captured_at_utc'])
NAMES = dict(E['names'])
# Display-only spacing and punctuation; original registry labels remain in evidence.json.
NAMES.update({'387679249': 'صادق صادقیان', '654806764': 'سعید شایسته'})
META = {
    'overflow_daily_test': ('سرریز ـ دفتر', 'سرریز روزانه.xlsx'),
    'overflow_daily_mechanical': ('سرریز ـ مکانیکی', 'سرریز روزانه.xlsx'),
    'driver_daily_office_supervisor': ('معایب رانندگان ـ دفتر؛ مکانیکی و آهنگری', 'گزارش روزانه رانندگان2.xlsx'),
    'driver_daily_mechanical': ('معایب رانندگان ـ مکانیکی', 'گزارش روزانه رانندگان2.xlsx'),
    'driver_daily_metalwork': ('معایب رانندگان ـ آهنگری', 'گزارش روزانه رانندگان2.xlsx'),
    'maintenance_daily_report': ('تعمیرات روزانه ماشین‌آلات', 'تعمیرات 1405.xlsx'),
    'oil_balance_daily_report': ('موازنه روغن', 'موازنه روغن مصرفی.xlsx'),
    'machinery_performance_daily_report': ('عملکرد ماشین‌آلات', 'عملکرد ماشین آلات.xlsx'),
}
ORDER = list(META)
JOBS = {j['id']: j for j in E['schedules']['schedules'] if j['enabled']}
RUNS = {(r['schedule_id'], r['due']): r for r in T['runs']}
SOURCES = json.loads((OUT / 'current-source-check.json').read_text(encoding='utf-8'))


def jd(d):
    return jdatetime.date.fromgregorian(date=d).strftime('%Y/%m/%d')


def local(v):
    if not v:
        return ''
    d = datetime.fromisoformat(v).astimezone(TZ)
    return jd(d.date()) + ' ' + d.strftime('%H:%M:%S')


def source_report(key, target):
    return next((r for r in SOURCES.get(key, {}).get('reports', []) if r['date'] == target), {})


def reason_for(sid, target, run, future):
    if future:
        if sid == 'maintenance_daily_report':
            return 'موعد نرسیده؛ در بررسی فعلی، داده این تاریخ در فایل محلی نیست؛ همگام‌سازی تعمیرات تعارض ساختار دارد.'
        if sid == 'machinery_performance_daily_report':
            return 'موعد نرسیده؛ در بررسی فعلی، اطلاعات روز ۱۸ آبان هنوز برای دستگاه‌ها ثبت نشده است.'
        return 'موعد نرسیده؛ وجود داده منبع بررسی شده است و به‌تنهایی تضمین ارسال بعدی نیست.'
    error = run.get('error') if run else None
    if not error:
        if sid.startswith('overflow') and target == '1405/08/15':
            return 'ارسال نهایی تأیید شد؛ ابتدا date_missing و پس از آماده شدن داده در ۰۸:۰۳ ارسال شد.'
        if sid.startswith('overflow') and target == '1405/08/17':
            return 'ارسال نهایی تأیید شد؛ خطای ارتباط بله برطرف شد و در ۰۸:۱۹ ارسال شد.'
        if sid == 'machinery_performance_daily_report' and target == '1405/08/17':
            return 'ارسال در ۱۹ آبان ۰۳:۱۰؛ صف FIFO منتظر روزهای قدیمی ناقص بود؛ ابتدا آن روزها کنار گذاشته شدند.'
        return 'رسید فایل گزارش تأیید شد.'
    if sid.startswith('driver_daily'):
        text = 'در فایل محلی رانندگان، شیت با تاریخ هدف شناسایی نشد؛ در بررسی فعلی نیز تاریخ‌های ۱۵ و ۱۶ آبان موجود نیستند.'
    elif sid.startswith('overflow'):
        text = 'گزارش تاریخ هدف در فایل سرریز موجود نبود؛ بررسی فعلی هم date_missing را تأیید کرد.'
    elif sid == 'maintenance_daily_report':
        text = 'هیچ رکورد تعمیراتی برای تاریخ هدف در فایل محلی پیدا نشد؛ آخرین تاریخ محلی ۱۵ آبان است. همگام‌سازی به‌علت تعارض ساختار شیت متفرقه متوقف است؛ وضعیت ثبت در فایل معدن از این بررسی معلوم نیست.'
    elif sid == 'oil_balance_daily_report':
        text = 'برای تاریخ هدف ردیفی در موازنه روغن پیدا نشد؛ بررسی فعلی: صفر ردیف برای ۱۶ و ۱۷ آبان، با وجود داده روز ۱۸ آبان.'
    elif sid == 'machinery_performance_daily_report':
        text = 'داده روز هدف کامل نبود. در بررسی فعلی، ۵۱ دستگاه آماده‌اند و فقط ۷۰۴ ناقص است؛ شیت 8، ردیف 27. این جزئیات مربوط به فایل فعلی است؛ لاگ تاریخی فقط date_missing را نگه داشته است.'
    else:
        text = 'علت ثبت‌شده: ' + str(error)
    if error == 'missing_date_expired':
        text += ' پس از ۳۶ ساعت از موعد اصلی، این نوبت با missing_date_expired بسته شد؛ ارسال خودکار آن ادامه ندارد.'
    elif error == 'superseded_missing_date':
        text += ' پس از ۳۶ ساعت و آماده شدن روز جدیدتر، با superseded_missing_date کنار گذاشته شد؛ ارسال خودکار این نوبت ادامه ندارد.'
    else:
        text += ' این نوبت هنوز در صف انتظار داده است.'
    return text


def occurrence(sid, date):
    job = JOBS[sid]
    due_local = datetime.combine(date, datetime.min.time(), TZ).replace(hour=job['trigger']['hour'], minute=job['trigger']['minute'])
    due = due_local.astimezone(ZoneInfo('UTC')).isoformat()
    run = RUNS.get((sid, due))
    target = jd(date - timedelta(days=1))
    future = due_local > ASOF.astimezone(TZ)
    recipients = list(E['current_recipients'][sid]['recipients'])
    receipts = [r for r in T['receipts'] if r['schedule_id'] == sid and r['due'] == due]
    artifacts = [r for r in T['office_artifact_receipts'] if r['schedule_id'] == sid and r['due'] == due]
    historical = {r['recipient_id'] for r in receipts + artifacts}
    recipients = sorted(set(recipients) | historical)
    details = []
    for uid in recipients:
        receipt = next((r for r in receipts if r['recipient_id'] == uid and r['delivery_kind'] == 'report'), None)
        arts = [r for r in artifacts if r['recipient_id'] == uid]
        # Office parent receipts intentionally have no message_id; every artifact must prove delivery.
        required = {'mechanical', 'metalwork'} if sid == 'driver_daily_office_supervisor' else None
        office_ok = bool(arts) and all(a['status'] == 'sent' and a['message_id'] for a in arts)
        if required is not None:
            office_ok = office_ok and {a['artifact_id'] for a in arts} == required
        report_ok = bool(receipt and receipt['status'] == 'sent' and (office_ok if sid in {'driver_daily_office_supervisor', 'overflow_daily_test'} else receipt['message_id']))
        confirmed = bool(run and run['status'] == 'succeeded' and report_ok)
        uncertain = any(a['status'] in {'uncertain', 'sending'} for a in arts) or bool(receipt and receipt['status'] in {'uncertain', 'sending'})
        status = 'موعد نرسیده' if future else ('ارسال تأییدشده' if confirmed else ('نامطمئن' if uncertain else 'ارسال نشده'))
        ids = [a['message_id'] for a in arts if a['status'] == 'sent' and a['message_id']] if arts else ([receipt['message_id']] if receipt and receipt['message_id'] and receipt['status'] == 'sent' else [])
        updates = [a['updated'] for a in arts if a['status'] == 'sent'] if arts else ([receipt['updated']] if receipt and receipt['status'] == 'sent' else [])
        details.append({'name': NAMES.get(uid, uid), 'uid': uid, 'status': status, 'message_ids': ', '.join(ids), 'sent_at': local(max(updates)) if updates else '', 'receipt_state': receipt['status'] if receipt else '', 'artifact_count': len(arts)})
    states = {d['status'] for d in details}
    state = states.pop() if len(states) == 1 else 'ارسال ناقص/نیازمند بررسی'
    return {'sid': sid, 'title': META[sid][0], 'source': META[sid][1], 'day': jd(date), 'target': target, 'due': due, 'due_local': local(due), 'status': state, 'run': run, 'details': details, 'reason': reason_for(sid, target, run, future)}


DAYS = [datetime(2026, 11, day).date() for day in (7, 8, 9, 10)]
OCC = [occurrence(sid, date) for date in DAYS for sid in ORDER]
ANNEX = [occurrence(sid, datetime(2026, 11, 6).date()) for sid in ORDER]
counts = Counter(o['status'] for o in OCC)
recipient_counts = Counter(d['status'] for o in OCC for d in o['details'])
S = {'captured_at_tehran': E['captured_at_tehran'], 'occurrence_counts': dict(counts), 'recipient_counts': dict(recipient_counts), 'occurrences': OCC, 'annex_1405_08_15': ANNEX}
(OUT / 'summary.json').write_text(json.dumps(S, ensure_ascii=False, indent=2), encoding='utf-8')


def md_table(headers, rows):
    def cell(v):
        return str(v if v is not None else '').replace('|', ' / ').replace('\n', '<br>')
    return '\n'.join(['| ' + ' | '.join(map(cell, headers)) + ' |', '| ' + ' | '.join('---' for _ in headers) + ' |'] + ['| ' + ' | '.join(map(cell, row)) + ' |' for row in rows])


notes = [
    'بازه اصلی: ۱۴۰۵/۰۸/۱۶ تا ۱۴۰۵/۰۸/۱۹؛ وضعیت دفتر رسیدها تا ساعت ۰۸:۲۵:۳۲ روز ۱۹ آبان به وقت تهران ثبت شده است. تاریخ داده همه گزارش‌های روزانه، یک روز قبل از موعد ارسال است.',
    'تعریف شمارش: هر نوبت، یک مسیر گزارش در یک روز است؛ یک نوبت ممکن است چند گیرنده و چند فایل داشته باشد. از ۳۲ نوبت چهار روز: ۱۲ ارسال کامل، ۱۴ ارسال‌نشده، ۶ نوبت آینده. در سطح گیرنده: ۲۲ ارسال گزارش تأییدشده، ۲۴ ارسال‌نشده، ۱۰ تعهد با موعد آینده. اصلاحیه دستی دو گیرنده دارد و جدا شمرده شده است.',
    'ارسال تأییدشده یعنی رسید موفق بله با شناسه پیام. این رسید اثبات خواندن پیام توسط فرد نیست. برای گزارش‌های چندفایلی دفتر، رسید تک‌تک فایل‌ها بررسی شده است. هشدار، یادآوری و پاسخ گفت‌وگو، ارسال فایل گزارش محسوب نشده‌اند.',
    'بررسی فایل‌های فعلی پس از ثبت دفتر رسیدها انجام شد. جزئیات فعلی منبع را نباید به‌جای نسخه تاریخی هنگام تلاش ارسال تلقی کرد؛ هش فایل‌ها و زمان بررسی در current-source-check.json ثبت شده است.',
]
matrix = []
for sid in ORDER:
    row = [META[sid][0], '، '.join(NAMES[u] for u in E['current_recipients'][sid]['recipients'])]
    for day in DAYS:
        o = next(o for o in OCC if o['sid'] == sid and o['day'] == jd(day))
        state = o['status']
        if state == 'ارسال نشده':
            state += '؛ ' + ('در صف' if o['run']['status'] == 'waiting_for_data' else 'بسته شده')
        elif state == 'موعد نرسیده':
            state += '؛ ' + o['due_local'][-8:-3]
        elif sid == 'machinery_performance_daily_report' and o['target'] == '1405/08/17':
            state += '؛ ۱۹ آبان ۰۳:۱۰'
        row.append(state)
    matrix.append(row)

text = '\n\n'.join(['# بررسی ارسال گزارش‌های چهار روز اخیر', *notes, '## وضعیت همه مسیرهای گزارش', md_table(['گزارش', 'گیرندگان', 'موعد ۱۶ آبان / داده ۱۵', 'موعد ۱۷ آبان / داده ۱۶', 'موعد ۱۸ آبان / داده ۱۷', 'موعد ۱۹ آبان / داده ۱۸'], matrix)])
sentrows, missingrows, allrows = [], [], []
for o in OCC:
    for d in o['details']:
        r = o['run'] or {}
        row = [o['day'], o['target'], o['title'], d['name'], d['uid'], d['status'], o['due_local'], d['sent_at'], d['message_ids'], r.get('status', ''), r.get('error', ''), r.get('attempt', ''), local(r.get('next_attempt_at')), o['source'], o['reason']]
        allrows.append(row)
        if d['status'] == 'ارسال تأییدشده':
            sentrows.append([o['target'], o['title'], d['name'], d['sent_at'], d['message_ids']])
    if o['status'] == 'ارسال نشده':
        r = o['run'] or {}
        missingrows.append([o['day'], o['target'], o['title'], '، '.join(d['name'] for d in o['details']), r.get('error', ''), r.get('attempt', ''), local(r.get('next_attempt_at')), o['reason']])
text += '\n\n## ارسال‌های تأییدشده به تفکیک فرد\n\n' + md_table(['تاریخ داده', 'گزارش', 'گیرنده', 'زمان ارسال تهران', 'شناسه پیام فایل'], sentrows)
text += '\n\n## گزارش‌های ارسال‌نشده و علت\n\n' + md_table(['روز موعد', 'تاریخ داده', 'گزارش', 'گیرندگان', 'خطای ثبت‌شده', 'تلاش', 'تلاش بعدی در زمان ثبت وضعیت', 'شرح علت و وضعیت صف'], missingrows)
text += '\n\n## نکات لازم برای پیگیری\n\n' + '\n\n'.join([
    'رانندگان: تاریخ‌های ۱۵ و ۱۶ آبان در شیت‌های شناخته‌شده فایل محلی وجود ندارند. نسخه‌های دفتر و مکانیکی پس از ۳۶ ساعت بسته شده‌اند. نسخه آهنگری برای صادق صادقیان همچنان در صف است؛ این مسیر در مجموعه MISSING_EXPIRY_TASKS قرار ندارد. برای آهنگریِ این دو تاریخ فقط هشدار نبود داده با شناسه‌های 7258 و 7281 ارسال شده و فایل گزارش ارسال نشده است.',
    'سرریز: داده ۱۶ آبان در بررسی فعلی هم موجود نیست. نسخه‌های دفتر و مکانیکی پس از پایان مهلت ۳۶ ساعت بسته شده‌اند. سرریز داده ۱۵ آبان ابتدا منتظر داده بود و در ۱۶ آبان ساعت ۰۸:۰۳ ارسال شد. سرریز داده ۱۷ آبان پس از اختلال ارتباط بله در ۱۸ آبان ساعت ۰۸:۱۹ ارسال شد.',
    'تعمیرات: فایل محلی تا تاریخ ۱۵ آبان رکورد دارد. تاریخ‌های ۱۶، ۱۷ و ۱۸ آبان صفر رکورد دارند. وضعیت Sync نشان می‌دهد تعارض ساختار شیت «متفرقه» با MAINTENANCE_SYNC_STRUCTURE_CONFLICT جایگزینی را متوقف کرده و هر دو نسخه نگهداری شده‌اند. خالی بودن نسخه محلی اثبات نبود ثبت در فایل معدن نیست. نوبت داده ۱۶ بسته شده؛ نوبت داده ۱۷ در صف است؛ موعد داده ۱۸ هنوز نرسیده است.',
    'موازنه روغن: برای ۱۶ و ۱۷ آبان صفر ردیف وجود دارد؛ با این حال روز ۱۸ آبان دو ردیف دارد. پس نبود داده این دو روز را نمی‌توان صرفاً با قدیمی بودن کل فایل توضیح داد. نوبت داده ۱۶ بسته شده و نوبت داده ۱۷ در صف است.',
    'عملکرد: در فایل فعلی روزهای ۱۵ و ۱۶ آبان ۵۱ دستگاه آماده‌اند و تنها کد ۷۰۴ داده ثبت‌شده ندارد. شیت 8، ردیف 27؛ روز ۱۵: EP27، EQ27، ET27، EU27 خالی؛ روز ۱۶: EZ27، FA27، FD27، FE27 خالی. شرط ارسال، کامل بودن همه دستگاه‌هاست. لاگ تاریخی خطای کلی date_missing را ثبت کرده و علت جزئی فوق از فایل فعلی استخراج شده است. این دو نوبت پس از گذشت ۳۶ ساعت و آماده بودن روز جدیدتر کنار گذاشته شده‌اند. داده روز ۱۷ برای ۵۲ دستگاه کامل است و به امامی و هدایتی در ۱۹ آبان ۰۳:۱۰ ارسال شده؛ نوبت روز ۱۸ هنوز موعد ندارد ولی داده آن فعلاً برای همه دستگاه‌ها خالی است.',
    'برنامه امروز: رانندگان ساعت ۰۹:۰۰، تعمیرات ساعت ۱۲:۰۰، موازنه روغن و عملکرد ساعت ۱۵:۰۰. در بررسی فعلی، داده ۱۸ آبان رانندگان و موازنه روغن موجود است؛ تعمیرات و عملکرد هنوز داده لازم را ندارند. وجود داده به‌تنهایی تضمین موفقیت تولید یا ارسال بعدی نیست.',
    'دو برنامه قدیمی repairs_daily و metalwork_daily در تنظیمات فعلی غیرفعال‌اند و در این چهار روز اجرای ثبت‌شده ندارند؛ این دو با مسیر فعال «تعمیرات روزانه ماشین‌آلات» متفاوت‌اند.',
    'هیچ گزارش بازفرستاده نشد، هیچ صف یا تنظیمات عملیاتی تغییر نکرد و هیچ سرویس بازراه‌اندازی نشد. این خروجی فقط یک بررسی و ثبت شواهد است.',
])
manual = json.loads((OUT / 'manual-correction-evidence.json').read_text(encoding='utf-8'))
manualrows = []
for r in T['office_artifact_receipts']:
    if r['schedule_id'].startswith('manual-driver-metalwork-correction-'):
        manualrows.append(['1405/08/17', NAMES[r['recipient_id']], r['recipient_id'], local(r['updated']), r['status'], r['message_id'], r['sha256']])
text += '\n\n## اصلاحیه دستی جدا از ارسال زمان‌بندی‌شده\n\n' + manual['caption'].replace('\n', '\n\n') + '\n\n' + md_table(['تاریخ داده', 'گیرنده', 'شناسه فرد', 'زمان تهران', 'وضعیت', 'شناسه پیام', 'هش فایل'], manualrows)
text += '\n\nنسخه اصلی دفتر با شناسه‌های 7316 برای امامی و 7318 برای پاکدل حفظ شده است. اصلاحیه فوق برای این دو نفر ارسال شده؛ در دفتر رسیدها، اصلاحیه جداگانه برای صادق صادقیان ثبت نشده است. پیام اولیه او 7321 است؛ از رسید به‌تنهایی نمی‌توان درباره محتوای آن فایل حکم داد.'
annexrows = [[o['day'], o['target'], o['title'], '؛ '.join(d['name'] + ': ' + d['message_ids'] for d in o['details']), o['status']] for o in ANNEX]
text += '\n\n## ضمیمه روز ۱۵ آبان\n\nبرای روشن بودن تفاوت «چهار روز تقویمی با امروز» و «چهار روز کامل گذشته»، روز ۱۵ آبان نیز در این ضمیمه آمده است؛ در آمار اصلی شمرده نشده است. هر هشت مسیر آن روز ارسال کامل داشته‌اند.\n\n' + md_table(['روز موعد', 'تاریخ داده', 'گزارش', 'گیرندگان و شناسه پیام', 'وضعیت'], annexrows)
text += '\n\n## شواهد\n\nمنابع بررسی: snapshot سازگار SQLite از runtime/scheduler/runs.sqlite3؛ جداول runs، receipts، office_artifact_receipts، report_preflight_runs و یادآوری‌ها؛ تنظیمات schedules.yaml؛ لاگ‌های scheduler؛ فایل‌های محلی فقط‌خواندنی در E:\\Function؛ ثبت اصلاحیه دستی؛ وضعیت ذخیره‌شده Sync. فهرست هش فایل‌های خروجی در manifest.json ثبت شده است.'
(OUT / 'report-fa.md').write_text(text + '\n', encoding='utf-8')

wb = Workbook()
wb.remove(wb.active)


def sheet(title, headers, rows, widths=None):
    ws = wb.create_sheet(title)
    ws.sheet_view.rightToLeft = True
    ws.freeze_panes = 'A2'
    ws.append(headers)
    for row in rows:
        ws.append(['' if v is None else v for v in row])
    ws.auto_filter.ref = ws.dimensions
    ws.sheet_properties.pageSetUpPr.fitToPage = True
    ws.page_setup.orientation = 'landscape'
    ws.page_setup.paperSize = ws.PAPERSIZE_A3
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.print_title_rows = '1:1'
    for c in ws[1]:
        c.font = Font(name='Tahoma', size=10, bold=True, color='FFFFFF')
        c.fill = PatternFill('solid', fgColor='23445C')
        c.alignment = Alignment(horizontal='right', vertical='center', wrap_text=True)
    ws.row_dimensions[1].height = 38
    for row in ws.iter_rows(min_row=2):
        state = next((str(c.value) for c in row if c.value in {'ارسال تأییدشده', 'ارسال نشده', 'موعد نرسیده', 'نامطمئن'}), '')
        color = {'ارسال تأییدشده': 'E6F2E6', 'ارسال نشده': 'FCE8E6', 'موعد نرسیده': 'E8F0FE', 'نامطمئن': 'FFF2CC'}.get(state)
        for c in row:
            c.font = Font(name='Tahoma', size=10)
            c.alignment = Alignment(horizontal='right', vertical='top', wrap_text=True)
            if color:
                c.fill = PatternFill('solid', fgColor=color)
        line_count = max(sum(max(1, math.ceil(len(part) / max(8, int((widths or {}).get(c.column, 24) * 0.9)))) for part in str(c.value or '').split('\n')) for c in row)
        ws.row_dimensions[row[0].row].height = min(240, max(60, 15 * line_count))
    for col in range(1, len(headers) + 1):
        ws.column_dimensions[get_column_letter(col)].width = (widths or {}).get(col, 24)
    return ws


sheet('راهنما', ['موضوع', 'شرح'], [[f'توضیح {i+1}', n] for i, n in enumerate(notes)] + [['نتیجه', '۱۲ نوبت کامل؛ ۱۴ ارسال‌نشده؛ ۶ نوبت آینده؛ یک نسخه اصلاحی با ۲ رسید برای ۲ نفر جدا از شمارش اصلی.'], ['داده تاریخی و فعلی', 'وضعیت ارسال از دفتر رسیدهاست؛ جزئیات فعلی منبع زمان بررسی جدا دارد.'], ['محدودیت', 'رسید بله اثبات خواندن توسط انسان نیست؛ فایل معدن به‌صورت مستقیم بررسی نشده است.'], ['پیگیری', 'بسته شدن نوبت به معنی ارسال نیست. اصلاح فایل به‌تنهایی نوبت بسته‌شده را دوباره ارسال نمی‌کند.']], {1: 24, 2: 125})
sheet('نمای کلی', ['گزارش', 'گیرندگان', 'موعد ۱۶، داده ۱۵', 'موعد ۱۷، داده ۱۶', 'موعد ۱۸، داده ۱۷', 'موعد ۱۹، داده ۱۸'], matrix, {1: 37, 2: 40, 3: 35, 4: 35, 5: 35, 6: 35})
sheet('همه افراد و روزها', ['روز موعد', 'تاریخ داده', 'گزارش', 'فرد', 'شناسه فرد', 'وضعیت ارسال', 'موعد تهران', 'زمان ارسال تهران', 'شناسه پیام فایل', 'وضعیت اجرا', 'خطا', 'تعداد تلاش', 'تلاش بعدی در زمان بررسی', 'فایل منبع', 'علت و وضعیت صف'], allrows, {3: 38, 4: 28, 9: 26, 14: 36, 15: 100})
sheet('ارسال شده', ['تاریخ داده', 'گزارش', 'فرد', 'زمان ارسال تهران', 'شناسه پیام فایل'], sentrows, {2: 40, 3: 28, 4: 27, 5: 26})
sheet('ارسال نشده', ['روز موعد', 'تاریخ داده', 'گزارش', 'گیرندگان', 'خطای ثبت‌شده', 'تعداد تلاش', 'تلاش بعدی در زمان بررسی', 'علت و وضعیت صف'], missingrows, {3: 39, 4: 42, 5: 33, 8: 110})
sheet('اصلاحیه دستی', ['تاریخ داده', 'فرد', 'شناسه فرد', 'زمان ارسال تهران', 'وضعیت', 'شناسه پیام', 'SHA256'], manualrows, {2: 28, 4: 27, 7: 70})
sheet('ضمیمه ۱۵ آبان', ['روز موعد', 'تاریخ داده', 'گزارش', 'گیرندگان و شناسه پیام', 'وضعیت'], annexrows, {3: 38, 4: 85})
raw = []
for table in ['receipts', 'office_artifact_receipts', 'completion_reminder_receipts', 'delivery_alert_receipt', 'source_notice_alert_receipt']:
    for r in T[table]:
        timestamp = r.get('updated') or r.get('due') or r.get('slot')
        if timestamp and datetime.fromisoformat(timestamp).astimezone(TZ).date() < DAYS[0]:
            continue
        uid = r.get('recipient_id', r.get('operator_id', ''))
        kind = r.get('delivery_kind') or ('report_artifact' if table == 'office_artifact_receipts' else 'reminder_or_alert')
        raw.append([table, r['schedule_id'], local(r.get('due', r.get('slot'))), r.get('target_date', ''), NAMES.get(uid, uid), uid, kind, r.get('artifact_id', r.get('alert_kind', '')), r['status'], r.get('message_id', ''), local(r.get('updated')), r.get('error', '')])
sheet('رسیدها و هشدارها', ['جدول', 'مسیر', 'موعد تهران', 'تاریخ داده', 'نام', 'شناسه فرد', 'نوع رسید', 'نوع فایل یا هشدار', 'وضعیت', 'شناسه پیام', 'ثبت رسید تهران', 'خطا'], raw, {1: 35, 2: 50, 5: 28, 12: 40})
source_rows = []
for name, source in SOURCES.items():
    if not isinstance(source, dict):
        continue
    for r in source.get('reports', []):
        source_rows.append([name, r['date'], r.get('status', r.get('reason', '')), r.get('rows', r.get('rows_matched', '')), r.get('latest_date', max(r.get('available_dates', []), default='')), ', '.join(r.get('missing_codes', [])), json.dumps(r.get('sections', {}), ensure_ascii=False), source.get('sha256', '')])
sheet('بررسی فعلی منابع', ['منبع', 'تاریخ داده', 'وضعیت', 'ردیف', 'آخرین تاریخ کامل', 'کدهای ناقص', 'بخش‌های رانندگان', 'SHA256'], source_rows, {1: 30, 6: 75, 7: 80, 8: 70})
pre = [[r['schedule_id'], local(r['due']), r['status'], local(r['checked']), r.get('sample_date'), r.get('error_code'), r.get('error_detail')] for r in T['report_preflight_runs'] if datetime.fromisoformat(r['due']).astimezone(TZ).date() in DAYS]
sheet('بررسی پیش از موعد', ['مسیر', 'موعد تهران', 'وضعیت', 'زمان بررسی تهران', 'تاریخ نمونه بررسی', 'خطا', 'جزئیات'], pre, {1: 45, 7: 70})
path = OUT / 'delivery-audit-fa.xlsx'
wb.save(path)

# Structural acceptance of the generated artifact; no operational writes or sends.
check = load_workbook(path, read_only=True, data_only=True)
assert check['همه افراد و روزها'].max_row == 57
assert check['ارسال شده'].max_row == 23
assert check['ارسال نشده'].max_row == 15
assert counts == {'ارسال تأییدشده': 12, 'ارسال نشده': 14, 'موعد نرسیده': 6}, counts
assert recipient_counts == {'ارسال تأییدشده': 22, 'ارسال نشده': 24, 'موعد نرسیده': 10}, recipient_counts
assert len(manualrows) == 2 and all(r[4] == 'sent' and r[5] for r in manualrows)
assert all(o['status'] == 'ارسال تأییدشده' for o in ANNEX)
assert not list(OUT.rglob('*.partial'))
check.close()
manifest = {p.name: {'bytes': p.stat().st_size, 'sha256': hashlib.sha256(p.read_bytes()).hexdigest()} for p in OUT.iterdir() if p.is_file() and p.name != 'manifest.json'}
(OUT / 'manifest.json').write_text(json.dumps(manifest, indent=2, ensure_ascii=False), encoding='utf-8')
print(json.dumps({'output': str(path), 'occurrences': counts, 'recipients': recipient_counts, 'manual_corrections': len(manualrows), 'structural_checks': 'passed'}, ensure_ascii=False))
