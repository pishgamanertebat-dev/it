from __future__ import annotations

import re
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)

PARSER_VERSION = "fault_rules_v1"


def clean(text):
    if text is None:
        return ""

    return (
        str(text)
        .replace("\u200c", " ")
        .replace("ي", "ی")
        .replace("ك", "ک")
        .strip()
    )


def split_fragments(text):
    text = clean(text)

    if not text:
        return []

    parts = re.split(
        r"[\n\r;؛]+|\s+-\s+|-\s*",
        text,
    )

    result = []

    for part in parts:
        part = re.sub(
            r"\s+",
            " ",
            part,
        ).strip(" -،,")

        if (
            part
            and part != "-"
            and len(part) >= 2
        ):
            result.append(part)

    return result


def classify(fragment):
    text = clean(fragment)

    # Retarder
    if "ریتاردر" in text:
        bad_words = (
            "عمل نمی کند",
            "عمل نمیکند",
            "کار نمی کند",
            "کار نمیکند",
            "کار نکرد",
            "قطع است",
            "قطع شده",
            "خراب",
        )

        if any(word in text for word in bad_words):
            return (
                "RETARDER_NOT_WORKING",
                "ریتاردر عمل نمی‌کند",
            )

    # Weak brake
    if (
        "ترمز" in text
        and "ضعیف" in text
    ):
        return (
            "BRAKE_WEAK",
            "ضعف ترمز",
        )

    # Transmission shift shock
    if "دنده" in text:
        words = (
            "تقه",
            "ضربه",
            "دیر",
            "تعویض",
        )

        if any(word in text for word in words):
            return (
                "TRANSMISSION_SHIFT_SHOCK",
                "ضربه/تقه در تعویض دنده",
            )

    # Oil leak
    if any(
        word in text
        for word in (
            "روغن ریزی",
            "روغنریزی",
            "نشتی روغن",
        )
    ):
        return (
            "OIL_LEAK",
            "روغن‌ریزی / نشتی روغن",
        )

    # Undercarriage
    if "زیربندی" in text:
        return (
            "UNDERCARRIAGE_ISSUE",
            "ایراد زیربندی",
        )

    return (
        None,
        None,
    )


def init_db(conn):
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS issue_mentions (
            id INTEGER PRIMARY KEY AUTOINCREMENT,

            daily_fault_report_id INTEGER NOT NULL,
            machine_id INTEGER NOT NULL,

            report_date TEXT NOT NULL,

            field_name TEXT NOT NULL,
            fragment_index INTEGER NOT NULL,

            raw_fragment TEXT NOT NULL,

            issue_family TEXT,
            issue_label TEXT,

            classification_status TEXT NOT NULL,
            parser_version TEXT NOT NULL,

            classified_at TEXT NOT NULL,

            UNIQUE (
                daily_fault_report_id,
                field_name,
                fragment_index
            ),

            FOREIGN KEY (daily_fault_report_id)
                REFERENCES daily_fault_reports(id),

            FOREIGN KEY (machine_id)
                REFERENCES machines(id)
        )
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_issue_mentions_machine
        ON issue_mentions(machine_id)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_issue_mentions_family
        ON issue_mentions(issue_family)
        """
    )

    conn.execute(
        """
        CREATE INDEX IF NOT EXISTS
        idx_issue_mentions_date
        ON issue_mentions(report_date)
        """
    )

    conn.commit()


def main():
    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)
    conn.execute("PRAGMA foreign_keys = ON")

    init_db(conn)

    rows = conn.execute(
        """
        SELECT
            d.id,
            i.machine_id,
            d.report_date,
            d.mechanical_raw,
            d.fabrication_raw,
            d.general_raw

        FROM daily_fault_reports d

        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id

        ORDER BY d.id
        """
    ).fetchall()

    now = datetime.now(
        timezone.utc
    ).isoformat(timespec="seconds")

    total_fragments = 0
    classified = 0
    unclassified = 0

    inserted = 0
    updated = 0
    unchanged = 0

    family_counts = {}

    with conn:
        for (
            report_id,
            machine_id,
            report_date,
            mechanical,
            fabrication,
            general,
        ) in rows:

            fields = (
                ("MECHANICAL", mechanical),
                ("FABRICATION", fabrication),
                ("GENERAL", general),
            )

            for field_name, text in fields:

                fragments = split_fragments(text)

                for fragment_index, fragment in enumerate(
                    fragments,
                    start=1,
                ):
                    total_fragments += 1

                    issue_family, issue_label = classify(
                        fragment
                    )

                    if issue_family:
                        status = "CLASSIFIED"
                        classified += 1

                        family_counts[issue_family] = (
                            family_counts.get(
                                issue_family,
                                0,
                            ) + 1
                        )

                    else:
                        status = "UNCLASSIFIED"
                        unclassified += 1

                    existing = conn.execute(
                        """
                        SELECT
                            raw_fragment,
                            issue_family,
                            issue_label,
                            classification_status,
                            parser_version

                        FROM issue_mentions

                        WHERE
                            daily_fault_report_id = ?
                            AND field_name = ?
                            AND fragment_index = ?
                        """,
                        (
                            report_id,
                            field_name,
                            fragment_index,
                        ),
                    ).fetchone()

                    new_values = (
                        fragment,
                        issue_family,
                        issue_label,
                        status,
                        PARSER_VERSION,
                    )

                    if existing is None:

                        conn.execute(
                            """
                            INSERT INTO issue_mentions (
                                daily_fault_report_id,
                                machine_id,
                                report_date,
                                field_name,
                                fragment_index,
                                raw_fragment,
                                issue_family,
                                issue_label,
                                classification_status,
                                parser_version,
                                classified_at
                            )
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                            """,
                            (
                                report_id,
                                machine_id,
                                report_date,
                                field_name,
                                fragment_index,
                                fragment,
                                issue_family,
                                issue_label,
                                status,
                                PARSER_VERSION,
                                now,
                            ),
                        )

                        inserted += 1

                    elif existing == new_values:

                        unchanged += 1

                    else:

                        # Derived data:
                        # اگر Ruleها در آینده بهتر شدند،
                        # Raw تغییر نمی‌کند و فقط Classification
                        # دوباره ساخته می‌شود.
                        conn.execute(
                            """
                            UPDATE issue_mentions

                            SET
                                machine_id = ?,
                                report_date = ?,
                                raw_fragment = ?,
                                issue_family = ?,
                                issue_label = ?,
                                classification_status = ?,
                                parser_version = ?,
                                classified_at = ?

                            WHERE
                                daily_fault_report_id = ?
                                AND field_name = ?
                                AND fragment_index = ?
                            """,
                            (
                                machine_id,
                                report_date,
                                fragment,
                                issue_family,
                                issue_label,
                                status,
                                PARSER_VERSION,
                                now,
                                report_id,
                                field_name,
                                fragment_index,
                            ),
                        )

                        updated += 1

    print()
    print("ISSUE MENTIONS COMPLETE")
    print(f"Reports scanned: {len(rows)}")
    print(f"Fragments: {total_fragments}")
    print(f"Classified: {classified}")
    print(f"Unclassified: {unclassified}")
    print(f"Inserted: {inserted}")
    print(f"Updated: {updated}")
    print(f"Unchanged: {unchanged}")

    print()
    print("CLASSIFIED FAMILIES")

    for family in sorted(
        family_counts,
        key=family_counts.get,
        reverse=True,
    ):
        print(
            f"  {family}: "
            f"{family_counts[family]}"
        )

    print()
    print("HD714 - LAST 14 DAYS")

    hd714 = conn.execute(
        """
        SELECT id
        FROM machines
        WHERE canonical_code = 'HD714'
        """
    ).fetchone()

    if hd714:

        rows = conn.execute(
            """
            SELECT
                issue_family,
                issue_label,
                COUNT(DISTINCT report_date),
                MIN(report_date),
                MAX(report_date)

            FROM issue_mentions

            WHERE
                machine_id = ?
                AND issue_family IS NOT NULL
                AND report_date IN (
                    SELECT DISTINCT report_date
                    FROM issue_mentions
                    WHERE machine_id = ?
                    ORDER BY report_date DESC
                    LIMIT 14
                )

            GROUP BY
                issue_family,
                issue_label

            ORDER BY
                COUNT(DISTINCT report_date) DESC
            """,
            (
                hd714[0],
                hd714[0],
            ),
        ).fetchall()

        for row in rows:
            print(
                f"{row[0]} | "
                f"days={row[2]} | "
                f"{row[3]} -> {row[4]} | "
                f"{row[1]}"
            )

    print()
    print("UNCLASSIFIED SAMPLE")

    rows = conn.execute(
        """
        SELECT
            d.report_date,
            m.canonical_code,
            i.field_name,
            i.raw_fragment

        FROM issue_mentions i

        JOIN machines m
            ON m.id = i.machine_id

        JOIN daily_fault_reports d
            ON d.id = i.daily_fault_report_id

        WHERE i.classification_status = 'UNCLASSIFIED'

        ORDER BY d.report_date DESC

        LIMIT 20
        """
    ).fetchall()

    for row in rows:
        print(
            f"{row[0]} | "
            f"{row[1]} | "
            f"{row[2]} | "
            f"{row[3]}"
        )

    conn.close()


if __name__ == "__main__":
    main()