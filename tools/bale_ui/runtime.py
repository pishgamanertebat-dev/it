"""Application composition root. Register additional domains here."""
from .core import Router

router = Router()


def dispatch(event, gateway, *, send):
    if 'work_order' not in router.handlers:
        from tools.fleet.work_orders.channels.bale.message_handler import _handler
        router.register('work_order', _handler.handle)
    return router.dispatch(event, gateway, send=send)
