from __future__ import annotations

import importlib
import re
from pathlib import Path

from tools.fleet.work_orders.core.db import (
    connect_db,
)

from tools.fleet.work_orders.core.paths import (
    WORK_ORDER_OUTPUT_ROOT,
)

from tools.fleet.work_orders.core.registry import (
    get_work_order_spec,
)


def validate_jalali_date(
    value: str,
) -> str:

    value = str(value).strip()

    match = re.fullmatch(
        r"1405/(\d{2})/(\d{2})",
        value,
    )

    if not match:

        raise ValueError(
            "تاریخ باید مانند "
            "1405/06/10 باشد"
        )

    month = int(
        match.group(1)
    )

    day = int(
        match.group(2)
    )

    if not 1 <= month <= 12:

        raise ValueError(
            "ماه نامعتبر است"
        )

    max_day = (
        31
        if month <= 6
        else 30
    )

    if month == 12:
        max_day = 29

    if not 1 <= day <= max_day:

        raise ValueError(
            "روز نامعتبر است"
        )

    return value


def _load_builder(
    work_order_type: str,
):

    spec = get_work_order_spec(
        work_order_type
    )

    if not spec.operational:

        raise RuntimeError(
            f"{spec.label_fa} "
            "هنوز عملیاتی نشده است"
        )

    if not spec.builder_module:

        raise RuntimeError(
            f"{spec.code}: "
            "builder_module تعریف نشده است"
        )

    return importlib.import_module(
        spec.builder_module
    )


def _next_work_order_no(
    con,
    *,
    prefix: str,
    jalali_date: str,
) -> str:

    date_part = (
        jalali_date
        .replace("/", "-")
    )

    base = (
        f"{prefix}-"
        f"{date_part}-"
    )

    rows = con.execute(
        """
        SELECT work_order_no
        FROM service_work_orders
        WHERE work_order_no LIKE ?
        """,
        (
            base + "%",
        )
    ).fetchall()

    highest = 0

    for row in rows:

        try:

            seq = int(
                row["work_order_no"]
                .rsplit("-", 1)[1]
            )

            highest = max(
                highest,
                seq,
            )

        except Exception:
            continue

    return (
        f"{base}"
        f"{highest + 1:03d}"
    )


def _find_machine_id(
    con,
    machine_code: str,
):

    code = (
        str(machine_code)
        .strip()
        .upper()
    )

    candidates = [
        code
    ]

    if code.isdigit():

        n = int(code)

        if 461 <= n <= 469:
            candidates.append(
                f"HD{n}"
            )

        if 701 <= n <= 716:
            candidates.append(
                f"HD{n}"
            )

        if n == 231:
            candidates.append(
                "EX231"
            )

    placeholders = ",".join(
        "?"
        for _ in candidates
    )

    row = con.execute(
        f"""
        SELECT
            id,
            canonical_code
        FROM machines
        WHERE UPPER(canonical_code)
              IN ({placeholders})
        ORDER BY id
        LIMIT 1
        """,
        candidates,
    ).fetchone()

    if row:
        return row["id"]

    return None


def get_work_order(
    work_order_no: str,
) -> dict:

    con = connect_db()

    try:

        order = con.execute(
            """
            SELECT
                wo.*,
                s.display_name
                    AS staff_name,
                s.bale_id
                    AS staff_bale_id,
                s.service_role
                    AS staff_role,
                s.active
                    AS staff_active
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id =
                   wo.assigned_staff_id
            WHERE wo.work_order_no = ?
            """,
            (
                work_order_no,
            )
        ).fetchone()

        if not order:

            raise KeyError(
                "WORK ORDER NOT FOUND: "
                + work_order_no
            )

        items = con.execute(
            """
            SELECT
                *
            FROM service_work_order_items
            WHERE work_order_id = ?
            ORDER BY item_no
            """,
            (
                order["id"],
            )
        ).fetchall()

        result = dict(order)

        result["items"] = [
            dict(item)
            for item in items
        ]

        try:

            spec = get_work_order_spec(
                order["work_order_type"]
            )

            result[
                "work_order_label_fa"
            ] = spec.label_fa

        except Exception:

            result[
                "work_order_label_fa"
            ] = order[
                "work_order_type"
            ]

        return result

    finally:

        con.close()


def create_work_order(
    *,
    work_order_type: str,
    jalali_date: str,
    shift: str,
    machine_codes,
    created_by: str,
    item_actions=None,
    notes: str | None = None,
) -> dict:

    spec = get_work_order_spec(
        work_order_type
    )

    if not spec.operational:

        raise RuntimeError(
            f"نوع حکم «{spec.label_fa}» "
            "هنوز فعال نشده است"
        )

    jalali_date = (
        validate_jalali_date(
            jalali_date
        )
    )

    builder = _load_builder(
        spec.code
    )

    items = builder.get_items(machine_codes) if item_actions is None else builder.get_items(machine_codes, actions=item_actions)

    con = connect_db()

    output_path = None
    cleanup_output = False

    try:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        work_order_no = (
            _next_work_order_no(
                con,
                prefix=spec.number_prefix,
                jalali_date=jalali_date,
            )
        )

        date_folder = (
            jalali_date[:7]
            .replace("/", "-")
        )

        type_folder = (
            spec.code
            .lower()
        )

        output_path = (
            WORK_ORDER_OUTPUT_ROOT
            / type_folder
            / date_folder
            / f"{work_order_no}.xlsx"
        )

        if output_path.exists():

            raise RuntimeError(
                "OUTPUT ALREADY EXISTS: "
                f"{output_path}"
            )

        cleanup_output = True

        cur = con.execute(
            """
            INSERT INTO
                service_work_orders (
                    work_order_no,
                    work_order_type,
                    jalali_date,
                    shift,
                    status,
                    assigned_staff_id,
                    template_key,
                    excel_path,
                    pdf_path,
                    notes,
                    created_by
                )
            VALUES (
                ?, ?, ?, ?, ?,
                ?, ?, ?, ?, ?, ?
            )
            """,
            (
                work_order_no,
                spec.code,
                jalali_date,
                shift,
                "DRAFT",
                None,
                spec.template_key,
                None,
                None,
                notes,
                created_by,
            )
        )

        work_order_id = (
            cur.lastrowid
        )

        for item_no, item in enumerate(
            items,
            start=1,
        ):

            machine_id = (
                _find_machine_id(
                    con,
                    item[
                        "machine_code"
                    ],
                )
            )

            con.execute(
                """
                INSERT INTO
                    service_work_order_items (
                        work_order_id,
                        item_no,
                        machine_id,
                        machine_code,
                        machine_name,
                        action_code,
                        action_text,
                        item_status
                    )
                VALUES (
                    ?, ?, ?, ?, ?,
                    ?, ?, ?
                )
                """,
                (
                    work_order_id,
                    item_no,
                    machine_id,
                    item[
                        "machine_code"
                    ],
                    item[
                        "machine_name"
                    ],
                    item[
                        "action_code"
                    ],
                    item[
                        "action_text"
                    ],
                    "PENDING",
                )
            )

        builder.build_document(
            output_path=output_path,
            jalali_date=jalali_date,
            items=items,
            shift=shift,
        )

        if not output_path.exists():

            raise RuntimeError(
                "Work Order document "
                "was not created"
            )

        con.execute(
            """
            UPDATE service_work_orders
            SET
                status = 'FILE_READY',
                excel_path = ?,
                finalized_at =
                    CURRENT_TIMESTAMP,
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                str(output_path),
                work_order_id,
            )
        )

        con.commit()
        cleanup_output = False

        return get_work_order(
            work_order_no
        )

    except Exception:

        con.rollback()

        if (
            cleanup_output
            and output_path is not None
            and output_path.exists()
        ):

            try:
                output_path.unlink()
            except Exception:
                pass

        raise

    finally:

        con.close()


def list_eligible_staff(
    work_order_type: str,
) -> list[dict]:

    spec = get_work_order_spec(
        work_order_type
    )

    if not spec.staff_role:

        return []

    con = connect_db()

    try:

        rows = con.execute(
            """
            SELECT
                id,
                display_name,
                bale_id,
                service_role,
                active
            FROM service_staff
            WHERE
                service_role = ?
                AND active = 1
            ORDER BY
                display_name,
                id
            """,
            (
                spec.staff_role,
            )
        ).fetchall()

        return [
            dict(row)
            for row in rows
        ]

    finally:

        con.close()
