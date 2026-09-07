from tools.fleet.work_orders.core.contracts import WorkOrderTypeSpec


SPEC = WorkOrderTypeSpec(
    code="AIR_FILTER",
    label_fa="هواکش",
    menu_order=1,
    operational=True,
    staff_role="AIR_FILTER",
    number_prefix="AF",
    template_key="AIR_FILTER_DAY9_V1",
    builder_module=(
        "tools.fleet.work_orders."
        "types.air_filter.builder"
    ),
)
