"""Manager document review, independent of assignment and dispatch approval."""
from __future__ import annotations

import hashlib
import json
import re
from datetime import datetime, timezone
from pathlib import Path

from tools.fleet.work_orders.core.db import connect_db


def normalize_shift(value: str) -> str:
    parts = re.split(r"[\s,،\-–—]+", str(value).strip())
    parts = ["ظهر" if part == "عصر" else part for part in parts if part]
    if not parts or any(part not in {"صبح", "ظهر", "شب"} for part in parts):
        raise ValueError("شیفت را از صبح، ظهر و شب وارد کنید؛ مانند صبح ظهر.")
    return "-".join(dict.fromkeys(parts))


def confirm_document_review(work_order_no: str, bale_id: str) -> dict:
    """Called by the permission-checked worker; retain all existing notes."""
    con = connect_db()
    try:
        with con:
            con.execute("BEGIN IMMEDIATE")
            row = con.execute("SELECT status,created_by,excel_path,notes FROM service_work_orders WHERE work_order_no=?", (work_order_no,)).fetchone()
            if not row or row["created_by"] != f"bale:{bale_id}":
                raise ValueError("این حکم متعلق به حساب شما نیست یا پیدا نشد.")
            if row["status"] != "FILE_READY":
                raise ValueError("این حکم دیگر در مرحلهٔ بررسی فایل نیست.")
            fingerprint = hashlib.sha256(Path(row["excel_path"]).read_bytes()).hexdigest()
            notes = row["notes"] or ""
            prefix = "MANAGER_DOCUMENT_REVIEW: "
            for line in notes.splitlines():
                if line.startswith(prefix):
                    try:
                        previous = json.loads(line[len(prefix):])
                    except ValueError:
                        continue
                    if previous.get("bale_id") == bale_id and previous.get("sha256") == fingerprint:
                        return previous
            record = {"bale_id": bale_id, "sha256": fingerprint, "reviewed_at": datetime.now(timezone.utc).isoformat()}
            notes = notes + ("\n" if notes else "") + prefix + json.dumps(record)
            con.execute("UPDATE service_work_orders SET notes=?, updated_at=CURRENT_TIMESTAMP WHERE work_order_no=?", (notes, work_order_no))
            return record
    finally:
        con.close()
