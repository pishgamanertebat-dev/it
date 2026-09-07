from __future__ import annotations

from pathlib import Path

from tools.fleet.work_orders.core.db import connect_db


def send_work_order(
    *,
    work_order_no: str,
    sender,
) -> dict:
    """
    Generic work order delivery.

    sender must implement:
        sender.send_document(
            chat_id,
            file_path,
            file_name,
        )
    """

    con = connect_db()

    try:

        order = con.execute(
            """
            SELECT
                wo.id,
                wo.work_order_no,
                wo.status,
                wo.excel_path,
                wo.send_attempts,
                s.display_name,
                s.bale_id
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id = wo.assigned_staff_id
            WHERE wo.work_order_no = ?
            """,
            (
                work_order_no,
            )
        ).fetchone()


        if not order:
            raise RuntimeError(
                "WORK ORDER NOT FOUND"
            )


        if order["status"] == "SENT":

            return {
                "status": "ALREADY_SENT",
                "work_order_no": work_order_no,
            }


        if order["status"] != "APPROVED":

            raise RuntimeError(
                "INVALID STATUS FOR SEND: "
                + order["status"]
            )


        if not order["bale_id"]:

            raise RuntimeError(
                "STAFF BALE ID EMPTY"
            )


        file_path = Path(
            order["excel_path"]
        )


        if not file_path.exists():

            raise RuntimeError(
                "WORK ORDER FILE NOT FOUND"
            )


        result = sender.send_document(
            chat_id=order["bale_id"],
            file_path=str(file_path),
            file_name=file_path.name,
        )


        con.execute(
            """
            UPDATE service_work_orders
            SET
                status='SENT',
                send_attempts =
                    send_attempts + 1,
                sent_at=CURRENT_TIMESTAMP,
                last_send_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                order["id"],
            )
        )


        con.commit()


        return {
            "status": "SENT",
            "work_order_no":
                work_order_no,
            "staff":
                order["display_name"],
            "bale_id":
                order["bale_id"],
        }


    except Exception as e:

        con.execute(
            """
            UPDATE service_work_orders
            SET
                send_attempts =
                    send_attempts + 1,
                last_send_error=?,
                updated_at=CURRENT_TIMESTAMP
            WHERE work_order_no=?
            """,
            (
                str(e),
                work_order_no,
            )
        )

        con.commit()

        raise


    finally:

        con.close()