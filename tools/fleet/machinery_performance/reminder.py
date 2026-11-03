"""Completion reminders only; each day's 14:00 and 17:00 slots are independent."""
from tools.scheduler.report_errors import ReportFailure, public_reason
from .report import load


def probe(target):
    report = load(target)
    return dict(status='ready' if report['rows'] and not report['missing_codes'] else 'date_missing',
                missing_codes=report['missing_codes'])


def run(conn, job, now, store, *, checker=None, sender_factory=None):
    from tools.scheduler.completion_reminder import reminder_recipients, _pending, _claim, _send
    from tools.scheduler.delivery_alert import _default_sender
    from tools.scheduler.misfire import due_storage_key
    from tools.scheduler.tasks import overflow_report_date
    start = now.replace(hour=0, minute=0, second=0, microsecond=0)
    due = job['trigger'].get_next_fire_time(None, start)
    if due is None or due.date() != now.date():
        return
    target = overflow_report_date(due)
    slot = due_storage_key(now.replace(minute=0, second=0, microsecond=0))
    users = [u for u in reminder_recipients(job, store) if _pending(conn, (job['id'], slot, u), now)]
    if not users:
        return
    try:
        result = (checker or probe)(target)
    except ReportFailure as exc:
        # Do not accuse the owner of missing entries when the source is locked.
        result = dict(status='unavailable', error=exc.code)
    if result['status'] == 'ready':
        for user in users:
            _claim(conn, (job['id'],slot,user),target,now,'not_needed')
        return
    if result['status'] == 'date_missing':
        text = (f'⏰ یادآوری تکمیل عملکرد ماشین آلات\nتاریخ گزارش: {target}\n'
                'اطلاعات این روز برای همه دستگاه‌ها هنوز در فایل عملکرد ماشین آلات تکمیل نشده است.\n'
                'لطفاً فایل را به‌روز کنید؛ پس از تکمیل، گزارش خودکار ارسال می‌شود.')
    elif result['status'] == 'unavailable':
        text = f'⚠️ بررسی فایل عملکرد ماشین آلات\nتاریخ گزارش: {target}\n' + public_reason(result['error'])
    else:
        raise ValueError('Unexpected performance readiness state')
    for user in users:
        _send(conn,(job['id'],slot,user),target,text,now,store,sender_factory or _default_sender,job=job)
