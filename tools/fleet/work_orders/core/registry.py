from __future__ import annotations

from tools.fleet.work_orders.core.contracts import WorkOrderTypeSpec
from tools.fleet.work_orders.types.air_filter.definition import SPEC as AIR_FILTER
from tools.fleet.work_orders.types.oil_change.definition import SPEC as OIL_CHANGE
from tools.fleet.work_orders.types.greasing.definition import SPEC as GREASING


# Each type's definition is the single source of metadata for Core and channels.
WORK_ORDER_REGISTRY = {
    spec.code: spec for spec in (AIR_FILTER, OIL_CHANGE, GREASING)
}
for _spec in WORK_ORDER_REGISTRY.values():
    _spec.validate()


def get_work_order_spec(key: str) -> WorkOrderTypeSpec:
    try:
        return WORK_ORDER_REGISTRY[key]
    except KeyError:
        raise RuntimeError("UNKNOWN WORK ORDER TYPE: " + key) from None


def list_work_order_specs(enabled_only: bool = False) -> list[WorkOrderTypeSpec]:
    return [
        spec
        for spec in sorted(WORK_ORDER_REGISTRY.values(), key=lambda item: item.menu_order)
        if not enabled_only or spec.operational
    ]


def _menu_item(spec: WorkOrderTypeSpec) -> dict:
    return {
        "key": spec.code,
        "label": spec.label_fa,
        "enabled": spec.operational,
        "builder": spec.builder_module,
    }


def get_work_order_type(key: str) -> dict:
    """Keep the existing dictionary interface used by channel menus."""
    return _menu_item(get_work_order_spec(key))


def list_work_order_types(enabled_only: bool = True) -> list[dict]:
    return [_menu_item(spec) for spec in list_work_order_specs(enabled_only)]
