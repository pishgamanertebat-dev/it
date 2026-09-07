from tools.fleet.work_orders.core.contracts import WorkOrderTypeSpec


SPEC = WorkOrderTypeSpec(
    code="OIL_CHANGE",
    label_fa="تعویض روغن",
    menu_order=2,
    operational=False,
    staff_role="OIL_CHANGE",
    number_prefix=None,
    template_key=None,
    builder_module=None,
)