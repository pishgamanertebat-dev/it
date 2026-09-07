import json
import sys
from pathlib import Path

import pymupdf


MAX_RESULTS = 5
MAX_TEXT_CHARS = 7000


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


def parse_terms(raw_query):
    terms = [
        term.strip().lower()
        for term in raw_query.split("|")
        if term.strip()
    ]

    if not terms:
        return [raw_query.strip().lower()]

    return terms


def score_page(text, terms):
    text_lower = text.lower()

    matched_terms = []
    total_occurrences = 0

    for term in terms:
        count = text_lower.count(term)

        if count:
            matched_terms.append(term)
            total_occurrences += count

    if not matched_terms:
        return 0, []

    # Matching several different concepts matters more than
    # repeating one generic word many times.
    score = (len(matched_terms) * 10) + min(total_occurrences, 20)

    return score, matched_terms


def main():
    if len(sys.argv) < 4:
        print(
            "Usage:\n"
            "manual_search.py <manual_sections.json> "
            "<term1|term2|term3> <section> [section ...]"
        )
        raise SystemExit(2)

    map_path = Path(sys.argv[1])
    raw_query = sys.argv[2]
    section_names = sys.argv[3:]

    terms = parse_terms(raw_query)

    data = json.loads(
        map_path.read_text(encoding="utf-8")
    )

    pdf_path = Path(data["manual"])

    ranges = []

    for section_name in section_names:
        section = find_section(
            data["sections"],
            section_name
        )

        if section is None:
            print(
                f"SECTION_NOT_FOUND: {section_name}"
            )
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

    results = []
    searched_pages = set()

    for section_name, start, end in ranges:
        print(
            f"SECTION: {section_name} | "
            f"PDF {start}-{end}"
        )

        for pdf_page in range(start, end + 1):
            if pdf_page in searched_pages:
                continue

            searched_pages.add(pdf_page)

            text = doc[pdf_page - 1].get_text(
                "text"
            )

            score, matched_terms = score_page(
                text,
                terms
            )

            if score > 0:
                results.append(
                    {
                        "page": pdf_page,
                        "section": section_name,
                        "score": score,
                        "matched_terms": matched_terms,
                        "text": text,
                    }
                )

    doc.close()

    results.sort(
        key=lambda item: item["score"],
        reverse=True,
    )

    top_results = results[:MAX_RESULTS]

    print()
    print(
        "TERMS:",
        ", ".join(terms)
    )
    print(
        f"SEARCHED_PAGES: {len(searched_pages)}"
    )
    print(
        f"MATCHING_PAGES: {len(results)}"
    )
    print(
        f"RETURNED_TOP_RESULTS: "
        f"{len(top_results)}"
    )
    print()

    for item in top_results:
        print("=" * 80)
        print(
            f"PDF_PAGE: {item['page']}"
        )
        print(
            f"SECTION: {item['section']}"
        )
        print(
            f"SCORE: {item['score']}"
        )
        print(
            "MATCHED_TERMS:",
            ", ".join(item["matched_terms"])
        )
        print("=" * 80)

        print(
            item["text"][:MAX_TEXT_CHARS]
        )
        print()


if __name__ == "__main__":
    main()