"""Read-only Part Book lookup (router) + targeted PDF verification.

The SQLite index only ROUTES to candidate rows/pages. Evidence is the real PDF:
use --verify to re-read ONLY the candidate row regions from the PDF, and
--render to render ONLY the candidate page(s) with tools/render_page.py.
A full Part Book scan is never performed here.

Index miss != "part does not exist". Coverage is reported on every answer.

Examples (native Windows):
  E:\\KomatsoAI\\.venv\\Scripts\\python.exe E:\\KomatsoAI\\tools\\fleet\\partbook_lookup.py --model HD785-7 --part-number 600-319-3550 --verify
  ... --part-number-prefix 561-15 --limit 5
  ... --query "turbocharger gasket"
  ... --figure A1530-A7D6 --item 16 --serial N10050 --verify
  ... --figure E0100-01A0 --item 1 --serial N10200 --render
"""
from __future__ import annotations

import argparse
import json
import os
import re
import sqlite3
import subprocess
import sys
import time
from pathlib import Path

from partbook_resolver import (resolve_model, select_books, book_serial_match,
                               candidate_provenance, load_registry, DIGITS)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "runtime" / "partbook" / "partbook_index.sqlite"
PYTHON = ROOT / ".venv" / "Scripts" / "python.exe"
RENDERER = ROOT / "tools" / "render_page.py"


def norm_pn(raw: str) -> str:
    s = raw.strip().strip("()").replace(" ", "")
    s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF
                                      or ch in "\u2606\u2605"))
    if s[:1] == "u" and len(s) > 1 and s[1].isupper():
        s = s[1:]
    return s.upper()


def parse_user_serial(raw: str | None):
    if not raw:
        return None
    m = re.match(r"^\s*([A-Za-z]*)\s*-?\s*(\d+)\s*$", raw.translate(DIGITS))
    if not m:
        raise SystemExit(json.dumps({"error": f"unparseable serial: {raw}"}))
    return m.group(1).upper(), int(m.group(2))


def fts_query(text: str) -> str:
    toks = [t for t in re.findall(r"[A-Za-z0-9][A-Za-z0-9\-\.']*", text) if len(t) > 1]
    return " ".join('"' + t.replace('"', "") + '"*' for t in toks)


def connect(db: Path) -> sqlite3.Connection:
    if not db.exists():
        raise SystemExit(json.dumps({
            "error": "partbook index missing",
            "rebuild": r"E:\KomatsoAI\.venv\Scripts\python.exe E:\KomatsoAI\tools\partbook_index.py --book BOOK_ID"}))
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)  # read-only
    con.row_factory = sqlite3.Row
    return con


def coverage(con, model):
    books = [dict(r) for r in con.execute(
        "SELECT book_id, index_status, machine_serial_prefix, machine_serial_from, machine_serial_to, source_pdf "
        "FROM books WHERE model=?", (model,))]
    return books


def coverage_note(model, books):
    if model == "HD785-7":
        return ("Index covers only HD785-7 B1 (N10001-N10560). B2 (N8173 and up) is scanned and "
                "not indexed. A miss does NOT mean the part does not exist. Index is a router; "
                "the PDF page is the evidence.")
    summaries = []
    for book in books:
        prefix = book.get("machine_serial_prefix") or ""
        start, end = book.get("machine_serial_from"), book.get("machine_serial_to")
        if start is None:
            serial = "serial coverage unknown"
        elif end is None:
            serial = f"{prefix}{start}-UP"
        else:
            serial = f"{prefix}{start}-{prefix}{end}"
        summaries.append(f"{book['book_id']}: {book['index_status']}, {serial}")
    return (f"Index coverage for {model}: " + ("; ".join(summaries) or "no indexed books")
            + ". Coverage is incomplete. A miss is NOT evidence of absence; verify actual PDF rows and applicability.")


def applicability(con, occ_ids):
    out = {}
    if not occ_ids:
        return out
    q = ",".join("?" * len(occ_ids))
    for r in con.execute(f"SELECT * FROM row_applicability WHERE occ_id IN ({q})", occ_ids):
        out.setdefault(r["occ_id"], []).append(dict(r))
    return out


def serial_match(apps, kind, serial, prefix=None):
    """True / False / None(unknown)."""
    rel = [a for a in apps if a["kind"] == kind]
    if not rel:
        return None
    for a in rel:
        if prefix is not None and (a.get("prefix") or "").upper() != prefix:
            continue
        if a["from_num"] is None:
            return None
        if a["from_num"] <= serial and (a["to_num"] is None or serial <= a["to_num"]):
            return True
    return False


def search(con, a):
    where, params, order = ["o.status != 'rejected'"], [], "o.book_id, o.pdf_page, o.row_ordinal"
    join_fts = False
    if hasattr(a, "resolved_book_ids"):
        if not a.resolved_book_ids:
            return []
        where.append("o.book_id IN (" + ",".join("?" for _ in a.resolved_book_ids) + ")")
        params.extend(a.resolved_book_ids)
    elif a.book:
        where.append("o.book_id = ?"); params.append(a.book)
    else:
        where.append("o.book_id IN (SELECT book_id FROM books WHERE model=?)"); params.append(a.model)
    if a.part_number:
        where.append("o.pn_norm = ?"); params.append(norm_pn(a.part_number))
    if a.part_number_prefix:
        p = norm_pn(a.part_number_prefix)
        where.append("o.pn_norm >= ? AND o.pn_norm < ?"); params += [p, p + "\uffff"]
    if a.figure:
        f = a.figure.upper().replace("FIG.", "").strip()
        where.append("(o.fig_no = ? OR o.fig_no = ?)"); params += [f, "RAW:" + f]
    if a.item is not None:
        where.append("o.item_no = ?"); params.append(a.item)
    if a.group:
        where.append("o.fig_no IN (SELECT fig_no FROM figures WHERE group_code = ? AND book_id=o.book_id)")
        params.append(a.group.upper())
    if a.query:
        join_fts = True
        where.append("parts_fts MATCH ?"); params.append(fts_query(a.query))
        order = "bm25(parts_fts, 10.0, 3.0, 1.0, 0.5, 5.0)"
    sql = ("SELECT o.*, f.title AS fig_title, f.group_code FROM part_occurrences o "
           + ("JOIN parts_fts ON parts_fts.rowid = o.occ_id " if join_fts else "")
           + "JOIN figures f ON f.book_id=o.book_id AND f.fig_no=o.fig_no "
           + "WHERE " + " AND ".join(where) + f" ORDER BY {order} LIMIT ?")
    params.append(max(a.limit * 8, 200))
    return [dict(r) for r in con.execute(sql, params)]


def view_pages(con, book_id, fig_no):
    return [dict(r) for r in con.execute(
        "SELECT pdf_page, page_label, has_view FROM figure_pages WHERE book_id=? AND fig_no=? ORDER BY pdf_page",
        (book_id, fig_no))]


def verify_rows(cands, con):
    """Re-read ONLY each candidate's row bbox from the real PDF page."""
    import pymupdf
    by_pdf = {}
    for c in cands:
        by_pdf.setdefault(c["_source_pdf"], []).append(c)
    pages_read = set()
    for pdf, cs in by_pdf.items():
        with pymupdf.open(ROOT / pdf) as doc:
            for c in cs:
                page = doc[c["pdf_pages"][0] - 1]
                pages_read.add((pdf, c["pdf_pages"][0]))
                x0, y0, x1, y1 = map(float, c["_row_bbox"].split(","))
                r = pymupdf.Rect(x0 - 40, y0 - 1.2, x1 + 40, y1 + 1.2) * page.derotation_matrix
                text = " ".join(w[4] for w in sorted(
                    page.get_text("words", clip=r.normalize()),
                    key=lambda w: (w[0], w[1])))
                toks = set(text.split())
                checks = {}
                if c["part_number_raw"]:
                    checks["part_number"] = (c["part_number_raw"] in toks or
                                              (" " in c["part_number_raw"] and
                                               c["part_number_raw"] in text))
                if c["item_raw"]:
                    checks["item"] = c["item_raw"] in toks
                if c["quantity"]:
                    checks["quantity"] = c["quantity"] in toks
                if c["description"]:
                    first = c["description"].split(" || ")[0]
                    checks["description"] = all(t in text for t in first.split()[:3])
                if c["serial_raw"]:
                    checks["applicability"] = all(t in toks for t in c["serial_raw"].split())
                c["pdf_verification"] = {
                    "status": "VERIFIED" if checks and all(checks.values()) else "MISMATCH",
                    "checks": checks, "pdf_row_text": text[:240]}
    return len(pages_read)


def verified_view_candidates(cands):
    """At most two distinct verified figures; a SEE FIG row reuses its primary."""
    if not cands or any(
        (c.get("pdf_verification") or {}).get("status") != "VERIFIED"
        or "serial_applicability_unknown" in c.get("flags", ())
        or "row_serial_mismatch" in c.get("flags", ())
        or "engine_serial_mismatch" in c.get("flags", ())
        or any(str(flag).startswith("ambiguous") for flag in c.get("flags", ()))
        for c in cands
    ):
        return []
    def referenced_figure(candidate):
        return re.search(r"SEE\s+FIG\.?\s*([A-Z0-9-]+)",
                         candidate.get("description", "").upper())

    primary = [c for c in cands if not referenced_figure(c)]
    primary_figures = {c["figure"].upper() for c in primary}
    selected = list(primary)
    for candidate in cands:
        reference = referenced_figure(candidate)
        if not reference:
            continue
        target = reference.group(1)
        if not any(figure == target or figure.startswith(target + "-")
                   for figure in primary_figures):
            selected.append(candidate)
    distinct = {}
    for candidate in selected:
        distinct.setdefault((candidate["_source_pdf"], candidate["figure"]), candidate)
    if not distinct or len(distinct) > 2:
        return []
    return list(distinct.values())


def render(cands, label):
    out = []
    pages = []
    for c in cands:
        for p in c["view_pages"][:1]:
            key = (c["_source_pdf"], p)
            if key not in pages:
                pages.append(key)
    for pdf, p in pages[:3]:
        r = subprocess.run([str(PYTHON), str(RENDERER), str(ROOT / pdf), str(p), label],
                           capture_output=True, text=True, env=os.environ.copy())
        line = (r.stdout.strip().splitlines() or [""])[-1]
        out.append({"pdf_page": p, "ok": r.returncode == 0, "output": line or r.stderr.strip()[-300:]})
    return out


def main(argv=None):
    if hasattr(sys.stdout, "reconfigure"):
        sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--model", default="HD785-7")
    ap.add_argument("--query")
    ap.add_argument("--part-number")
    ap.add_argument("--part-number-prefix")
    ap.add_argument("--group")
    ap.add_argument("--figure")
    ap.add_argument("--item", type=int)
    ap.add_argument("--serial", help="machine serial, e.g. N10050")
    ap.add_argument("--engine-serial", help="engine serial as printed, e.g. 500090")
    ap.add_argument("--book")
    ap.add_argument("--limit", type=int, default=10)
    ap.add_argument("--verify", action="store_true", help="re-read candidate rows from the real PDF")
    ap.add_argument("--render", action="store_true", help="render candidate view page(s) (max 3)")
    ap.add_argument("--auto-render-verified", action="store_true", help="render at most two verified exploded views for direct Part identification")
    ap.add_argument("--label", default="partbook")
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args(argv)
    if not any([a.query, a.part_number, a.part_number_prefix, a.figure, a.group]):
        ap.error("give --part-number, --part-number-prefix, --query, --figure or --group")
    t0 = time.perf_counter()
    registry = load_registry()
    resolution = resolve_model(a.model, registry)
    if resolution["resolution_status"] != "RESOLVED":
        result = dict(resolution, found=0, candidates=[], coverage_complete=False,
                      indexed_lookup_available=False)
        print(json.dumps(result, ensure_ascii=False, indent=1))
        return result
    con = connect(Path(a.db))
    try:
        books = select_books(con, resolution, a.book, registry)
    except ValueError:
        con.close()
        raise
    a.resolved_book_ids = [b["book_id"] for b in books if b["index_status"] == "indexed_partial"]
    book_by_id = {b["book_id"]: b for b in books}
    book_pdf = {b["book_id"]: b["source_pdf"] for b in books}
    notes = []
    serial = parse_user_serial(a.serial)
    eng = parse_user_serial(a.engine_serial)
    for b in books:
        if b["index_status"] != "indexed_partial":
            notes.append(f"{b['book_id']} NOT indexed ({b['index_status']}): {b['source_pdf']}")
    if serial:
        inb = [b for b in books if b["index_status"] == "indexed_partial"
               and book_serial_match(b, serial) is True]
        if not inb:
            notes.append(f"serial {a.serial} is outside indexed book coverage; check the scanned book manually")
    rows = search(con, a)
    apps = applicability(con, [r["occ_id"] for r in rows])
    if serial or eng:
        def serial_priority(row):
            row_apps = apps.get(row["occ_id"], [])
            matches = [serial_match(row_apps, kind, value[1], value[0])
                       for kind, value in (("machine", serial), ("engine", eng)) if value]
            return (any(match is False for match in matches),
                    any(match is None for match in matches))
        rows.sort(key=serial_priority)
    cands = []
    engine_matches = []
    for r in rows:
        ap_rows = apps.get(r["occ_id"], [])
        flags = json.loads(r["flags"])
        ok_serial = None
        if serial:
            ok_serial = serial_match(ap_rows, "machine", serial[1], serial[0])
            if ok_serial is False:
                flags.append("row_serial_mismatch")
            if ok_serial is None:
                flags.append("serial_applicability_unknown")
        m = None
        if eng:
            m = serial_match(ap_rows, "engine", eng[1], eng[0])
            if m is False:
                flags.append("engine_serial_mismatch")
            if m is None and any(x["kind"] == "engine" for x in ap_rows) is False:
                flags.append("engine_serial_not_applicable_to_row")
        vp = view_pages(con, r["book_id"], r["fig_no"])
        views = [p["pdf_page"] for p in vp if p["has_view"]]
        views.sort(key=lambda page: (page != r["pdf_page"], abs(page - r["pdf_page"]),
                                     page > r["pdf_page"], page))
        rel = [dict(x) for x in con.execute(
            "SELECT p.relation, o.item_raw, o.pn_raw, o.description_raw, o.pdf_page FROM part_relations p "
            "JOIN part_occurrences o ON o.occ_id=p.related_occ_id WHERE p.occ_id=?", (r["occ_id"],))]
        cands.append({
            "book_id": r["book_id"], "figure": r["fig_no"], "figure_title": r["fig_title"],
            "group": r["group_code"], "item": r["item_no"], "item_raw": r["item_raw"],
            "part_number": r["pn_norm"], "part_number_raw": r["pn_raw"],
            "description": r["description_raw"], "quantity": r["qty_raw"],
            "applicability": r["serial_raw"], "serial_raw": r["serial_raw"],
            "serial_match": ok_serial,
            "pdf_pages": [r["pdf_page"]],
            # Only exploded-view pages. A parts-list page is not a substitute image.
            "view_pages": views,
            "figure_pdf_pages": [p["pdf_page"] for p in vp],
            "row": {"ordinal": r["row_ordinal"], "bbox": r["row_bbox"], "method": r["extraction_method"]},
            "status": r["status"], "flags": flags, "relations": rel[:4],
            "coverage_complete": False, "requires_pdf_verification": True,
            "_source_pdf": book_pdf[r["book_id"]], "_row_bbox": r["row_bbox"],
        })
        engine_matches.append(m)
        if len(cands) >= a.limit:
            break
    t_lookup = time.perf_counter() - t0
    pages_read = 0
    if a.verify and cands:
        pages_read = verify_rows(cands, con)
    for candidate, engine_match in zip(cands, engine_matches):
        candidate.update(candidate_provenance(candidate, book_by_id[candidate["book_id"]],
                                              resolution, serial, engine_match))
    t_verify = time.perf_counter() - t0
    if a.auto_render_verified and not a.verify:
        ap.error("--auto-render-verified requires --verify")
    if a.auto_render_verified:
        # Keep nonmatching rows as source facts, while preserving the historical
        # image selection for rows matching a supplied machine/engine serial.
        matching = [c for c in cands if "row_serial_mismatch" not in c["flags"]
                    and "engine_serial_mismatch" not in c["flags"]]
        render_candidates = (verified_view_candidates(matching)
                             if all(c.get("part_number") for c in cands) else [])
    else:
        render_candidates = cands
    rendered = render(render_candidates, a.label) if (a.render or a.auto_render_verified) and render_candidates else []
    t_total = time.perf_counter() - t0
    for c in cands:
        c.pop("_source_pdf"); c.pop("_row_bbox"); c.pop("serial_raw")
        if not c["relations"]:
            c.pop("relations")
        if "pdf_verification" in c and c["pdf_verification"]["status"] == "VERIFIED":
            c["requires_pdf_verification"] = False
    result = {
        "model": a.model, "found": len(cands),
        "requested_model": a.model, "resolution": resolution,
        "resolved_fleet_asset": resolution["fleet_asset"],
        "selected_part_books": [{key: b[key] for key in (
            "book_id", "model", "index_status", "machine_serial_prefix",
            "machine_serial_from", "machine_serial_to", "source_pdf", "source_identity")} for b in books],
        "indexed_lookup_available": bool(a.resolved_book_ids),
        "part_found_in_source": any(c["part_found_status"] == "PART_FOUND_IN_SOURCE" for c in cands),
        "part_applicability_confirmed": False,
        "applicability_status": "PART_APPLICABILITY_UNCONFIRMED",
        "coverage_complete": False,
        "coverage_note": coverage_note(resolution["fleet_asset"]["display_model"], books),
        "notes": notes,
        "candidates": cands,
        "timing_ms": {"lookup": round(t_lookup * 1000, 1),
                      "lookup_plus_verify": round(t_verify * 1000, 1) if a.verify else None,
                      "total": round(t_total * 1000, 1)},
        "pdf_pages_read": pages_read,
    }
    if rendered:
        result["rendered"] = rendered
    if not cands:
        result["miss"] = "no indexed candidate; NOT evidence of absence (check this model's book coverage, supersession and spelling)"
    print(json.dumps(result, ensure_ascii=False, indent=1))
    con.close()
    return result


if __name__ == "__main__":
    main()
