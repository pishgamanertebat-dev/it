"""Missing exact-day notices are independent of actual report delivery."""
from tools.fleet.overflow.report import validate_date


def notice_text(title, target, latest=None):
    target = validate_date(target)
    latest = validate_date(latest) if latest else None
    text = f'{title} مربوط به تاریخ {target} ارسال نشد، زیرا اطلاعات این تاریخ هنوز ثبت یا به‌روز نشده است.'
    if latest:
        text += f' آخرین اطلاعات معتبر موجود مربوط به تاریخ {latest} است.'
    else:
        text += ' تاریخ معتبر دیگری در اطلاعات موجود پیدا نشد.'
    return text + ' پس از ثبت اطلاعات تاریخ موردنظر، گزارش در بازه پیگیری هفت‌روزه به‌صورت خودکار ارسال خواهد شد.'


def notify_missing(title, target, latest, recipients, *, on_receipt=None, closed=None,
                   reason='date_missing'):
    """Re-resolve recipients; an uncertain notice never blocks a later actual report."""
    from .tasks import BaleSender, TransportUnavailable, closed_sets
    closed = closed or {}
    report_sent, report_uncertain = closed_sets(closed)
    notice_sent, notice_uncertain = closed_sets(closed.get('notice'))
    initial = tuple(dict.fromkeys(recipients()))
    if initial and set(initial) <= report_sent:
        return {'status': 'succeeded'}
    sender = None
    failed, uncertain, sent = [], [], []
    try:
        for user in initial:
            if user in report_sent or user in report_uncertain or user in notice_sent:
                continue
            if user in notice_uncertain:
                uncertain.append(user)
                continue
            if user not in recipients():
                continue
            def receipt(status, message_id=None):
                if on_receipt:
                    on_receipt(user, status, message_id, 'notice')
            attempted = False
            try:
                if sender is None:
                    sender = BaleSender()
                sender.check_connection()
                if user not in recipients():
                    continue
                receipt('sending')
                attempted = True
                message_id = sender.message(user, notice_text(title, target, latest))
            except TransportUnavailable:
                receipt('failed')
                failed.append(user)
            except Exception:
                receipt('uncertain' if attempted else 'failed')
                (uncertain if attempted else failed).append(user)
            else:
                receipt('sent', message_id)
                sent.append(user)
    finally:
        if sender is not None:
            sender.close()
    return {'status': 'waiting_for_data', 'reason': reason, 'latest_date': latest,
            'notice_delivery_state': 'retry_wait' if failed else ('uncertain' if uncertain else 'sent'),
            'notice_sent_count': len(sent)}


def current_recipients(task, store):
    """Use existing authorization resolvers; no role or capability changes."""
    from .tasks import MULTI_RECIPIENT_TASKS
    from tools.authorization import DRIVER_RECEIVE, DRIVER_REPORT_READ, OVERFLOW_READ
    if task in {'overflow', 'driver_daily'}:
        from tools.authorization.office_delivery import OFFICE_TASKS
        result = store.resolve_active_recipients(OFFICE_TASKS[task])
        return tuple(result.recipients) if result.status == 'ready' else ()
    result = store.resolve_active_recipients(MULTI_RECIPIENT_TASKS[task])
    users = result.recipients if result.status == 'ready' else ()
    if task in {'mechanical_driver_daily', 'mechanical_overflow'}:
        capability = DRIVER_REPORT_READ if task == 'mechanical_driver_daily' else OVERFLOW_READ
        users = tuple(u for u in users if store.has_capability(u, capability))
    return tuple(users)
