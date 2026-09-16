from __future__ import annotations

from pathlib import Path

from tools.fleet.work_orders.core.permissions import require_work_order_permission
from tools.fleet.work_orders.core.registry import list_work_order_types


class InvalidWorkOrderSelection(ValueError):
    pass


class WorkOrderTypeDisabled(ValueError):
    pass


def build_work_order_menu(
    *,
    bale_id: str | int,
    db_path: Path | str | None = None,
    inline: bool = False,
) -> str:
    require_work_order_permission(bale_id, db_path=db_path)
    if inline:
        return 'حکم کار\nنوع حکم مورد نظر را از دکمه‌های زیر انتخاب کنید.'
    items = list_work_order_types(enabled_only=False)
    lines = ["حکم کار", "", "لطفاً شمارهٔ نوع حکم کار را انتخاب کنید:", ""]
    for index, item in enumerate(items, start=1):
        suffix = "" if item["enabled"] else " (به‌زودی)"
        lines.append(f"{index}) {item['label']}{suffix}")
    return "\n".join(lines)


def resolve_work_order_selection(
    selection: int | str,
    *,
    bale_id: str | int,
    db_path: Path | str | None = None,
) -> dict:
    # Recheck authorization even if the user already received the menu.
    require_work_order_permission(bale_id, db_path=db_path)
    if isinstance(selection, bool) or not isinstance(selection, (int, str)):
        raise InvalidWorkOrderSelection("شمارهٔ گزینه نامعتبر است.")
    text = str(selection).strip()
    if not text.isdecimal():
        raise InvalidWorkOrderSelection("شمارهٔ گزینه نامعتبر است.")
    try:
        index = int(text)
    except ValueError:
        raise InvalidWorkOrderSelection("شمارهٔ گزینه نامعتبر است.") from None
    items = list_work_order_types(enabled_only=False)
    if index < 1 or index > len(items):
        raise InvalidWorkOrderSelection("شمارهٔ گزینه نامعتبر است.")
    item = items[index - 1]
    if not item["enabled"]:
        raise WorkOrderTypeDisabled(f"«{item['label']}» هنوز فعال نشده است.")
    return item
