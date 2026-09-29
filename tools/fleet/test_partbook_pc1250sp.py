"""Source-grounded PC1250SP-8R Part Book and preservation checks."""
import contextlib
import io
import json
import os
import shutil
import sqlite3
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import pymupdf

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))
import partbook_index as index  # noqa: E402
import partbook_lookup as lookup  # noqa: E402

DB = Path(os.environ.get("PARTBOOK_TEST_DB", str(lookup.DEFAULT_DB)))
PDF = ROOT / "PC1250-8R/PC1250-8R-1 PARTBOOK-pdf.pdf"
OTHERS = ("HD785-7-B1", "WA600-6-2010", "HD785-5", "HD465-7R")


def run(*args, model="PC1250SP-8R"):
    with contextlib.redirect_stdout(io.StringIO()):
        return lookup.main(["--model", model, "--db", str(DB), *args])


def source_has(page, *terms):
    with pymupdf.open(PDF) as doc:
        text = doc[page - 1].get_text()
    return all(term in text for term in terms)


class PC1250SPPartBook(unittest.TestCase):
    def test_identity_and_model_scope_from_pdf(self):
        with pymupdf.open(PDF) as doc:
            self.assertEqual(len(doc), 789)
        self.assertTrue(source_has(2, "PC1250-8R", "35001-UP"))
        self.assertTrue(source_has(4, "SAA6D170E-5CR-W", "610001-UP"))
        self.assertTrue(source_has(137, "PC1250SP-8R", "35001-UP", "W/O EGR"))
        self.assertTrue(source_has(136, "D375A-6R"))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            self.assertEqual(con.execute(
                "SELECT count(*) FROM part_occurrences WHERE book_id='PC1250SP-8R' "
                "AND pdf_page=136").fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT page_count,index_status FROM books WHERE book_id='PC1250SP-8R'"
            ).fetchone(), (789, "indexed_partial"))

    def test_machine_exact_pn_and_direct_view(self):
        row = run("--part-number", "21N-01-11170", "--verify")["candidates"][0]
        self.assertEqual((row["figure"], row["item_raw"], row["pdf_pages"],
                          row["description"], row["quantity"], row["applicability"],
                          row["view_pages"]),
                         ("B0100-01A0", "1", [137], "BRACKET", "2", "35001-", [137]))
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(137, "Reference:B0100-01A0", "21N-01-11170", "35001-"))
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "pc1250sp-view-test"}):
            image = run("--part-number", "21N-01-11170", "--verify",
                        "--auto-render-verified")
        self.assertEqual([r["pdf_page"] for r in image["rendered"]], [137])
        self.assertTrue(image["rendered"][0]["output"].startswith("MEDIA:"))

    def test_engine_and_continuation(self):
        engine = run("--part-number", "6240-41-4430", "--verify")["candidates"][0]
        self.assertEqual((engine["figure"], engine["item_raw"], engine["pdf_pages"],
                          engine["quantity"], engine["applicability"], engine["view_pages"]),
                         ("A1010-A6B8", "19", [5], "24", "SN:610001--", [4]))
        self.assertEqual(engine["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(5, "6240-41-4430", "SN:610001--"))
        continued = run("--part-number", "08037-02512", "--figure",
                        "B0300-01A2", "--verify")["candidates"][0]
        self.assertEqual((continued["item_raw"], continued["pdf_pages"],
                          continued["view_pages"], continued["quantity"]),
                         ("31", [139], [138], "2"))
        self.assertEqual(continued["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(139, "Reference:B0300-01A2", "08037-02512"))

    def test_applicability_ranges_and_suffix_ar(self):
        closed = run("--part-number", "21N-62-33271", "--serial", "35016",
                     "--verify")["candidates"][0]
        self.assertEqual((closed["figure"], closed["item_raw"], closed["pdf_pages"],
                          closed["applicability"], closed["serial_match"]),
                         ("H0120-01A0", "50", [251], "35001-35016", True))
        self.assertEqual(run("--part-number", "21N-62-33271", "--serial", "35017")["found"], 0)
        self.assertTrue(source_has(251, "21N-62-33271", "35001-35016"))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            engine = con.execute(
                "SELECT a.kind,a.from_num,a.to_num FROM row_applicability a "
                "JOIN part_occurrences o ON o.occ_id=a.occ_id "
                "WHERE o.book_id='PC1250SP-8R' AND o.pdf_page=67 "
                "AND o.pn_norm='6245-71-5790'").fetchone()
            self.assertEqual(engine, ("engine", 610001, 610006))
        self.assertTrue(source_has(67, "6245-71-5790", "SN:610001--610006"))
        suffix = run("--part-number", "HM6400-0012", "--figure",
                     "K1110-06A0A", "--verify")["candidates"][0]
        self.assertEqual((suffix["item_raw"], suffix["quantity"], suffix["pdf_pages"]),
                         ("10A", "3", [418]))
        self.assertEqual(suffix["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(418, "HM6400-0012", "10A"))
        ar = run("--part-number", "09920-00150", "--figure",
                 "A2110-A6J9", "--verify")["candidates"][0]
        self.assertEqual((ar["item_raw"], ar["quantity"], ar["pdf_pages"]),
                         ("55", "AR", [39]))
        self.assertTrue(source_has(39, "09920-00150", "AR"))

    def test_narrow_columns_group_items_and_short_suffix_pns(self):
        cases = (
            ("08192-12200", "E0200-10A0", "1", [206], "CONNECTOR", "2"),
            ("RFU419B-418-A", "Y1062-01A0A", "1", [642], "ROD", "1"),
            ("707-01-XZ502", "Y1620-01A0", "G1", [713],
             "CYLINDER GROUP,L.H. (FINAL COATING)", "1"),
            ("YU702743-3", "Y1680-01A0", "1", [770], "JOINT", "1"),
        )
        for pn, figure, item, pages, desc, qty in cases:
            result = run("--part-number", pn, "--figure", figure, "--verify")
            self.assertEqual(result["found"], 1)
            row = result["candidates"][0]
            self.assertEqual((row["figure"], row["item_raw"], row["pdf_pages"],
                              row["description"].split(" || ")[0], row["quantity"],
                              row["pdf_verification"]["status"]),
                             (figure, item, pages, desc, qty, "VERIFIED"))
            self.assertTrue(source_has(pages[0], pn, figure))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            self.assertEqual(con.execute(
                "SELECT count(*) FROM part_occurrences WHERE book_id='PC1250SP-8R' "
                "AND pn_norm='08192-12200CONNECTOR'").fetchone()[0], 0)

    def test_unusual_pump_and_ambiguity_have_no_media(self):
        pump = run("--part-number", "708-2H-00440", "--limit", "40",
                   "--verify", "--auto-render-verified")
        self.assertGreaterEqual(len({c["figure"] for c in pump["candidates"]}), 16)
        self.assertIn("Y1600-46A0", {c["figure"] for c in pump["candidates"]})
        self.assertNotIn("rendered", pump)
        self.assertTrue(source_has(689, "(708-2H-00440)", "Y1600-46A0"))
        ambiguous = run("--part-number", "21N-00-41170", "--verify",
                        "--auto-render-verified")
        self.assertEqual(ambiguous["found"], 1)
        self.assertEqual(ambiguous["candidates"][0]["applicability"], "35001-@")
        self.assertIn("ambiguous_applicability_symbol", ambiguous["candidates"][0]["flags"])
        self.assertEqual(ambiguous["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertNotIn("rendered", ambiguous)
        self.assertTrue(source_has(605, "21N-00-41170", "35001-@"))

    def test_blank_source_pn_is_rejected_without_reconstruction(self):
        self.assertTrue(source_has(5, "GROMMET,WATER", "SN:610001--"))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            rows = con.execute(
                "SELECT pn_raw,pn_norm,status FROM part_occurrences "
                "WHERE book_id='PC1250SP-8R' AND pdf_page=5 "
                "AND item_raw='27' AND description_raw='GROMMET,WATER'").fetchall()
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row == (None, None, "rejected") for row in rows))

    def test_miss_footers_duplicates_and_isolation(self):
        self.assertEqual(run("--part-number", "99999-99-99999")["found"], 0)
        self.assertEqual(run("--part-number", "Reference:B0100-01A0")["found"], 0)
        shared = run("--part-number", "08037-02512", "--verify",
                     "--auto-render-verified")
        self.assertEqual(len({c["figure"] for c in shared["candidates"]}), 3)
        self.assertNotIn("rendered", shared)
        with contextlib.closing(sqlite3.connect(DB)) as con:
            self.assertEqual(con.execute(
                "SELECT count(*) FROM part_occurrences WHERE book_id='PC1250SP-8R' "
                "AND pn_raw LIKE 'Reference:%'").fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT count(*) FROM (SELECT pdf_page,row_ordinal,count(*) n "
                "FROM part_occurrences WHERE book_id='PC1250SP-8R' "
                "GROUP BY pdf_page,row_ordinal HAVING n>1)").fetchone()[0], 0)
        for old_model, pn in (("HD785-7", "6218-11-5830"),
                              ("WA600-6", "426-22-31340"),
                              ("HD785-5", "561-86-67410"),
                              ("HD465-7R", "569-46-62810")):
            self.assertEqual(run("--part-number", pn)["found"], 0)
            self.assertEqual(run("--part-number", "21N-01-11170",
                                 model=old_model)["found"], 0)

    def test_shared_pn_keeps_each_books_figure_and_serial(self):
        # Same raw PN occurs in three actual books with different source identity.
        cases = (
            ("PC1250SP-8R", "PC1250SP-8R", "A1030-A6A2", [6], "SN:610001--"),
            ("HD465-7R", "HD465-7R", "A1030-A6A3", [14], "610017"),
            ("WA600-6", "WA600-6-2010", "A1030-A6A5", [38], "510001-"),
        )
        for model, book, figure, pages, serial in cases:
            row = run("--part-number", "6245-21-6110", "--verify",
                      model=model)["candidates"][0]
            self.assertEqual((row["book_id"], row["figure"], row["pdf_pages"],
                              row["applicability"], row["pdf_verification"]["status"]),
                             (book, figure, pages, serial, "VERIFIED"))
        self.assertTrue(source_has(6, "6245-21-6110", "A1030-A6A2"))

    def test_second_build_and_four_book_preservation(self):
        with contextlib.closing(sqlite3.connect(DB)) as con:
            before = {b: index.book_digest(con, b) for b in OTHERS}
        with tempfile.TemporaryDirectory(prefix="pc1250sp-digest-") as tmp:
            candidate = Path(tmp) / "candidate.sqlite"
            shutil.copy2(DB, candidate)
            index.build("PC1250SP-8R", candidate)
            with contextlib.closing(sqlite3.connect(candidate)) as con:
                first = index.book_digest(con, "PC1250SP-8R")
                mid = {b: index.book_digest(con, b) for b in OTHERS}
            index.build("PC1250SP-8R", candidate)
            with contextlib.closing(sqlite3.connect(candidate)) as con:
                second = index.book_digest(con, "PC1250SP-8R")
                after = {b: index.book_digest(con, b) for b in OTHERS}
        self.assertEqual(first, second)
        self.assertEqual(before, mid)
        self.assertEqual(before, after)
        self.assertEqual(set(first), {
            "groups", "figures", "figure_pages", "part_occurrences",
            "row_applicability", "part_relations", "parts_fts", "combined"})


if __name__ == "__main__":
    unittest.main()
