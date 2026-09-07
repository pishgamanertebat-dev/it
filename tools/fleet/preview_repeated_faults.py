from __future__ import annotations

import argparse
import re
import sqlite3
from collections import defaultdict
from pathlib import Path


DB_PATH = Path(
    r"E:\KomatsoAI\data\fleet\db\fleet_ops.db"
)


def clean(text):
    if text is None:
        return ""

    text = str(text)

    return (
        text
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

    # --------------------------------------------
    # Retarder
    # --------------------------------------------

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

    # --------------------------------------------
    # Weak brake
    # --------------------------------------------

    if (
        "ترمز" in text
        and "ضعیف" in text
    ):
        return (
            "BRAKE_WEAK",
            "ضعف ترمز",
        )

    # --------------------------------------------
    # Transmission / shift shock
    # --------------------------------------------

    if "دنده" in text:
        shift_words = (
            "تقه",
            "ضربه",
            "دیر",
            "تعویض",
        )

        if any(
            word in text
            for word in shift_words
        ):
            return (
                "TRANSMISSION_SHIFT_SHOCK",
                "ضربه/تقه در تعویض دنده",
            )

    # --------------------------------------------
    # Oil leak
    # --------------------------------------------

    oil_leak_words = (
        "روغن ریزی",
        "روغنریزی",
        "نشتی روغن",
    )

    if any(
        word in text
        for word in oil_leak_words
    ):
        return (
            "OIL_LEAK",
            "روغن‌ریزی / نشتی روغن",
        )

    # --------------------------------------------
    # Undercarriage
    # --------------------------------------------

    if "زیربندی" in text:
        return (
            "UNDERCARRIAGE_ISSUE",
            "ایراد زیربندی",
        )

    return None


def resolve_machine(conn, code):
    code = code.strip()

    rows = conn.execute(
        """
        SELECT DISTINCT
            m.id,
            m.canonical_code,
            m.model_key
        FROM machine_aliases a

        JOIN machines m
            ON m.id = a.machine_id

        WHERE
            UPPER(a.alias_code) = UPPER(?)
            AND a.verified = 1
        """,
        (code,),
    ).fetchall()

    if len(rows) == 1:
        return rows[0]

    rows = conn.execute(
        """
        SELECT
            id,
            canonical_code,
            model_key
        FROM machines
        WHERE UPPER(canonical_code) = UPPER(?)
        """,
        (code,),
    ).fetchall()

    if len(rows) == 1:
        return rows[0]

    raise SystemExit(
        f"ERROR: machine not found or ambiguous: {code}"
    )


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument(
        "machine",
    )

    parser.add_argument(
        "--limit",
        type=int,
        default=14,
    )

    args = parser.parse_args()

    if not DB_PATH.exists():
        raise SystemExit(
            f"ERROR: DB not found: {DB_PATH}"
        )

    conn = sqlite3.connect(DB_PATH)

    machine_id, canonical, model = resolve_machine(
        conn,
        args.machine,
    )

    rows = conn.execute(
        """
        SELECT
            d.report_date,
            d.mechanical_raw,
            d.fabrication_raw,
            d.general_raw
        FROM daily_fault_reports d

        JOIN daily_fault_report_identity i
            ON i.daily_fault_report_id = d.id

        WHERE i.machine_id = ?

        ORDER BY
            d.report_date DESC,
            d.id DESC

        LIMIT ?
        """,
        (
            machine_id,
            args.limit,
        ),
    ).fetchall()

    fleet_latest_date = conn.execute(
        """
        SELECT MAX(report_date)
        FROM daily_fault_reports
        """
    ).fetchone()[0]

    issue_dates = defaultdict(set)
    issue_labels = {}
    issue_samples = defaultdict(list)

    unclassified = []

    for (
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

            for fragment in split_fragments(text):

                result = classify(fragment)

                if result:
                    issue_code, label = result

                    issue_dates[
                        issue_code
                    ].add(report_date)

                    issue_labels[
                        issue_code
                    ] = label

                    if (
                        len(
                            issue_samples[
                                issue_code
                            ]
                        ) < 3
                    ):
                        issue_samples[
                            issue_code
                        ].append(
                            (
                                report_date,
                                fragment,
                            )
                        )

                else:
                    if len(unclassified) < 20:
                        unclassified.append(
                            (
                                report_date,
                                field_name,
                                fragment,
                            )
                        )

    print("REPEATED FAULT PREVIEW")
    print("=" * 70)
    print(f"Machine: {canonical}")
    print(f"Model: {model}")
    print(f"Analyzed reports: {len(rows)}")
    print(
        f"Fleet latest date: "
        f"{fleet_latest_date}"
    )

    print()
    print("DETECTED ISSUE FAMILIES")
    print("=" * 70)

    sorted_issues = sorted(
        issue_dates,
        key=lambda code: (
            len(issue_dates[code]),
            max(issue_dates[code]),
        ),
        reverse=True,
    )

    if not sorted_issues:
        print("None")

    for issue_code in sorted_issues:

        dates = sorted(
            issue_dates[issue_code]
        )

        repeated = (
            len(dates) >= 2
        )

        current = (
            fleet_latest_date
            in issue_dates[issue_code]
        )

        print()
        print(
            f"{issue_code}"
        )

        print(
            f"Label: "
            f"{issue_labels[issue_code]}"
        )

        print(
            f"Days detected: {len(dates)}"
        )

        print(
            f"First in window: {dates[0]}"
        )

        print(
            f"Last in window: {dates[-1]}"
        )

        print(
            f"Repeated: {repeated}"
        )

        print(
            f"Present on latest fleet date: "
            f"{current}"
        )

        print("Samples:")

        for sample_date, sample in (
            issue_samples[issue_code]
        ):
            print(
                f"  {sample_date}: {sample}"
            )

    print()
    print("UNCLASSIFIED FRAGMENT SAMPLE")
    print("=" * 70)

    if not unclassified:
        print("None")

    for (
        report_date,
        field_name,
        fragment,
    ) in unclassified:

        print(
            f"{report_date} | "
            f"{field_name} | "
            f"{fragment}"
        )

    conn.close()


if __name__ == "__main__":
    main()