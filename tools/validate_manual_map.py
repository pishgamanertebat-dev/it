import json
import sys
from pathlib import Path

import pymupdf


def validate_children(parent_name, parent, errors):
    children = parent.get("subsections")

    if not isinstance(children, dict):
        return

    parent_start = parent["pdf_start"]
    parent_end = parent["pdf_end"]

    ranges = []

    for name, item in children.items():
        start = item.get("pdf_start")
        end = item.get("pdf_end")

        if start is None or end is None:
            errors.append(
                f"{parent_name}.{name}: missing pdf_start/pdf_end"
            )
            continue

        if start > end:
            errors.append(
                f"{parent_name}.{name}: invalid range {start}-{end}"
            )

        if start < parent_start or end > parent_end:
            errors.append(
                f"{parent_name}.{name}: outside parent range"
            )

        ranges.append((start, end, name))

    ranges.sort()

    for i in range(1, len(ranges)):
        prev_start, prev_end, prev_name = ranges[i - 1]
        start, end, name = ranges[i]

        if start <= prev_end:
            errors.append(
                f"{parent_name}: overlap "
                f"{prev_name} {prev_start}-{prev_end} "
                f"with {name} {start}-{end}"
            )

        elif start != prev_end + 1:
            errors.append(
                f"{parent_name}: gap between "
                f"{prev_name} ({prev_end}) "
                f"and {name} ({start})"
            )


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: validate_manual_map.py "
            "<manual_sections.json>"
        )
        raise SystemExit(2)

    map_path = Path(sys.argv[1])

    data = json.loads(
        map_path.read_text(encoding="utf-8")
    )

    manual_path = Path(data["manual"])
    expected_pages = int(data["pdf_pages"])

    print("MAP:")
    print(map_path)

    print()
    print("MANUAL:")
    print(manual_path)

    if not manual_path.exists():
        print()
        print("ERROR: Manual PDF does not exist.")
        raise SystemExit(3)

    doc = pymupdf.open(manual_path)
    actual_pages = doc.page_count
    doc.close()

    print()
    print(f"Expected PDF pages: {expected_pages}")
    print(f"Actual PDF pages:   {actual_pages}")

    errors = []

    if actual_pages != expected_pages:
        errors.append(
            f"PDF page count mismatch: "
            f"{actual_pages} != {expected_pages}"
        )

    sections = data["sections"]

    top_ranges = []

    for name, item in sections.items():
        start = item["pdf_start"]
        end = item["pdf_end"]

        top_ranges.append(
            (start, end, name)
        )

        validate_children(
            name,
            item,
            errors
        )

    top_ranges.sort()

    print()
    print("TOP LEVEL SECTIONS")
    print("=" * 70)

    for start, end, name in top_ranges:
        print(
            f"{name:<28} "
            f"{start:>4}-{end:<4} "
            f"({end - start + 1} pages)"
        )

    if top_ranges[0][0] != 1:
        errors.append(
            f"Top-level map does not start at page 1"
        )

    if top_ranges[-1][1] != expected_pages:
        errors.append(
            f"Top-level map does not end at "
            f"page {expected_pages}"
        )

    for i in range(1, len(top_ranges)):
        p_start, p_end, p_name = top_ranges[i - 1]
        start, end, name = top_ranges[i]

        if start <= p_end:
            errors.append(
                f"Top-level overlap: "
                f"{p_name} {p_start}-{p_end} "
                f"with {name} {start}-{end}"
            )

        elif start != p_end + 1:
            errors.append(
                f"Top-level gap: "
                f"{p_name} ends {p_end}, "
                f"{name} starts {start}"
            )

    print()
    print("=" * 70)

    if errors:
        print("VALIDATION FAILED")
        print()

        for error in errors:
            print("ERROR:", error)

        raise SystemExit(1)

    print("PDF PAGE COUNT: OK")
    print("TOP LEVEL COVERAGE: OK")
    print("NO TOP LEVEL GAPS: OK")
    print("NO TOP LEVEL OVERLAPS: OK")
    print("SUBSECTION RANGES: OK")
    print()
    print("MANUAL MAP VALIDATION: PASS")


if __name__ == "__main__":
    main()