"""Application composition root. Register additional domains here."""
from .core import Router

router = Router()


def dispatch(event, gateway, *, send):
    from tools.fleet.work_orders.channels.bale.message_handler import _handler as work_order
    from tools.fleet.repairs.entry_bale import _handler as repairs_entry
    if 'repairs_entry' not in router.handlers:
        router.register('repairs_entry', repairs_entry.handle)
    raw = getattr(event, 'raw_message', None)
    if not (isinstance(raw, dict) and raw.get('bale_inline_callback') is True):
        from tools.fleet.repairs.entry_bale import normalized
        if normalized(event.text) == 'شرح خرابی':
            import time
            key = ('bale', str(getattr(event.source, 'user_id', '') or ''), str(event.source.chat_id))
            current = work_order.pending.get(key)
            if current and current.expires > time.time() and current.stage not in {'RESULT'}:
                send(gateway, key[2], 'فرم حکم کار شما هنوز باز است؛ ابتدا آن را کامل کنید یا «انصراف» بفرستید، سپس «شرح خرابی» را بنویسید.')
                return {'action': 'skip', 'reason': 'repairs-entry-work-order-active'}
        result = repairs_entry.handle(event, gateway, send=send)
        if result is not None:
            return result
    if 'work_order' not in router.handlers:
        router.register('work_order', work_order.handle)
    return router.dispatch(event, gateway, send=send)
