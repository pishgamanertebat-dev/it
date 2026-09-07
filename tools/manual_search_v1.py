import json
import sys
from pathlib import Path

import pymupdf


def find_section(obj, wanted_key):
    if not isinstance(obj, dict):
        return None

    for key, value in obj.items():
        if key == wanted_key and isinstance(value, dict):
            if "pdf_start" in value and "pdf_end" in value:
                return value

        if isinstance(value, dict):
            found = find_section(value, wanted_key)
            if found:
                return found

    return None


def main():
    if len(sys.argv) < 4:
        print(
            "Usage:\n"
            "manual_search.py <manual_sections.json> <query> <section> [section ...]"
        )
        raise SystemExit(2)

    map_path = Path(sys.argv[1])
    query = sys.argv[2]
    section_names = sys.argv[3:]

    data = json.loads(map_path.read_text(encoding="utf-8"))

    pdf_path = Path(data["manual"])

    ranges = []

    for section_name in section_names:
        section = find_section(data["sections"], section_name)

        if section is None:
            print(f"SECTION_NOT_FOUND: {section_name}")
            continue

        ranges.append(
            (
                section_name,
                int(section["pdf_start"]),
                int(section["pdf_end"]),
            )
        )

    if not ranges:
        print("NO_VALID_SECTIONS")
        raise SystemExit(3)

    doc = pymupdf.open(pdf_path)

    query_lower = query.lower()
    hits = []

    searched_pages = set()

    for section_name, start, end in ranges:
        print(f"SECTION: {section_name} | PDF {start}-{end}")

        for pdf_page in range(start, end + 1):
            if pdf_page in searched_pages:
                continue

            searched_pages.add(pdf_page)

            text = doc[pdf_page - 1].get_text("text")

            if query_lower in text.lower():
                hits.append((pdf_page, section_name, text))

    print()
    print(f"SEARCHED_PAGES: {len(searched_pages)}")
    print(f"HITS: {len(hits)}")
    print()

    for pdf_page, section_name, text in hits:
        print("=" * 80)
        print(f"PDF_PAGE: {pdf_page}")
        print(f"SECTION: {section_name}")
        print("=" * 80)

        # Return enough real Manual text for the agent to inspect
        # without requiring another extraction call.
        print(text[:8000])
        print()

    doc.close()


if __name__ == "__main__":
    main()