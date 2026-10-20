"""Snapshot-only, individually confirmed oil editing through inline buttons."""
from tools.fleet.work_orders.channels.bale.proposal_form import (
    oil_edit_item, apply_oil_edits, render,
)
from tools.fleet.work_orders.types.oil_change.builder import normalize_interval, parse_action

STAGES = frozenset({'EDIT_CODE', 'EDIT_INTERVAL', 'EDIT_CONFIRM'})


def reset(session):
    """Discard only the pending device; keep the confirmed draft and list page."""
    session.edit_machine_code = ''
    session.edit_interval = None
    session.edit_previous_interval = None
    session.edit_codes = []
    session.edit_intervals = {}
    session.oil_editor_version = 2


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
    item = oil_edit_item(session.proposal, session.edit_machine_code)
    old = parse_action(item['action_code'])[1]
    new = session.edit_intervals[item['machine_code']]
    selected = any(i['machine_code'] == item['machine_code'] for i in session.proposal['items'])
    if not selected:
        change = (f'افزودن با نوبت پیشنهادی {new}' if old == new else
                  f'افزودن با سرویس {new} به‌جای نوبت پیشنهادی {old}')
    elif old == new:
        change = f'از قبل در حکم؛ سرویس {old}، بدون تغییر'
    else:
        change = f'تغییر نوبت از {old} به {new}'
    return (f"پیش‌نمایش؛ هنوز اعمال نشده است:\n{item['machine_code']}: {change} ساعتی\n"
            + ('' if selected else 'دستگاه جدید با تأیید ویرایش به حکم اضافه می‌شود.\n')
            + 'برای اعمال، «تأیید ویرایش» را بزنید.')


def handle(session, text, picker_text, *, inline=False):
    """The outer handler authenticates callbacks; typed codes cannot select units."""
    if session.stage == 'EDIT_CODE' and text in {'افزودن/ویرایش', 'ویرایش اکسل', 'اضافه'}:
        # Reopen a migrated draft without discarding its confirmed selections.
        return picker_text(session)
    if text == 'پایان و بازگشت به پیشنهاد' and inline and session.stage == 'EDIT_CODE':
        reset(session)
        session.stage = 'PROPOSAL'
        return render(session.proposal)
    if text == 'برگشت':
        if session.stage == 'EDIT_CONFIRM':
            session.edit_intervals[session.edit_machine_code] = session.edit_previous_interval
            session.edit_interval = None
            session.stage = 'EDIT_INTERVAL'
            return interval_text(session)
        reset(session)
        session.stage = 'EDIT_CODE'
        return picker_text(session)
    if session.stage == 'EDIT_CODE':
        if not inline:
            raise ValueError('دستگاه را فقط از دکمه‌ها انتخاب کنید.')
        item = oil_edit_item(session.proposal, text)
        session.edit_machine_code = item['machine_code']
        session.edit_codes = [item['machine_code']]
        session.edit_intervals = {item['machine_code']: parse_action(item['action_code'])[1]}
        session.edit_interval = None
        session.stage = 'EDIT_INTERVAL'
        return interval_text(session)
    if session.stage == 'EDIT_INTERVAL':
        interval = (session.edit_intervals[session.edit_machine_code] if text == 'حفظ نوبت'
                    else normalize_interval(text))
        session.edit_previous_interval = session.edit_intervals[session.edit_machine_code]
        session.edit_intervals[session.edit_machine_code] = interval
        session.edit_interval = interval
        session.stage = 'EDIT_CONFIRM'
        return confirmation_text(session)
    if text in {'تایید ویرایش', 'تأیید ویرایش'} and inline:
        apply_oil_edits(session.proposal, session.edit_codes, session.edit_intervals)
        reset(session)
        session.stage = 'EDIT_CODE'
        return picker_text(session)
    raise ValueError('از دکمه‌های همین مرحله انتخاب کنید.')
