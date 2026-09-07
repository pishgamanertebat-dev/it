from tools.fleet.work_orders.core.contracts import WorkOrderTypeSpec


SPEC = WorkOrderTypeSpec(
    code="GREASING",
    label_fa="گریس‌کاری",
    menu_order=3,
    operational=True,
    staff_role="GREASING",
    number_prefix="GR",
    template_key="GREASING_DAILY_V1",
    builder_module="tools.fleet.work_orders.types.greasing.builder",
)
