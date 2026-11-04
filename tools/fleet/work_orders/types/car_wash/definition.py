from tools.fleet.work_orders.core.contracts import WorkOrderTypeSpec

SPEC = WorkOrderTypeSpec(
    code='CAR_WASH', label_fa='کارواش', menu_order=4, operational=True,
    staff_role='CAR_WASH', number_prefix='CW', template_key='CAR_WASH_DAILY_V1',
    builder_module='tools.fleet.work_orders.types.car_wash.builder',
)
