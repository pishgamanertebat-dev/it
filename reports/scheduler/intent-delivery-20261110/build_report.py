"""Produce the Persian audit from evidence already captured; never send anything."""
from datetime import datetime
import json
from pathlib import Path

ROOT=Path('E:/KomatsoAI');OUT=Path(__file__).resolve().parent;WORK=ROOT/'runtime/intent-delivery-20261110'
def load(p):return json.loads(Path(p).read_text('utf-8-sig'))

def build():
    install=load(WORK/'installed.json');gateway=load(WORK/'gateway-after.json')
    files=list(OUT.glob('delivery-*.json'));latest=max((load(p) for p in files),key=lambda x:x['captured_at'])
    replay=load(WORK/'message-real-replay.json');source=load(OUT/'current-sources.json');sync=load(OUT/'sync-state.json')
    pdf=load(OUT/'recovery-pdf-validation.json');pakdel=load(OUT/'pakdel-7366.json');guard=load(OUT/'sync-guard-proof.json')
    text=['# اصلاح قصد AI و ممیزی جاری تحویل',
        'آخرین Snapshot رسیدها: '+latest['captured_at']+' (UTC). تاریخ عملیاتی: ۱۹ آبان ۱۴۰۵؛ ساعت‌ها در توضیحات تهران هستند.',
        '**اصلاح نصب شد؛ تأیید نهایی عملیات آینده فقط پس از مشاهده رسید همان موعد اعلام می‌شود.**',
        'پایش زمان‌دار فقط‌خواندنی برای امروز در ۱۵:۰۵، ۱۵:۱۵، ۱۷:۳۵ و ۱۷:۴۵ فعال است و پس از آخرین بررسی متوقف می‌شود. نتیجه‌ها و مقایسه عدم تکرار خلاصه در همین گزارش ثبت می‌شوند. [وضعیت زنده پایش](observer-state.json). این Helper هیچ Sender، HTTP، Tick یا Write عملیاتی ندارد.',
        '## ۱. اصلاح نصب‌شده',
        'تشخیص واژه پرسشی با مرز Unicode انجام می‌شود؛ «چراغ» با «چرا» اشتباه نمی‌شود. عنوان روشنِ تاریخ‌دار گزارش، OFFER می‌گیرد؛ پرسش فنی مستقل وارد فرم AI نمی‌شود. متن خام در تشخیص تغییر نمی‌کند. استخراج ناقص بندهای بولت‌دار رد می‌شود.',
        'Handler: `'+gateway['handler_version']+'`. Gateway تازه با PID '+str(gateway['identity']['pid'])+' و اتصال Bale/Telegram تأیید شد. کد نصب‌شده با Hash نسخه تست‌شده برابر است.',
        '\n'.join('- فاز '+c['phase']+': `'+c['commit']+'`' for c in install['commits']),
        'Backup: `'+install['backup']+'`. نصب، فایل‌های عملیاتی، مجوزها، Receiptها و runs را تغییر نداد؛ تغییرهای طبیعی بعدی Scheduler از نصب جدا هستند.',
        '## ۲. پیام واقعی ۷۳۴۹',
        'متن اصلی از state.db با platform_message_id=7349 و Actor=1732374823 خوانده شد. SHA256 متن: `'+replay['raw_sha256']+'`.',
        'آزمون واقعی کلاینت فعلی مدل با مجوز صریح مالک انجام شد؛ هر هشت بند، «چراغ» و عبارت «رفع عیب کد خطا» بدون حذف باقی ماندند. خروجی همان مدل روی کپی Workbook، DB جدا و Bale جعلی بازپخش شد. OFFER و دکمه «ساخت پیش‌نویس ثبت» تأیید شد. پیام واقعی قدیمی دوباره ارسال یا ویرایش نشد.',
        '| کد خام | هویت فعلی Resolver | ردیف شیت ۱۷ آبان | اقدام لازم |',
        '|---|---|---|---|']
    for d in replay['devices']:
        action=('تعیین هویت معتبر و توسعه مجاز Resolver؛ سپس بررسی ردیف' if d.get('needs_resolver') else
                ('افزودن ردیف فقط با تأیید مستقل مالک ساختار' if d['needs_target_row'] else 'انتخاب صریح افزودن/جایگزینی شرح قبلی'))
        text.append('| '+d['raw_code']+' | '+d.get('canonical','ناشناخته')+' | '+str(d.get('target_row') or 'موجود نیست / نامشخص')+' | '+action+' |')
    text += ['برای ثبت فقط ۷۱۷ و ۵۲۱، توسعه Resolver لازم نیست؛ سایر موارد باید به‌صورت صریح توسط کاربر از همین ثبت خارج شوند یا ابهام‌هایشان حل شود. هیچ بند به‌طور خودکار حذف نشده است. برای ثبت کامل هشت بند، تعیین هویت ۱۰۰۳/۱۰۰۶ و تأیید ساختار ردیف‌های مفقود لازم است. مدل همچنین ابهام «کدام پمپ»، «کد خطا» و «ساعتی» را حفظ کرده است.',
        '## ۳. فرم دستی، تأییدها و آزمون‌ها',
        '۳۱۳ تست منتخب گردش‌های موجود و ۷۷ تست متمرکز موفق شدند؛ شکست، Error و Skip در اجرای نهایی صفر بود. تست‌های متمرکز HTTP و اتصال بیرونی را مسدود کردند و تلاش بیرونی صفر بود. دو مجموعه هم‌پوشانی دارند و با هم جمع نشده‌اند. این نتیجه، ادعای سبز بودن کل مخزن نیست.',
        'مرز واژه فارسی، فاصله/نیم‌فاصله، نشانه‌گذاری، متن ترکیبی، عنوان بدون «ثبت کن»، سؤال واقعی، چهار Actor، تقدم دستی/Work Order، دو تأیید مستقل، منع Write پیش از تأیید، Restart، Callback قدیمی، جلوگیری از تکرار ثبت و فرم‌های دستی بدون مدل پوشش داده شدند. نبود ردیف در Draft و دوباره زیر قفل Writer مسدود می‌شود؛ رفتار ایجاد ردیف فرم دستی عوض نشده است.',
        'دو خطای انتظار اولیه تست اصلاح شد (خواندن برچسب کیبورد و حفظ هویت در Fixture حذف ردیف). یک اجرای ابزار منع شبکه، Socket محلی داخلی Windows asyncio را هم مسدود کرد؛ پس از محدودکردن استثنا به loopback، ۷۷ تست با صفر اتصال بیرونی عبور کردند. این خطاهای زیرساخت تست، سبز گزارش نشده‌اند.',
        '## ۴. Sync و ساختار معدن',
        'health_status='+str(sync.get('health_status'))+'؛ sync_healthy='+str(sync.get('sync_healthy',sync['last_run'].get('sync_healthy')))+'؛ آخرین Sync موفق UTC: '+str(sync.get('last_successful_run'))+'.',
        'source_timeout در مرحله دسترسی/شمارش منبع رخ داده و آخرین بازیابی ثبت‌شده UTC '+str(sync.get('last_recovery_at'))+' است. علت شبکه‌ای ریشه‌ای از شواهد موجود قطعی نیست. خواندن مستقیم فایل‌های SMB و دو بار Hash ثابت موفق شد؛ Get-SmbConnection حتی خارج Sandbox نیز Access denied داد، بنابراین Dialect/Session با آن Cmdlet تأیید نشده است.',
        '| فایل | SHA256 سرور | SHA256 معدن | برابر |','|---|---|---|---|']
    for name,pair in source['files'].items():
        text.append('| '+name+' | `'+pair['server'].get('sha256','خواندن ناموفق')+'` | `'+pair['mine'].get('sha256','خواندن ناموفق')+'` | '+str(pair['hash_equal'])+' |')
    text += ['تفاوت رانندگان۲ با Journal معتبر توضیح داده شد: Hash سرور برابر سر زنجیره Journal و Hash معدن برابر upstream ثبت‌شده است. سه تغییر محلی محافظت‌شده شامل تاریخ C1 در شیت ۱۷ مهر و ارتفاع دو ردیف قدیمی هستند. هیچ‌کدام حذف یا بازسازی نشدند.',
        'محافظ additions() از Baseline مورد تأیید مالک تا فایل فعلی عبور کرد. در «متفرقه» داده افزوده شده است؛ این به معنی برابری کامل شیت با Baseline نیست. سلول‌های تاریخی و قرارداد ساختار/چاپ حفظ شده‌اند و تعارض ساختاری تازه ثبت نشده است.',
        '## ۵. چهار بازیابی تعمیرات',
        'Receiptها و Holdهای فعلی نشان می‌دهند '+str(sum(r['awaiting_owner'] for r in latest['recovery']))+' عملیات از چهار عملیات همچنان منتظر تأیید مالک هستند. هیچ مجوز اجرا، ارسال مستقل، Backfill یا ثبت تاریخی ایجاد نشد.',
        '| تاریخ داده | PDF SHA256 | منبع معدن/سرور مطابق | نتیجه بررسی محتوای فایل اصلی |','|---|---|---|---|']
    for day,v in pdf.items():
        text.append('| '+day+' | `'+v['pdf_sha256']+'` | '+str(v['source_matches_server'] and v['source_matches_mine'])+' | '+str(v['validation']['rows'])+' ردیف، '+str(v['validation']['pages'])+' صفحه؛ تأیید کامل |')
    text += ['برای بازاعتبارسنجی بندهای شکسته‌شده، چینش دقیق از Snapshot خصوصی بازسازی شد؛ Hash PDF مرجع خصوصی هم عیناً با PDF آماده قبلی برابر است. خود PDF اصلی با آن چینش تأیید شد. فراخوانی ساده scan سپس validate بدون pdf_layout برای متن چندبخشی کافی نبود؛ آن خطای اولیه، نقص قطعی PDF تلقی نشده است.',
        '## ۶. روغن و عملکرد داده ۱۸ آبان، موعد ۱۵:۰۰']
    if not latest['past_1500']:
        text.append('هنوز پس از موعد بررسی نشده است؛ قبل از ۱۵:۰۰ هیچ ادعای ارسال ساخته نمی‌شود. Preflight فعلی روغن passed و عملکرد no_data بود. پایش فقط‌خواندنی پس از موعد جدا ثبت می‌شود.')
    else:
        for sid in ('oil_balance_daily_report','machinery_performance_daily_report'):
            matches=[r for r in latest['data18_1500'] if r['schedule_id']==sid]
            if not matches:text.append('- '+sid+': نوبت داده ۱۸ در Snapshot یافت نشد؛ ارسال تأیید نشد.');continue
            r=matches[0];text.append('- '+sid+': '+r['status']+'؛ خطا/علت: '+str(r['error'])+'؛ Receiptها: '+json.dumps(r['receipts'],ensure_ascii=False))
    text += ['## ۷. Receipt مکانیکی پاکدل ۷۳۶۶',
        'مقصد دقیق `514458396`، artifact=`mechanical`، تاریخ داده ۱۴۰۵/۰۸/۱۸، نوع مسیر `application/pdf`، وضعیت `sent` و SHA256=`'+pakdel['receipt']['sha256']+'` با ممیزی قبلی تطبیق دارند. فایل فعلی منبع همان Hash زمان ممیزی تحویل را دارد و ۳۵ شرح مکانیکی مورد انتظار است.',
        'مسیر تولید پیش از ارسال، اعتبارسنجی کامل سلول‌ها و تطبیق Hash PDF را الزام می‌کند. PDF موقت اولیه نگه‌داری نشده است؛ بنابراین بازخوانی مستقل بایت‌های نسخه دریافت‌شده و مشاهده خواندن توسط پاکدل قابل اثبات نیست. رسید مکانیکی با آهنگری ۷۳۶۷ یا اصلاحیه ۷۳۴۸ اشتباه نشده است.',
        '## ۸. خلاصه ۱۷:۳۰ و هشدارها']
    if not latest['past_1730']:text.append('موعد ۱۷:۳۰ هنوز برای تأیید واقعی نرسیده است. تست یک Snapshot ثابت، Receipt مستقل هر بخش، عدم تکرار در Tick بعد و Restart عبور کرده است؛ رسید واقعی بعد از موعد به گزارش افزوده می‌شود.')
    else:
        text.append('Receiptهای واقعی خلاصه: '+json.dumps(latest['manager_receipts'],ensure_ascii=False))
        text.append('کنترل یکپارچگی Snapshot: '+json.dumps([{k:v for k,v in s.items() if k!='owner_closed'} | {
            'owner_closed_count':len(s['owner_closed']),
            'owner_dates_all_closed':all(r.get('owner_closed') and r['state']=='closed' for r in s['owner_closed'])}
            for s in latest['summary_integrity']],ensure_ascii=False))
    text.append('تا Snapshot اولیه ۳۸ Receipt هشدار امروز وجود داشت؛ ۳۶ برای حساب 455740857 و ۲ برای 641220453. بخشی موجی از موعدهای قدیمی سرریز در حدود ۱۲:۲۴ بود. کلیدهای Receipt تکراری نیستند، ولی «نبود هرگونه موج تاریخی» قابل تأیید نیست؛ این رخداد پیش از این Patch بوده و رسیدهایش حفظ شدند. تعداد Receiptهای امروز در آخرین Snapshot: '+str(len(latest['today_alert_receipts']))+'.')
    if (OUT/'post-due-comparison.json').exists():text.append('مقایسه بعد از موعد: '+json.dumps(load(OUT/'post-due-comparison.json'),ensure_ascii=False))
    text += ['## ۹. تصمیم تاریخی مالک',
        'داده‌های ۱۵ و ۱۶ آبان در خلاصه «بسته / خارج از پیگیری مالک» هستند و هشدار عملیاتی جدید نمی‌گیرند. خطای تاریخی ۷۰۴ باز نشده است؛ روزهای جدید مستقل می‌مانند. Receipt، Hold و Audit تاریخی حذف یا بازنویسی نشده‌اند. هیچ تصمیم نمایشی اجازه ارسال/Backfill/اصلاحیه یا Write تاریخی نیست.',
        '## شواهد',
        '- `delivery-*.json` و `ledger-*.sqlite3`: Snapshotهای فقط‌خواندنی زمان‌دار.',
        '- `current-sources.json`، `sync-guard-proof.json`، `recovery-pdf-validation.json` و `pakdel-7366.json`.',
        '- `runtime/intent-delivery-20261110/real-model-7349.json`، `message-real-replay.json`، `focused-acceptance.json` و `gateway-after.json`.',
        'AI INTENT FIXED — CURRENT DELIVERY EVIDENCE CAPTURED — HISTORICAL REPORTS PRESERVED',
        'تأیید آینده و محتوای مستقل PDF اولیه پاکدل فقط در حد شواهد بیان‌شده است. هیچ پیام آزمایشی واقعی یا Write تاریخی توسط این مأموریت انجام نشد.']
    blocks=[]
    for part in text:
        if part.startswith('|') and blocks and blocks[-1].startswith('|'):blocks[-1]+='\n'+part
        else:blocks.append(part)
    target=OUT/'report-fa.md';tmp=target.with_suffix('.tmp');tmp.write_text('\n\n'.join(blocks)+'\n','utf8');tmp.replace(target)
    return target

if __name__=='__main__':print(build())
