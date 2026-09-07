"""Shared six-column work-order Excel layout; business rules stay in type builders."""
from copy import copy
from pathlib import Path
from openpyxl import load_workbook

DATA_START_ROW = 3
TEMPLATE_DATA_ROWS = 6


def _copy_row_style(
    ws,
    source_row: int,
    target_row: int,
) -> None:

    for col in range(1, 7):

        src = ws.cell(
            source_row,
            col
        )

        dst = ws.cell(
            target_row,
            col
        )

        dst._style = copy(
            src._style
        )

        dst.font = copy(
            src.font
        )

        dst.fill = copy(
            src.fill
        )

        dst.border = copy(
            src.border
        )

        dst.alignment = copy(
            src.alignment
        )

        dst.protection = copy(
            src.protection
        )

        dst.number_format = (
            src.number_format
        )

    ws.row_dimensions[
        target_row
    ].height = (
        ws.row_dimensions[
            source_row
        ].height
    )


def build_document(
    *,
    output_path: Path,
    jalali_date: str,
    items: list[dict],
    template_path: Path,
    template_sheet: str,
    title: str,
) -> Path:
    TEMPLATE_PATH = template_path
    TEMPLATE_SHEET = template_sheet

    if not TEMPLATE_PATH.exists():

        raise RuntimeError(
            f"Work Order template "
            f"not found: {TEMPLATE_PATH}"
        )

    if not items:

        raise ValueError(
            "Work Order Work Order "
            "has no items"
        )

    wb = load_workbook(
        TEMPLATE_PATH
    )

    try:

        if (
            TEMPLATE_SHEET
            not in wb.sheetnames
        ):

            raise RuntimeError(
                "Work Order template "
                "sheet not found: "
                f"{TEMPLATE_SHEET}"
            )

        ws = wb[
            TEMPLATE_SHEET
        ]

        for other_ws in list(
            wb.worksheets
        ):

            if (
                other_ws.title
                != TEMPLATE_SHEET
            ):

                wb.remove(
                    other_ws
                )

        count = len(items)

        if count > TEMPLATE_DATA_ROWS:

            extra = (
                count
                - TEMPLATE_DATA_ROWS
            )

            insert_at = (
                DATA_START_ROW
                + TEMPLATE_DATA_ROWS
            )

            ws.insert_rows(
                insert_at,
                amount=extra
            )

            source_style_row = (
                DATA_START_ROW
                + TEMPLATE_DATA_ROWS
                - 1
            )

            for row in range(
                insert_at,
                DATA_START_ROW + count
            ):

                _copy_row_style(
                    ws,
                    source_style_row,
                    row,
                )

        elif count < TEMPLATE_DATA_ROWS:

            remove_count = (
                TEMPLATE_DATA_ROWS
                - count
            )

            ws.delete_rows(
                DATA_START_ROW + count,
                amount=remove_count
            )

        ws["F1"] = jalali_date
        ws["A1"] = title

        for index, item in enumerate(
            items,
            start=1,
        ):

            row = (
                DATA_START_ROW
                + index
                - 1
            )

            ws.cell(
                row,
                1
            ).value = index

            ws.cell(
                row,
                2
            ).value = item[
                "machine_name"
            ]

            ws.cell(
                row,
                3
            ).value = item[
                "machine_code"
            ]

            ws.cell(
                row,
                4
            ).value = item[
                "action_text"
            ]

            ws.cell(
                row,
                5
            ).value = None

            ws.cell(
                row,
                6
            ).value = None

        signature_row = (
            DATA_START_ROW
            + count
        )

        ws.print_area = (
            f"A1:F{signature_row}"
        )

        output_path.parent.mkdir(
            parents=True,
            exist_ok=True
        )

        wb.save(
            output_path
        )

    finally:

        wb.close()

    if not output_path.exists():

        raise RuntimeError(
            "Work Order Excel "
            "was not created"
        )

    return output_path
