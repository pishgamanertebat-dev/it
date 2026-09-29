"""Source-verified HD785-5 Part Book index, view and rebuild regressions."""
import contextlib
import io
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
PDF = ROOT / "HD785-5/HD785-5 part book.pdf"
OTHERS = ("HD785-7-B1", "WA600-6-2010")


def run(*args, model="HD785-5"):
    with contextlib.redirect_stdout(io.StringIO()):
        return lookup.main(["--model", model, "--db", str(DB), *args])


def source_has(page, *terms):
    with pymupdf.open(PDF) as doc:
        text = doc[page - 1].get_text("text")
    return all(term in text for term in terms)


class HD7855PartBook(unittest.TestCase):
    def test_index_identity_and_single_duplicate_section(self):
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        self.assertEqual(con.execute(
            "SELECT page_count,index_status FROM books WHERE book_id='HD785-5'"
        ).fetchone(), (554, "indexed_partial"))
        copies = con.execute(
            "SELECT count(*) FROM part_occurrences WHERE book_id='HD785-5' AND fig_no='B0100-01A0'"
        ).fetchone()[0]
        pages = con.execute(
            "SELECT count(*) FROM figure_pages WHERE book_id='HD785-5' AND fig_no='B0100-01A0'"
        ).fetchone()[0]
        con.close()
        self.assertGreater(copies, 0)
        self.assertEqual(pages, 1)
        self.assertTrue(source_has(163, "Ref. B0100-01A0"))
        self.assertTrue(source_has(168, "Ref. B0100-01A0"))

    def test_second_build_digests_match_and_other_books_stay(self):
        prod = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        before = {book: index.book_digest(prod, book) for book in OTHERS}
        prod.close()
        folder = Path(tempfile.mkdtemp(prefix="hd7855-digest-"))
        candidate = folder / "candidate.sqlite"
        shutil.copy2(DB, candidate)
        index.build("HD785-5", candidate)
        first = sqlite3.connect(candidate)
        built = index.book_digest(first, "HD785-5")
        mid = {book: index.book_digest(first, book) for book in OTHERS}
        first.close()
        index.build("HD785-5", candidate)
        second = sqlite3.connect(candidate)
        rebuilt = index.book_digest(second, "HD785-5")
        after = {book: index.book_digest(second, book) for book in OTHERS}
        second.close()
        shutil.rmtree(folder, ignore_errors=True)
        self.assertEqual(built, rebuilt)
        self.assertEqual(mid, before)
        self.assertEqual(after, before)

    def test_engine_assembly_row_and_symbol_only_item(self):
        result = run("--part-number", "6212-11-1103", "--verify")
        self.assertEqual(result["found"], 1)
        row = result["candidates"][0]
        self.assertEqual((row["figure"], row["quantity"], row["applicability"], row["pdf_pages"]),
                         ("A1010-A7A5", "12", "0012121-", [3]))
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual(row["view_pages"], [3])
        self.assertTrue(source_has(3, "Ref. A1010-A7A5", "6212-11-1103"))
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        symbol = con.execute(
            "SELECT pn_norm,status FROM part_occurrences "
            "WHERE book_id='HD785-5' AND pdf_page=3 AND item_raw='1'"
        ).fetchone()
        kind = con.execute(
            "SELECT a.kind,a.from_raw,a.from_num,a.to_num FROM row_applicability a "
            "JOIN part_occurrences o ON o.occ_id=a.occ_id "
            "WHERE o.book_id='HD785-5' AND o.pdf_page=3 AND o.pn_norm='6212-11-1103'"
        ).fetchone()
        con.close()
        self.assertEqual(symbol, (None, "rejected"))
        self.assertEqual(kind, ("engine", "0012121", 12121, None))

    def test_five_character_figure_and_machine_item(self):
        cover = run("--part-number", "6210-11-8111", "--figure", "A1110-A7A3A", "--verify")
        self.assertEqual(cover["found"], 1)
        self.assertEqual((cover["candidates"][0]["item"], cover["candidates"][0]["pdf_pages"]),
                         (1, [4]))
        self.assertEqual(cover["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        lamp = run("--part-number", "561-86-67410", "--verify")
        self.assertEqual({(row["figure"], row["item"], row["quantity"], row["applicability"],
                           row["pdf_pages"][0], row["view_pages"][0])
                          for row in lamp["candidates"]},
                         {("E0820-01A0", 1, "1", "J10001-", 199, 199),
                          ("E0820-02A0", 1, "1", "J10001-", 200, 200)})
        self.assertTrue(all(row["pdf_verification"]["status"] == "VERIFIED" for row in lamp["candidates"]))
        self.assertTrue(source_has(199, "561-86-67410", "Ref. E0820-01A0"))
        self.assertTrue(source_has(200, "561-86-67410", "Ref. E0820-02A0"))

    def test_closed_serial_range_filters_without_dropping_the_raw_note(self):
        result = run("--part-number", "07000-02120", "--figure", "C0120-01A0", "--verify")
        closed = [row for row in result["candidates"] if row["applicability"] == "J10001-J10030"]
        self.assertEqual(len(closed), 1)
        self.assertEqual(closed[0]["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(175, "07000-02120", "J10001-J10030"))
        inside = run("--part-number", "07000-02120", "--figure", "C0120-01A0",
                     "--serial", "J10020", "--verify")
        self.assertEqual([(row["applicability"], row["serial_match"]) for row in inside["candidates"]],
                         [("J10001-J10030", True)])
        outside = run("--part-number", "07000-02120", "--figure", "C0120-01A0",
                      "--serial", "J10040", "--verify")
        self.assertFalse(any(row["applicability"] == "J10001-J10030" for row in outside["candidates"]))

    def test_lettered_item_ar_quantity_and_book_isolation(self):
        suffix = run("--part-number", "561-43-62410", "--figure", "H0100-03A0", "--verify")
        self.assertEqual(suffix["found"], 1)
        row = suffix["candidates"][0]
        self.assertEqual((row["item"], row["item_raw"], row["quantity"], row["applicability"]),
                         (5, "5A", "1", "J10031-"))
        self.assertIn("item_suffix_A", row["flags"])
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(287, "5A", "561-43-62410"))
        ar = run("--part-number", "09920-00150", "--figure", "A2110-A7D4",
                 "--item", "32", "--verify")["candidates"][0]
        self.assertEqual((ar["book_id"], ar["quantity"], ar["part_number_raw"]),
                         ("HD785-5", "AR", "(09920-00150)"))
        self.assertEqual(ar["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(all(row["book_id"] == "HD785-5"
                            for row in run("--part-number", "09920-00150", "--verify")["candidates"]))
        self.assertEqual(run("--part-number", "600-319-3550", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "6245-21-6110", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "561-86-67410", "--verify", model="HD785-7")["found"], 0)
        self.assertEqual(run("--part-number", "561-86-67410", "--verify", model="WA600-6")["found"], 0)

    def test_viewless_figure_has_text_and_no_media_page(self):
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "hd7855-no-view"}):
            result = run("--part-number", "6215-K1-9901", "--figure", "A8111-A7A9",
                         "--verify", "--auto-render-verified")
        self.assertGreaterEqual(result["found"], 1)
        self.assertTrue(all(row["pdf_verification"]["status"] == "VERIFIED" for row in result["candidates"]))
        self.assertTrue(all(row["view_pages"] == [] for row in result["candidates"]))
        self.assertNotIn("rendered", result)
        self.assertTrue(source_has(154, "6215-K1-9901", "Ref. A8111-A7A9"))

    def test_direct_verified_part_renders_the_left_view(self):
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "hd7855-view"}):
            result = run("--part-number", "561-86-67410", "--verify", "--auto-render-verified")
        self.assertEqual([page["pdf_page"] for page in result["rendered"]], [199, 200])
        media = result["rendered"][0]["output"]
        self.assertTrue(media.startswith("MEDIA:"))
        self.assertGreater(Path(media[6:]).stat().st_size, 50_000)


if __name__ == "__main__":
    unittest.main()
