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
MODELS = json.loads((ROOT / 'tools/manual_models.json').read_text(encoding='utf-8'))
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
    "باکت": ("bucket", "work equipment"), "بوم": ("boom", "work equipment"),
    "boom": ("boom", "work equipment"), "bucket": ("bucket", "work equipment"),
    "slow": ("low", "speed"), "کند": ("low", "speed"),
    "ضعف": ("low", "weak"),
}
STOP = {"the", "and", "for", "with", "from", "does", "not", "work", "working",
        "failure", "fault", "problem", "system", "machine", "check", "کار", "نمی",
        "کند", "مشکل", "دستگاه", "خراب", "است", "را", "در", "به", "از", "با", "و"}
# Modifiers describe a symptom. Background words occur throughout a shop manual and
# do not by themselves name the component the question is about.
COVERAGE_MODIFIERS = {
    "during", "after", "before", "into", "over", "under", "low", "high", "weak",
    "heavy", "slow", "fast", "poor", "normal", "abnormal", "both", "left", "right",
    "front", "rear", "upper", "lower", "operation", "operating", "test", "testing",
    "check", "checking", "change", "speed", "power", "line", "work", "leak", "leaks",
    "leakage", "leaking", "ineffective", "insufficient", "braking", "working",
    "starting", "code", "mode", "item", "troubleshooting", "adjusting",
    "maintenance", "procedure", "specification",
}
COVERAGE_BACKGROUND = {
    "oil", "engine", "fuel", "pressure", "hydraulic", "temperature", "valve", "pump",
    "sensor", "switch", "lever", "pedal", "circuit", "control", "brake", "wheel",
    "water", "air", "gas", "level", "electrical", "mechanical",
}

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
        if re.search(r"(?<!\w)" + re.escape(trigger) + r"(?!\w)",
                     source.replace("\u200c", "")):
            terms.extend(synonyms)
    terms.extend(word for word in re.findall(r"[a-z][a-z0-9-]{2,}", source)
                 if word not in STOP)
    if code:
        terms.insert(0, code.casefold())
    return list(dict.fromkeys(terms))[:14]

def route(sections, terms, problem, code="", limit=4):
    """Diversify by manual chapter intent; sparse multipart indexes stay together."""
    text = problem.casefold()
    symptom = any(word in text for word in
                  ("not", "fail", "weak", "heavy", "slow", "fault", "خراب", "ضعف", "نمی", "خطا", "سنگین", "کند"))
    testing = any(word in text for word in ("test", "pressure", "adjust", "measurement", "تست", "فشار", "تنظیم"))
    removal = any(word in text for word in ("remove", "install", "disassembl", "بازکردن", "بازوبست"))
    diagram = any(word in text for word in ("diagram", "wiring", "circuit", "نقشه", "مدار"))
    consolidated = {path for path, item in nodes(sections)
                    if item.get("subsections") and all(
                        key.startswith("part_") and not any(child.get(field)
                        for field in ("title", "description", "topics"))
                        for key, child in item["subsections"].items())}
    ranked = []
    for path, item in nodes(sections):
        if any(path.startswith(parent + "/") for parent in consolidated):
            continue
        if item.get("subsections") and path not in consolidated:
            continue
        if not code and "failure_code" in path:
            continue
        category = path.split("/")[0]
        if category == "disassembly_assembly" and not removal:
            continue
        label = " ".join((path.replace("_", " "), str(item.get("title", "")),
                          str(item.get("description", "")),
                          " ".join(map(str, item.get("topics", []))))).casefold()
        matches = [term for term in terms if term in label]
        score = sum(5 if " " in term else 3 for term in matches)
        if category == "troubleshooting" and symptom:
            score += 3
        if category == "testing_adjusting" and (symptom or testing):
            score += 3
        if category == "diagrams" and diagram:
            score += 5
        if code and "failure_code" in path:
            score += 5
        if "index" in path or "foreword" in path:
            score -= 8
        if score > 0:
            ranked.append((score, path, item))
    ranked.sort(key=lambda value: (-value[0], int(value[2]["pdf_end"]) - int(value[2]["pdf_start"]), value[1]))
    priorities = (["troubleshooting", "testing_adjusting", "structure_function"] if symptom
                  else ["testing_adjusting", "standard_values", "structure_function"] if testing
                  else ["disassembly_assembly", "structure_function"] if removal
                  else ["diagrams", "structure_function"] if diagram else [])
    chosen = []
    for category in priorities:
        candidates = [value for value in ranked if value[1].split("/")[0] == category]
        if category == "troubleshooting" and not code:
            # The same mode names are supplied by the manual indexes, across models.
            mode = "engine_s_mode" if any(term in terms for term in ("engine", "starting", "starter")) else (
                "electrical_e_mode" if any(term in terms for term in ("electrical", "wiring", "circuit"))
                else "hydraulic_mechanical_h_mode")
            preferred = [value for value in candidates if mode in value[1]]
            candidates = preferred or candidates
        if candidates and len(chosen) < limit:
            chosen.append((candidates[0][1], candidates[0][2]))
    for _, path, item in ranked:
        if len(chosen) == limit:
            break
        if all(path != selected[0] for selected in chosen):
            chosen.append((path, item))
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
                    heading = page_structure(doc[page - 1])["heading"] or ""
                    head = re.sub(r"\s+", " ", heading.casefold())
                    score += sum((50 if term == terms[0] else 5) * (2 if " " in term else 1)
                                 for term in matched if term in head)
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
            "search_terms": list(terms),
            "note": "Excerpts may omit table columns or diagrams. Verify complete PDF pages before using exact values, pins or procedures.",
        }


def page_structure(page):
    """Recognize a topic heading from PDF typography, never from page numbers."""
    spans = [span for block in page.get_text("dict")["blocks"]
             for line in block.get("lines", []) for span in line["spans"]]
    height = page.rect.height
    header = [span for span in spans if span["bbox"][1] < height * .075]
    forms = re.findall(r"\b(?:SEN|SEBM|CEBM|TEN)\d+(?:-\d+)?\b",
                       " ".join(span["text"] for span in header))
    header_size = max((span["size"] for span in header), default=10)
    titles = [span for span in spans
              if height * .075 <= span["bbox"][1] < height * .16
              and span["size"] > header_size * 1.12
              and ("bold" in span["font"].casefold() or span["flags"] & 16)
              and re.search(r"[A-Za-z]{3}", span["text"])]
    title_size = max((span["size"] for span in titles), default=0)
    title = " ".join(span["text"].strip() for span in
                     sorted(titles, key=lambda span: (span["bbox"][1], span["bbox"][0]))
                     if span["size"] >= title_size - .3)
    return {"form": forms[0] if forms else None, "heading": title or None,
            "heading_size": round(title_size, 2)}


def topic_pages(doc, metadata, seed, max_pages=4):
    """Follow continuation pages within the same indexed leaf and form.

    A new heading of the same or larger type size ends the topic. Unrecognized
    layouts retain only the requested page; limits are explicit, never silent.
    """
    bounds = [(int(item["pdf_start"]), int(item["pdf_end"]))
              for _, item in nodes(metadata["sections"])
              if not item.get("subsections") and
              int(item["pdf_start"]) <= seed <= int(item["pdf_end"])]
    if not bounds:
        return [seed], False
    start, end = min(bounds, key=lambda bound: bound[1] - bound[0])
    cache = {}
    def structure(number):
        if number not in cache:
            cache[number] = page_structure(doc[number - 1])
        return cache[number]
    original = structure(seed)
    if not original["form"]:
        return [seed], False
    anchor = seed
    if not original["heading"]:
        for number in range(seed - 1, max(start - 1, seed - max_pages), -1):
            item = structure(number)
            if item["form"] != original["form"]:
                break
            if item["heading"]:
                anchor = number
                break
    anchor_info = structure(anchor)
    if not anchor_info["heading"]:
        return [seed], False
    selected = list(range(anchor, seed + 1))
    limited = False
    for number in range(seed + 1, end + 1):
        item = structure(number)
        if item["form"] != original["form"]:
            break
        if item["heading"] and item["heading_size"] >= anchor_info["heading_size"] - .3:
            break
        if len(selected) >= max_pages:
            limited = True
            break
        selected.append(number)
    return selected, limited


def _flat(value):
    return re.sub(r"\s+", " ", (value or "").casefold())


def _contains_term(text, term):
    """Match a manual term without letting a shorter word hide inside a longer one."""
    if " " in term:
        return term in text
    return re.search(r"(?<![a-z0-9])" + re.escape(term) + r"(?![a-z0-9])", text) is not None


def _family(term):
    folded = term.casefold()
    members = {folded}
    for trigger, synonyms in ALIASES.items():
        group = {trigger.casefold(), *(item.casefold() for item in synonyms)}
        if folded in group:
            members |= group
    return members


def _distinctive(term):
    folded = term.casefold()
    if folded in COVERAGE_MODIFIERS:
        return False
    if " " in folded:
        return True
    return folded not in COVERAGE_BACKGROUND


def _diagnostic(section):
    return section.split("/")[0] in {"troubleshooting", "testing_adjusting", "full_manual_fallback"}


def evidence_coverage(result):
    """Say whether the packet already contains the documented topic.

    A component is covered only when a troubleshooting or test heading names it,
    or names another term from the same manual alias family. A passing mention in
    an unrelated fault is not coverage. No machine, page, or benchmark question
    is special-cased.
    """
    terms = [term.casefold() for term in result.get("search_terms") or []]
    evidence = result.get("evidence") or []
    groups = result.get("topic_groups") or []
    by_page = {item["pdf_page"]: item for item in evidence}
    if result.get("exact_fault_code_found") is False:
        return {
            "status": "incomplete", "action": "refined_retrieve",
            "reason": "The requested failure code was not found in the searched pages.",
            "missing_component_terms": [term for term in terms if any(ch.isdigit() for ch in term)],
            "matched_topics": [], "resume_at_pdf_pages": [],
        }
    alias_families, loose, seen = [], [], set()
    for term in terms:
        if not _distinctive(term) or any(ch.isdigit() for ch in term):
            continue
        members = _family(term)
        key = tuple(sorted(members))
        if key in seen:
            continue
        seen.add(key)
        # Alias families are manual vocabulary. Extra words the model adds, such as
        # "force" or "service", do not create another required topic once that
        # family is named by a heading. A word with no alias, such as a component
        # the index never grouped, still has to appear in a heading.
        (alias_families if len(members) > 1 else loose).append((term, members))
    families = alias_families or loose

    def heading_text(group):
        pages = [by_page[number] for number in group["pages"] if number in by_page]
        return _flat(" ".join(item.get("heading") or "" for item in pages))

    def complete(group):
        if group.get("continuation_limited"):
            return False
        return not any(by_page[number].get("text_truncated") for number in group["pages"] if number in by_page)

    def covers(group, members):
        if not _diagnostic(group.get("section") or ""):
            return False
        heading = heading_text(group)
        return any(_distinctive(member) and _contains_term(heading, member) for member in members)

    missing, truncated, matched = [], [], []
    for term, members in families:
        covering = [group for group in groups if covers(group, members)]
        done = [group for group in covering if complete(group)]
        chosen = done or covering
        if not chosen:
            missing.append(term)
            continue
        group = chosen[0]
        pages = [by_page[number] for number in group["pages"] if number in by_page]
        heading = next((item.get("heading") for item in pages if item.get("heading")), None)
        matched.append({
            "heading": heading, "section": group.get("section"),
            "pdf_pages": list(group["pages"]), "complete": bool(done),
        })
        if not done:
            truncated.append(max(group["pages"]) + 1)
    if not families:
        substantive = [term for term in terms if term not in COVERAGE_MODIFIERS]
        diagnostic = [group for group in groups if _diagnostic(group.get("section") or "")]
        done = [group for group in diagnostic if complete(group)
                and any(_contains_term(heading_text(group), term) for term in substantive)]
        partial = [group for group in diagnostic if not complete(group)
                   and any(_contains_term(heading_text(group), term) for term in substantive)]
        if done:
            group = done[0]
            pages = [by_page[number] for number in group["pages"] if number in by_page]
            matched.append({
                "heading": next((item.get("heading") for item in pages if item.get("heading")), None),
                "section": group.get("section"), "pdf_pages": list(group["pages"]), "complete": True,
            })
        elif partial:
            truncated.append(max(partial[0]["pages"]) + 1)
        elif substantive:
            missing.extend(substantive[:4])
    if missing:
        shown = ", ".join(missing)
        return {
            "status": "incomplete", "action": "refined_retrieve",
            "reason": "No troubleshooting or test heading covers: " + shown + ". "
                      "Mentions inside unrelated faults are not that topic.",
            "missing_component_terms": missing, "matched_topics": matched,
            "resume_at_pdf_pages": [],
        }
    if any(not item["complete"] for item in matched) or (truncated and not matched):
        return {
            "status": "truncated", "action": "finish_read_pages",
            "reason": "The matching topic is in this packet, but the page limit cut it off.",
            "missing_component_terms": [], "matched_topics": matched,
            "resume_at_pdf_pages": truncated,
        }
    if matched:
        return {
            "status": "complete", "action": "finish",
            "reason": "A troubleshooting or test topic already names the requested component and its text is complete. "
                      "Other index hits are different topics, not missing pages of this one.",
            "missing_component_terms": [], "matched_topics": matched,
            "resume_at_pdf_pages": [],
        }
    return {
        "status": "incomplete", "action": "refined_retrieve",
        "reason": "The search returned no documented pages.",
        "missing_component_terms": [], "matched_topics": [], "resume_at_pdf_pages": [],
    }


def evidence_packet(section_map, metadata, result, text_budget=22000):
    """Replace snippets with deduplicated topic pages and bounded full text."""
    groups, requested, limited_seeds = [], [], []
    with pymupdf.open(result["manual"]) as doc:
        primary, covered_sections = [], set()
        for hit in result["evidence"]:
            if hit["section"] not in covered_sections:
                primary.append(hit)
                covered_sections.add(hit["section"])
        pool = primary + result["top_page_index"] + result["evidence"]
        seed_limit = len(result["evidence"])
        for hit in pool:
            if len(groups) >= seed_limit:
                break
            pages, limited = topic_pages(doc, metadata, hit["pdf_page"])
            if limited:
                limited_seeds.append(hit["pdf_page"])
            if any(set(pages) == set(group["pages"]) for group in groups):
                continue
            groups.append({"seed": hit["pdf_page"], "section": hit["section"],
                           "pages": pages, "continuation_limited": limited})
            requested.extend(page for page in pages if page not in requested)
        # Seed pages precede their continuations when the text budget is tight.
        seeds = list(dict.fromkeys(group["seed"] for group in groups))
        requested = seeds + [page for page in requested if page not in seeds]
        evidence, left = [], text_budget
        for number in requested:
            page = doc[number - 1]
            raw = page.get_text("text")
            structure = page_structure(page)
            cap = min(6000, left)
            text = raw[:cap]
            left -= len(text)
            evidence.append({"pdf_page": number, "form": structure["form"],
                             "heading": structure["heading"], "text": text,
                             "text_truncated": len(raw) > cap,
                             "total_page_chars": len(raw),
                             "has_graphics": bool(page.get_images() or page.get_drawings())})
    result["evidence"] = evidence
    result["topic_groups"] = groups
    result["text_chars"] = text_budget - left
    result["needs_more_text_pages"] = [item["pdf_page"] for item in evidence
                                        if item["text_truncated"]]
    result["continuation_limited_seeds"] = limited_seeds
    coverage = evidence_coverage(result)
    if coverage["status"] == "complete":
        matched_pages = {page for topic in coverage["matched_topics"] for page in topic["pdf_pages"]}
        evidence_pages = {item["pdf_page"] for item in evidence}
        outside = [hit for hit in result.get("top_page_index") or [] if hit["pdf_page"] not in evidence_pages]
        related = [hit["pdf_page"] for hit in outside
                   if (hit.get("section") or "").split("/")[0] == "testing_adjusting"][:4]
        coverage["related_read_pages"] = related
        result["continuation_limited_seeds"] = [seed for seed in limited_seeds if seed in matched_pages]
        result["other_index_hits"] = {
            "count": len(outside),
            "related_read_pages": related,
            "note": "Different indexed topics. Not missing text. Do not retrieve again. "
                    "related_read_pages may be passed to finish read_pages when the complete topic names a test whose steps are not already in the text.",
        }
        result["top_page_index"] = []
        note = ("Actual PDF text. evidence_coverage.status is complete: the documented topic is already here. "
                "Do not retrieve again. Select the smallest necessary images. "
                "related_read_pages, if any, are optional finish reads, not another retrieve.")
    elif coverage["status"] == "truncated":
        note = ("Actual PDF text. evidence_coverage.status is truncated: the matching topic was cut off by the page "
                "limit. Do not retrieve again. Read evidence_coverage.resume_at_pdf_pages with the finish call.")
    else:
        note = ("Actual PDF text. evidence_coverage.status is incomplete: no returned troubleshooting or test heading "
                "covers the missing component terms. Retrieve with refined keywords for those terms, then broad=true "
                "if that packet is still incomplete. A mention inside an unrelated fault is not the procedure.")
    result["evidence_coverage"] = coverage
    result["note"] = note
    ordered = {}
    for key in ("evidence_coverage", "note"):
        ordered[key] = result.pop(key)
    ordered.update(result)
    return ordered

def batch_pages(section_map, metadata, pages, page_chars=5000, render=False,
                render_only=False):
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
            raw = "" if render_only else doc[page - 1].get_text("text")
            item = {"pdf_page": page}
            if not render_only:
                item.update(text=raw[:page_chars], text_truncated=len(raw) > page_chars,
                            total_page_chars=len(raw))
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
                pixmap = pymupdf.Pixmap(media)
                if not pixmap.width or not pixmap.height:
                    raise RuntimeError(f"Rendered page {page} is unreadable")
                item.update(media=media, image_validated=True,
                            image_bytes=Path(media).stat().st_size,
                            image_width=pixmap.width, image_height=pixmap.height)
                del pixmap
            selected.append(item)
    return {"manual": str(pdf), "source_type": "Shop Manual",
            "pages": selected,
            "note": "Text is clipped when text_truncated is true; inspect the rendered page for omitted table columns or diagram labels."}

def page_numbers(value):
    """Accept CLI page lists separated by whitespace, commas, or both."""
    try:
        parts = value.split(",")
        if any(not part.strip() for part in parts):
            raise ValueError
        return [int(part) for part in parts]
    except ValueError:
        raise argparse.ArgumentTypeError("Pages must be integers separated by spaces or commas")


def main():
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("section_map", nargs="?", type=Path)
    ap.add_argument("terms", nargs="?", help="Legacy: terms separated by |")
    ap.add_argument("sections", nargs="*")
    ap.add_argument("--model", choices=sorted(MODELS))
    ap.add_argument("--problem", help="Question or symptom")
    ap.add_argument("--request-file", type=Path, help="Prepared private Maintenance request inside runtime")
    ap.add_argument("--fault-code", default="")
    ap.add_argument("--component", default="")
    ap.add_argument("--top", type=int, default=6)
    ap.add_argument("--packet", action="store_true", help="Return bounded complete topic pages")
    ap.add_argument("--render-only", action="store_true", help="Render and validate pages without repeating text")
    ap.add_argument("--page-chars", type=int, default=2500)
    ap.add_argument("--max-sections", type=int, default=4)
    ap.add_argument("--no-fallback", action="store_true")
    ap.add_argument("--broad", action="store_true", help="Skip index routing and scan the whole manual")
    ap.add_argument("--pages", nargs="+", type=page_numbers)
    ap.add_argument("--render", action="store_true")
    ap.add_argument("--full-page-chars", type=int, default=5000)
    args = ap.parse_args()
    if args.request_file:
        request_path = args.request_file.resolve()
        if not request_path.is_relative_to((ROOT / "runtime").resolve()):
            ap.error("Prepared request must be inside project runtime")
        request = json.loads(request_path.read_text(encoding="utf-8"))
        if args.model or args.problem:
            ap.error("Do not combine --request-file with --model/--problem")
        args.model, args.problem = request["model"], request["question"]
        if args.model not in MODELS or not isinstance(args.problem, str) or not args.problem.strip():
            ap.error("Invalid prepared model/question")
    if args.pages:
        args.pages = [page for group in args.pages for page in group]
    if args.model:
        if not args.problem and not args.pages:
            ap.error("--problem is required with --model")
        section_map = ROOT / MODELS[args.model] / "manual_sections.json"
    elif args.section_map and args.terms and args.sections:
        section_map = args.section_map
    else:
        ap.error("Use --model MODEL --problem TEXT or legacy MAP TERMS SECTIONS...")
    if args.render_only:
        args.render = True
    if args.packet and args.pages:
        ap.error("--packet belongs to retrieval; use --pages for explicit follow-up")
    if args.render and not args.pages:
        ap.error("--render requires --pages")
    if args.pages and not args.model:
        ap.error("--pages requires --model")
    metadata = json.loads(section_map.read_text(encoding="utf-8"))
    if args.pages:
        result = batch_pages(section_map, metadata, args.pages,
                             min(max(args.full_page_chars, 500), 6000), args.render, args.render_only)
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
    if args.broad:
        if args.no_fallback:
            ap.error("--broad is the whole-manual fallback")
        chosen = []
    result = probe(section_map, metadata, chosen, terms,
                   min(max(args.top, 1), 10), min(max(args.page_chars, 500), 4000),
                   not args.no_fallback, args.fault_code)
    if args.packet:
        result = evidence_packet(section_map, metadata, result)
    result["search_terms"] = terms
    result["next_commands"] = {
        "render": "E:/KomatsoAI/.venv/Scripts/python.exe E:/KomatsoAI/tools/manual_evidence_probe.py --model MODEL --pages SELECTED_PAGES --render-only",
        "read": "E:/KomatsoAI/.venv/Scripts/python.exe E:/KomatsoAI/tools/manual_evidence_probe.py --model MODEL --pages MISSING_PAGES",
        "limits": "At most 8 explicitly selected pages per follow-up; do not call --help or re-read text already returned in the packet.",
    }
    print(json.dumps(result, ensure_ascii=True))

if __name__ == "__main__":
    try:
        main()
    except (OSError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"manual_evidence_probe: {type(exc).__name__}: {exc}", file=sys.stderr)
        raise SystemExit(2)
