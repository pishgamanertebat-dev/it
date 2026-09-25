"""Bounded, read-only PDF evidence probe for a routed manual section map."""
from __future__ import annotations

import argparse
import json
from pathlib import Path
import pymupdf


def find_section(sections, key):
    """Resolve a section key, honoring an explicit parent/child path."""
    parts = key.split("/")
    if len(parts) > 1:
        node = sections
        for index, part in enumerate(parts):
            node = node.get(part) if isinstance(node, dict) else None
            if node is None:
                return None
            if index < len(parts) - 1:
                node = node.get("subsections", {})
        return node if isinstance(node, dict) and "pdf_start" in node else None
    if key in sections and isinstance(sections[key], dict):
        hit = sections[key]
        if "pdf_start" in hit and "pdf_end" in hit:
            return hit
    for value in sections.values():
        if isinstance(value, dict):
            hit = find_section(value.get("subsections", {}), key)
            if hit is not None:
                return hit
    return None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("section_map", type=Path)
    ap.add_argument("terms", help="case-insensitive terms separated by |")
    ap.add_argument("sections", nargs="+")
    ap.add_argument("--top", type=int, default=3)
    ap.add_argument("--page-chars", type=int, default=4200)
    args = ap.parse_args()
    args.top = min(max(args.top, 1), 3)
    args.page_chars = min(max(args.page_chars, 500), 4200)
    metadata = json.loads(args.section_map.read_text(encoding="utf-8"))
    pdf_path = Path(metadata["manual"]).resolve()
    if not pdf_path.is_file():
        ap.error("Manual PDF is missing")
    sections = []
    for requested in args.sections:
        name = requested.rsplit("/", 1)[-1]
        section = find_section(metadata["sections"], requested)
        if section is None:
            ap.error(f"Unknown section: {name}")
        sections.append((name, int(section["pdf_start"]), int(section["pdf_end"])))
    terms = [term.strip().casefold() for term in args.terms.split("|") if term.strip()]
    if not terms:
        ap.error("At least one search term is required")
    doc = pymupdf.open(pdf_path)
    seen = set()
    hits = []
    for name, start, end in sections:
        for page_no in range(max(start, 1), min(end, len(doc)) + 1):
            if page_no in seen:
                continue
            seen.add(page_no)
            raw = doc[page_no - 1].get_text("text")
            folded = raw.casefold()
            matched = [term for term in terms if term in folded]
            if not matched:
                continue
            score = 10 * len(matched) + min(
                sum(folded.count(term) for term in matched), 20
            )
            hits.append((score, page_no, name, matched, raw))
    hits.sort(key=lambda row: (-row[0], row[1]))
    selected = []
    for name, _, _ in sections:
        first = next((hit for hit in hits if hit[2] == name), None)
        if first is not None and first not in selected and len(selected) < args.top:
            selected.append(first)
    selected.extend(hit for hit in hits if hit not in selected)
    selected = selected[:args.top]
    print(json.dumps({
        "manual": str(pdf_path),
        "searched_sections": [
            {"name": name, "pdf_start": start, "pdf_end": end}
            for name, start, end in sections
        ],
        "searched_pages": len(seen),
        "matching_pages": len(hits),
        "top_page_index": [
            {"pdf_page": page, "section": section, "score": score,
             "matched_terms": matched}
            for score, page, section, matched, _ in hits[:12]
        ],
        "page_text": [
            {"pdf_page": page, "section": section,
             "text": raw[:args.page_chars],
             "truncated": len(raw) > args.page_chars}
            for score, page, section, matched, raw in selected
        ],
    }, ensure_ascii=False))
    doc.close()


if __name__ == "__main__":
    main()
