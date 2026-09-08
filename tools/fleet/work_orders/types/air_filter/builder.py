from __future__ import annotations

from copy import copy
from pathlib import Path

from openpyxl import load_workbook

from tools.fleet.work_orders.core.paths import (
    WORK_ORDER_TEMPLATE_ROOT,
)


TEMPLATE_PATH = (
    WORK_ORDER_TEMPLATE_ROOT
    / "air_filter"
    / "air_filter_work_order_v1.xlsx"
)

TEMPLATE_SHEET = "Sheet1 (486)"

DATA_START_ROW = 3

TEMPLATE_DATA_ROWS = 6

DEFAULT_ACTION_CODE = (
    "AIR_FILTER_OUTER"
)

DEFAULT_ACTION_TEXT = (
    "تعویض هواکش بیرونی"
)


def normalize_machine_code(
    value: str,
) -> str:

    return str(value).strip().upper()


def machine_name_from_code(
    code: str,
) -> str:

    c = normalize_machine_code(
        code
    )

    if c.isdigit():

        n = int(c)

        if 461 <= n <= 469:
            return "دامپتراک"

        if 701 <= n <= 716:
            return "دامپتراک"

        if n == 231:
            return "بیل مکانیکی"

        if n in {151, 152}:
            return "بلدوزر"

    if c.startswith("EX") and c[2:].isdigit():
        return "بیل مکانیکی"

    if ((c.startswith("WA") and c[2:].isdigit())
            or (c.startswith("W") and c[1:].isdigit())):
        return "لودر"

    if c.startswith("D") and c[1:].isdigit():
        return "بلدوزر"

    if c.startswith("MZ"):
        return "مزدا"

    if c in {"S1", "S3"}:
        return "کامیون سهند زرد"

    if c == "TA1":
        return "کامیون آب پاش"

    if c == "TR1":
        return "خاور"

    if c == "DG1":
        return "ژنراتور"

    if c in {"PR2", "PR3"}:
        return "پیکاپ ریچ"

    raise ValueError(
        "کد دستگاه برای حکم هواکش "
        f"شناخته نشده: {code}"
    )


def validate_machine_codes(
    codes,
) -> list[str]:

    normalized = [
        normalize_machine_code(code)
        for code in codes
    ]

    if not normalized:

        raise ValueError(
            "هیچ دستگاهی انتخاب نشده است"
        )

    duplicates = sorted({
        code
        for code in normalized
        if normalized.count(code) > 1
    })

    if duplicates:

        raise ValueError(
            "کد دستگاه تکراری: "
            + ", ".join(duplicates)
        )

    for code in normalized:
        machine_name_from_code(code)

    return normalized


def get_items(
    codes,
    actions=None,
) -> list[dict]:

    normalized = (
        validate_machine_codes(
            codes
        )
    )

    items = [
        {
            "machine_code": code,
            "machine_name":
                machine_name_from_code(
                    code
                ),
            "action_code":
                DEFAULT_ACTION_CODE,
            "action_text":
                DEFAULT_ACTION_TEXT,
        }
        for code in normalized
    ]
    if actions is not None:
        from tools.fleet.air_filter.rules import ACTIONS, BOTH, OUTER, rule_for
        if set(actions) != set(normalized):
            raise ValueError('نوع تعویض باید برای همهٔ دستگاه‌های انتخابی مشخص باشد.')
        for item in items:
            rule = rule_for(item['machine_code'], item['machine_name'])
            action = actions[item['machine_code']]
            if action not in ACTIONS or ('calendar' in rule and action != OUTER) or (rule.get('together') and action != BOTH):
                raise ValueError('نوع تعویض برای دستگاه ' + item['machine_code'] + ' معتبر نیست.')
            item.update(action_code=action, action_text=ACTIONS[action])
    return items


def build_document(*, output_path: Path, jalali_date: str, items: list[dict], shift: str = "صبح") -> Path:
    from tools.fleet.work_orders.core.excel_document import build_document as render_excel
    return render_excel(output_path=output_path, jalali_date=jalali_date, items=items,
                        template_path=TEMPLATE_PATH, template_sheet=TEMPLATE_SHEET,
                        title=f"لیست هواکش شیفت {shift}")
