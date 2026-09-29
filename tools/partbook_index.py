"""Rebuildable SQLite + FTS5 index for Komatsu text-layer Part Books.

Indexed text-layer books use one geometry/SQLite pipeline with book profiles.
HD785-7 B2 (N8173 and up) remains scanned and deliberately unindexed.

The index is a ROUTER, not technical evidence. Every row keeps raw text,
PDF page, bbox and row ordinal so the lookup tool can re-read that exact
page region from the real PDF for verification.

Rebuild:
    E:\\KomatsoAI\\.venv\\Scripts\\python.exe E:\\KomatsoAI\\tools\\partbook_index.py --book HD785-7-B1
    E:\\KomatsoAI\\.venv\\Scripts\\python.exe E:\\KomatsoAI\\tools\\partbook_index.py --book WA600-6-2010

Only the generated SQLite file (runtime/partbook/, gitignored) is written.
No operational DB, fleet data, gateway or messaging state is touched.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import sys
import time
from collections import Counter
from pathlib import Path

import pymupdf

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "runtime" / "partbook" / "partbook_index.sqlite"
EXTRACTOR_VERSION = "pb-geom-1"

# Source-layout choices only. Rows, normalization, SQLite storage, and PDF
# verification remain shared across text-layer books.
PROFILES = {
    "hd785_raster": {"groups": "toc", "columns": "learned",
                     "figure": "left_heading", "view": "raster",
                     "engine_groups": (), "item_suffix_letters": False},
    "wa600_vector": {"groups": "printed_label", "columns": "header_offsets",
                     "figure": "table_heading", "view": "vector_header",
                     "pn_offset": -28, "desc_offset": -55,
                     "qty_offset": -10, "serial_offset": -14,
                     "engine_groups": ("AA",), "quantity_tokens": ("AR",),
                     "item_suffix_letters": True},
}

BOOKS = {
    "HD785-7-B1": {
        "model": "HD785-7",
        "profile": "hd785_raster",
        "pdf": "HD785-7/KOMATSU HD785-7 DUMP TRUCK PART BOOK SERIAL NUMBERS N10001- N10560.pdf",
        "title": "HD785-7 Parts Book, Serial N10001-N10560, Engine SAA12V140E-3B-02",
        "machine_serial_prefix": "N",
        "machine_serial_from": 10001,
        "machine_serial_to": 10560,
        "engine_serial_raw": "50013 and up (rows use SN:500013-)",
        "text_layer": True,
    },
    "HD785-7-B2": {
        "model": "HD785-7",
        "profile": "hd785_raster",
        "pdf": "HD785-7/KOMATSU HD785-7 DUMP TRUCK PART BOOK SERIAL NUMBERS N8173 and up.pdf",
        "title": "HD785-7 Parts Book, Serial N8173 and up (scanned, NOT indexed)",
        "machine_serial_prefix": "N",
        "machine_serial_from": 8173,
        "machine_serial_to": None,
        "engine_serial_raw": None,
        "text_layer": False,
    },
    "WA600-6-2010": {
        "model": "WA600-6",
        "profile": "wa600_vector",
        "pdf": "WA600-6/WA600-6-partbook-2010.pdf",
        "title": "WA600-6 Parts Book, Serial 60217 and up, Engine SAA6D170E-5A-01",
        "machine_serial_prefix": "",
        "machine_serial_from": 60217,
        "machine_serial_to": None,
        "engine_serial_raw": "511030 and up",
        "text_layer": True,
        "extractor_version": "pb-geom-wa-1",
    },
}

SCHEMA = """
CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT);
CREATE TABLE books (
  book_id TEXT PRIMARY KEY, model TEXT NOT NULL, title TEXT, source_pdf TEXT NOT NULL,
  pdf_sha256 TEXT, page_count INTEGER, machine_serial_prefix TEXT,
  machine_serial_from INTEGER, machine_serial_to INTEGER, engine_serial_raw TEXT,
  index_status TEXT NOT NULL, extractor_version TEXT, indexed_at TEXT);
CREATE TABLE book_coverages (
  book_id TEXT NOT NULL, page_from INTEGER, page_to INTEGER, kind TEXT NOT NULL,
  status TEXT NOT NULL, note TEXT);
CREATE TABLE groups (
  book_id TEXT NOT NULL, group_code TEXT NOT NULL, title TEXT, page_from INTEGER,
  page_to INTEGER, PRIMARY KEY (book_id, group_code));
CREATE TABLE figures (
  book_id TEXT NOT NULL, fig_no TEXT NOT NULL, title TEXT, group_code TEXT,
  first_page INTEGER, last_page INTEGER, row_count INTEGER,
  PRIMARY KEY (book_id, fig_no));
CREATE TABLE figure_pages (
  book_id TEXT NOT NULL, fig_no TEXT NOT NULL, pdf_page INTEGER NOT NULL,
  page_label TEXT, rotation INTEGER, has_view INTEGER, view_bbox TEXT,
  list_bbox TEXT, is_continuation INTEGER, title_raw TEXT,
  PRIMARY KEY (book_id, pdf_page));
CREATE TABLE part_occurrences (
  occ_id INTEGER PRIMARY KEY, book_id TEXT NOT NULL, fig_no TEXT NOT NULL,
  pdf_page INTEGER NOT NULL, row_ordinal INTEGER NOT NULL,
  item_raw TEXT, item_no INTEGER, item_marker TEXT, item_inherited INTEGER,
  pn_raw TEXT, pn_norm TEXT, description_raw TEXT, desc_level INTEGER,
  qty_raw TEXT, serial_raw TEXT, row_bbox TEXT NOT NULL,
  extraction_method TEXT NOT NULL, raw_text TEXT NOT NULL,
  status TEXT NOT NULL, flags TEXT NOT NULL,
  UNIQUE (book_id, pdf_page, row_ordinal));
CREATE TABLE row_applicability (
  occ_id INTEGER NOT NULL, kind TEXT NOT NULL, prefix TEXT, from_raw TEXT,
  to_raw TEXT, from_num INTEGER, to_num INTEGER, flags TEXT);
CREATE TABLE part_relations (
  occ_id INTEGER NOT NULL, related_occ_id INTEGER NOT NULL, relation TEXT NOT NULL,
  basis TEXT NOT NULL);
CREATE INDEX ix_occ_pn ON part_occurrences(pn_norm);
CREATE INDEX ix_occ_fig_item ON part_occurrences(book_id, fig_no, item_no);
CREATE INDEX ix_occ_page ON part_occurrences(book_id, pdf_page);
CREATE INDEX ix_fig_group ON figures(book_id, group_code);
CREATE INDEX ix_app ON row_applicability(kind, from_num, to_num);
CREATE INDEX ix_app_occ ON row_applicability(occ_id);
CREATE INDEX ix_rel ON part_relations(occ_id);
CREATE VIRTUAL TABLE parts_fts USING fts5(
  description, figure_title, group_title, remarks, pn_raw,
  tokenize = "unicode61 remove_diacritics 0 tokenchars '-'");
CREATE VIRTUAL TABLE figures_fts USING fts5(fig_no, title, group_title);
"""

PN_RE = re.compile(r"^[\(\[]?[A-Za-z0-9]{2,6}(?:-[A-Za-z0-9]{2,8}){1,3}[A-Za-z]{0,3}[\)\]]?$|^[A-Za-z0-9]{8,14}$")
ITEM_RE = re.compile(r"^(\d{1,3})([*\-]?)$")
ITEM_SUFFIX_RE = re.compile(r"^(\d{1,3})([A-Z*\-]?)$")
FOOTER_TOKENS = {"HD785-7", "SAA12V140E-3B", "HD785", "SAA12V140E"}
FIG_RE = re.compile(r"^(?:FIG\.?\s*)?([A-Z]\d{4}-(?:[A-Z0-9]{4}|\d{6}[A-Z]?))\b")
FIG_GARBLED_RE = re.compile(r"FIG\.?\s*(\S+)")


def norm_pn(raw: str) -> tuple[str, list[str]]:
    """Non-destructive key: uppercase, drop parentheses/space. Raw is kept separately."""
    flags = []
    s = raw.strip()
    if s.startswith("(") and s.endswith(")"):
        flags.append("pn_parenthesized")
        s = s[1:-1]
    s = s.replace(" ", "")
    if s.startswith("(") and s.endswith(")"):
        s = s[1:-1]  # double parentheses (( )) seen on some group rows
    symbols = "".join(ch for ch in s if 0xE000 <= ord(ch) <= 0xF8FF or ch in "\u2606\u2605")
    if symbols:
        # Legend symbols share the PN cell; retain the untouched raw cell too.
        flags.append("pn_symbol_prefix:" + ",".join(f"U+{ord(ch):04X}" for ch in symbols))
        s = "".join(ch for ch in s if not (0xE000 <= ord(ch) <= 0xF8FF or ch in "\u2606\u2605"))
    # Glyph 'u' rendered in front of assembly/kit PNs (symbol font leak). Keep raw,
    # expose stripped key, mark for PDF verification.
    if s[:1] == "u" and len(s) > 1 and s[1].isupper():
        flags.append("pn_leading_symbol_u")
        s = s[1:]
    return s.upper(), flags


def parse_serial(raw: str, book: dict, default_kind: str = "machine") -> list[dict]:
    out = []
    if not raw:
        return out
    s = raw.replace(" ", "")
    kind, prefix = default_kind, (book["machine_serial_prefix"] if default_kind == "machine" else "")
    if s.upper().startswith("SN:"):
        kind, prefix, s = "engine", "", s[3:]
    for part in s.split(","):
        m = re.match(r"^([A-Z]*)(\d+)(?:-(\d*))?$", part)
        if not m:
            out.append({"kind": kind, "prefix": prefix, "from_raw": part, "to_raw": None,
                        "from_num": None, "to_num": None, "flags": ["serial_unparsed"]})
            continue
        pfx, a, b = m.group(1) or prefix, m.group(2), m.group(3)
        flags = []
        if b is None and "-" not in part:
            to_num = int(a)  # single serial
            flags.append("single_serial")
        elif not b:
            to_num = None  # open ended "N10001" style handled below
        else:
            if len(b) < len(a):
                to_num = int(a[: len(a) - len(b)] + b)
                flags.append("range_end_abbreviated")
            else:
                to_num = int(b)
        if kind == "machine" and b is None and "-" not in part:
            # Machine rows in B1 list only the start serial ("N10001") = from that serial on.
            to_num = None
            flags = ["open_start_only"]
        out.append({"kind": kind, "prefix": pfx, "from_raw": a, "to_raw": b,
                    "from_num": int(a), "to_num": to_num, "flags": flags})
    return out


def page_words(page):
    m = page.rotation_matrix
    res = []
    for w in page.get_text("words"):
        r = pymupdf.Rect(w[:4]) * m
        res.append((r.x0, r.y0, r.x1, r.y1, w[4]))
    return res


def cluster_rows(words, tol=3.2):
    words = sorted(words, key=lambda w: ((w[1] + w[3]) / 2, w[0]))
    rows, cur, cy = [], [], None
    for w in words:
        yc = (w[1] + w[3]) / 2
        if cy is None or abs(yc - cy) <= tol:
            cur.append(w)
            cy = yc if cy is None else (cy * (len(cur) - 1) + yc) / len(cur)
        else:
            rows.append(sorted(cur, key=lambda z: z[0]))
            cur, cy = [w], yc
    if cur:
        rows.append(sorted(cur, key=lambda z: z[0]))
    return rows


def rect_str(r):
    return ",".join(f"{v:.1f}" for v in r)


def clean_description(raw: str, profile: dict) -> str:
    if profile["columns"] == "header_offsets":
        return re.sub(r"\.{3,}\s*$", "", raw).rstrip()
    return raw


def vector_view_bbox(page, list_bbox):
    """Bounds of vector paths on the view half, in displayed page coordinates."""
    selected = []
    for path in page.get_drawings():
        rect = path["rect"] * page.rotation_matrix
        if (rect.width > 3 and rect.height > 3 and
                rect.x0 < list_bbox.x0 and rect.x1 <= list_bbox.x0 + 3 and
                rect.y1 > 20 and rect.y0 < page.rect.height - 20):
            selected.append(rect)
    if not selected:
        return ""
    region = selected[0]
    for rect in selected[1:]:
        region |= rect
    return rect_str(region)


def parse_list_page(page, pno: int, profile: dict | None = None):
    """Return (source page metadata, rows) or None without a parts-list table."""
    profile = profile or PROFILES["hd785_raster"]
    item_re = ITEM_SUFFIX_RE if profile["item_suffix_letters"] else ITEM_RE
    words = page_words(page)
    hdr = {}
    for w in words:
        t = w[4]
        if t == "INDEX" and "index" not in hdr:
            hdr["index"] = w
        elif t == "PART" and "part" not in hdr:
            hdr["part"] = w
        elif t == "DESCRIPTION" and "desc" not in hdr:
            hdr["desc"] = w
        elif t in ("Q'TY", "QT'Y", "QTY") and "qty" not in hdr:
            hdr["qty"] = w
        elif t == "SERIAL" and "serial" not in hdr:
            hdr["serial"] = w
    if len(hdr) < 5:
        return None
    hy = hdr["index"][3]
    # B1 has several physical layouts (header vs data x offsets differ), so column
    # starts are learned from this page's own data tokens, anchored by the headers.
    x_idx = hdr["index"][0] - 30  # item markers ("12*") start left of header
    page_h = page.rect.height
    foot_y = [w[1] for w in words if w[1] > page_h * 0.7 and w[4] in FOOTER_TOKENS]
    bottom = min(foot_y) - 1 if foot_y else page_h - 60
    body = [w for w in words if w[1] > hy + 0.5 and w[3] < bottom]
    title_words = [w for w in words if w[0] < x_idx and w[1] <= hy + 30 and w[3] < bottom]
    footer = [w for w in words if w[1] >= bottom]
    lst = [w for w in body if w[0] >= x_idx]
    def mode_x(cands, default):
        c = Counter(round(w[0]) for w in cands)
        return c.most_common(1)[0][0] if c else default
    if profile["columns"] == "header_offsets":
        # WA600 has the same five logical columns in two horizontal placements.
        # Their data offsets from the printed headers are stable on both halves.
        x_pn = hdr["part"][0] + profile["pn_offset"]
        x_desc = hdr["desc"][0] + profile["desc_offset"]
        x_qty = hdr["qty"][0] + profile["qty_offset"]
        x_ser = hdr["serial"][0] + profile["serial_offset"]
    else:
        x_pn = mode_x([w for w in lst if PN_RE.match(w[4]) and w[0] < hdr["desc"][0] - 20
                       and not item_re.match(w[4])], hdr["part"][0]) - 1.5
        x_ser = mode_x([w for w in lst if re.match(r"^(N\d{3,}|SN:)", w[4]) and w[0] > hdr["qty"][0] - 20],
                       hdr["serial"][0] - 12) - 1.5
        x_desc = mode_x([w for w in lst if x_pn + 45 < w[0] < hdr["qty"][0] - 20
                         and re.match(r"^[.A-Z(]", w[4])], hdr["desc"][0] - 50) - 3
        x_qty = x_ser - 22  # qty is right-aligned just before serial column
    fig_line = " ".join(w[4] for w in sorted(
        [w for w in words if abs(w[1] - hdr["index"][1]) < 9 and w[0] < x_idx], key=lambda z: z[0]))
    title_cont = " ".join(w[4] for w in sorted(
        [w for w in title_words if w[1] > hdr["index"][3]], key=lambda z: (z[1], z[0])))
    rows = []
    for ordinal, rw in enumerate(cluster_rows(lst), start=1):
        cols = {"item": [], "pn": [], "desc": [], "qty": [], "serial": []}
        for w in rw:
            x, t = w[0], w[4]
            if x < x_pn and not cols["item"] and not cols["pn"] and item_re.match(t):
                cols["item"].append(w)
            elif x < x_desc:
                cols["pn"].append(w)
            elif x < x_qty:
                cols["desc"].append(w)
            elif x < x_ser:
                if (re.match(r"^\d+$", t) or t in profile.get("quantity_tokens", ())) and not cols["qty"]:
                    cols["qty"].append(w)
                else:
                    cols["desc"].append(w)
            else:
                cols["serial"].append(w)
        txt = {k: " ".join(w[4] for w in v) for k, v in cols.items()}
        bbox = pymupdf.Rect(min(w[0] for w in rw), min(w[1] for w in rw),
                            max(w[2] for w in rw), max(w[3] for w in rw))
        raw = " | ".join(f"{k}={v}" for k, v in txt.items() if v)
        rows.append({"ordinal": ordinal, "txt": txt, "bbox": bbox, "raw": raw})
    fp = {"fig_line": fig_line, "title_cont": title_cont,
          "footer": " ".join(w[4] for w in sorted(footer, key=lambda z: z[0])),
          "list_bbox": pymupdf.Rect(x_idx, hdr["index"][1], page.rect.width, bottom),
          "columns": {"pn": x_pn, "desc": x_desc, "qty": x_qty, "serial": x_ser}}
    if profile["figure"] == "table_heading":
        text = page.get_text("text")
        figure = re.search(r"FIG\s+NO\.\s*:\s*([A-Z]\d{4}-[A-Z0-9]{4,7})", text, re.I)
        printed = re.search(r"(?m)^\s*([A-Z]{1,2}\d?)-\s*(\d+)\s*$", text)
        before_table = text.split("INDEX", 1)[0]
        view = re.search(r"FIG\.\s*([A-Z]\d{4}-[A-Z0-9]{4,7})\s*([^\n]*)",
                         before_table, re.I)
        fp["table_figure"] = figure.group(1).upper() if figure else None
        fp["view_figure"] = view.group(1).upper() if view else None
        fp["page_label"] = (printed.group(1) + "-" + printed.group(2)) if printed else None
        fp["group_code"] = printed.group(1) if printed else None
        if view:
            lines = before_table[view.end():].splitlines()
            title = [view.group(2).strip()]
            for line in lines[:3]:
                line = line.strip()
                if not line or re.match(r"^[A-Z]{1,2}\d?-\s*\d+$", line):
                    break
                title.append(line)
            fp["view_title"] = " ".join(x for x in title if x)
    return fp, rows


def other_book_counts(con: sqlite3.Connection, selected_book: str) -> dict:
    """Guard additive builds against changing another book's indexed records."""
    result = {}
    for (bid,) in con.execute("SELECT book_id FROM books WHERE book_id<>?", (selected_book,)):
        counts = [con.execute(f"SELECT count(*) FROM {table} WHERE book_id=?", (bid,)).fetchone()[0]
                  for table in ("groups", "figures", "figure_pages",
                                "part_occurrences", "book_coverages")]
        counts.append(con.execute(
            "SELECT count(*) FROM row_applicability a JOIN part_occurrences o "
            "ON o.occ_id=a.occ_id WHERE o.book_id=?", (bid,)).fetchone()[0])
        counts.append(con.execute(
            "SELECT count(*) FROM part_relations r JOIN part_occurrences o "
            "ON o.occ_id=r.occ_id WHERE o.book_id=?", (bid,)).fetchone()[0])
        counts.append(con.execute(
            "SELECT count(*) FROM parts_fts f JOIN part_occurrences o "
            "ON o.occ_id=f.rowid WHERE o.book_id=?", (bid,)).fetchone()[0])
        result[bid] = (con.execute("SELECT * FROM books WHERE book_id=?", (bid,)).fetchone(),
                       tuple(counts))
    return result


def build(book_id: str, db_path: Path) -> dict:
    book = BOOKS[book_id]
    profile = PROFILES[book["profile"]]
    item_re = ITEM_SUFFIX_RE if profile["item_suffix_letters"] else ITEM_RE
    pdf = ROOT / book["pdf"]
    t0 = time.perf_counter()
    tmp = db_path.with_suffix(".building")
    db_path.parent.mkdir(parents=True, exist_ok=True)
    if tmp.exists():
        tmp.unlink()
    con = sqlite3.connect(tmp)
    if db_path.exists():
        source = sqlite3.connect(f"file:{db_path.resolve().as_posix()}?mode=ro", uri=True)
        source.backup(con)
        source.close()
        con.execute("DELETE FROM part_relations WHERE occ_id IN "
                    "(SELECT occ_id FROM part_occurrences WHERE book_id=?) "
                    "OR related_occ_id IN "
                    "(SELECT occ_id FROM part_occurrences WHERE book_id=?)",
                    (book_id, book_id))
        con.execute("DELETE FROM row_applicability WHERE occ_id IN "
                    "(SELECT occ_id FROM part_occurrences WHERE book_id=?)", (book_id,))
        con.execute("DELETE FROM parts_fts WHERE rowid IN "
                    "(SELECT occ_id FROM part_occurrences WHERE book_id=?)", (book_id,))
        for table in ("part_occurrences", "figure_pages", "figures", "groups",
                      "book_coverages", "books"):
            con.execute(f"DELETE FROM {table} WHERE book_id=?", (book_id,))
    else:
        con.executescript(SCHEMA)
    preserved = other_book_counts(con, book_id)
    sha = hashlib.sha256(pdf.read_bytes()).hexdigest()
    doc = pymupdf.open(pdf)
    groups = []
    printed_groups = {}
    if profile["groups"] == "toc":
        toc = [(t[1], t[2]) for t in doc.get_toc()]
        for i, (title, start) in enumerate(toc):
            m = re.match(r"^([A-Z]\d)\s+(.*)$", title)
            end = (toc[i + 1][1] - 1) if i + 1 < len(toc) else doc.page_count
            if m:
                groups.append((m.group(1), m.group(2).strip(), start, end))
        con.executemany("INSERT INTO groups VALUES (?,?,?,?,?)", [(book_id, *g) for g in groups])

    def group_for(p):
        for g in groups:
            if g[2] <= p <= g[3]:
                return g
        return None

    stats = {"pages": doc.page_count, "list_pages": 0, "rows": 0, "needs_verification": 0,
             "rejected_rows": 0, "figures": 0, "no_table_pages": [],
             "figure_missing_pages": [], "figure_view_mismatch_pages": []}
    last = {"fig": None, "item_no": None, "occ": None, "item_raw": None}
    fig_rows = {}
    fig_meta = {}
    occ_id = con.execute("SELECT coalesce(max(occ_id),0) FROM part_occurrences").fetchone()[0]
    for pi in range(doc.page_count):
        pno = pi + 1
        g = group_for(pno) if profile["groups"] == "toc" else None
        if profile["groups"] == "toc" and (g is None or g[0] in ("NU",)):
            continue
        page = doc[pi]
        parsed = parse_list_page(page, pno, profile)
        if parsed is None:
            stats["no_table_pages"].append(pno)
            continue
        fp, rows = parsed
        if profile["groups"] == "printed_label":
            code = fp.get("group_code")
            if not code:
                stats.setdefault("group_missing_pages", []).append(pno)
                continue
            bounds = printed_groups.setdefault(code, [pno, pno])
            bounds[1] = pno
            g = (code, code, bounds[0], bounds[1])
        stats["list_pages"] += 1
        page_flags = []
        if profile["figure"] == "table_heading":
            fig_no = fp.get("table_figure") or f"UNKNOWN-P{pno}"
            title = fp.get("view_title") or fig_meta.get(fig_no, {}).get("title", "")
            fig_line = "FIG NO. : " + (fp.get("table_figure") or "")
            if not fp.get("table_figure"):
                page_flags.append("figure_header_missing")
                stats["figure_missing_pages"].append(pno)
            if fp.get("view_figure") and fp["view_figure"] != fig_no:
                page_flags.append("view_figure_mismatch")
                stats["figure_view_mismatch_pages"].append(pno)
        else:
            fig_line = re.sub(r"^FIG\.\s+", "FIG.", fp["fig_line"])
            fm = FIG_RE.match(fig_line)
            if fm:
                fig_no = fm.group(1)
                title = fig_line[fm.end():].strip()
                if fp["title_cont"]:
                    title = f"{title} {fp['title_cont']}".strip()
            elif FIG_GARBLED_RE.search(fp["fig_line"]):
                gm = FIG_GARBLED_RE.search(fp["fig_line"])
                fig_no = "RAW:" + gm.group(1)
                title = fp["fig_line"][gm.end():].strip()
                page_flags.append("figure_header_garbled")
            else:
                fig_no = last["fig"] or f"UNKNOWN-P{pno}"
                title = fp["fig_line"]
                page_flags.append("figure_header_missing")
        is_cont = fig_no == last["fig"]
        if fig_no != last["fig"]:
            last.update(item_no=None, occ=None, item_raw=None)
        if profile["view"] == "vector_header":
            has_view = fp.get("view_figure") == fig_no
            vb = vector_view_bbox(page, fp["list_bbox"]) if has_view else ""
            label = fp.get("page_label") or ""
            title_raw = fp.get("view_title") or fig_line
        else:
            views = [i for i in page.get_image_info() if i["bbox"][2] - i["bbox"][0] > 100]
            vb = ""
            if views:
                vr = pymupdf.Rect(views[0]["bbox"]) * page.rotation_matrix
                vb = rect_str(vr)
            has_view = bool(views)
            label = fp["footer"]
            title_raw = fp["fig_line"]
        con.execute("INSERT OR REPLACE INTO figure_pages VALUES (?,?,?,?,?,?,?,?,?,?)",
                    (book_id, fig_no, pno, label, page.rotation, int(has_view), vb,
                     rect_str(fp["list_bbox"]), int(is_cont), title_raw))
        meta = fig_meta.setdefault(fig_no, {"title": title, "group": g[0], "first": pno, "last": pno})
        meta["last"] = pno
        if len(title) > len(meta["title"]) and not is_cont:
            meta["title"] = title
        prev_row = None
        for r in rows:
            t = r["txt"]
            flags = list(page_flags)
            item_raw = t["item"] or None
            pn_raw = t["pn"] or None
            item_no, marker, inherited = None, None, 0
            if item_raw:
                im = item_re.match(item_raw)
                if im:
                    item_no, marker = int(im.group(1)), (im.group(2) or None)
                else:
                    flags.append("item_unparsed")
            # Description-only lines continue the preceding source row.
            if not item_raw and not pn_raw and not t["qty"] and not t["serial"] and t["desc"] and prev_row:
                con.execute("UPDATE part_occurrences SET description_raw = description_raw || ' ' || ?, "
                            "raw_text = raw_text || ' || ' || ?, flags = json_insert(flags, '$[#]', 'desc_wrapped') "
                            "WHERE occ_id=?", (clean_description(t["desc"], profile), r["raw"], prev_row))
                continue
            pn_norm, pnf = norm_pn(pn_raw) if pn_raw else (None, [])
            flags += pnf
            if pn_raw and not pn_norm:
                flags.append("pn_symbol_only")
            elif not pn_raw:
                flags.append("no_part_number")
            elif not PN_RE.match(pn_norm):
                flags.append("pn_pattern_unusual")
            if item_no is None and not item_raw:
                if last["item_no"] is not None:
                    item_no, inherited = last["item_no"], 1
                    flags.append("item_inherited_from_previous_row")
                    if prev_row is None:
                        flags.append("continued_from_previous_page")
                else:
                    flags.append("item_missing")
            if marker:
                flags.append("item_marker_star" if marker == "*" else
                             "item_marker_dash" if marker == "-" else
                             "item_suffix_" + marker)
            if "pn_symbol_only" in flags:
                pn_norm = None
            if not t["qty"]:
                flags.append("qty_missing")
            elif not re.match(r"^\d+$", t["qty"]) and t["qty"] not in profile.get("quantity_tokens", ()):
                flags.append("qty_nonnumeric")
            if not t["serial"]:
                flags.append("serial_missing")
            desc = clean_description(t["desc"], profile) or None
            level = 0
            if desc:
                level = len(desc) - len(desc.lstrip("."))
            hard = {"no_part_number", "item_unparsed", "item_missing", "pn_pattern_unusual",
                    "figure_header_missing", "figure_header_garbled", "qty_nonnumeric", "pn_symbol_only", "qty_missing"}
            status = "needs_verification" if (hard & set(flags)) else "ok"
            if "no_part_number" in flags:
                status = "rejected"
                stats["rejected_rows"] += 1
            elif status == "needs_verification":
                stats["needs_verification"] += 1
            occ_id += 1
            con.execute("INSERT INTO part_occurrences VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                        (occ_id, book_id, fig_no, pno, r["ordinal"], item_raw, item_no, marker, inherited,
                         pn_raw, pn_norm, desc, level, t["qty"] or None, t["serial"] or None,
                         rect_str(r["bbox"]), book.get("extractor_version", EXTRACTOR_VERSION),
                         r["raw"], status, json.dumps(flags)))
            serial_kind = "engine" if g[0] in profile["engine_groups"] else "machine"
            for a in parse_serial(t["serial"], book, serial_kind):
                con.execute("INSERT INTO row_applicability VALUES (?,?,?,?,?,?,?,?)",
                            (occ_id, a["kind"], a["prefix"], a["from_raw"], a["to_raw"],
                             a["from_num"], a["to_num"], json.dumps(a["flags"])))
            if inherited and last["occ"]:
                con.execute("INSERT INTO part_relations VALUES (?,?,?,?)",
                            (occ_id, last["occ"], "same_item_variant",
                             "row has no item number; follows row of same item (serial split / alternative)"))
            if marker == "*" or marker == "-":
                pass  # linked after the whole figure is known
            stats["rows"] += 1
            fig_rows[fig_no] = fig_rows.get(fig_no, 0) + 1
            if item_no is not None:
                if not inherited:
                    last["occ"] = occ_id
                last["item_no"] = item_no
            prev_row = occ_id
        last["fig"] = fig_no
    if profile["groups"] == "printed_label":
        groups = [(code, code, bounds[0], bounds[1])
                  for code, bounds in sorted(printed_groups.items(), key=lambda pair: pair[1][0])]
        con.executemany("INSERT INTO groups VALUES (?,?,?,?,?)",
                        [(book_id, *group) for group in groups])
    # star/dash marked rows: relate to the unmarked row with same item in the same figure
    con.execute("""INSERT INTO part_relations
        SELECT a.occ_id, b.occ_id, CASE a.item_marker WHEN '*' THEN 'marked_star_same_item' ELSE 'marked_dash_same_item' END,
               'item marker in INDEX column; meaning per book symbol legend, verify on PDF'
        FROM part_occurrences a JOIN part_occurrences b
          ON a.book_id=b.book_id AND a.fig_no=b.fig_no AND a.item_no=b.item_no
         AND b.item_marker IS NULL AND b.item_inherited=0 AND a.occ_id<>b.occ_id
        WHERE a.item_marker IN ('*','-') AND a.book_id=?""", (book_id,))
    for fig_no, m in fig_meta.items():
        con.execute("INSERT INTO figures VALUES (?,?,?,?,?,?,?)",
                    (book_id, fig_no, m["title"], m["group"], m["first"], m["last"], fig_rows.get(fig_no, 0)))
        gt = next((g[1] for g in groups if g[0] == m["group"]), "")
    stats["figures"] = len(fig_meta)
    con.execute("DELETE FROM figures_fts")
    con.execute("""INSERT INTO figures_fts(fig_no,title,group_title)
        SELECT f.fig_no, coalesce(f.title,''), coalesce(g.title,'')
        FROM figures f LEFT JOIN groups g ON g.book_id=f.book_id AND g.group_code=f.group_code
        ORDER BY f.book_id, f.first_page, f.fig_no""")
    con.execute("""INSERT INTO parts_fts(rowid, description, figure_title, group_title, remarks, pn_raw)
        SELECT o.occ_id, coalesce(o.description_raw,''), coalesce(f.title,''), coalesce(g.title,''),
               coalesce(o.serial_raw,''), coalesce(o.pn_raw,'')
        FROM part_occurrences o JOIN figures f ON f.book_id=o.book_id AND f.fig_no=o.fig_no
        LEFT JOIN groups g ON g.book_id=f.book_id AND g.group_code=f.group_code
        WHERE o.book_id=?""", (book_id,))
    now = time.strftime("%Y-%m-%dT%H:%M:%S")
    for bid, b in BOOKS.items():
        if b["model"] != book["model"]:
            continue
        status = "indexed_partial" if bid == book_id else "not_indexed_scanned"
        con.execute("INSERT OR REPLACE INTO books VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    (bid, b["model"], b["title"], b["pdf"], sha if bid == book_id else None,
                     doc.page_count if bid == book_id else None, b["machine_serial_prefix"],
                     b["machine_serial_from"], b["machine_serial_to"], b["engine_serial_raw"], status,
                     b.get("extractor_version", EXTRACTOR_VERSION) if bid == book_id else None,
                     now if bid == book_id else None))
    cov = [(book_id, g[2], g[3], f"group {g[0]} {g[1]}", "text_layer_indexed",
            "geometry extraction; rows may be flagged needs_verification") for g in groups]
    if stats["no_table_pages"]:
        cov.append((book_id, None, None, "pages_without_parts_table", "not_indexed",
                    "section dividers / blank / non-table pages: " + ",".join(map(str, stats["no_table_pages"]))))
    if profile["groups"] == "toc":
        # Keep the existing HD text-layer coverage declarations unchanged.
        cov.append((book_id, 1, 21, "front matter & contents", "not_indexed",
                    "cover/foreword/contents"))
        cov.append((book_id, 1096, doc.page_count, "NUMERICAL INDEX", "not_indexed",
                    "custom-encoded font, text not extractable; use figure rows instead"))
    else:
        indexed_pages = sorted(
            page for page, in con.execute("SELECT pdf_page FROM figure_pages WHERE book_id=?", (book_id,)))
        if indexed_pages:
            if indexed_pages[0] > 1:
                cov.append((book_id, 1, indexed_pages[0] - 1, "front matter", "not_indexed",
                            "no parts-list table"))
            if indexed_pages[-1] < doc.page_count:
                cov.append((book_id, indexed_pages[-1] + 1, doc.page_count,
                            "end matter / numerical index", "not_indexed", "no parts-list table"))
    for bid, other in BOOKS.items():
        if other["model"] == book["model"] and not other["text_layer"]:
            con.execute("DELETE FROM book_coverages WHERE book_id=?", (bid,))
            cov.append((bid, None, None, "entire book", "not_indexed",
                        "scanned without text layer; OCR out of pilot scope"))
    con.executemany("INSERT INTO book_coverages VALUES (?,?,?,?,?,?)", cov)
    stats["seconds"] = round(time.perf_counter() - t0, 2)
    stats["no_table_pages_count"] = len(stats["no_table_pages"])
    per_book_meta = [
        ("stats:" + book_id, json.dumps({k: v for k, v in stats.items() if k != "no_table_pages"})),
        ("extractor_version:" + book_id, book.get("extractor_version", EXTRACTOR_VERSION)),
        ("built_at:" + book_id, now),
    ]
    con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", per_book_meta)
    if profile["groups"] == "toc":
        con.executemany("INSERT OR REPLACE INTO meta VALUES (?,?)", [
            ("extractor_version", EXTRACTOR_VERSION), ("built_at", now),
            ("stats", json.dumps({k: v for k, v in stats.items() if k != "no_table_pages"})),
            ("coverage_complete", "false"),
            ("coverage_note", "Only HD785-7 B1 (N10001-N10560) indexed. B2 (N8173 and up) scanned, not indexed. "
                              "Index miss never means the part does not exist.")])
    if other_book_counts(con, book_id) != preserved:
        raise ValueError("Additive Part Book build changed another book; candidate DB not installed")
    con.commit()
    con.execute("VACUUM")
    con.close()
    doc.close()
    tmp.replace(db_path)
    return stats


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--book", default="HD785-7-B1", choices=[k for k, v in BOOKS.items() if v["text_layer"]])
    ap.add_argument("--db", default=str(DEFAULT_DB))
    a = ap.parse_args()
    st = build(a.book, Path(a.db))
    st.pop("no_table_pages", None)
    print(json.dumps({"db": a.db, **st}, ensure_ascii=False))


if __name__ == "__main__":
    main()
