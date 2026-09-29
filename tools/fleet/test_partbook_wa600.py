"""Source-verified WA600-6 2010 Part Book index and view regressions."""
import contextlib
import io
import os
import sys
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
PDF = ROOT / "WA600-6/WA600-6-partbook-2010.pdf"


def run(*args, model="WA600-6"):
    with contextlib.redirect_stdout(io.StringIO()):
        return lookup.main(["--model", model, "--db", str(DB), *args])


def source_has(page, *terms):
    with pymupdf.open(PDF) as doc:
        text = doc[page - 1].get_text("text")
    return all(term in text for term in terms)


class WA600PartBook(unittest.TestCase):
    def test_index_identity_and_view_coverage(self):
        import sqlite3
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        self.assertEqual(con.execute(
            "SELECT page_count,index_status FROM books WHERE book_id='WA600-6-2010'"
        ).fetchone(), (795, "indexed_partial"))
        self.assertEqual(con.execute(
            "SELECT count(*),sum(has_view) FROM figure_pages WHERE book_id='WA600-6-2010'"
        ).fetchone(), (674, 650))
        self.assertEqual(con.execute(
            "SELECT count(*) FROM figures WHERE book_id='WA600-6-2010'"
        ).fetchone(), (621,))
        con.close()

    def test_additive_guard_detects_cross_book_relation_changes(self):
        import sqlite3
        source = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        scratch = sqlite3.connect(":memory:")
        source.backup(scratch)
        source.close()
        before = index.other_book_counts(scratch, "WA600-6-2010")
        row = scratch.execute(
            "SELECT r.* FROM part_relations r JOIN part_occurrences o ON o.occ_id=r.occ_id "
            "WHERE o.book_id='HD785-7-B1' LIMIT 1").fetchone()
        scratch.execute("INSERT INTO part_relations VALUES (?,?,?,?)", row)
        self.assertNotEqual(index.other_book_counts(scratch, "WA600-6-2010"), before)
        scratch.close()

    def test_normal_layout_exact_part_and_source_fields(self):
        result = run("--part-number", "6245-21-6110", "--verify")
        self.assertEqual(result["found"], 1)
        row = result["candidates"][0]
        self.assertEqual((row["figure"], row["item"], row["part_number"],
                          row["description"], row["quantity"], row["applicability"]),
                         ("A1030-A6A5", 1, "6245-21-6110",
                          "BLOCK,WATER", "1", "510001-"))
        self.assertEqual((row["pdf_pages"], row["view_pages"]), ([38], [38]))
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual(result["pdf_pages_read"], 1)
        self.assertTrue(source_has(38, "FIG.A1030-A6A5", "6245-21-6110"))

    def test_lettered_item_keeps_printed_suffix(self):
        result = run("--part-number", "01643-31232", "--figure", "A1030-A6A5",
                     "--item", "19", "--verify")
        self.assertEqual(result["found"], 1)
        row = result["candidates"][0]
        self.assertEqual((row["item"], row["item_raw"], row["pdf_pages"]), (19, "19A", [38]))
        self.assertIn("item_suffix_A", row["flags"])
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(38, "19A 01643-31232"))

    def test_left_continuation_maps_verified_part_to_previous_view(self):
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "wa600-partbook-test"}):
            result = run("--part-number", "6240-19-1810", "--verify",
                         "--auto-render-verified")
        self.assertEqual(result["found"], 1)
        row = result["candidates"][0]
        self.assertEqual((row["figure"], row["item"], row["quantity"],
                          row["pdf_pages"], row["view_pages"]),
                         ("A1010-A6B8", 26, "6", [37], [36]))
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual([p["pdf_page"] for p in result["rendered"]], [36])
        media = result["rendered"][0]["output"]
        self.assertTrue(media.startswith("MEDIA:"))
        self.assertGreater(Path(media[6:]).stat().st_size, 50_000)
        self.assertTrue(source_has(36, "FIG.A1010-A6B8"))
        self.assertTrue(source_has(37, "6240-19-1810"))

    def test_serial_split_keeps_both_quantities_and_source_ranges(self):
        result = run("--part-number", "01643-31645", "--figure", "M4310-06C0", "--verify")
        self.assertEqual(result["found"], 2)
        self.assertEqual({(r["quantity"], r["applicability"]) for r in result["candidates"]},
                         {("4", "60465-"), ("7", "60217-60464")})
        self.assertTrue(all(r["pdf_verification"]["status"] == "VERIFIED"
                            and r["view_pages"] == [600] for r in result["candidates"]))
        later = run("--part-number", "01643-31645", "--figure", "M4310-06C0",
                    "--serial", "60465", "--verify")
        self.assertEqual([(r["quantity"], r["serial_match"]) for r in later["candidates"]],
                         [("4", True)])
        self.assertTrue(source_has(600, "01643-31645", "60217-60464", "60465-"))

    def test_engine_and_machine_serial_notes_remain_distinct(self):
        import sqlite3
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        engine = con.execute(
            "SELECT a.kind,a.from_num,a.to_num FROM row_applicability a "
            "JOIN part_occurrences o ON o.occ_id=a.occ_id "
            "WHERE o.book_id='WA600-6-2010' AND o.pdf_page=38 AND o.pn_norm='6245-21-6110'"
        ).fetchone()
        machine = con.execute(
            "SELECT a.kind,a.from_num,a.to_num FROM row_applicability a "
            "JOIN part_occurrences o ON o.occ_id=a.occ_id "
            "WHERE o.book_id='WA600-6-2010' AND o.pdf_page=600 "
            "AND o.pn_norm='01643-31645' AND o.qty_raw='7'"
        ).fetchone()
        con.close()
        self.assertEqual(engine, ("engine", 510001, None))
        self.assertEqual(machine, ("machine", 60217, 60464))
        self.assertTrue(source_has(1, "Serial No.60217 and up", "Engine No.511030 and up"))

    def test_optional_symbol_and_ar_quantity_are_preserved(self):
        symbol = run("--part-number", "702-73-11810", "--verify")["candidates"][0]
        self.assertEqual((symbol["figure"], symbol["item"], symbol["pdf_pages"],
                          symbol["part_number"]), ("Y1640-03A0", 1, [701], "702-73-11810"))
        self.assertEqual(symbol["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(any("U+2605" in flag for flag in symbol["flags"]))
        ar = run("--part-number", "09920-00150", "--figure", "A2410-B6C1",
                 "--item", "20", "--verify")["candidates"][0]
        self.assertEqual((ar["quantity"], ar["description"], ar["pdf_pages"]),
                         ("AR", "LIQUID GASKET,LG-7, 150G", [101]))
        self.assertEqual(ar["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(101, "09920-00150", "AR"))

    def test_two_materially_distinct_verified_figures_render_two_pages(self):
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "wa600-two-figures-test"}):
            result = run("--part-number", "6245-11-4110", "--verify",
                         "--auto-render-verified")
        self.assertEqual(result["found"], 2)
        self.assertEqual({(r["figure"], r["applicability"]) for r in result["candidates"]},
                         {("A1310-A6F3", "510001-"), ("A1310-A6F4", "510020-")})
        self.assertTrue(all(r["pdf_verification"]["status"] == "VERIFIED"
                            for r in result["candidates"]))
        self.assertEqual([p["pdf_page"] for p in result["rendered"]], [42, 43])
        self.assertTrue(source_has(42, "6245-11-4110"))
        self.assertTrue(source_has(43, "6245-11-4110"))

    def test_multi_figure_part_preserves_alternatives_without_ambiguous_image(self):
        result = run("--part-number", "426-22-31340", "--verify",
                     "--auto-render-verified")
        self.assertEqual(result["found"], 4)
        self.assertEqual({(r["figure"], r["pdf_pages"][0], r["view_pages"][0])
                          for r in result["candidates"]},
                         {("F4400-06C0", 301, 300), ("F4400-06C1", 303, 302),
                          ("F4500-06C0", 322, 322), ("F4500-06C1", 324, 324)})
        self.assertTrue(all(r["pdf_verification"]["status"] == "VERIFIED"
                            for r in result["candidates"]))
        self.assertNotIn("rendered", result)

    def test_miss_normalization_and_model_isolation(self):
        self.assertEqual(run("--part-number", "99999-99-99999", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "( 6245 -21-6110 )", "--verify")["found"], 1)
        self.assertEqual(run("--part-number", "6218-11-5830", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "6245-21-6110", "--verify",
                             model="HD785-7")["found"], 0)


if __name__ == "__main__":
    unittest.main()