from tools.fleet.work_orders.core.contracts import WorkOrderTypeSpec


SPEC = WorkOrderTypeSpec(
    code="OIL_CHANGE",
    label_fa="تعویض روغن",
    menu_order=2,
    operational=True,
    staff_role="OIL_CHANGE",
    number_prefix="OC",
    template_key="FLEET_PM_200_2000_v5",
    builder_module="tools.fleet.work_orders.types.oil_change.builder",
)
