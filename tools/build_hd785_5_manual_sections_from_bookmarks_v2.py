from pathlib import Path
import json
import re
import fitz

PDF = Path(r"E:\KomatsoAI\HD785-5\HD785-5 shop manual.pdf")
OUT = Path(r"E:\KomatsoAI\HD785-5\manual_sections.bookmarks_candidate.json")

EXPECTED = [
    ("general", "01 GENERAL"),
    ("structure_function", "10 STRUCTURE AND FUNCTION"),
    ("testing_adjusting", "20 TESTING AND ADJUSTING"),
    ("disassembly_assembly", "30 DISASSEMBLY AND ASSEMBLY"),
    ("maintenance_standard", "40 MAINTENANCE STANDARD"),
    ("others", "90 OTHERS"),
]

def norm(s: str) -> str:
    return " ".join((s or "").upper().split())

def slugify(title: str) -> str:
    s = title.lower()
    s = re.sub(r"\([^)]*\)", " ", s)
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")
    return s or "item"

def unique_key(base: str, used: set[str]) -> str:
    key = base
    n = 2
    while key in used:
        key = f"{base}_{n}"
        n += 1
    used.add(key)
    return key

if not PDF.is_file():
    raise SystemExit(f"PDF not found: {PDF}")

with fitz.open(PDF) as doc:
    if doc.page_count != 1392:
        raise SystemExit(f"Unexpected PDF page count: {doc.page_count} (expected 1392)")

    raw_toc = doc.get_toc()
    if not raw_toc:
        raise SystemExit("No embedded PDF bookmarks found.")

    toc = [
        {"level": int(level), "title": str(title).strip(), "page": int(page)}
        for level, title, page in raw_toc
        if int(page) > 0
    ]

    # Locate the six main Shop Manual sections by exact embedded bookmark title.
    top_hits = []
    for sec_key, expected_title in EXPECTED:
        matches = [
            (i, row)
            for i, row in enumerate(toc)
            if row["level"] == 1 and norm(row["title"]) == norm(expected_title)
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"Expected exactly one top-level bookmark {expected_title!r}; found {len(matches)}"
            )
        idx, row = matches[0]
        top_hits.append((sec_key, expected_title, idx, row["page"]))

    starts = [x[3] for x in top_hits]
    expected_starts = [29, 37, 319, 1076, 1326, 1367]
    if starts != expected_starts:
        raise SystemExit(f"Unexpected section starts: {starts}; expected {expected_starts}")

    sections = {}
    stats = {}
    overlap_adjustments = []

    for sec_i, (sec_key, sec_title, toc_idx, sec_start) in enumerate(top_hits):
        next_sec_start = (
            top_hits[sec_i + 1][3]
            if sec_i + 1 < len(top_hits)
            else doc.page_count + 1
        )
        sec_end = next_sec_start - 1

        # Collect bookmark rows structurally belonging to this level-1 section.
        rows = []
        for row in toc[toc_idx + 1:]:
            if row["level"] == 1:
                break
            if sec_start <= row["page"] <= sec_end:
                rows.append(dict(row))

        l2_positions = [i for i, r in enumerate(rows) if r["level"] == 2]
        first_l2_page = min((rows[i]["page"] for i in l2_positions), default=sec_start)

        section = {
            "pdf_start": sec_start,
            "pdf_end": sec_end,
            "contents_start": sec_start,
            "contents_end": max(sec_start, first_l2_page - 1),
            "title": sec_title,
            "subsections": {}
        }

        used_l2 = set()
        l3_total = 0
        deep_total = 0

        for pos_idx, row_idx in enumerate(l2_positions):
            row = rows[row_idx]
            next_row_idx = (
                l2_positions[pos_idx + 1]
                if pos_idx + 1 < len(l2_positions)
                else len(rows)
            )
            next_l2_page = (
                rows[next_row_idx]["page"]
                if next_row_idx < len(rows)
                else sec_end + 1
            )

            segment = rows[row_idx + 1:next_row_idx]
            l3_rows = [(j, r) for j, r in enumerate(segment) if r["level"] == 3]

            nominal_end = max(row["page"], min(sec_end, next_l2_page - 1))

            # Some embedded bookmarks place a child on the same destination page
            # as the next L2 sibling. That makes a strict "next L2 - 1" parent end
            # too short. Extend only enough to contain its actual child bookmark(s).
            max_child_page = max((r["page"] for _, r in l3_rows), default=row["page"])
            parent_end = max(nominal_end, max_child_page)
            parent_end = min(parent_end, sec_end)

            if parent_end != nominal_end:
                overlap_adjustments.append({
                    "section": sec_key,
                    "title": row["title"],
                    "pdf_start": row["page"],
                    "nominal_end": nominal_end,
                    "adjusted_end": parent_end,
                    "next_l2_page": next_l2_page,
                })

            l2_key = unique_key(slugify(row["title"]), used_l2)
            l2_node = {
                "pdf_start": row["page"],
                "pdf_end": parent_end,
                "title": row["title"]
            }

            # Build L3 children from bookmark order.
            if l3_rows:
                l2_node["subsections"] = {}
                used_l3 = set()
                l3_total += len(l3_rows)

                for child_idx, (seg_idx, child) in enumerate(l3_rows):
                    child_start = child["page"]

                    if child_idx + 1 < len(l3_rows):
                        next_child_start = l3_rows[child_idx + 1][1]["page"]
                        child_end = max(child_start, next_child_start - 1)
                    else:
                        child_end = parent_end

                    # Never let a child escape its parent, even if bookmark destinations
                    # are slightly out of order.
                    child_end = min(max(child_start, child_end), parent_end)

                    ckey = unique_key(slugify(child["title"]), used_l3)
                    child_node = {
                        "pdf_start": child_start,
                        "pdf_end": child_end,
                        "title": child["title"]
                    }

                    # Fold level 4+ bookmarks following this L3 bookmark, up to the next L3.
                    next_seg_idx = (
                        l3_rows[child_idx + 1][0]
                        if child_idx + 1 < len(l3_rows)
                        else len(segment)
                    )
                    deep_titles = [
                        r["title"]
                        for r in segment[seg_idx + 1:next_seg_idx]
                        if r["level"] >= 4
                    ]
                    if deep_titles:
                        child_node["topics"] = list(dict.fromkeys(deep_titles))
                        deep_total += len(deep_titles)

                    l2_node["subsections"][ckey] = child_node

            # If this L2 has no L3 children, fold all deeper bookmarks in its segment as topics.
            else:
                deep_titles = [r["title"] for r in segment if r["level"] >= 3]
                if deep_titles:
                    l2_node["topics"] = list(dict.fromkeys(deep_titles))
                    deep_total += len(deep_titles)

            section["subsections"][l2_key] = l2_node

        sections[sec_key] = section
        stats[sec_key] = {
            "pdf_start": sec_start,
            "pdf_end": sec_end,
            "bookmark_rows_total": len(rows),
            "level2_nodes": len(l2_positions),
            "level3_nodes": l3_total,
            "deep_topics": deep_total,
        }

    data = {
        "manual": str(PDF),
        "models": ["HD785-5"],
        "pdf_pages": doc.page_count,
        "bookmark_count": len(raw_toc),
        "routing_source": "Embedded PDF bookmarks",
        "note": (
            "Routing metadata only. All technical facts, values, procedures, pressures, "
            "voltages, resistance values, torque values, part numbers and safety instructions "
            "must be verified from the actual HD785-5 Shop Manual."
        ),
        "sections": {
            "front_matter": {
                "pdf_start": 1,
                "pdf_end": 28,
                "title": "Cover / master contents / front matter"
            },
            **sections
        }
    }

    # Validation: section bounds and child containment.
    problems = []

    for sec_key, sec in sections.items():
        if not (1 <= sec["pdf_start"] <= sec["pdf_end"] <= doc.page_count):
            problems.append(f"Bad section range: {sec_key}")

        for sub_key, sub in sec["subsections"].items():
            if not (sec["pdf_start"] <= sub["pdf_start"] <= sub["pdf_end"] <= sec["pdf_end"]):
                problems.append(f"Bad L2 range: {sec_key}.{sub_key}")

            for child_key, child in sub.get("subsections", {}).items():
                if not (sub["pdf_start"] <= child["pdf_start"] <= child["pdf_end"] <= sub["pdf_end"]):
                    problems.append(f"Bad L3 range: {sec_key}.{sub_key}.{child_key}")

    if problems:
        print("VALIDATION: FAIL")
        for p in problems[:50]:
            print(" -", p)
        raise SystemExit(f"Validation failed with {len(problems)} problem(s).")

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("HD785-5 BOOKMARK ROUTING BUILD V2")
    print(f"PDF pages            : {doc.page_count}")
    print(f"Bookmarks            : {len(raw_toc)}")
    print(f"Boundary adjustments : {len(overlap_adjustments)}")
    print(f"Output               : {OUT}")
    print()
    print("SECTION SUMMARY")
    for sec_key, _ in EXPECTED:
        s = stats[sec_key]
        print(
            f"{sec_key:22} | PDF {s['pdf_start']:4}-{s['pdf_end']:4} | "
            f"L2={s['level2_nodes']:3} | L3={s['level3_nodes']:3} | "
            f"topics={s['deep_topics']:3}"
        )

    if overlap_adjustments:
        print()
        print("BOOKMARK BOUNDARY ADJUSTMENTS")
        for x in overlap_adjustments[:30]:
            print(
                f"{x['section']:22} | {x['title']} | "
                f"{x['nominal_end']} -> {x['adjusted_end']} "
                f"(next L2 starts {x['next_l2_page']})"
            )
        if len(overlap_adjustments) > 30:
            print(f"... {len(overlap_adjustments) - 30} more")

    print()
    print("VALIDATION: PASS")
    print("STATUS: CLEAN")
