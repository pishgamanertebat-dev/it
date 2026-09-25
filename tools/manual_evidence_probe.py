"""Bounded, read-only manual retrieval. Index routes; only PDF text is evidence."""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
MODELS = {
    "PC800-8R": "PC800", "PC850-8R": "PC800",
    "PC1250-8R": "PC1250-8R", "HD785-7": "HD785-7",
    "HD785-5": "HD785-5", "HD465-7R": "HD465-7R_HD605-7R",
    "HD605-7R": "HD465-7R_HD605-7R", "WA600-6": "WA600-6",
    "R330LC-9S": "R330LC-9S",
}
ALIASES = {
    "retarder": ("retarder", "rear brake", "retarder lever", "brake oil pressure", "brake", "retarder control"),
    "ریتاردر": ("retarder", "rear brake", "retarder lever", "brake oil pressure", "brake", "retarder control"),
    "ترمز": ("brake", "braking"), "brake": ("brake", "braking"),
    "هیدرولیک": ("hydraulic", "pressure"), "فشار": ("pressure",),
    "گیربکس": ("transmission", "power train"), "دنده": ("transmission", "shift"),
    "موتور": ("engine", "fuel"), "استارت": ("starting", "starter"),
    "برق": ("electrical", "circuit"), "سنسور": ("sensor",),
    "سیم": ("wiring", "electrical"), "پمپ": ("pump", "pressure"),
    "فرمان": ("steering",), "آکومولاتور": ("accumulator", "charge"),
    "باکت": ("bucket",), "بوم": ("boom",),
}
STOP = {"the", "and", "for", "with", "from", "does", "not", "work", "working",
        "failure", "fault", "problem", "system", "machine", "check", "کار", "نمی",
        "کند", "مشکل", "دستگاه", "خراب", "است", "را", "در", "به", "از", "با", "و"}

def nodes(sections, prefix=""):
    for key, value in sections.items():
        if not isinstance(value, dict):
            continue
        path = f"{prefix}/{key}" if prefix else key
        if "pdf_start" in value and "pdf_end" in value:
            yield path, value
        yield from nodes(value.get("subsections", {}), path)

def find_section(sections, key):
    found = [(path, item) for path, item in nodes(sections)
             if path == key or path.rsplit("/", 1)[-1] == key]
    return found[0][1] if len(found) == 1 else None

def search_terms(problem, component="", code=""):
    source = f"{problem} {component} {code}".casefold()
    terms = []
    for trigger, synonyms in ALIASES.items():
        if trigger in source:
            terms.extend(synonyms)
    terms.extend(word for word in re.findall(r"[a-z][a-z0-9-]{2,}", source)
                 if word not in STOP)
    if code:
        terms.insert(0, code.casefold())
    return list(dict.fromkeys(terms))[:14]

def route(sections, terms, problem, code="", limit=4):
    symptom = any(x in problem.casefold() for x in
                  ("not", "fail", "weak", "fault", "خراب", "ضعف", "نمی", "خطا"))
    ranked = []
    for path, item in nodes(sections):
        if not code and "failure_code" in path:
            continue
        if item.get("subsections"):
            continue
        label = " ".join((path.replace("_", " "), str(item.get("title", "")),
                          str(item.get("description", "")),
                          " ".join(map(str, item.get("topics", []))))).casefold()
        matches = [term for term in terms if term in label]
        score = sum(5 if " " in term else 3 for term in matches)
        if symptom and ("troubleshooting" in path or "testing_adjusting" in path):
            score += 2
        if code and "failure_code" in path:
            score += 3
        if not code and "failure_code" in path:
            score -= 2
        if "index" in path or "foreword" in path:
            score -= 8
        if score > 0:
            ranked.append((score, path, item))
    ranked.sort(key=lambda x: (-x[0], int(x[2]["pdf_end"]) - int(x[2]["pdf_start"]), x[1]))
    chosen = []
    code_sections = 0
    for _, path, item in ranked:
        if "failure_code" in path and not code:
            if code_sections:
                continue
            code_sections += 1
        chosen.append((path, item))
        if len(chosen) == limit:
            break
    return chosen

def excerpt(raw, terms, limit):
    if len(raw) <= limit:
        return raw, False
    lines = raw.splitlines()
    matches = [i for i, line in enumerate(lines)
               if any(term in line.casefold() for term in terms)]
    if not matches:
        return raw[:limit], True
    chosen = set()
    for i in matches[:8]:
        chosen.update(range(max(i - 3, 0), min(i + 11, len(lines))))
    chunks, previous = [], -2
    for i in sorted(chosen):
        if i > previous + 1:
            chunks.append("…")
        chunks.append(lines[i])
        previous = i
    result = "\n".join(chunks)
    return result[:limit], True

def probe(section_map, metadata, chosen, terms, top=5, page_chars=2400,
          allow_fallback=True, required_code=""):
    pdf = Path(metadata["manual"]).resolve()
    if not pdf.is_file() or pdf.suffix.lower() != ".pdf":
        raise ValueError("Indexed Shop Manual PDF is missing")
    if pdf.parent != section_map.resolve().parent:
        raise ValueError("Indexed manual must be inside the selected model folder")
    with pymupdf.open(pdf) as doc:
        expected = metadata.get("pdf_pages")
        if expected and int(expected) != len(doc):
            raise ValueError("PDF page count differs from manual_sections.json")
        seen, hits = set(), []
        def scan(ranges):
            for path, item in ranges:
                start, end = int(item["pdf_start"]), int(item["pdf_end"])
                if start < 1 or end < start or end > len(doc):
                    raise ValueError(f"Invalid page range: {path}")
                for page in range(start, end + 1):
                    if page in seen:
                        continue
                    seen.add(page)
                    raw = doc[page - 1].get_text("text")
                    folded = re.sub(r"\s+", " ", raw.casefold().replace("-\n", ""))
                    matched = [term for term in terms if term in folded]
                    if not matched:
                        continue
                    score = sum((16 if t == terms[0] else 8) if " " in t else (12 if t == terms[0] else 4) for t in matched)
                    score += min(sum(folded.count(t) for t in matched), 20)
                    head = folded[:350]
                    score += sum(3 * len(term) if " " in term else 8 for term in matched
                                 if term in head)
                    snippet, clipped = excerpt(raw, matched, page_chars)
                    hits.append({"pdf_page": page, "section": path, "score": score,
                                 "matched_terms": matched, "excerpt": snippet,
                                 "excerpt_truncated": clipped})
        scan(chosen)
        fallback = False
        if allow_fallback and ((len(hits) < 2 and len(seen) < 20) or not hits or (required_code and not any(required_code.casefold() in h["matched_terms"] for h in hits))):
            fallback = True
            scan([("full_manual_fallback", {"pdf_start": 1, "pdf_end": len(doc)})])
        hits.sort(key=lambda hit: (-hit["score"], hit["pdf_page"]))
        selected = []
        if required_code:
            selected.extend(h for h in hits
                            if required_code.casefold() in h["matched_terms"])
            selected = selected[:min(3, top)]
        for ordinal in range(3):
            for path, _ in chosen:
                candidates = [h for h in hits if h["section"] == path]
                if len(candidates) > ordinal and candidates[ordinal] not in selected and len(selected) < top:
                    selected.append(candidates[ordinal])
        selected.extend(h for h in hits if h not in selected)
        return {
            "manual": str(pdf), "source_type": "Shop Manual",
            "index_is_evidence": False,
            "searched_sections": [{"key": path,
                                   "title": item.get("title") or item.get("description"),
                                   "pdf_start": item["pdf_start"],
                                   "pdf_end": item["pdf_end"]} for path, item in chosen],
            "diagram_ranges": [
                {"key": path, "pdf_start": item["pdf_start"],
                 "pdf_end": item["pdf_end"],
                 "title": item.get("title") or item.get("description")}
                for path, item in nodes(metadata["sections"])
                if not item.get("subsections") and
                ("diagram" in path.casefold() or
                 "diagram" in str(item.get("title", "")).casefold())
            ][:6],
            "searched_pages": len(seen), "matching_pages": len(hits),
            "full_manual_fallback": fallback,
            "exact_fault_code_found": (any(required_code.casefold() in h["matched_terms"] for h in hits)
                                       if required_code else None),
            "top_page_index": [{k: v for k, v in hit.items()
                                if k not in ("excerpt", "excerpt_truncated")}
                               for hit in hits[:12]],
            "evidence": selected[:top],
            "note": "Excerpts may omit table columns or diagrams. Verify complete PDF pages before using exact values, pins or procedures.",
        }

def batch_pages(section_map, metadata, pages, page_chars=5000, render=False):
    """Bounded complete-page follow-up; optionally render all chosen pages."""
    pdf = Path(metadata["manual"]).resolve()
    if not pdf.is_file() or pdf.parent != section_map.resolve().parent:
        raise ValueError("Selected model Shop Manual PDF is missing or mismatched")
    unique = list(dict.fromkeys(pages))
    if not unique or len(unique) > 8:
        raise ValueError("Select between one and eight PDF pages")
    page_chars = min(page_chars, max(800, 20000 // len(unique)))
    with pymupdf.open(pdf) as doc:
        if metadata.get("pdf_pages") and int(metadata["pdf_pages"]) != len(doc):
            raise ValueError("PDF page count differs from manual_sections.json")
        if any(page < 1 or page > len(doc) for page in unique):
            raise ValueError("PDF page number outside selected Shop Manual")
        selected = []
        for page in unique:
            raw = doc[page - 1].get_text("text")
            item = {"pdf_page": page, "text": raw[:page_chars],
                    "text_truncated": len(raw) > page_chars,
                    "total_page_chars": len(raw)}
            if render:
                result = subprocess.run(
                    [sys.executable, str(ROOT / "tools" / "render_page.py"),
                     str(pdf), str(page), "manual_evidence"],
                    check=True, capture_output=True, text=True,
                )
                media = next((line[6:] for line in result.stdout.splitlines()
                              if line.startswith("MEDIA:")), None)
                if not media or not Path(media).is_file():
                    raise RuntimeError(f"Rendered page {page} is missing")
                item["media"] = media
            selected.append(item)
    return {"manual": str(pdf), "source_type": "Shop Manual",
            "pages": selected,
            "note": "Text is clipped when text_truncated is true; inspect the rendered page for omitted table columns or diagram labels."}

def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("section_map", nargs="?", type=Path)
    ap.add_argument("terms", nargs="?", help="Legacy: terms separated by |")
    ap.add_argument("sections", nargs="*")
    ap.add_argument("--model", choices=sorted(MODELS))
    ap.add_argument("--problem", help="Question or symptom")
    ap.add_argument("--fault-code", default="")
    ap.add_argument("--component", default="")
    ap.add_argument("--top", type=int, default=9)
    ap.add_argument("--page-chars", type=int, default=2500)
    ap.add_argument("--max-sections", type=int, default=4)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--pages", nargs="+", type=int)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--full-page-chars", type=int, default=5000)
    args = ap.parse_args()
    if args.model:
        if not args.problem and not args.pages:
            ap.error("--problem is required with --model")
        section_map = ROOT / MODELS[args.model] / "manual_sections.json"
    elif args.section_map and args.terms and args.sections:
        section_map = args.section_map
    else:
        ap.error("Use --model MODEL --problem TEXT or legacy MAP TERMS SECTIONS...")
    if args.render and not args.pages:
        ap.error("--render requires --pages")
    if args.pages and not args.model:
        ap.error("--pages requires --model")
    metadata = json.loads(section_map.read_text(encoding="utf-8"))
    if args.pages:
        result = batch_pages(section_map, metadata, args.pages,
                             min(max(args.full_page_chars, 500), 6000), args.render)
        print(json.dumps(result, ensure_ascii=True))
        return
    if args.model:
        terms = search_terms(args.problem, args.component, args.fault_code)
        chosen = route(metadata["sections"], terms, args.problem, args.fault_code,
                       min(max(args.max_sections, 1), 6))
        if args.fault_code:
            existing = {path for path, _ in chosen}
            chosen += [(path, item) for path, item in nodes(metadata["sections"])
                       if "failure_code" in path and not item.get("subsections")
                       and path not in existing]
    else:
        terms = [term.strip().casefold() for term in args.terms.split("|") if term.strip()]
        chosen = []
        for key in args.sections:
            item = find_section(metadata["sections"], key)
            if item is None:
                ap.error(f"Unknown or ambiguous section: {key}")
            chosen.append((key, item))
    if not terms:
        ap.error("No useful search terms; provide a component or fault code")
    result = probe(section_map, metadata, chosen, terms,
                   min(max(args.top, 1), 10), min(max(args.page_chars, 500), 4000),
                   not args.no_fallback, args.fault_code)
    result["search_terms"] = terms
    print(json.dumps(result, ensure_ascii=True))

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"manual_evidence_probe: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
