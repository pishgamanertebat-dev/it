from __future__ import annotations

import json
import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(r"E:\KomatsoAI\data\fleet\db\fleet_ops.db")

PARSER_VERSION = "daily_v1"

DATE_RE = re.compile(
    r"(14\d{2})[./_-](\d{1,2})[./_-](\d{1,2})"
)


def clean(value) -> str:
    if value is None:
        return ""

    return (
        str(value)
        .replace("\u200c", " ")
        .strip()
    )


def normalize_header(value) -> str:
    text = clean(value)

    text = (
        text
        .replace("ي", "ی")
        .replace("ك", "ک")
    )

    return re.sub(r"\s+", " ", text)


def extract_sheet_date(sheet_name: str) -> str | None:
    match = DATE_RE.search(sheet_name)

    if not match:
        return None

    year, month, day = map(int, match.groups())

    return f"{year:04d}/{month:02d}/{day:02d}"


def find_column(headers, predicate):
    for index, header in enumerate(headers):
        if predicate(header):
            return index

    return None


def find_machine_code_column(headers):
    priorities = [
        lambda h: h == "کد جدید",
        lambda h: h == "کد مکانیزم",
        lambda h: h == "کد دستگاه",
        lambda h: h == "کد",
    ]

    for predicate in priorities:
        result = find_column(headers, predicate)

        if result is not None:
            return result

    return None


def get_value(values, index) -> str:
    if index is None:
        return ""

    if index >= len(values):
        return ""

    return clean(values[index])


def init_db(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS daily_fault_reports (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            raw_excel_row_id INTEGER NOT NULL UNIQUE,
            ingest_source_id INTEGER NOT NULL,

            parser_version TEXT NOT NULL,

            report_date TEXT NOT NULL,
            sheet_name TEXT NOT NULL,
            sheet_index INTEGER NOT NULL,
            excel_row INTEGER NOT NULL,

            row_no TEXT,

            machine_type_raw TEXT,
            machine_code_raw TEXT NOT NULL,

            mechanical_raw TEXT,
            fabrication_raw TEXT,
            general_raw TEXT,

            normalized_at TEXT NOT NULL,

            FOREIGN KEY (raw_excel_row_id)
                REFERENCES raw_excel_rows(id),

            FOREIGN KEY (ingest_source_id)
                REFERENCES ingest_sources(id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_daily_fault_reports_date
        ON daily_fault_reports(report_date)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_daily_fault_reports_machine_raw
        ON daily_fault_reports(machine_code_raw)
        """
    )

    conn.commit()


def load_raw_sheets(
    conn: sqlite3.Connection,
    ingest_source_id: int,
):
    rows = conn.execute(
        """
        SELECT
            id,
            sheet_index,
            sheet_name,
            excel_row,
            row_json
        FROM raw_excel_rows
        WHERE ingest_source_id = ?
        ORDER BY
            sheet_index,
            excel_row
        """,
        (ingest_source_id,),
    ).fetchall()

    sheets = {}

    for row in rows:
        raw_id, sheet_index, sheet_name, excel_row, row_json = row

        sheets.setdefault(
            (sheet_index, sheet_name),
            [],
        ).append(
            {
                "raw_id": raw_id,
                "excel_row": excel_row,
                "values": json.loads(row_json),
            }
        )

    return sheets


def detect_header(rows):
    # فقط ردیف‌های ابتدایی هر شیت را بررسی می‌کنیم.
    for item in rows:
        if item["excel_row"] > 12:
            break

        headers = [
            normalize_header(value)
            for value in item["values"]
        ]

        has_code = (
            find_machine_code_column(headers)
            is not None
        )

        has_fault = any(
            "شرح معایب" in header
            for header in headers
        )

        if has_code and has_fault:
            return item["excel_row"], headers

    return None, None


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    conn.execute("PRAGMA foreign_keys = ON")

    init_db(conn)

    source = conn.execute(
        """
        SELECT id
        FROM ingest_sources
        WHERE source_type = 'daily_fault_reports'
        ORDER BY id DESC
        LIMIT 1
        """
    ).fetchone()

    if not source:
        raise SystemExit(
            "ERROR: no daily_fault_reports source found"
        )

    ingest_source_id = source[0]

    sheets = load_raw_sheets(
        conn,
        ingest_source_id,
    )

    normalized_at = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    dated_sheets = 0
    parsed_sheets = 0
    unparsed_sheets = 0

    candidate_rows = 0
    inserted = 0
    skipped = 0

    with conn:
        for (
            sheet_index,
            sheet_name,
        ), rows in sheets.items():

            report_date = extract_sheet_date(
                sheet_name
            )

            if not report_date:
                continue

            dated_sheets += 1

            header_row, headers = detect_header(
                rows
            )

            if header_row is None:
                unparsed_sheets += 1
                print(
                    f"SKIP SHEET - header not found: "
                    f"{sheet_name!r}"
                )
                continue

            parsed_sheets += 1

            col_row_no = find_column(
                headers,
                lambda h: h == "ردیف",
            )

            col_machine_type = find_column(
                headers,
                lambda h:
                    "نوع دستگاه" in h
                    or "نوع مکانیزم" in h,
            )

            col_machine_code = (
                find_machine_code_column(headers)
            )

            col_mechanical = find_column(
                headers,
                lambda h:
                    "شرح معایب مکانیکی" in h,
            )

            col_fabrication = find_column(
                headers,
                lambda h:
                    "شرح معایب آهنگری" in h,
            )

            col_general = find_column(
                headers,
                lambda h:
                    h.startswith("شرح معایب")
                    and "مکانیکی" not in h
                    and "آهنگری" not in h,
            )

            for item in rows:
                if item["excel_row"] <= header_row:
                    continue

                values = item["values"]

                machine_code = get_value(
                    values,
                    col_machine_code,
                )

                machine_type = get_value(
                    values,
                    col_machine_type,
                )

                # ردیف دستگاه باید حداقل کد دستگاه داشته باشد.
                # هیچ Machine Identity یا اصلاحی اینجا انجام نمی‌دهیم.
                if not machine_code:
                    continue

                candidate_rows += 1

                cur = conn.execute(
                    """
                    INSERT OR IGNORE INTO daily_fault_reports (
                        raw_excel_row_id,
                        ingest_source_id,
                        parser_version,
                        report_date,
                        sheet_name,
                        sheet_index,
                        excel_row,
                        row_no,
                        machine_type_raw,
                        machine_code_raw,
                        mechanical_raw,
                        fabrication_raw,
                        general_raw,
                        normalized_at
                    )
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        item["raw_id"],
                        ingest_source_id,
                        PARSER_VERSION,
                        report_date,
                        sheet_name,
                        sheet_index,
                        item["excel_row"],
                        get_value(
                            values,
                            col_row_no,
                        ),
                        machine_type,
                        machine_code,
                        get_value(
                            values,
                            col_mechanical,
                        ),
                        get_value(
                            values,
                            col_fabrication,
                        ),
                        get_value(
                            values,
                            col_general,
                        ),
                        normalized_at,
                    ),
                )

                if cur.rowcount == 1:
                    inserted += 1
                else:
                    skipped += 1

    print()
    print("NORMALIZATION COMPLETE")
    print(f"Dated sheets: {dated_sheets}")
    print(f"Parsed sheets: {parsed_sheets}")
    print(f"Unparsed sheets: {unparsed_sheets}")
    print(f"Candidate machine rows: {candidate_rows}")
    print(f"Inserted: {inserted}")
    print(f"Skipped: {skipped}")

    print()
    print("LATEST HD714 SAMPLE")

    samples = conn.execute(
        """
        SELECT
            report_date,
            machine_code_raw,
            machine_type_raw,
            mechanical_raw,
            fabrication_raw
        FROM daily_fault_reports
        WHERE machine_code_raw = 'HD714'
        ORDER BY report_date DESC
        LIMIT 3
        """
    ).fetchall()

    for row in samples:
        print("-" * 60)
        print(f"Date: {row[0]}")
        print(f"Machine: {row[1]}")
        print(f"Type: {row[2]}")
        print(f"Mechanical: {row[3]}")
        print(f"Fabrication: {row[4]}")

    conn.close()


if __name__ == "__main__":
    main()