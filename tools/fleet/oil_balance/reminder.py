"""Source readiness before delivery; receipt-backed delivery checks afterwards."""
from datetime import timedelta

from tools.scheduler.report_errors import ReportFailure, public_reason
from .report import load


def probe(target):
    report = load(target)
    return {'status': 'ready' if report['rows'] else 'date_missing',
            'latest_date': max(report['available_dates'], default=None)}


def text(target, result, *, after_due, deadline, uncertain=False):
    title = '⏰ یادآوری تکمیل موازنه روغن' if not after_due else '⚠️ موازنه روغن هنوز ارسال نشده است'
    if after_due and uncertain:
        title = '⚠️ وضعیت ارسال موازنه روغن تأیید نشده است'
    if result['status'] == 'delivery_pending':
        reason = 'فایل آماده است، اما رسید ارسال گزارش به آقای امامی هنوز ثبت نشده است.'
    elif result['status'] == 'date_missing':
        reason = 'ردیف تعویض روغن برای این تاریخ در جدول «موازنه روغن مصرفی و وارده (تعویض ها)» ثبت نشده است.'
    else:
        reason = 'فایل قابل استفاده نیست؛ ' + public_reason(result['error'])
    message = f'{title}\nتاریخ گزارش روز قبل: {target}\n{reason}\n'
    if not after_due:
        message += f'لطفاً فایل را برای ارسال ساعت {deadline} به آقای امامی تکمیل کنید.'
    else:
        message += ('ارسال گزارش به آقای امامی تأیید نشده است.' if uncertain else 'گزارش به آقای امامی ارسال نشده است.')
        if uncertain:
            message += '\nنتیجه نامطمئن ارسال نیاز به بررسی دارد و ارسال مجدد خودکار متوقف است.'
        elif result['status'] == 'delivery_pending':
            message += '\nگزارش در صف پیگیری ارسال خودکار است؛ وضعیت ارسال را بررسی کنید.'
        else:
            message += '\nلطفاً فایل را تکمیل یا اصلاح کنید تا ارسال خودکار انجام شود.'
    if result.get('latest_date'):
        message += '\nآخرین تاریخ دارای تعویض ثبت‌شده: ' + result['latest_date']
    return message


def run(conn, job, now, store, *, checker=None, sender_factory=None):
    from tools.scheduler.completion_reminder import reminder_recipients, _pending, _claim, _send
    from tools.scheduler.delivery_alert import _default_sender
    from tools.scheduler.misfire import due_storage_key
    from tools.scheduler.tasks import overflow_report_date
    target = overflow_report_date(now)
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    due = job['trigger'].get_next_fire_time(None, start)
    if due is None or due.date() != now.date():
        return
    after_due = now >= due
    slot = due_storage_key(now.replace(minute=0, second=0, microsecond=0))
    pending = [u for u in reminder_recipients(job, store) if _pending(conn, (job['id'], slot, u), now)]
    if not pending:
        return
    # A matching real receipt is authoritative even if the workbook was later edited.
    from tools.authorization.oil_balance import REPORT_USER
    # The scheduler preserves the original due on a clock change. Look up the
    # Tehran reporting day so an earlier 12:00 receipt also closes the 15:00 job.
    receipts = conn.execute("""SELECT status,message_id FROM receipts WHERE schedule_id=?
        AND due>=? AND due<? AND recipient_id=? AND delivery_kind='report'""",
        (job['id'], due_storage_key(start), due_storage_key(start+timedelta(days=1)), REPORT_USER)).fetchall()
    delivered = any(r['status'] == 'sent' and r['message_id'] for r in receipts)
    uncertain = any(r['status'] in {'uncertain', 'sending'} or r['status'] == 'sent' and not r['message_id'] for r in receipts)
    if delivered:
        result = {'status': 'ready'}
    else:
        try:
            result = (checker or probe)(target)
        except ReportFailure as exc:
            result = {'status': 'invalid_source', 'error': exc.code}
    if result['status'] == 'ready' and (delivered or not after_due):
        for user in pending:
            _claim(conn, (job['id'], slot, user), target, now, 'not_needed')
        return
    if result['status'] == 'ready':
        result = dict(result, status='delivery_pending')
    if result['status'] not in {'date_missing', 'invalid_source', 'delivery_pending'}:
        raise ValueError('Unexpected oil balance source state')
    deadline = due.strftime('%H:%M').translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
    message = text(target, result, after_due=after_due, deadline=deadline, uncertain=uncertain)
    for user in pending:
        _send(conn, (job['id'], slot, user), target, message, now, store,
              sender_factory or _default_sender, job=job)
