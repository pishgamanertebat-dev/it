"""Own-order views and confirmed draft cancellation on the existing authority."""
from pathlib import Path
from .db import connect_db
from .oil_safety import RELIABLE_TYPES, audit, current
from . import operational
from .permissions import require_work_order_permission


def own_orders(actor, *, date=None):
    require_work_order_permission(actor, capabilities=('work_orders.read_own',))
    date = date or operational.operational_today()
    con = connect_db()
    try:
        rows = con.execute('''SELECT w.*,d.state AS api_state,d.recipient_id,d.attempt_id
            FROM service_work_orders w LEFT JOIN service_work_order_dispatch d USING(work_order_no)
            WHERE w.created_by=? AND w.jalali_date=?
            AND w.work_order_type IN ('OIL_CHANGE','GREASING','AIR_FILTER') ORDER BY w.id DESC''', (f'bale:{actor}', date)).fetchall()
        result = []
        for row in rows:
            order = dict(row)
            order['machines'] = [r[0] for r in con.execute('SELECT machine_code FROM service_work_order_items WHERE work_order_id=? ORDER BY item_no', (row['id'],))]
            order['operational'] = date == operational.operational_today() and not row['superseded_by'] and row['status'] not in {'CANCELLED','VOID'}
            result.append(order)
        return result
    finally:
        con.close()


def status_text(order):
    kind = {'OIL_CHANGE':'OC', 'GREASING':'GR', 'AIR_FILTER':'AF'}[order['work_order_type']]
    api = order.get('api_state') or ('SENT' if order['status'] == 'SENT' else 'شروع نشده')
    ack = order['acknowledged_at'] or 'تأیید دریافت ثبت نشده'
    machines = order.get('machines') or [i['machine_code'] for i in order.get('items', [])]
    return (f"{order['work_order_no']} | {kind} | {', '.join(machines)}\n"
            f"وضعیت: {order['status']}" + (f" | نسخهٔ تازه: {order['superseded_by']}" if order.get('superseded_by') else '') +
            f"\nصادرکننده: {order['created_by']}\nانتقال API: {api}\nACK سرویسکار: {ack}\nانجام واقعی سرویس: از این وضعیت‌ها قابل استنتاج نیست.")


def cancel_draft(number, actor, reason):
    reason = str(reason or '').strip()
    if not reason:
        raise ValueError('علت لغو پیش‌نویس الزامی است.')
    require_work_order_permission(actor, capabilities=('work_orders.read_own', 'work_orders.create'))
    con = connect_db()
    try:
        with con:
            con.execute('BEGIN IMMEDIATE')
            row = current(con, number)
            if row['created_by'] != f'bale:{actor}' or row['work_order_type'] not in RELIABLE_TYPES:
                raise ValueError('لغو فقط برای پیش‌نویس متعلق به خودتان مجاز است.')
            dispatch = con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?', (number,)).fetchone()
            attempts = con.execute('SELECT 1 FROM service_work_order_delivery_attempts WHERE work_order_no=?', (number,)).fetchone()
            if row['status'] != 'FILE_READY' or dispatch or attempts:
                raise ValueError('فقط پیش‌نویس ارسال‌نشدهٔ FILE_READY قابل لغو است.')
            require_work_order_permission(actor, capabilities=('work_orders.read_own', 'work_orders.create'))
            con.execute("UPDATE service_work_orders SET status='CANCELLED',updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?", (number,))
            audit(con, number, 'DRAFT_CANCELLED', actor=str(actor), reason=reason)
    finally:
        con.close()


def historical_document(number, actor):
    """Read verified bytes only. Never regenerate or register a preview."""
    require_work_order_permission(actor, capabilities=('work_orders.read_own',))
    from .service import get_work_order
    from .review import approved_pdf_bytes, last_record, DocumentReviewRequired
    row = get_work_order(number)
    if row['created_by'] != f'bale:{actor}' or row['work_order_type'] not in RELIABLE_TYPES:
        raise ValueError('این حکم متعلق به حساب شما نیست.')
    if not row['pdf_path']:
        raise ValueError('PDF تاریخی ثبت نشده است؛ بازسازی خودکار مجاز نیست.')
    if row['status'] == 'SENT' and not last_record(row, 'MANAGER_DOCUMENT_REVIEW'):
        raise ValueError('هویت بایت‌های ارسال‌شده در سابقه تأیید نشده است؛ بررسی ممیزی لازم است.')
    try:
        data = approved_pdf_bytes(row) if row['status'] == 'SENT' else Path(row['pdf_path']).read_bytes()
    except DocumentReviewRequired as exc:
        raise ValueError('فایل تاریخی با Hash تأییدشده یکسان نیست؛ بررسی ممیزی لازم است. فایل بازسازی نشد.') from exc
    if data is None or not data.startswith(b'%PDF-'):
        raise ValueError('PDF تأییدشده برای این سابقه موجود نیست.')
    return row, data
