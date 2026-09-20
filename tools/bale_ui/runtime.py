"""Application composition root. Register additional domains here."""
from dataclasses import replace
from pathlib import Path

from .core import Router, StateStore
from .reply_keyboard import ReplyButton, ReplyMenu, ReplyMenuPresenter, load_registry, normalize

ROOT = Path(__file__).resolve().parents[2]

router = Router()

# Reply menus are the persistent bottom-of-chat UI layer. Their audience lives
# in configuration, and each button only injects an existing command as text.
reply_menus = load_registry(ROOT / 'settings/bale_reply_menus.json')
reply_presenter = ReplyMenuPresenter(reply_menus,
    state_store=StateStore(ROOT / 'runtime/bale_ui/reply_menu.json'),
    text='منوی اصلی آماده است؛ از دکمه‌های پایین صفحه استفاده کنید یا دستور را مثل قبل تایپ کنید.')


def _reply_menu_role(user_id):
    """Read the existing work-order role; this layer does not invent roles."""
    from tools.fleet.work_orders.core.permissions import check_work_order_permission
    result = check_work_order_permission(user_id)
    return result.role if result.allowed else None


def revoke_reply_menu(gateway, chat_id, user_id, *, send, text):
    """Drop the client keyboard when registration or authorization is removed."""
    return reply_presenter.remove(gateway, chat_id, user_id, text=text, send=send)


def reply_menu_step(event, gateway, *, send, bale_approved=False):
    """Translate a tapped label into its command and keep the menu available.

    No business rule, permission decision or flow state belongs here: every
    command continues through the existing handlers and their own checks.
    """
    source = getattr(event, 'source', None)
    if source is None or str(getattr(source.platform, 'value', source.platform)).lower() != 'bale':
        return None
    if getattr(source, 'chat_type', None) != 'dm':
        return None
    user_id = str(getattr(source, 'user_id', '') or '')
    chat_id = str(getattr(source, 'chat_id', '') or '')
    if not chat_id:
        return None
    raw = getattr(event, 'raw_message', None)
    if isinstance(raw, dict) and raw.get('bale_inline_callback') is True:
        # Inline callbacks keep their own lifecycle; reply menus never own them.
        return None
    role = _reply_menu_role(user_id)
    # Authorization, not the config users list, decides whether the operational
    # menu may stay on the client.
    menu = reply_menus.menu_for(user_id, role) if role else None
    # Approval is supplied by the Bale registration gate, never inferred from
    # an operational role. Compose after lookup so specialized menus win.
    if bale_approved:
        common_row = (ReplyButton('🔄 شروع گفتگوی جدید', '/new'),)
        menu = (replace(menu, rows=menu.rows + (common_row,)) if menu else
                ReplyMenu('new_chat', (common_row,)))
    key = (user_id, chat_id)
    if menu is None:
        if (key in reply_presenter.delivered or key in reply_presenter.pending
                or key in reply_presenter.pending_removal):
            reply_presenter.remove(gateway, chat_id, user_id, text='\u2060', send=send)
        return None
    command = menu.command_for(event.text)
    if command and normalize(event.text) != command:
        event.text = command
    if reply_menus.triggered(event.text):
        reply_presenter.present(gateway, chat_id, user_id, menu, send=send, force=True)
        return {'action': 'skip', 'reason': 'reply-menu-shown'}
    reply_presenter.present(gateway, chat_id, user_id, menu, send=send)
    return None


def dispatch(event, gateway, *, send, bale_approved=False):
    from tools.fleet.work_orders.channels.bale.message_handler import _handler as work_order
    from tools.fleet.repairs.entry_bale import _handler as repairs_entry
    from tools.fleet.repairs.maintenance_bale import _handler as maintenance_entry
    if 'repairs_entry' not in router.handlers:
        router.register('repairs_entry', repairs_entry.handle)
    if 'maintenance_entry' not in router.handlers:
        router.register('maintenance_entry', maintenance_entry.handle)
    result = reply_menu_step(event, gateway, send=send, bale_approved=bale_approved)
    if result is not None:
        return result
    raw = getattr(event, 'raw_message', None)
    if not (isinstance(raw, dict) and raw.get('bale_inline_callback') is True):
        from tools.fleet.repairs.entry_bale import normalized
        if normalized(event.text) in {'شرح خرابی', 'تعمیرات'}:
            import time
            key = ('bale', str(getattr(event.source, 'user_id', '') or ''), str(event.source.chat_id))
            current = work_order.pending.get(key)
            if current and current.expires > time.time() and current.stage not in {'RESULT'}:
                send(gateway, key[2], 'فرم حکم کار شما هنوز باز است؛ ابتدا آن را کامل کنید یا «انصراف» بفرستید، سپس بخش موردنظر را باز کنید.')
                return {'action': 'skip', 'reason': 'repairs-entry-work-order-active'}
        # Let the current form retire before routing a command to another form.
        handlers = (maintenance_entry, repairs_entry) if normalized(event.text) == 'شرح خرابی' else (repairs_entry, maintenance_entry)
        for handler in handlers:
            result = handler.handle(event, gateway, send=send)
            if result is not None:
                return result
    if 'work_order' not in router.handlers:
        router.register('work_order', work_order.handle)
    return router.dispatch(event, gateway, send=send)
