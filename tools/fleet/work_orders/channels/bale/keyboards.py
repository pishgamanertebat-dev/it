"""Work-order presentation and action names, separate from business execution."""
from tools.bale_ui import Action, InlineKeyboardBuilder

_ROLES = frozenset({'MAINTENANCE_MANAGER'})
_STAGES = frozenset({'PROPOSAL'})


def action(name, label):
    return Action(name, label, 'work_order.manage', _STAGES, _ROLES)


proposal_keyboard = InlineKeyboardBuilder('work_order', (
    (action('add', '➕ افزودن دستگاه'), action('remove', '❌ حذف دستگاه')),
    (action('confirm', '✅ تایید و ساخت اکسل'), action('cancel', '🚪 انصراف')),
))

COMMANDS = {'add': 'اضافه', 'remove': 'حذف', 'confirm': 'تایید', 'cancel': 'انصراف'}
