"""Manager document review, independent of assignment and dispatch approval."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tools.fleet.work_orders.core.db import connect_db


class DocumentReviewRequired(ValueError):
    pass


def last_record(row, prefix):
    for line in reversed(((row['notes'] if 'notes' in row.keys() else '') or '').splitlines()):
        if line.startswith(prefix + ': '):
            return json.loads(line.split(': ',1)[1])
    return None


def document_identity(row, pdf_bytes=None):
    try:
        data = Path(row['pdf_path']).read_bytes() if pdf_bytes is None else pdf_bytes
        if not data.startswith(b'%PDF-'):
            raise ValueError('Invalid PDF')
        return {'work_order_no':row['work_order_no'],
                'revision_of':row['revision_of'] if 'revision_of' in row.keys() else None,
                'sha256':hashlib.sha256(Path(row['excel_path']).read_bytes()).hexdigest(),
                'pdf_sha256':hashlib.sha256(data).hexdigest()}, data
    except (OSError,TypeError,ValueError) as exc:
        raise DocumentReviewRequired('فایل PDF موجود یا معتبر نیست؛ فایل را دوباره دریافت و بررسی و تأیید کنید.') from exc


def approved_pdf_bytes(row):
    """Read once: the validated bytes, rather than a reopenable path, reach transport."""
    review = last_record(row,'MANAGER_DOCUMENT_REVIEW')
    if not review or not review.get('pdf_sha256'):
        if row['work_order_type'] in {'GREASING','AIR_FILTER'} or last_record(row,'MANAGER_DOCUMENT_PREVIEW'):
            raise DocumentReviewRequired('تأیید PDF لازم است؛ بنویسید: ارسال مجدد ' + row['work_order_no'])
        return None  # Existing non-greasing CLI contracts retain their prior behavior.
    identity,data = document_identity(row)
    if (any(review.get(k)!=v for k,v in identity.items()) or
            'bale:'+str(review.get('bale_id')) != row['created_by']):
        raise DocumentReviewRequired('PDF یا Excel پس از بازبینی تغییر کرده است؛ دریافت و تأیید تازه لازم است: ارسال مجدد ' + row['work_order_no'])
    preview = last_record(row,'MANAGER_DOCUMENT_PREVIEW')
    if preview and preview.get('preview_id') != review.get('preview_id'):
        raise DocumentReviewRequired('فایل دوباره نمایش داده شده است؛ بررسی و تأیید تازه لازم است.')
    return data


def normalize_shift(value: str) -> str:
    parts = re.split(r"[\s,،\-–—]+", str(value).strip())
    parts = [part for part in parts if part]
    if not parts or any(part not in {"صبح", "ظهر", "عصر", "شب"} for part in parts):
        raise ValueError("شیفت را از صبح، ظهر، عصر و شب انتخاب کنید؛ مانند صبح-ظهر.")
    return "-".join(dict.fromkeys(parts))


def confirm_document_review(work_order_no: str, bale_id: str) -> dict:
    """Called by the permission-checked worker; retain all existing notes."""
    from tools.fleet.work_orders.core.permissions import require_work_order_permission
    from tools.fleet.work_orders.core import db as work_order_db
    con = connect_db()
    try:
        with con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT * FROM service_work_orders WHERE work_order_no=?", (work_order_no,)).fetchone()
            if not row or row["created_by"] != f"bale:{bale_id}":
                raise ValueError("این حکم متعلق به حساب شما نیست یا پیدا نشد.")
            from .oil_safety import current
            current(con, work_order_no)
            if row["status"] not in {'FILE_READY','ASSIGNED','APPROVED'}:
                raise ValueError("این حکم دیگر در مرحلهٔ بررسی فایل نیست.")
            if con.execute("SELECT 1 FROM sqlite_master WHERE name='service_work_order_dispatch'").fetchone():
                dispatch = con.execute('SELECT state FROM service_work_order_dispatch WHERE work_order_no=?',(work_order_no,)).fetchone()
                if dispatch and dispatch['state'] in {'SENDING','UNCERTAIN','SENT'}:
                    raise ValueError('ابتدا نتیجهٔ ارسال قبلی را تعیین تکلیف کنید.')
            require_work_order_permission(bale_id, db_path=work_order_db.DB_PATH, capabilities=('work_orders.review',))
            fingerprint = hashlib.sha256(Path(row["excel_path"]).read_bytes()).hexdigest()
            preview = last_record(row,'MANAGER_DOCUMENT_PREVIEW')
            identity = None
            if preview or row['work_order_type'] in {'GREASING','AIR_FILTER'}:
                identity,_ = document_identity(row)
                if not preview or any(preview.get(k)!=v for k,v in identity.items()):
                    raise DocumentReviewRequired('فایل با نسخهٔ نمایش‌داده‌شده یکسان نیست؛ دریافت و بررسی تازه لازم است.')
            notes = row["notes"] or ""
            prefix = "MANAGER_DOCUMENT_REVIEW: "
            for line in notes.splitlines():
                if line.startswith(prefix):
                    try:
                        previous = json.loads(line[len(prefix):])
                    except ValueError:
                        continue
                    if previous.get("bale_id") == bale_id and previous.get("sha256") == fingerprint and (not identity or (previous.get('pdf_sha256')==identity['pdf_sha256'] and previous.get('preview_id')==preview['preview_id'])):
                        return previous
            record = {"bale_id": bale_id, "sha256": fingerprint, "reviewed_at": datetime.now(timezone.utc).isoformat()}
            if identity:
                record.update(identity, preview_id=preview['preview_id'])
            notes = notes + ("\n" if notes else "") + prefix + json.dumps(record)
            require_work_order_permission(bale_id, db_path=work_order_db.DB_PATH, capabilities=('work_orders.review',))
            con.execute("UPDATE service_work_orders SET notes=?, updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?", (notes, work_order_no))
            return record
    finally:
        con.close()
