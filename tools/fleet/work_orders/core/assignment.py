from __future__ import annotations

from tools.fleet.work_orders.core.db import connect_db
from tools.fleet.work_orders.core.service import get_work_order


def assign_work_order(
    *,
    work_order_no: str,
    staff_id: int,
) -> dict:

    con = connect_db()

    try:

        con.execute(
            "BEGIN IMMEDIATE"
        )

        order = con.execute(
            """
            SELECT
                id,
                work_order_no,
                work_order_type,
                status,
                assigned_staff_id
            FROM service_work_orders
            WHERE work_order_no = ?
            """,
            (
                work_order_no,
            )
        ).fetchone()


        if not order:

            raise RuntimeError(
                "WORK ORDER NOT FOUND: "
                + work_order_no
            )


        if order["assigned_staff_id"]:

            staff = con.execute(
                """
                SELECT
                    id,
                    display_name,
                    bale_id
                FROM service_staff
                WHERE id = ?
                """,
                (
                    order["assigned_staff_id"],
                )
            ).fetchone()


            con.rollback()

            return {
                "status": "ALREADY_ASSIGNED",
                "work_order_no":
                    work_order_no,
                "staff_id":
                    order["assigned_staff_id"],
                "staff_name":
                    staff["display_name"]
                    if staff else None,
            }


        if order["status"] not in (
            "FILE_READY",
        ):

            raise RuntimeError(
                "INVALID STATUS FOR ASSIGN: "
                + order["status"]
            )


        staff = con.execute(
            """
            SELECT
                id,
                display_name,
                bale_id,
                service_role,
                active
            FROM service_staff
            WHERE id = ?
            """,
            (
                staff_id,
            )
        ).fetchone()


        if not staff:

            raise RuntimeError(
                "STAFF NOT FOUND"
            )


        if staff["active"] != 1:

            raise RuntimeError(
                "STAFF NOT ACTIVE"
            )


        if not staff["bale_id"]:

            raise RuntimeError(
                "STAFF BALE ID EMPTY"
            )


        con.execute(
            """
            UPDATE service_work_orders
            SET
                assigned_staff_id = ?,
		status = 'ASSIGNED',
                updated_at =
                    CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                staff_id,
                order["id"],
            )
        )


        con.commit()


        return {
            "status": "ASSIGNED",
            "work_order_no":
                work_order_no,
            "staff_id":
                staff["id"],
            "staff_name":
                staff["display_name"],
            "bale_id":
                staff["bale_id"],
        }


    except Exception:

        con.rollback()
        raise


    finally:

        con.close()