from __future__ import annotations

from datetime import datetime, timezone

from tools.fleet.work_orders.core.db import connect_db


def approve_work_order(
    *,
    work_order_no: str,
    approved_by: str,
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
                status,
                assigned_staff_id,
                approved_by,
                approved_at
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


        if order["status"] == "APPROVED":

            con.rollback()

            return {
                "status": "ALREADY_APPROVED",
                "work_order_no":
                    work_order_no,
                "approved_by":
                    order["approved_by"],
                "approved_at":
                    order["approved_at"],
            }


        if order["status"] != "ASSIGNED":

            raise RuntimeError(
                "INVALID STATUS FOR APPROVAL: "
                + order["status"]
            )


        now = datetime.now(
            timezone.utc
        ).strftime(
            "%Y-%m-%d %H:%M:%S"
        )


        con.execute(
            """
            UPDATE service_work_orders
            SET
                status = 'APPROVED',
                approved_by = ?,
                approved_at = ?,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                approved_by,
                now,
                order["id"],
            )
        )


        con.commit()


        return {
            "status": "APPROVED",
            "work_order_no":
                work_order_no,
            "approved_by":
                approved_by,
            "approved_at":
                now,
        }


    except Exception:

        con.rollback()
        raise


    finally:

        con.close()