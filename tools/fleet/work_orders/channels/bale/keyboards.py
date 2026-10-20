"""Work-order presentation and action names, separate from business execution."""
from tools.bale_ui import Action, InlineKeyboardBuilder, MultiSelect

_ROLES = frozenset({'MAINTENANCE_MANAGER'})


def action(name, label, stage='PROPOSAL', *, refresh=False):
    return Action(name, label, 'work_order.manage', frozenset({stage}), _ROLES, refresh=refresh)


proposal_keyboard = InlineKeyboardBuilder('work_order', (
    (action('add', '➕ افزودن دستگاه'), action('remove', '❌ حذف دستگاه')),
    (action('confirm', '✅ تایید و ساخت اکسل'), action('cancel', 'انصراف')),
))

oil_proposal_keyboard = InlineKeyboardBuilder('work_order', (
    (action('edit_excel', '➕ افزودن/ویرایش'), action('remove', '❌ حذف دستگاه')),
    (action('confirm', '✅ تایید و ساخت اکسل'), action('cancel', 'انصراف')),
))

OIL_EDIT_PAGE_SIZE = 16


def oil_edit_code_keyboard(session):
    from tools.fleet.work_orders.channels.bale.proposal_form import oil_edit_candidates
    from tools.fleet.work_orders.types.oil_change.builder import parse_action
    candidates = oil_edit_candidates(session.proposal)
    selected = {item['machine_code'] for item in session.proposal['items']}
    start = session.edit_code_page * OIL_EDIT_PAGE_SIZE
    buttons = [action(f'edit_unit_{index}',
                      f"{'✅' if item['machine_code'] in selected else '➕'} {item['machine_code']}-{parse_action(item['action_code'])[1]}",
                      'EDIT_CODE')
               for index, item in enumerate(candidates[start:start + OIL_EDIT_PAGE_SIZE], start)]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    navigation = []
    if start:
        navigation.append(action('edit_prev', '⬅️ صفحه قبل', 'EDIT_CODE', refresh=True))
    if start + OIL_EDIT_PAGE_SIZE < len(candidates):
        navigation.append(action('edit_next', 'صفحه بعد ➡️', 'EDIT_CODE', refresh=True))
    if navigation:
        rows.append(navigation)
    rows.append((action('edit_batch', 'افزودن چند دستگاه', 'EDIT_CODE'),))
    rows.append((action('edit_back', '↩️ بازگشت', 'EDIT_CODE'),
                 action('edit_cancel', 'انصراف از همین ویرایش', 'EDIT_CODE')))
    rows.append((action('cancel', 'خروج از کل فرم', 'EDIT_CODE'),))
    return InlineKeyboardBuilder('work_order', rows)

oil_interval_keyboard = InlineKeyboardBuilder('work_order', (
    (action('interval_keep', '✅ حفظ همین نوبت', 'EDIT_INTERVAL'),),
    *[(action(f'interval_{n}', str(n), 'EDIT_INTERVAL'),
       action(f'interval_{n+200}', str(n+200), 'EDIT_INTERVAL')) for n in range(200, 2000, 400)],
    (action('edit_back', '↩️ بازگشت', 'EDIT_INTERVAL'),
     action('edit_cancel', 'انصراف از همین ویرایش', 'EDIT_INTERVAL')),
    (action('cancel', 'خروج از کل فرم', 'EDIT_INTERVAL'),),
))

oil_batch_codes_keyboard = InlineKeyboardBuilder('work_order', (
    (action('edit_back', '↩️ بازگشت', 'EDIT_BATCH_CODES'),
     action('edit_cancel', 'انصراف از همین ویرایش', 'EDIT_BATCH_CODES')),
    (action('cancel', 'خروج از کل فرم', 'EDIT_BATCH_CODES'),),
))

oil_edit_confirm_keyboard = InlineKeyboardBuilder('work_order', (
    (action('edit_apply', '✅ تأیید ویرایش', 'EDIT_CONFIRM'),
     action('edit_change', '✏️ تغییر نوبت', 'EDIT_CONFIRM')),
    (action('edit_back', '↩️ بازگشت', 'EDIT_CONFIRM'),
     action('edit_cancel', 'انصراف از همین ویرایش', 'EDIT_CONFIRM')),
    (action('cancel', 'خروج از کل فرم', 'EDIT_CONFIRM'),),
))


def oil_confirmation_keyboard(session):
    if len(session.edit_codes) <= 1:
        return oil_edit_confirm_keyboard
    start = session.edit_confirm_page * OIL_EDIT_PAGE_SIZE
    buttons = [action(f'edit_pending_{index}',
                      f'{code}-{session.edit_intervals[code]}', 'EDIT_CONFIRM')
               for index, code in enumerate(session.edit_codes[start:start + OIL_EDIT_PAGE_SIZE], start)]
    rows = [buttons[index:index + 2] for index in range(0, len(buttons), 2)]
    navigation = []
    if start:
        navigation.append(action('edit_confirm_prev', '⬅️ صفحه قبل', 'EDIT_CONFIRM', refresh=True))
    if start + OIL_EDIT_PAGE_SIZE < len(session.edit_codes):
        navigation.append(action('edit_confirm_next', 'صفحه بعد ➡️', 'EDIT_CONFIRM', refresh=True))
    if navigation:
        rows.append(navigation)
    rows.extend(((action('edit_apply', '✅ تأیید ویرایش', 'EDIT_CONFIRM'),),
                 (action('edit_back', '↩️ بازگشت', 'EDIT_CONFIRM'),
                  action('edit_cancel', 'انصراف از همین ویرایش', 'EDIT_CONFIRM')),
                 (action('cancel', 'خروج از کل فرم', 'EDIT_CONFIRM'),)))
    return InlineKeyboardBuilder('work_order', rows)


def oil_recovery_keyboard(session):
    if session.oil_recovery == 'inspect':
        return InlineKeyboardBuilder('work_order', (
            (action('proposal_inspect', '🔎 بررسی آخرین حکم', 'OIL_RECOVERY'),),
            (action('proposal_back', '↩️ بازگشت به پیشنهاد', 'OIL_RECOVERY'),),
            (action('cancel', 'خروج از کل فرم', 'OIL_RECOVERY'),),
        ))
    command, label = (('proposal_refresh', '🔄 دریافت پیشنهاد تازه') if session.oil_recovery == 'refresh'
                      else ('proposal_back', '↩️ بازگشت به پیشنهاد'))
    return InlineKeyboardBuilder('work_order', (
        (action(command, label, 'OIL_RECOVERY'),),
        (action('cancel', 'خروج از کل فرم', 'OIL_RECOVERY'),),
    ))

COMMANDS = {'add': 'اضافه', 'remove': 'حذف', 'confirm': 'تایید', 'cancel': 'انصراف',
            'edit_excel': 'افزودن/ویرایش', 'edit_back': 'برگشت',
            'edit_apply': 'تایید ویرایش', 'edit_change': 'تغییر نوبت',
            'edit_cancel': 'انصراف از ویرایش', 'edit_batch': 'افزودن چند دستگاه',
            'interval_keep': 'حفظ نوبت', 'proposal_back': 'بازگشت به پیشنهاد',
            'proposal_refresh': 'دریافت پیشنهاد تازه', 'proposal_inspect': 'بررسی آخرین حکم'}

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
    buttons.append(action('delivery_review', '🔎 بررسی ارسال', 'MENU'))
    buttons.append(action('cancel', 'انصراف', 'MENU'))
    return InlineKeyboardBuilder('work_order', [buttons[index:index+2] for index in range(0,len(buttons),2)])


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
            return oil_edit_code_keyboard(session)
        if session.stage == 'EDIT_INTERVAL':
            return oil_interval_keyboard
        if session.stage == 'EDIT_CONFIRM':
            return oil_confirmation_keyboard(session)
        if session.stage == 'EDIT_BATCH_CODES':
            return oil_batch_codes_keyboard
        if session.stage == 'OIL_RECOVERY':
            return oil_recovery_keyboard(session)
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


def command_for(action_name, *, order_no='', proposal=None, edit_codes=()):
    if action_name.startswith('edit_pending_') and action_name[13:].isdecimal():
        return edit_codes[int(action_name[13:])]
    if action_name.startswith('edit_unit_') and action_name[10:].isdecimal():
        from tools.fleet.work_orders.channels.bale.proposal_form import oil_edit_candidates
        candidates = oil_edit_candidates(proposal)
        index = int(action_name[10:])
        if index >= len(candidates):
            raise ValueError('دستگاه انتخاب‌شده در این پیشنهاد موجود نیست.')
        return candidates[index]['machine_code']
    if action_name.startswith('interval_') and action_name[9:].isdecimal():
        return action_name[9:]
    if action_name == 'delivery_review':
        return 'بررسی ارسال'
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
