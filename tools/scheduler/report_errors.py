"""Structured report errors with bounded, public diagnostics; never persist raw exceptions."""
import json
import re

REASONS = {
    'source_unavailable': 'فایل منبع قفل است یا قابل خواندن نیست',
    'source_missing': 'فایل منبع پیدا نشد',
    'source_locked': 'فایل منبع در حال ویرایش یا جایگزینی است و قفل شده است',
    'source_permission': 'مجوز خواندن فایل منبع در دسترس نیست',
    'source_changed': 'فایل منبع هنگام تولید گزارش تغییر کرده است',
    'source_invalid': 'ساختار یا داده‌های فایل منبع معتبر نیست',
    'pdf_layout': 'متن در قالب PDF جا نمی‌شود',
    'pdf_validation': 'کنترل کامل‌بودن یا خوانایی PDF ناموفق بود',
    'pdf_export': 'تبدیل فایل اکسل به PDF ناموفق بود',
    'report_generation': 'تولید گزارش با خطای داخلی مواجه شد',
    'report_configuration': 'تنظیمات یا وابستگی‌های تولید گزارش کامل نیست',
    'transport_unavailable': 'ارتباط با بله پیش از ارسال برقرار نشد',
    'TransportUnavailable': 'ارتباط با بله پیش از ارسال برقرار نشد',
    'TransportRejected': 'بله فایل ارسالی را نپذیرفت',
    'TransportUncertain': 'نتیجه ارسال از بله تأیید نشد',
    'PermissionError': 'فایل منبع قفل است یا مجوز خواندن آن در دسترس نیست',
    'FileNotFoundError': 'فایل منبع پیدا نشد',
    'OSError': 'خواندن فایل منبع ناموفق بود',
}
REPORT_FAILURES = frozenset(k for k in REASONS if k.islower()) - {'transport_unavailable'}


class ReportFailure(ValueError):
    def __init__(self, code, *, stage=None, sheet=None, row=None, column=None):
        if code not in REPORT_FAILURES:
            raise ValueError('Unknown report failure category')
        self.code = code
        self.details = {}
        if stage in {'source', 'layout', 'validation', 'export', 'generation', 'configuration'}:
            self.details['stage'] = stage
        if isinstance(sheet, str) and re.fullmatch(r'[\w .-]{1,80}', sheet):
            self.details['sheet'] = sheet
        for key, value in [('row', row), ('column', column)]:
            if type(value) is int and 0 < value <= 10000:
                self.details[key] = value
        super().__init__(code)

    def serialized(self):
        return json.dumps(self.details, ensure_ascii=False, sort_keys=True)


def classify(exc, stage='generation'):
    if isinstance(exc, ReportFailure):
        return exc
    if isinstance(exc, TimeoutError):
        return ReportFailure('pdf_export', stage='export')
    if isinstance(exc,FileNotFoundError):return ReportFailure('source_missing',stage='source')
    if isinstance(exc,PermissionError):
        code='source_locked' if getattr(exc,'winerror',None) in {32,33} else 'source_permission'
        return ReportFailure(code,stage='source')
    if isinstance(exc, (OSError, FileNotFoundError)):
        return ReportFailure('source_unavailable', stage='source')
    if isinstance(exc, ValueError):
        return ReportFailure('source_invalid' if stage == 'source' else 'report_configuration',
                             stage=stage, **getattr(exc,'report_location',{}))
    return ReportFailure('report_generation', stage=stage)


def public_reason(code, details=None):
    reason = REASONS.get(code)
    if not reason:
        return None
    try:
        details = json.loads(details) if isinstance(details, str) else (details or {})
        safe = ReportFailure('report_generation', **{
            k: v for k, v in details.items() if k in {'stage', 'sheet', 'row', 'column'}}).details
    except (ValueError, TypeError, AttributeError):
        safe = {}
    location = []
    if 'sheet' in safe: location.append('شیت ' + safe['sheet'])
    if 'row' in safe: location.append('ردیف ' + str(safe['row']))
    if 'column' in safe: location.append('ستون ' + str(safe['column']))
    return reason + ('؛ ' + '، '.join(location) if location else '')
