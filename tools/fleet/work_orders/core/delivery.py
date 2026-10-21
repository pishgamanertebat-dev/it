from __future__ import annotations

from pathlib import Path
import logging
import json
import hashlib
import tempfile
from uuid import uuid4
from tools.fleet.work_orders.core import final_copy

logger = logging.getLogger(__name__)

from tools.fleet.work_orders.core.db import connect_db
from tools.fleet.work_orders.core.pdf_document import export_staff_pdf
from tools.fleet.work_orders.core.daily_archive import archive_delivered_order


def prepare_manager_pdf(number, actor):
    """Render a private snapshot, then fence its publication against dispatch."""
    from .operational import require_reviewable
    from .permissions import require_work_order_permission
    from .review import approved_pdf_bytes, last_record, DocumentReviewRequired
    con = connect_db()
    try:
        row = require_reviewable(con, number, actor)
        require_work_order_permission(actor, capabilities=('work_orders.read_own',))
        if last_record(row, 'MANAGER_DOCUMENT_REVIEW'):
            try:
                data = approved_pdf_bytes(row)
            except DocumentReviewRequired:
                data = None  # A deliberate new preview requires a fresh review.
            if data is not None:
                return Path(row['pdf_path']), data
        source = Path(row['excel_path'])
        workbook = source.read_bytes()
        fingerprint = hashlib.sha256(workbook).hexdigest()
        # Excel can take seconds. It must hold neither a fleet DB write lock nor
        # a replaceable production PDF while rendering. Keep the numbered name
        # so native OC/GR/AF print settings still apply to the private copy.
        with tempfile.TemporaryDirectory(prefix='manager_preview_',dir=source.parent) as temporary:
            snapshot = Path(temporary) / source.name
            snapshot.write_bytes(workbook)
            rendered = export_staff_pdf(snapshot)
            data = rendered.read_bytes()
            with con:
                con.execute('BEGIN IMMEDIATE')
                live = require_reviewable(con, number, actor)
                require_work_order_permission(actor, capabilities=('work_orders.read_own',))
                if live['excel_path'] != row['excel_path'] or hashlib.sha256(source.read_bytes()).hexdigest() != fingerprint:
                    raise ValueError('Excel changed during PDF preview; fresh review required')
                destination = source.with_suffix('.pdf')
                rendered.replace(destination)
                return destination, data
    finally:
        con.close()


def reviewed_staff_pdf(stored_path, excel_path: Path) -> Path:
    """Send the PDF the manager already reviewed; render it only if that copy is missing."""
    pdf_path = Path(stored_path) if stored_path else None
    if pdf_path and pdf_path.is_file():
        with pdf_path.open("rb") as document:
            if document.read(5) == b"%PDF-":
                return pdf_path
    return export_staff_pdf(excel_path)


def store_manager_pdf(work_order_no: str, pdf_path: Path, *, pdf_bytes=None, excel_sha256=None) -> None:
    """Remember the exact PDF delivered to the manager so staff dispatch reuses it."""
    con = connect_db()
    try:
        con.execute('BEGIN IMMEDIATE')
        from .operational import require_reviewable
        row = require_reviewable(con, work_order_no)
        from .review import document_identity
        identity,_ = document_identity(dict(row, pdf_path=str(pdf_path)),pdf_bytes)
        if excel_sha256 and identity['sha256'] != excel_sha256:
            raise ValueError('Excel changed during PDF preview; fresh review required')
        record = {**identity, 'preview_id':uuid4().hex}
        con.execute(
            """
            UPDATE service_work_orders
            SET pdf_path=?, notes=COALESCE(notes||char(10),'')||?, updated_at=CURRENT_TIMESTAMP
            WHERE work_order_no=? AND status!='SENT'
            """,
            (str(pdf_path), 'MANAGER_DOCUMENT_PREVIEW: '+json.dumps(record), work_order_no),
        )
        con.commit()
    finally:
        con.close()


def send_work_order(
    *,
    work_order_no: str,
    sender,
    actor=None,
    dispatch_token=None,
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
    oil = False
    oil_attempt = None

    try:

        order = con.execute(
            """
            SELECT
                wo.*,
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


        from . import oil_safety
        oil = order['work_order_type'] in oil_safety.RELIABLE_TYPES
        oil_safety.current(con, work_order_no)

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


        if oil:
            if dispatch_token is not None and (type(dispatch_token) is not int or dispatch_token <= 0):
                raise ValueError('تلاش ارسال معتبر نیست؛ بررسی ارسال را باز کنید.')
            oil_safety.migrate(con)
            con.execute('BEGIN IMMEDIATE')
            oil_attempt = dispatch_token if dispatch_token is not None else oil_safety.claim(con, work_order_no)
            con.commit()
            if not oil_attempt:
                raise ValueError('ارسال این حکم قفل است؛ «بررسی ارسال» را باز کنید.')

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


        from .review import approved_pdf_bytes
        document_bytes = approved_pdf_bytes(order)
        file_path = Path(order['pdf_path']) if document_bytes is not None else reviewed_staff_pdf(order["pdf_path"], excel_path)

        # Pin the exact final PDF before staff transport; no copy is queued yet.
        artifact = None
        try:
            artifact = final_copy.prepare(con, order, file_path, document_bytes=document_bytes)
        except Exception as exc:
            logger.error('Final copy preparation failed: %s', type(exc).__name__)
        authorize_publish()
        # Final version fence for all protected types, immediately before transport.
        live = oil_safety.current(con, work_order_no)
        if document_bytes is not None and approved_pdf_bytes(live) != document_bytes:
            raise ValueError('PDF changed before transport; fresh review required')
        if oil:
            con.execute('BEGIN IMMEDIATE')
            oil_safety.start_transfer(con, work_order_no, oil_attempt, file_path)
            con.commit()
        transport = {'document_bytes':document_bytes} if document_bytes is not None else {}
        result = sender.send_document(
            chat_id=order["bale_id"],
            file_path=artifact['path'] if artifact and artifact['path'] else str(file_path),
            file_name=file_path.name,
            **transport,
        )


        if oil:
            con.execute('BEGIN IMMEDIATE')
            if oil_safety.complete(con, work_order_no, oil_attempt) != 'SENT':
                con.commit()
                return {'status':'UNCERTAIN','work_order_no':work_order_no}
        con.execute(
            """
            UPDATE service_work_orders
            SET
                status='SENT',
                pdf_path=?,
                send_attempts =
                    send_attempts + CASE WHEN work_order_type IN ('OIL_CHANGE','GREASING','AIR_FILTER') THEN 0 ELSE 1 END,
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


        # Atomic with SENT, but copy failures must never roll back the staff operation.
        con.execute('SAVEPOINT final_copy_enqueue')
        try:
            final_copy.enqueue(con, order, artifact, result)
        except Exception as exc:
            con.execute('ROLLBACK TO final_copy_enqueue')
            logger.error('Final copy enqueue failed: %s', type(exc).__name__)
        finally:
            con.execute('RELEASE final_copy_enqueue')
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

        if oil:
            con.rollback()
            with con:
                con.execute('BEGIN IMMEDIATE')
                if oil_attempt and oil_safety.complete(con, work_order_no, oil_attempt, error=e):
                    con.execute('UPDATE service_work_orders SET last_send_error=?,updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?', (type(e).__name__,work_order_no))
            raise
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
