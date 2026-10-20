"""Snapshot-only, explicitly confirmed oil draft editing. No I/O or order creation."""
from tools.fleet.work_orders.channels.bale.proposal_form import (
    oil_edit_items, oil_edit_item, apply_oil_edits, render,
)
from tools.fleet.work_orders.types.oil_change.builder import normalize_interval, parse_action

STAGES = frozenset({'EDIT_CODE', 'EDIT_BATCH_CODES', 'EDIT_INTERVAL', 'EDIT_CONFIRM'})
BATCH_PROMPT = 'کد دستگاه‌ها را با فاصله یا ویرگول بنویسید؛ حداکثر ۱۰۰ دستگاه. نوبت پیشنهادی هر دستگاه پیش‌فرض است و پیش از تأیید قابل تغییر است.'


def reset(session):
    session.edit_machine_code = ''
    session.edit_interval = None
    session.edit_previous_interval = None
    session.edit_codes = []
    session.edit_intervals = {}
    session.edit_origin_stage = 'EDIT_CODE'
    session.edit_interval_return = 'EDIT_CODE'
    session.edit_confirm_return = 'EDIT_CODE'
    session.edit_confirm_page = 0
    session.oil_editor_version = 1


def interval_text(session):
    item = oil_edit_item(session.proposal, session.edit_machine_code)
    selected = any(i['machine_code'] == item['machine_code'] for i in session.proposal['items'])
    current = session.edit_intervals[item['machine_code']]
    return (f"دستگاه {item['machine_code']}؛ {'نوبت فعلی حکم' if selected else 'نوبت پیشنهادی برنامه'}: سرویس {parse_action(item['action_code'])[1]} ساعتی.\n"
            f'انتخاب موقت: سرویس {current} ساعتی.\n'
            'نوبت را حفظ کنید یا از ۲۰۰ تا ۲۰۰۰، با گام ۲۰۰، انتخاب کنید.\n'
            'فقط نوبت این حکم انتخاب می‌شود؛ ساعت‌کار واقعی و فایل برنامه‌ریزی تغییر نمی‌کند.'
            + ('' if selected else '\nاین دستگاه با تأیید ویرایش به حکم اضافه می‌شود.'))


def confirmation_text(session):
    selected = {i['machine_code'] for i in session.proposal['items']}
    lines = ['پیش‌نمایش افزودن/ویرایش؛ هنوز اعمال نشده است:']
    for code in session.edit_codes:
        item = oil_edit_item(session.proposal, code)
        old, new = parse_action(item['action_code'])[1], session.edit_intervals[code]
        if code not in selected:
            change = (f'افزودن با نوبت پیشنهادی {new}' if old == new else
                      f'افزودن با سرویس {new} به‌جای نوبت پیشنهادی {old}')
        elif old == new:
            change = f'از قبل در حکم؛ سرویس {old}، بدون تغییر'
        else:
            change = f'تغییر نوبت از {old} به {new}'
        lines.append(f'{code}: {change} ساعتی')
    if len(session.edit_codes) > 1:
        lines.append('برای تغییر اختیاری نوبت، دکمهٔ همان دستگاه را بزنید.')
    if any(code not in selected for code in session.edit_codes):
        lines.append('دستگاه جدید با تأیید ویرایش به حکم اضافه می‌شود.')
    lines.append('برای اعمال، «تأیید ویرایش» را بزنید.')
    return '\n'.join(lines)


def handle(session, text, picker_text):
    """Handle only oil editor stages; the outer handler authenticates each event."""
    if text == 'انصراف از ویرایش':
        reset(session)
        session.stage = 'PROPOSAL'
        return render(session.proposal)
    if text == 'برگشت':
        if session.stage == 'EDIT_CODE':
            reset(session)
            session.stage = 'PROPOSAL'
            return render(session.proposal)
        if session.stage == 'EDIT_BATCH_CODES':
            session.stage = 'EDIT_CODE'
            return picker_text(session)
        if session.stage == 'EDIT_INTERVAL':
            session.edit_interval = None
            session.stage = session.edit_interval_return
            if session.stage == 'EDIT_CONFIRM':
                session.edit_confirm_return = session.edit_origin_stage
        else:
            session.stage = session.edit_confirm_return
            if session.stage == 'EDIT_INTERVAL':
                # Undo the last unconfirmed interval choice on a step back.
                session.edit_intervals[session.edit_machine_code] = session.edit_previous_interval
                session.edit_interval = None
        if session.stage == 'EDIT_CODE':
            session.edit_codes, session.edit_intervals = [], {}
            return picker_text(session)
        if session.stage == 'EDIT_BATCH_CODES':
            session.edit_codes, session.edit_intervals = [], {}
            return BATCH_PROMPT
        return interval_text(session) if session.stage == 'EDIT_INTERVAL' else confirmation_text(session)
    if session.stage == 'EDIT_CODE' and text == 'افزودن چند دستگاه':
        session.stage = 'EDIT_BATCH_CODES'
        return BATCH_PROMPT
    if session.stage in {'EDIT_CODE', 'EDIT_BATCH_CODES'}:
        items = oil_edit_items(session.proposal, text)
        session.edit_origin_stage = session.stage
        session.edit_codes = [i['machine_code'] for i in items]
        session.edit_intervals = {i['machine_code']: parse_action(i['action_code'])[1] for i in items}
        session.edit_confirm_page = 0
        if len(items) > 1:
            session.edit_machine_code = ''
            session.edit_confirm_return = session.edit_origin_stage
            session.stage = 'EDIT_CONFIRM'
            return confirmation_text(session)
        session.edit_machine_code = items[0]['machine_code']
        session.edit_interval_return = session.edit_origin_stage
        session.edit_interval = None
        session.stage = 'EDIT_INTERVAL'
        return interval_text(session)
    if session.stage == 'EDIT_INTERVAL':
        interval = (session.edit_intervals[session.edit_machine_code] if text == 'حفظ نوبت'
                    else normalize_interval(text))
        session.edit_previous_interval = session.edit_intervals[session.edit_machine_code]
        session.edit_intervals[session.edit_machine_code] = interval
        session.edit_interval = interval
        session.edit_confirm_return = 'EDIT_INTERVAL'
        session.stage = 'EDIT_CONFIRM'
        return confirmation_text(session)
    if text in {'تایید ویرایش', 'تأیید ویرایش'}:
        apply_oil_edits(session.proposal, session.edit_codes, session.edit_intervals)
        reset(session)
        session.stage = 'PROPOSAL'
        return render(session.proposal)
    if text == 'تغییر نوبت' and len(session.edit_codes) == 1:
        code = session.edit_codes[0]
    else:
        item = oil_edit_item(session.proposal, text)
        code = item['machine_code']
        if code not in session.edit_codes:
            raise ValueError('دستگاه را از همین فهرست تغییرات انتخاب کنید.')
    session.edit_machine_code = code
    session.edit_interval = None
    session.edit_interval_return = 'EDIT_CONFIRM' if len(session.edit_codes) > 1 else session.edit_origin_stage
    session.stage = 'EDIT_INTERVAL'
    return interval_text(session)
