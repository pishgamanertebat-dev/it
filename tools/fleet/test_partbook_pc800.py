"""Source-grounded PC800-8 Part Book and preservation checks."""
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
PDF = ROOT / "PC800/KOMATSU PC800-8 PART BOOK.pdf"
OTHERS = ("HD785-7-B1", "WA600-6-2010", "HD785-5", "HD465-7R", "PC1250SP-8R")


def run(*args, model="PC800-8"):
    with contextlib.redirect_stdout(io.StringIO()):
        return lookup.main(["--model", model, "--db", str(DB), *args])


def source_has(page, *terms):
    with pymupdf.open(PDF) as doc:
        text = doc[page - 1].get_text()
    return all(term in text for term in terms)


class PC800PartBook(unittest.TestCase):
    def test_identity_excludes_lc_variant_pages(self):
        with pymupdf.open(PDF) as doc:
            self.assertEqual(len(doc), 1097)
        self.assertTrue(source_has(1, "PC800-8", "50001-UP"))
        self.assertFalse(source_has(1, "PC800-8R"))
        self.assertTrue(source_has(2, "PC800-8", "Reference:B0600-03A0"))
        self.assertTrue(source_has(574, "SAA6D140E-5F-03", "530001-UP"))
        self.assertTrue(source_has(368, "PC800LC-8", "209-06-73330"))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            self.assertEqual(con.execute(
                "SELECT page_count,index_status FROM books WHERE book_id='PC800-8'"
            ).fetchone(), (1097, "indexed_partial"))
            self.assertEqual(con.execute(
                "SELECT count(*) FROM part_occurrences WHERE book_id='PC800-8' "
                "AND pdf_page BETWEEN 368 AND 435").fetchone()[0], 0)
            self.assertEqual(con.execute(
                "SELECT count(*) FROM books WHERE model='PC800-8R'").fetchone()[0], 0)
            note = con.execute(
                "SELECT note FROM book_coverages WHERE book_id='PC800-8' "
                "AND kind='excluded_machine_banner'").fetchone()
            self.assertIsNotNone(note)
            self.assertIn("368-435", note[0])
        self.assertEqual(run("--part-number", "209-06-73330")["found"], 0)

    def test_machine_exact_pn_and_direct_view(self):
        row = run("--part-number", "209-01-42232", "--verify")["candidates"][0]
        self.assertEqual((row["book_id"], row["figure"], row["item_raw"], row["pdf_pages"],
                          row["description"], row["quantity"], row["applicability"],
                          row["view_pages"]),
                         ("PC800-8", "B0600-03A0", "1", [2], "BRACKET", "1", "50001-", [2]))
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(2, "Reference:B0600-03A0", "209-01-42232", "50001-"))
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "pc800-view-test"}):
            image = run("--part-number", "209-01-42232", "--verify", "--auto-render-verified")
        self.assertEqual([r["pdf_page"] for r in image["rendered"]], [2])
        self.assertTrue(image["rendered"][0]["output"].startswith("MEDIA:"))

    def test_neighbor_of_lc_block_and_continuation(self):
        elbow = run("--part-number", "20U-62-46570", "--figure", "N1310-01A2",
                    "--verify")["candidates"][0]
        self.assertEqual((elbow["pdf_pages"], elbow["item_raw"], elbow["quantity"],
                          elbow["applicability"]),
                         ([367], "29", "1", "50001-"))
        self.assertEqual(elbow["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(367, "PC800-8", "20U-62-46570"))
        continued = run("--part-number", "01643-31232", "--figure", "B0600-03A0",
                        "--item", "26", "--verify")["candidates"][0]
        self.assertEqual((continued["pdf_pages"], continued["view_pages"],
                          continued["quantity"], continued["applicability"]),
                         ([3], [2], "2", "50001-"))
        self.assertEqual(continued["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(3, "Reference:B0600-03A0", "01643-31232"))

    def test_engine_ranges_suffix_and_ar(self):
        closed = run("--part-number", "600-462-1600", "--figure", "A4710-A4H2",
                     "--verify")["candidates"][0]
        self.assertEqual((closed["item_raw"], closed["pdf_pages"], closed["quantity"],
                          closed["applicability"]),
                         ("1", [574], "1", "530043-530701"))
        self.assertEqual(closed["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(574, "SAA6D140E-5F-03", "600-462-1600", "530043-530701"))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            kind = con.execute(
                "SELECT a.kind,a.from_num,a.to_num FROM row_applicability a "
                "JOIN part_occurrences o ON o.occ_id=a.occ_id "
                "WHERE o.book_id='PC800-8' AND o.pdf_page=574 AND o.pn_norm='600-462-1600'"
            ).fetchone()
            self.assertEqual(kind, ("engine", 530043, 530701))
        opened = run("--part-number", "600-462-1601", "--figure", "A4710-A4H2A",
                     "--verify")["candidates"][0]
        self.assertEqual((opened["pdf_pages"], opened["applicability"]), ([575], "530702-"))
        self.assertTrue(source_has(575, "600-462-1601", "530702-"))
        suffix = run("--part-number", "198-06-53610", "--figure", "D0200-01A0B",
                     "--item", "20", "--verify", "--auto-render-verified")
        raws = {row["item_raw"]: row for row in suffix["candidates"]}
        self.assertEqual((raws["20A"]["quantity"], raws["20A"]["applicability"]), ("4", "55010-@"))
        self.assertEqual((raws["20B"]["quantity"], raws["20B"]["applicability"]), ("2", "55010-@"))
        self.assertIn("ambiguous_applicability_symbol", raws["20A"]["flags"])
        self.assertIn("ambiguous_applicability_symbol", raws["20B"]["flags"])
        self.assertNotIn("rendered", suffix)
        self.assertTrue(source_has(65, "198-06-53610", "20A", "55010-@"))
        ar_rows = run("--part-number", "ND094095-0330", "--figure", "A4010-C4R1",
                      "--verify")["candidates"]
        ar = next(row for row in ar_rows if row["quantity"] == "AR")
        self.assertEqual(ar["pdf_pages"], [613])
        self.assertEqual(ar["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(613, "ND094095-0330", "AR", "530001-"))

    def test_closed_range_wide_page_and_blank_pn(self):
        matched = run("--part-number", "07002-21823", "--figure", "B0600-03A0",
                      "--serial", "50016", "--verify")["candidates"][0]
        self.assertEqual((matched["applicability"], matched["serial_match"], matched["pdf_pages"]),
                         ("50001-55312", True, [2]))
        self.assertEqual(run("--part-number", "07002-21823", "--figure", "B0600-03A0",
                             "--serial", "55313")["found"], 0)
        self.assertTrue(source_has(2, "07002-21823", "50001-55312"))
        wide = run("--part-number", "6261-71-1110", "--figure", "A4010-C4R1",
                   "--verify")["candidates"][0]
        self.assertEqual(wide["pdf_pages"], [613])
        self.assertEqual(wide["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(1090, "CARTRIDGE", "600-311-4500"))
        with contextlib.closing(sqlite3.connect(DB)) as con:
            rows = con.execute(
                "SELECT pn_raw,pn_norm,status FROM part_occurrences "
                "WHERE book_id='PC800-8' AND pdf_page=1090 AND item_raw='1' "
                "AND description_raw LIKE 'CARTRIDGE%'").fetchall()
        self.assertGreaterEqual(len(rows), 1)
        self.assertTrue(all(row[1] is None and row[2] == "rejected" for row in rows))

    def test_ambiguous_multifigure_duplicates_and_isolation(self):
        ambiguous = run("--part-number", "209-03-41641", "--figure", "C0100-11A0",
                        "--verify", "--auto-render-verified")
        self.assertEqual(ambiguous["candidates"][0]["applicability"], "50001-@")
        self.assertIn("ambiguous_applicability_symbol", ambiguous["candidates"][0]["flags"])
        self.assertNotIn("rendered", ambiguous)
        self.assertTrue(source_has(27, "209-03-41641", "50001-@"))
        many = run("--part-number", "08037-02512", "--verify", "--auto-render-verified")
        self.assertGreaterEqual(len({row["figure"] for row in many["candidates"]}), 3)
        self.assertNotIn("rendered", many)
        self.assertEqual(run("--part-number", "99999-99-99999")["found"], 0)
        with contextlib.closing(sqlite3.connect(DB)) as con:
            self.assertEqual(con.execute(
                "SELECT pdf_page FROM figure_pages WHERE book_id='PC800-8' "
                "AND fig_no='K0600-01A0A' ORDER BY pdf_page").fetchall(), [(495,)])
            self.assertEqual(con.execute(
                "SELECT pdf_page FROM figure_pages WHERE book_id='PC800-8' "
                "AND fig_no='K0180-01A2' ORDER BY pdf_page").fetchall(), [(511,), (512,)])
        for old_model, pn in (("HD785-7", "6218-11-5830"),
                              ("WA600-6", "426-22-31340"),
                              ("HD785-5", "561-86-67410"),
                              ("HD465-7R", "569-46-62810"),
                              ("PC1250SP-8R", "YU702743-3")):
            self.assertEqual(run("--part-number", pn)["found"], 0)
            self.assertEqual(run("--part-number", "209-01-42232", model=old_model)["found"], 0)

    def test_shared_pn_keeps_each_books_page_and_serial(self):
        pc800 = run("--part-number", "21N-01-11170", "--verify")["candidates"][0]
        pc1250 = run("--part-number", "21N-01-11170", "--verify",
                     model="PC1250SP-8R")["candidates"][0]
        self.assertEqual((pc800["book_id"], pc800["figure"], pc800["pdf_pages"],
                          pc800["item_raw"], pc800["applicability"]),
                         ("PC800-8", "B0100-01A0", [23], "1", "50001-"))
        self.assertEqual((pc1250["book_id"], pc1250["figure"], pc1250["pdf_pages"],
                          pc1250["applicability"]),
                         ("PC1250SP-8R", "B0100-01A0", [137], "35001-"))
        self.assertEqual(pc800["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(23, "21N-01-11170", "B0100-01A0", "50001-"))

    def test_second_build_and_five_book_preservation(self):
        with contextlib.closing(sqlite3.connect(DB)) as con:
            before = {book: index.book_digest(con, book) for book in OTHERS}
        with tempfile.TemporaryDirectory(prefix="pc800-digest-") as tmp:
            candidate = Path(tmp) / "candidate.sqlite"
            shutil.copy2(DB, candidate)
            index.build("PC800-8", candidate)
            with contextlib.closing(sqlite3.connect(candidate)) as con:
                first = index.book_digest(con, "PC800-8")
                mid = {book: index.book_digest(con, book) for book in OTHERS}
            index.build("PC800-8", candidate)
            with contextlib.closing(sqlite3.connect(candidate)) as con:
                second = index.book_digest(con, "PC800-8")
                after = {book: index.book_digest(con, book) for book in OTHERS}
        self.assertEqual(first, second)
        self.assertEqual(before, mid)
        self.assertEqual(before, after)
        self.assertEqual(set(first), {
            "groups", "figures", "figure_pages", "part_occurrences",
            "row_applicability", "part_relations", "parts_fts", "combined"})


if __name__ == "__main__":
    unittest.main()
