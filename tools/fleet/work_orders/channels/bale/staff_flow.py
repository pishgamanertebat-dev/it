"""Bale transport for dispatch and receipt, independent of manager forms."""
import asyncio
import logging
import re
import time
from pathlib import Path

from tools.fleet.work_orders.core import staff_dispatch as core
from tools.fleet.work_orders.core.delivery import send_work_order
from tools.fleet.work_orders.core.permissions import normalize_bale_id
from tools.fleet.work_orders.core.registry import get_work_order_spec
from tools.fleet.work_orders.core.conversation import review_context

logger = logging.getLogger(__name__)
tasks = set()
busy = set()
receipt_choices = {}


def bot_for(gateway):
    return next(a._bot for p, a in gateway.adapters.items() if str(getattr(p, 'value', p)).lower() == 'bale')


def order_label(order):
    return get_work_order_spec(order['work_order_type']).label_fa


async def dispatch(gateway, number, actor, chat, staff_id, roster_id=None):
    order = await asyncio.to_thread(core.prepare_dispatch, number, actor, chat, staff_id, roster_id)
    if not await asyncio.to_thread(core.claim_send, number):
        return 'این حکم قبلاً ارسال شده یا ارسال آن در حال بررسی است؛ دوباره ارسال نشد.'
    loop = asyncio.get_running_loop()
    bot = bot_for(gateway)

    async def upload(chat_id, file_path, file_name):
        caption = f"حکم کار {order_label(order)} تاریخ {order['jalali_date']} برای شما ارسال شد.\nتایید می‌کنید؟ بنویسید: تایید\nشماره حکم: {number}"
        with Path(file_path).open('rb') as document:
            return await bot.send_document(chat_id=chat_id, document=document, filename=file_name, caption=caption)

    class Sender:
        def send_document(self, **kwargs):
            return asyncio.run_coroutine_threadsafe(upload(**kwargs), loop).result()

    try:
        await asyncio.to_thread(send_work_order, work_order_no=number, sender=Sender())
        await asyncio.to_thread(core.finish_send, number, 'SENT')
    except Exception:
        await asyncio.to_thread(core.finish_send, number, 'FAILED')
        logger.exception('Staff document delivery failed')
        return 'ارسال با خطا مواجه شد؛ حکم محفوظ است. اتصال یا شروع گفتگو با ربات توسط سرویسکار را بررسی کنید؛ برای تلاش دوباره همان شمارهٔ گزینه را بفرستید.'
    return f"✅ حکم کار {order_label(order)} تاریخ {order['jalali_date']} برای {order['staff_name']} ارسال شد؛ منتظر تایید دریافت هستیم."


async def receipt(gateway, actor, chat, number):
    try:
        order = await asyncio.to_thread(core.acknowledge, number, actor)
        bot = bot_for(gateway)
        await bot.send_message(chat_id=chat, text=f"✅ حکم {order_label(order)} تاریخ {order['jalali_date']} توسط شما تایید شد.")
        if not order['notified_at']:
            staff_name = core.staff_display_name({'bale_id':actor,'display_name':order['display_name']},order['work_order_type'])
            await bot.send_message(chat_id=order['manager_chat_id'], text=f"✅ {staff_name} دریافت حکم {order_label(order)} تاریخ {order['jalali_date']} را تایید کرد.\nشماره حکم: {number}")
            await asyncio.to_thread(core.mark_notified, number)
        review_context(actor, chat, 'staff', number='', stage='DONE')
    except Exception:
        logger.exception('Staff acknowledgement or manager notification failed')
        try:
            await bot_for(gateway).send_message(chat_id=chat, text='اطلاع‌رسانی کامل نشد؛ لطفاً دوباره «تایید» را بفرستید.')
        except Exception:
            logger.exception('Receipt retry prompt failed')
    finally:
        busy.discard(actor)


async def show_order(gateway, actor, chat, number):
    try:
        orders = await asyncio.to_thread(core.recipient_orders, actor)
        order = next(o for o in orders if o['work_order_no'] == number and not o['acknowledged_at'])
        with Path(order['pdf_path']).open('rb') as document:
            await bot_for(gateway).send_document(
                chat_id=chat, document=document, filename=Path(order['pdf_path']).name,
                caption=f"حکم کار {order_label(order)} — {order['jalali_date']}\nشماره حکم: {number}\nتایید می‌کنید؟ بنویسید: تایید")
        review_context(actor, chat, 'staff', number=number, stage='REVIEW')
    except Exception:
        logger.exception('Could not redisplay staff order')
        await bot_for(gateway).send_message(chat_id=chat, text='ارسال فایل ناموفق بود؛ برای تلاش دوباره «حکم کار» را بفرستید.')
    finally:
        busy.discard(actor)


def schedule_preview(gateway, actor, chat, number):
    review_context(actor, chat, 'staff', number=number, stage='RESULT')
    busy.add(actor)
    task = asyncio.get_running_loop().create_task(show_order(gateway, actor, chat, number))
    tasks.add(task)
    task.add_done_callback(tasks.discard)
    return {'action': 'skip', 'reason': 'staff-order-preview'}


def handle_staff_receipt(event, gateway, *, send):
    source = event.source
    if str(getattr(source.platform, 'value', source.platform)).lower() != 'bale' or source.chat_type != 'dm':
        return None
    actor = normalize_bale_id(getattr(source, 'user_id', None))
    # Plain "confirm" and numeric machine codes belong to the active defect
    # form, not to an older work-order receipt menu. Registration still gates
    # the normal dispatch path after this handler returns None.
    from tools.fleet.repairs.entry_bale import _handler as repairs_entry
    from tools.fleet.repairs.maintenance_bale import _handler as maintenance_entry
    if (event.text or '').strip() != 'حکم کار' and (repairs_entry.active_for(actor, source.chat_id)
                                                               or maintenance_entry.active_for(actor, source.chat_id)):
        return None
    text = ' '.join((event.text or '').translate(str.maketrans('كي', 'کی')).replace('\u200c', ' ').split())
    text = text.translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩','01234567890123456789'))
    match = re.fullmatch(r'(?:تایید|تأیید)(?: ((?:AF|GR|OC)-1405-\d{2}-\d{2}-\d+))?', text)
    choice_key = (actor, str(source.chat_id))
    choices = receipt_choices.get(choice_key)
    numeric_choice = text.isdecimal() and choices is not None
    is_entry = text == 'حکم کار'
    if not actor or (not match and not numeric_choice and not is_entry):
        return None
    if is_entry:
        from tools.fleet.work_orders.core.permissions import check_work_order_permission
        if check_work_order_permission(actor).allowed or not core.is_recipient(actor):
            return None
    if actor in busy:
        return {'action':'skip','reason':'staff-receipt-busy'}
    orders = core.recipient_orders(actor)
    if not orders and not is_entry:
        return None
    pending = [o for o in orders if not o['acknowledged_at']]
    if is_entry:
        receipt_choices.pop(choice_key, None)
        review_context(actor, source.chat_id, 'staff', number='', stage='MENU')
        if not pending:
            send(gateway, source.chat_id, 'شما حکم کار تایید نشده ندارید.')
            return {'action': 'skip', 'reason': 'staff-orders-empty'}
        if len(pending) == 1:
            return schedule_preview(gateway, actor, source.chat_id, pending[0]['work_order_no'])
        receipt_choices[choice_key] = {'numbers': [o['work_order_no'] for o in pending], 'expires': time.monotonic()+600, 'preview': True}
        send(gateway, source.chat_id, 'حکم‌های جاری در انتظار تایید؛ برای دریافت فایل شمارهٔ گزینه را بفرستید:\n\n' + '\n\n'.join(
            f"{i}) {order_label(o)} — {o['jalali_date']}\n{o['work_order_no']}" for i, o in enumerate(pending, 1)))
        return {'action': 'skip', 'reason': 'staff-orders-list'}
    number = match[1] if match else None
    if match and not number:
        saved = review_context(actor, source.chat_id, 'staff')
        if saved and saved[0]:
            if saved[1] != 'REVIEW':
                send(gateway, source.chat_id, 'ابتدا با «حکم کار» فایل را دریافت و بررسی کنید.')
                return {'action': 'skip', 'reason': 'staff-order-needs-preview'}
            number = saved[0]
    if numeric_choice:
        if choices['expires'] < time.monotonic():
            receipt_choices.pop(choice_key, None)
            send(gateway, source.chat_id, 'مهلت انتخاب تمام شد؛ برای نمایش دوبارهٔ فهرست، «تایید» را بفرستید.')
            return {'action':'skip','reason':'staff-receipt-expired'}
        if len(text) > 6 or not 1 <= int(text) <= len(choices['numbers']):
            send(gateway, source.chat_id, 'شمارهٔ یکی از حکم‌های همین فهرست را بفرستید.')
            return {'action':'skip','reason':'staff-receipt-invalid-choice'}
        number = choices['numbers'][int(text)-1]
        if choices.get('preview'):
            if not any(o['work_order_no'] == number for o in pending):
                send(gateway, source.chat_id, 'این حکم دیگر منتظر تایید نیست؛ برای فهرست جدید «حکم کار» را بفرستید.')
                return {'action': 'skip', 'reason': 'staff-order-stale'}
            return schedule_preview(gateway, actor, source.chat_id, number)
    if number:
        if not any(o['work_order_no'] == number for o in orders):
            send(gateway, source.chat_id, 'این حکم برای شما ارسال نشده است.')
            return {'action': 'skip', 'reason': 'staff-receipt-denied'}
    elif len(pending) > 1:
        receipt_choices[choice_key] = {'numbers':[o['work_order_no'] for o in pending], 'expires':time.monotonic()+600}
        send(gateway, source.chat_id, 'چند حکم در انتظار تایید است؛ فقط شمارهٔ گزینه را بفرستید، مثلاً 1 یا 2:\n\n' + '\n\n'.join(f"{i}) حکم {order_label(o)} — {o['jalali_date']}\n{o['work_order_no']}" for i,o in enumerate(pending,1)))
        return {'action': 'skip', 'reason': 'staff-receipt-select'}
    else:
        number = (pending or [o for o in orders if not o['notified_at']] or orders)[0]['work_order_no']
    if actor not in busy:
        busy.add(actor)
        task = asyncio.get_running_loop().create_task(receipt(gateway, actor, source.chat_id, number))
        tasks.add(task)
        task.add_done_callback(tasks.discard)
    return {'action': 'skip', 'reason': 'staff-receipt'}
