"""Work-order presentation and action names, separate from business execution."""
from tools.bale_ui import Action, InlineKeyboardBuilder, MultiSelect

_ROLES = frozenset({'MAINTENANCE_MANAGER'})


def action(name, label, stage='PROPOSAL'):
    return Action(name, label, 'work_order.manage', frozenset({stage}), _ROLES)


proposal_keyboard = InlineKeyboardBuilder('work_order', (
    (action('add', '➕ افزودن دستگاه'), action('remove', '❌ حذف دستگاه')),
    (action('confirm', '✅ تایید و ساخت اکسل'), action('cancel', 'انصراف')),
))

oil_proposal_keyboard = InlineKeyboardBuilder('work_order', (
    (action('add', '➕ افزودن دستگاه'), action('remove', '❌ حذف دستگاه')),
    (action('edit_excel', '✏️ ویرایش اکسل'),),
    (action('confirm', '✅ تایید و ساخت اکسل'), action('cancel', 'انصراف')),
))

oil_edit_code_keyboard = InlineKeyboardBuilder('work_order', ((
    action('edit_back', '↩️ بازگشت', 'EDIT_CODE'),
    action('cancel', 'انصراف', 'EDIT_CODE'),
),))

oil_interval_keyboard = InlineKeyboardBuilder('work_order', (
    *[(action(f'interval_{n}', str(n), 'EDIT_INTERVAL'),
       action(f'interval_{n+200}', str(n+200), 'EDIT_INTERVAL')) for n in range(200, 2000, 400)],
    (action('edit_back', '↩️ بازگشت', 'EDIT_INTERVAL'),
     action('cancel', 'انصراف', 'EDIT_INTERVAL')),
))

oil_edit_confirm_keyboard = InlineKeyboardBuilder('work_order', (
    (action('edit_apply', '✅ تأیید ویرایش', 'EDIT_CONFIRM'),
     action('edit_change', '✏️ تغییر نوبت', 'EDIT_CONFIRM')),
    (action('edit_back', '↩️ بازگشت', 'EDIT_CONFIRM'),
     action('cancel', 'انصراف', 'EDIT_CONFIRM')),
))

COMMANDS = {'add': 'اضافه', 'remove': 'حذف', 'confirm': 'تایید', 'cancel': 'انصراف',
            'edit_excel': 'ویرایش اکسل', 'edit_back': 'برگشت',
            'edit_apply': 'تایید ویرایش', 'edit_change': 'تغییر نوبت'}

MENU_TYPES = {'oil': 'OIL_CHANGE', 'greasing': 'GREASING', 'air_filter': 'AIR_FILTER'}
SHIFT_COMMANDS = {'shift_morning': 'صبح', 'shift_noon': 'ظهر',
                  'shift_morning_noon': 'صبح-ظهر', 'shift_evening': 'عصر',
                  'shift_night': 'شب', 'shift_back': 'برگشت'}
SHIFT_PROMPT = 'شیفت را از دکمه‌های زیر انتخاب کنید. پس از انتخاب، حکم ساخته می‌شود.'
shift_keyboard = InlineKeyboardBuilder('work_order', (
    (action('shift_morning', 'صبح', 'SHIFT'), action('shift_noon', 'ظهر', 'SHIFT')),
    (action('shift_morning_noon', 'صبح-ظهر', 'SHIFT'), action('shift_evening', 'عصر', 'SHIFT')),
    (action('shift_night', 'شب', 'SHIFT'), action('shift_back', 'بازگشت', 'SHIFT')),
))


def entry_keyboard():
    from tools.fleet.work_orders.core.registry import list_work_order_types
    enabled = {item['key'] for item in list_work_order_types(enabled_only=True)}
    buttons = [action(name, label, 'MENU') for name, label in (
        ('oil', 'تعویض روغن'), ('greasing', 'گریس کاری'), ('air_filter', 'هواکش'))
        if MENU_TYPES[name] in enabled]
    buttons.append(action('cancel', 'انصراف', 'MENU'))
    return InlineKeyboardBuilder('work_order', (buttons[:2], buttons[2:]))


review_keyboard = InlineKeyboardBuilder('work_order', ((
    action('review_confirm', '✅ تایید بررسی فایل', 'REVIEW'),
    action('review_edit', '✏️ ویرایش', 'REVIEW'),
),))


def staff_keyboard(options):
    rows = [(action(f'staff_{index}', staff['display_name'], 'STAFF'),)
            for index, staff in enumerate(options, 1)]
    rows.append((action('staff_back', '↩️ بازگشت', 'STAFF'),))
    return InlineKeyboardBuilder('work_order', rows)


def keyboard_for(session, *, review=False):
    if session.work_order_type == 'OIL_CHANGE':
        if session.stage == 'EDIT_CODE':
            return oil_edit_code_keyboard
        if session.stage == 'EDIT_INTERVAL':
            return oil_interval_keyboard
        if session.stage == 'EDIT_CONFIRM':
            return oil_edit_confirm_keyboard
    if session.stage == 'SHIFT':
        return shift_keyboard
    if session.stage == 'REMOVE' and session.removal_selection is not None:
        return MultiSelect(**session.removal_selection).keyboard('work_order',
            permission='work_order.manage', stage='REMOVE', roles=_ROLES,
            confirm_label='✅ تأیید حذف', back_label='↩️ بازگشت', selected_mark='🔴')
    if review and session.stage == 'REVIEW':
        return review_keyboard
    if session.stage == 'STAFF':
        return staff_keyboard(session.staff_options)
    if session.stage == 'MENU':
        return entry_keyboard()
    if session.stage == 'PROPOSAL':
        return oil_proposal_keyboard if session.work_order_type == 'OIL_CHANGE' else proposal_keyboard
    return None


def command_for(action_name, *, order_no=''):
    if action_name.startswith('interval_') and action_name[9:].isdecimal():
        return action_name[9:]
    if action_name in SHIFT_COMMANDS:
        return SHIFT_COMMANDS[action_name]
    if action_name == 'select_confirm':
        return 'تایید حذف'
    if action_name == 'select_back':
        return 'برگشت'
    if action_name == 'staff_back':
        return 'برگشت'
    if action_name.startswith('staff_') and action_name[6:].isdecimal():
        return action_name[6:]
    if action_name in MENU_TYPES:
        from tools.fleet.work_orders.core.registry import list_work_order_types
        for index, item in enumerate(list_work_order_types(enabled_only=False), 1):
            if item['key'] == MENU_TYPES[action_name] and item['enabled']:
                return str(index)
        raise ValueError('این نوع حکم در دسترس نیست.')
    if action_name in {'review_confirm', 'review_edit'}:
        if not order_no:
            raise ValueError('شماره حکم موجود نیست.')
        return ('ثبت تایید ' if action_name == 'review_confirm' else 'ویرایش ') + order_no
    return COMMANDS[action_name]
