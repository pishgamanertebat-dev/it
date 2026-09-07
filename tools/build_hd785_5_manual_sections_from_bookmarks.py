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
    i = 2
    while key in used:
        key = f"{base}_{i}"
        i += 1
    used.add(key)
    return key

if not PDF.is_file():
    raise SystemExit(f"PDF not found: {PDF}")

with fitz.open(PDF) as doc:
    toc_raw = doc.get_toc()
    if doc.page_count != 1392:
        raise SystemExit(f"Unexpected page count: {doc.page_count} (expected 1392)")
    if not toc_raw:
        raise SystemExit("PDF has no bookmarks/TOC.")

    toc = [
        {"level": int(level), "title": str(title).strip(), "page": int(page)}
        for level, title, page in toc_raw
        if int(page) > 0
    ]

    top_hits = []
    for key, expected_title in EXPECTED:
        matches = [
            (i, row)
            for i, row in enumerate(toc)
            if row["level"] == 1 and norm(row["title"]) == norm(expected_title)
        ]
        if len(matches) != 1:
            raise SystemExit(
                f"Top-level bookmark mismatch for {expected_title!r}: found {len(matches)} matches"
            )
        idx, row = matches[0]
        top_hits.append((key, expected_title, idx, row["page"]))

    pages = [x[3] for x in top_hits]
    if pages != sorted(pages):
        raise SystemExit(f"Top-level section pages are not increasing: {pages}")

    sections = {}
    stats = {}

    for sec_i, (sec_key, expected_title, toc_idx, sec_start) in enumerate(top_hits):
        next_sec_start = top_hits[sec_i + 1][3] if sec_i + 1 < len(top_hits) else doc.page_count + 1
        sec_end = next_sec_start - 1

        rows = []
        for row in toc[toc_idx + 1:]:
            if row["page"] > sec_end:
                break
            if row["level"] == 1:
                break
            if sec_start <= row["page"] <= sec_end:
                rows.append(dict(row))

        routing_rows = [r for r in rows if r["level"] in (2, 3)]
        first_content = min((r["page"] for r in routing_rows), default=sec_start)

        sec = {
            "pdf_start": sec_start,
            "pdf_end": sec_end,
            "contents_start": sec_start,
            "contents_end": max(sec_start, first_content - 1),
            "title": expected_title,
            "subsections": {}
        }

        used_l2 = set()
        l2_nodes = []

        for i, row in enumerate(routing_rows):
            if row["level"] != 2:
                continue

            end = sec_end
            for later in routing_rows[i + 1:]:
                if later["level"] == 2:
                    end = max(row["page"], later["page"] - 1)
                    break

            key = unique_key(slugify(row["title"]), used_l2)
            node = {
                "pdf_start": row["page"],
                "pdf_end": end,
                "title": row["title"]
            }
            sec["subsections"][key] = node
            l2_nodes.append((i, key, node))

        for l2_pos, (i, l2_key, l2_node) in enumerate(l2_nodes):
            next_l2_i = l2_nodes[l2_pos + 1][0] if l2_pos + 1 < len(l2_nodes) else len(routing_rows)
            child_rows = [
                (j, routing_rows[j])
                for j in range(i + 1, next_l2_i)
                if routing_rows[j]["level"] == 3
            ]

            if child_rows:
                l2_node["subsections"] = {}
                used_l3 = set()
                for child_pos, (j, row) in enumerate(child_rows):
                    if child_pos + 1 < len(child_rows):
                        nxt = child_rows[child_pos + 1][1]["page"]
                        end = max(row["page"], nxt - 1)
                    else:
                        end = l2_node["pdf_end"]

                    ckey = unique_key(slugify(row["title"]), used_l3)
                    l2_node["subsections"][ckey] = {
                        "pdf_start": row["page"],
                        "pdf_end": end,
                        "title": row["title"]
                    }

        flattened_targets = []
        for l2_node in sec["subsections"].values():
            flattened_targets.append((l2_node["pdf_start"], l2_node["pdf_end"], l2_node))
            for l3_node in l2_node.get("subsections", {}).values():
                flattened_targets.append((l3_node["pdf_start"], l3_node["pdf_end"], l3_node))

        deep_count = 0
        for row in rows:
            if row["level"] <= 3:
                continue
            deep_count += 1
            candidates = [
                (start, end, node)
                for start, end, node in flattened_targets
                if start <= row["page"] <= end
            ]
            if not candidates:
                continue
            start, end, node = min(candidates, key=lambda x: (x[1] - x[0], -x[0]))
            topics = node.setdefault("topics", [])
            if row["title"] not in topics:
                topics.append(row["title"])

        sections[sec_key] = sec
        stats[sec_key] = {
            "pdf_start": sec_start,
            "pdf_end": sec_end,
            "bookmark_rows_total": len(rows),
            "level2_nodes": len(sec["subsections"]),
            "level3_nodes": sum(len(x.get("subsections", {})) for x in sec["subsections"].values()),
            "deep_bookmarks_folded": deep_count,
        }

    data = {
        "manual": str(PDF),
        "models": ["HD785-5"],
        "pdf_pages": doc.page_count,
        "bookmark_count": len(toc_raw),
        "routing_source": "Embedded PDF bookmarks",
        "note": (
            "Routing metadata only. Technical facts, values, procedures, part numbers, "
            "pressures, voltages, resistance values, torque values and safety instructions "
            "must be verified from the actual HD785-5 Shop Manual."
        ),
        "sections": {
            "front_matter": {
                "pdf_start": 1,
                "pdf_end": top_hits[0][3] - 1,
                "title": "Cover / master contents / front matter"
            },
            **sections
        }
    }

    assert data["sections"]["front_matter"]["pdf_end"] == 28
    expected_starts = [29, 37, 319, 1076, 1326, 1367]
    actual_starts = [sections[k]["pdf_start"] for k, _ in EXPECTED]
    assert actual_starts == expected_starts, (actual_starts, expected_starts)

    for sec_key, sec in sections.items():
        assert 1 <= sec["pdf_start"] <= sec["pdf_end"] <= doc.page_count
        for sub in sec["subsections"].values():
            assert sec["pdf_start"] <= sub["pdf_start"] <= sub["pdf_end"] <= sec["pdf_end"]
            for child in sub.get("subsections", {}).values():
                assert sub["pdf_start"] <= child["pdf_start"] <= child["pdf_end"] <= sub["pdf_end"]

    OUT.write_text(json.dumps(data, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")

    print("HD785-5 BOOKMARK ROUTING BUILD")
    print(f"PDF pages      : {doc.page_count}")
    print(f"Bookmarks      : {len(toc_raw)}")
    print(f"Output         : {OUT}")
    print()
    print("SECTION SUMMARY")
    for sec_key, _ in EXPECTED:
        s = stats[sec_key]
        print(
            f"{sec_key:22} | PDF {s['pdf_start']:4}-{s['pdf_end']:4} | "
            f"L2={s['level2_nodes']:3} | L3={s['level3_nodes']:3} | "
            f"deep-folded={s['deep_bookmarks_folded']:3}"
        )
    print()
    print("VALIDATION: PASS")
    print("STATUS: CLEAN")
