from __future__ import annotations

from pathlib import Path

from tools.fleet.work_orders.core.db import connect_db
from tools.fleet.work_orders.core.pdf_document import export_staff_pdf
from tools.fleet.work_orders.core.daily_archive import archive_delivered_order


def reviewed_staff_pdf(stored_path, excel_path: Path) -> Path:
    """Send the PDF the manager already reviewed; render it only if that copy is missing."""
    pdf_path = Path(stored_path) if stored_path else None
    if pdf_path and pdf_path.is_file():
        with pdf_path.open("rb") as document:
            if document.read(5) == b"%PDF-":
                return pdf_path
    return export_staff_pdf(excel_path)


def store_manager_pdf(work_order_no: str, pdf_path: Path) -> None:
    """Remember the exact PDF delivered to the manager so staff dispatch reuses it."""
    con = connect_db()
    try:
        con.execute(
            """
            UPDATE service_work_orders
            SET pdf_path=?, updated_at=CURRENT_TIMESTAMP
            WHERE work_order_no=? AND status!='SENT'
            """,
            (str(pdf_path), work_order_no),
        )
        con.commit()
    finally:
        con.close()


def send_work_order(
    *,
    work_order_no: str,
    sender,
    actor=None,
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
                wo.work_order_type,
                wo.status,
                wo.excel_path,
                wo.pdf_path,
                wo.send_attempts,
                wo.created_by,
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


        def authorize_publish():
            if actor is not None:
                from tools.fleet.work_orders.core.permissions import require_work_order_permission
                if order['created_by'] != f'bale:{actor}':
                    raise PermissionError('Work order owner mismatch')
                required = ['work_orders.send']
                archive_cap = {'AIR_FILTER': 'work_orders.air_filter.archive_append',
                               'GREASING': 'work_orders.greasing.archive_append'}.get(order['work_order_type'])
                if archive_cap:
                    required.append(archive_cap)
                require_work_order_permission(actor, capabilities=required)
        authorize_publish()

        if order["status"] == "SENT":
            authorize_publish()
            archive_delivered_order(order)
            con.execute('UPDATE service_work_orders SET last_send_error=NULL WHERE id=?', (order['id'],))
            con.commit()
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


        excel_path = Path(
            order["excel_path"]
        )


        if not excel_path.exists():

            raise RuntimeError(
                "WORK ORDER FILE NOT FOUND"
            )


        file_path = reviewed_staff_pdf(order["pdf_path"], excel_path)

        authorize_publish()
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
                pdf_path=?,
                send_attempts =
                    send_attempts + 1,
                sent_at=CURRENT_TIMESTAMP,
                last_send_error=NULL,
                updated_at=CURRENT_TIMESTAMP
            WHERE id=?
            """,
            (
                str(file_path),
                order["id"],
            )
        )


        con.commit()

        authorize_publish()
        archive_delivered_order(order)

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
                    send_attempts + CASE WHEN status='SENT' THEN 0 ELSE 1 END,
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
