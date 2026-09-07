import argparse
import json
import mimetypes
import os
import sqlite3
import urllib.error
import urllib.request
import uuid
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

# ------------------------------------------------------------
# Hermes / Bale credential lookup
#
# Primary source:
#   %LOCALAPPDATA%\hermes\.env
#
# We deliberately do NOT hard-code the Bale token here.
# ------------------------------------------------------------

def _hermes_env_candidates():

    paths = []

    local_app_data = os.getenv(
        "LOCALAPPDATA",
        ""
    ).strip()

    if local_app_data:

        paths.append(
            Path(local_app_data)
            / "hermes"
            / ".env"
        )

    # Legacy / alternate Hermes location
    paths.append(
        Path.home()
        / ".hermes"
        / ".env"
    )

    # Remove duplicates while preserving order
    result = []

    seen = set()

    for path in paths:

        key = str(path).lower()

        if key in seen:
            continue

        seen.add(key)

        result.append(path)

    return result


def load_env_value(path, key):

    if not path.exists():
        return None

    try:

        text = path.read_text(
            encoding="utf-8-sig"
        )

        for raw_line in text.splitlines():

            line = raw_line.strip()

            if (
                not line
                or line.startswith("#")
            ):
                continue

            # Support:
            # export BALE_BOT_TOKEN=...
            if line.lower().startswith(
                "export "
            ):
                line = line[7:].strip()

            if "=" not in line:
                continue

            k, value = line.split(
                "=",
                1
            )

            if k.strip() != key:
                continue

            value = value.strip()

            if (
                len(value) >= 2
                and value[0] == value[-1]
                and value[0] in {"'", '"'}
            ):
                value = value[1:-1]

            value = value.strip()

            if value:
                return value

    except Exception as exc:

        raise RuntimeError(
            f"FAILED TO READ ENV FILE "
            f"{path}: {exc}"
        )

    return None


def get_bale_token():

    # First preference:
    # an already-loaded process environment variable.
    token = os.getenv(
        "BALE_BOT_TOKEN",
        ""
    ).strip()

    if token:

        print(
            "BALE TOKEN SOURCE: "
            "PROCESS ENVIRONMENT"
        )

        return token

    # Otherwise use Hermes' own .env file.
    checked = []

    for env_path in _hermes_env_candidates():

        checked.append(
            str(env_path)
        )

        token = load_env_value(
            env_path,
            "BALE_BOT_TOKEN"
        )

        if token:

            print(
                "BALE TOKEN SOURCE: "
                f"{env_path}"
            )

            return token

    raise RuntimeError(
        "BALE_BOT_TOKEN not found. "
        "Checked: "
        + " | ".join(checked)
    )

def multipart_body(
    fields,
    file_field,
    file_path
):

    boundary = (
        "----KomatsoAI"
        + uuid.uuid4().hex
    )

    body = bytearray()

    for name, value in fields.items():

        body.extend(
            f"--{boundary}\r\n".encode()
        )

        body.extend(
            (
                f'Content-Disposition: form-data; '
                f'name="{name}"\r\n\r\n'
            ).encode()
        )

        body.extend(
            str(value).encode("utf-8")
        )

        body.extend(
            b"\r\n"
        )

    mime_type = (
        mimetypes.guess_type(
            file_path.name
        )[0]
        or "application/octet-stream"
    )

    body.extend(
        f"--{boundary}\r\n".encode()
    )

    body.extend(
        (
            f'Content-Disposition: form-data; '
            f'name="{file_field}"; '
            f'filename="{file_path.name}"\r\n'
        ).encode("utf-8")
    )

    body.extend(
        (
            f"Content-Type: "
            f"{mime_type}\r\n\r\n"
        ).encode()
    )

    body.extend(
        file_path.read_bytes()
    )

    body.extend(
        b"\r\n"
    )

    body.extend(
        f"--{boundary}--\r\n".encode()
    )

    content_type = (
        "multipart/form-data; "
        f"boundary={boundary}"
    )

    return bytes(body), content_type


def send_document(
    token,
    chat_id,
    file_path,
    caption
):

    url = (
        "https://tapi.bale.ai/bot"
        + token
        + "/sendDocument"
    )

    body, content_type = (
        multipart_body(
            {
                "chat_id": chat_id,
                "caption": caption,
            },
            "document",
            file_path,
        )
    )

    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={
            "Content-Type": content_type,
            "User-Agent": "KomatsoAI-WorkOrder-V1",
        },
    )

    try:

        with urllib.request.urlopen(
            request,
            timeout=45
        ) as response:

            raw = response.read().decode(
                "utf-8",
                errors="replace"
            )

    except urllib.error.HTTPError as exc:

        error_body = exc.read().decode(
            "utf-8",
            errors="replace"
        )

        raise RuntimeError(
            f"BALE HTTP {exc.code}: "
            f"{error_body[:500]}"
        )

    except urllib.error.URLError as exc:

        raise RuntimeError(
            f"BALE NETWORK ERROR: "
            f"{exc.reason}"
        )

    try:

        data = json.loads(raw)

    except json.JSONDecodeError:

        raise RuntimeError(
            "BALE RETURNED INVALID JSON: "
            + raw[:500]
        )

    if not data.get("ok"):

        raise RuntimeError(
            "BALE API ERROR: "
            + json.dumps(
                data,
                ensure_ascii=False
            )[:800]
        )

    return data


def main():

    parser = argparse.ArgumentParser()

    parser.add_argument(
        "--work-order-no",
        required=True
    )

    args = parser.parse_args()

    if not DB_PATH.exists():

        raise SystemExit(
            f"DB NOT FOUND: {DB_PATH}"
        )

    con = sqlite3.connect(
        str(DB_PATH)
    )

    con.execute(
        "PRAGMA foreign_keys = ON"
    )

    try:

        order = con.execute(
            """
            SELECT
                wo.id,
                wo.work_order_no,
                wo.work_order_type,
                wo.jalali_date,
                wo.shift,
                wo.status,
                wo.excel_path,
                wo.send_attempts,
                wo.sent_at,
                s.id,
                s.display_name,
                s.bale_id,
                s.active
            FROM service_work_orders wo
            LEFT JOIN service_staff s
                ON s.id = wo.assigned_staff_id
            WHERE wo.work_order_no = ?
            """,
            (
                args.work_order_no,
            )
        ).fetchone()

        if not order:

            raise SystemExit(
                "WORK ORDER NOT FOUND"
            )

        # -----------------------------------------
        # جلوگیری از ارسال دوباره
        # -----------------------------------------

        if order[5] == "SENT":

            print("=" * 78)
            print("WORK ORDER ALREADY SENT")
            print("=" * 78)

            print(
                f"NUMBER:  {order[1]}"
            )

            print(
                f"SENT AT: {order[8]}"
            )

            print()
            print(
                "NO DUPLICATE SEND PERFORMED"
            )

            return

        # APPROVED یا retry پس از failure
        if order[5] not in {
            "APPROVED",
            "PENDING_SEND",
        }:

            raise SystemExit(
                "SEND BLOCKED: "
                f"STATUS={order[5]}"
            )

        if order[9] is None:

            raise SystemExit(
                "SEND BLOCKED: "
                "NO STAFF ASSIGNED"
            )

        if not order[11]:

            raise SystemExit(
                "SEND BLOCKED: "
                "STAFF HAS NO BALE ID"
            )

        if order[12] != 1:

            raise SystemExit(
                "SEND BLOCKED: "
                "STAFF IS INACTIVE"
            )

        if not order[6]:

            raise SystemExit(
                "SEND BLOCKED: "
                "NO EXCEL PATH"
            )

        excel_path = Path(
            order[6]
        )

        if not excel_path.exists():

            raise SystemExit(
                "SEND BLOCKED: "
                f"EXCEL NOT FOUND: {excel_path}"
            )

        token = get_bale_token()

        caption = (
            "📋 حکم کار سرویس هواکش\n\n"
            f"شماره حکم: {order[1]}\n"
            f"تاریخ: {order[3]}\n"
            f"شیفت: {order[4] or '-'}\n\n"
            "لطفاً فایل حکم کار را بررسی کنید.\n\n"
            "⚠️ این ارسال مربوط به تست Pilot سامانه است."
        )

        attempt_number = (
            int(order[7] or 0)
            + 1
        )

        print("=" * 78)
        print("SEND WORK ORDER TO BALE")
        print("=" * 78)

        print(
            f"NUMBER:       {order[1]}"
        )

        print(
            f"STATUS:       {order[5]}"
        )

        print(
            f"STAFF:        {order[10]}"
        )

        print(
            f"BALE ID:      {order[11]}"
        )

        print(
            f"FILE:         {excel_path}"
        )

        print(
            f"ATTEMPT:      {attempt_number}"
        )

        print()
        print(
            "Sending..."
        )

        try:

            result = send_document(
                token=token,
                chat_id=str(order[11]),
                file_path=excel_path,
                caption=caption,
            )

        except Exception as exc:

            error_text = str(exc)

            con.execute(
                """
                UPDATE service_work_orders
                SET
                    status = 'PENDING_SEND',
                    send_attempts = send_attempts + 1,
                    last_send_error = ?,
                    updated_at = CURRENT_TIMESTAMP
                WHERE id = ?
                """,
                (
                    error_text[:2000],
                    order[0],
                )
            )

            con.commit()

            print()
            print("=" * 78)
            print("SEND FAILED")
            print("=" * 78)

            print(
                error_text
            )

            print()
            print(
                "STATUS: PENDING_SEND"
            )

            print(
                f"ATTEMPT: {attempt_number}"
            )

            raise SystemExit(1)

        # -----------------------------------------
        # SUCCESS
        # -----------------------------------------

        con.execute(
            """
            UPDATE service_work_orders
            SET
                status = 'SENT',
                send_attempts = send_attempts + 1,
                last_send_error = NULL,
                sent_at = CURRENT_TIMESTAMP,
                updated_at = CURRENT_TIMESTAMP
            WHERE id = ?
            """,
            (
                order[0],
            )
        )

        con.commit()

        message = (
            result.get("result")
            or {}
        )

        message_id = (
            message.get("message_id")
            if isinstance(message, dict)
            else None
        )

        verify = con.execute(
            """
            SELECT
                status,
                send_attempts,
                last_send_error,
                sent_at
            FROM service_work_orders
            WHERE id = ?
            """,
            (
                order[0],
            )
        ).fetchone()

        print()
        print("=" * 78)
        print("BALE SEND SUCCESS")
        print("=" * 78)

        print(
            f"NUMBER:        {order[1]}"
        )

        print(
            f"MESSAGE ID:    {message_id or '-'}"
        )

        print(
            f"STATUS:        {verify[0]}"
        )

        print(
            f"SEND ATTEMPTS: {verify[1]}"
        )

        print(
            f"SENT AT:       {verify[3]}"
        )

        print()
        print("=" * 78)
        print("RESULT")
        print("=" * 78)

        print(
            "WORK ORDER SENT TO BALE"
        )

        print(
            "DATABASE STATUS: SENT"
        )

        print("=" * 78)

    finally:

        con.close()


if __name__ == "__main__":
    main()