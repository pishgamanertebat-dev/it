"""Source-verified HD465-7R Part Book index, view and rebuild regressions."""
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
PDF = ROOT / "HD465-7R_HD605-7R/HD465-7R  Parts Book.pdf"
OTHERS = ("HD785-7-B1", "WA600-6-2010", "HD785-5")
GROUPS = (
    ("03", "ENGINE", 12, 96),
    ("03-2", "ENGINE RELATED PARTS", 98, 99),
    ("06", "COOLING SYSTEM", 101, 110),
    ("09", "FUEL TANK AND RELATED PARTS", 112, 123),
    ("12", "ELECTRICAL SYSTEM", 125, 150),
    ("15", "TORQUE CONVERTER AND TRANSMISSION", 152, 228),
    ("18", "AIR SYSTEM", 230, 233),
    ("21", "HYDRAULIC SYSTEM", 235, 299),
    ("24", "MAIN FRAME AND RELATED PARTS", 301, 320),
    ("27", "OPERATOR\u2019S COMPARTMENT AND CONTROL SYSTEM", 322, 377),
    ("30", "PLATFORM AND RELATED PARTS", 379, 402),
    ("33", "GUARD", 404, 418),
    ("36", "RIM AND TIRE", 420, 427),
    ("39", "BODY", 429, 437),
    ("42", "MARK AND PLATES", 439, 443),
    ("45", "TOOL", 445, 449),
    ("48", "MISCELLANEOUS", 451, 463),
    ("51", "SERVICE KIT AND COMPONENT PARTS", 465, 466),
)


def run(*args, model="HD465-7R"):
    with contextlib.redirect_stdout(io.StringIO()):
        return lookup.main(["--model", model, "--db", str(DB), *args])


def source_has(page, *terms):
    with pymupdf.open(PDF) as doc:
        text = doc[page - 1].get_text("text")
    return all(term in text for term in terms)


class HD465PartBook(unittest.TestCase):
    def test_group_codes_come_from_prefix_run_order(self):
        self.assertEqual(
            [index.group_code_for_prefix_run("03", n) for n in (1, 2, 3)],
            ["03", "03-2", "03-3"])
        self.assertEqual(index.group_code_for_prefix_run("06", 1), "06")
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        rows = con.execute(
            "SELECT group_code, title, page_from, page_to FROM groups "
            "WHERE book_id='HD465-7R' ORDER BY page_from, group_code").fetchall()
        con.close()
        self.assertEqual(rows, list(GROUPS))

    def test_bare_engine_serial_stays_scoped_to_the_hd465_engine_banner(self):
        profile = index.PROFILES["hd465_raster"]
        book = index.BOOKS["HD465-7R"]
        engine = index.serial_kind_for_page(profile, "SAA6D170E-5R S/N 610017--", "03")
        machine = index.serial_kind_for_page(profile, "HD 465-7R S/N J20116--UP", "03-2")
        self.assertEqual((engine, machine), ("engine", "machine"))
        opened = index.parse_serial(
            "610017", book, engine,
            open_bare_engine=bool(profile["bare_engine_open_start"] and engine == "engine"),
            open_trailing_dashes=True)
        self.assertEqual(opened[0]["kind"], "engine")
        self.assertIsNone(opened[0]["to_num"])
        self.assertEqual(opened[0]["flags"], ["open_start_only"])
        # The same digits are not reinterpreted unless this profile's engine banner is active.
        untouched = index.parse_serial("610017", index.BOOKS["HD785-5"], "engine")
        self.assertEqual(untouched[0]["to_num"], 610017)
        self.assertEqual(untouched[0]["flags"], ["single_serial"])
        hyphen = index.parse_serial("0012121-", index.BOOKS["HD785-5"], "engine")
        self.assertIsNone(hyphen[0]["to_num"])
        self.assertNotIn("single_serial", hyphen[0]["flags"])
        self.assertEqual(
            index.parse_serial("N10001", index.BOOKS["HD785-7-B1"], "machine")[0]["flags"],
            ["open_start_only"])
        sn = index.parse_serial("SN:500013-", index.BOOKS["HD785-7-B1"], "machine")[0]
        self.assertEqual((sn["kind"], sn["from_num"], sn["to_num"]), ("engine", 500013, None))
        repeated = index.parse_serial("J10001-J10030", index.BOOKS["HD785-5"], "machine")[0]
        self.assertEqual((repeated["from_num"], repeated["to_num"], repeated["flags"]),
                         (10001, 10030, ["range_end_repeated_prefix"]))
        self.assertEqual(
            index.parse_serial("J20116--", index.BOOKS["HD785-7-B1"], "machine")[0]["flags"],
            ["serial_unparsed"])
        dashed = index.parse_serial(
            "J20116--", book, "machine", open_trailing_dashes=True)[0]
        self.assertEqual((dashed["prefix"], dashed["from_num"], dashed["to_num"], dashed["flags"]),
                         ("J", 20116, None, ["open_trailing_dashes"]))
        wa = index.parse_serial("510001-", index.BOOKS["WA600-6-2010"], "engine")[0]
        self.assertEqual((wa["kind"], wa["from_num"], wa["to_num"]), ("engine", 510001, None))

    def test_second_build_digests_match_and_other_books_stay(self):
        prod = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        before = {book: index.book_digest(prod, book) for book in OTHERS}
        prod.close()
        folder = Path(tempfile.mkdtemp(prefix="hd465-digest-"))
        candidate = folder / "candidate.sqlite"
        shutil.copy2(DB, candidate)
        index.build("HD465-7R", candidate)
        first = sqlite3.connect(candidate)
        built = index.book_digest(first, "HD465-7R")
        mid = {book: index.book_digest(first, book) for book in OTHERS}
        first.close()
        index.build("HD465-7R", candidate)
        second = sqlite3.connect(candidate)
        rebuilt = index.book_digest(second, "HD465-7R")
        after = {book: index.book_digest(second, book) for book in OTHERS}
        groups = second.execute(
            "SELECT group_code, title, page_from, page_to FROM groups "
            "WHERE book_id='HD465-7R' ORDER BY page_from, group_code").fetchall()
        second.close()
        shutil.rmtree(folder, ignore_errors=True)
        self.assertEqual(built, rebuilt)
        self.assertEqual(built["groups"], rebuilt["groups"])
        self.assertEqual(groups, list(GROUPS))
        self.assertEqual(mid, before)
        self.assertEqual(after, before)

    def test_ordinary_engine_row_uses_the_list_ref(self):
        result = run("--part-number", "6245-21-6110", "--verify")
        self.assertEqual(result["found"], 1)
        row = result["candidates"][0]
        self.assertEqual((row["book_id"], row["figure"], row["item"], row["quantity"],
                          row["applicability"], row["description"], row["pdf_pages"], row["view_pages"]),
                         ("HD465-7R", "A1030-A6A3", 1, "1", "610017", "BLOCK, WATER", [14], [14]))
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertNotEqual(row["figure"], "A1030-06A3")
        self.assertTrue(source_has(14, "6245-21-6110", "A1030-A6A3", "030020"))
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        kind = con.execute(
            "SELECT a.kind, a.from_num, a.to_num, a.flags FROM row_applicability a "
            "JOIN part_occurrences o ON o.occ_id=a.occ_id "
            "WHERE o.book_id='HD465-7R' AND o.pn_norm='6245-21-6110'").fetchone()
        con.close()
        self.assertEqual(kind, ("engine", 610017, None, '["open_start_only"]'))

    def test_suffix_ar_open_dashes_and_machine_reuse_of_prefix_03(self):
        suffix = run("--part-number", "707-51-16650", "--figure", "F4400-06A0", "--verify")
        self.assertEqual(suffix["found"], 1)
        row = suffix["candidates"][0]
        self.assertEqual((row["item_raw"], row["description"], row["quantity"], row["pdf_pages"]),
                         ("12A", "*RING", "1", [214]))
        self.assertIn("item_suffix_A", row["flags"])
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(214, "12A", "707-51-16650", "F4400-06A0"))
        gasket = run("--part-number", "09920-00150", "--figure", "A2410-B6C1", "--verify")
        self.assertEqual(gasket["candidates"][0]["quantity"], "AR")
        self.assertEqual(gasket["candidates"][0]["part_number_raw"], "(09920-00150)")
        self.assertEqual(gasket["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        dashed = run("--part-number", "07281-10197", "--figure", "C0120-01A0",
                     "--item", "1", "--verify")
        self.assertEqual(dashed["candidates"][0]["applicability"], "J20116--")
        self.assertEqual(dashed["candidates"][0]["quantity"], "2")
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        stored = con.execute(
            "SELECT a.kind, a.prefix, a.from_num, a.to_num, a.flags, f.group_code "
            "FROM part_occurrences o JOIN row_applicability a ON a.occ_id=o.occ_id "
            "JOIN figures f ON f.book_id=o.book_id AND f.fig_no=o.fig_no "
            "WHERE o.book_id='HD465-7R' AND o.pdf_page=107 AND o.item_raw='1' "
            "AND o.pn_norm='07281-10197'").fetchone()
        mounting = con.execute(
            "SELECT a.kind, a.prefix, a.to_num, f.group_code FROM part_occurrences o "
            "JOIN row_applicability a ON a.occ_id=o.occ_id "
            "JOIN figures f ON f.book_id=o.book_id AND f.fig_no=o.fig_no "
            "WHERE o.book_id='HD465-7R' AND o.pdf_page=98 AND o.item_raw='1' "
            "AND o.pn_norm='569-01-81130'").fetchone()
        con.close()
        self.assertEqual(stored, ("machine", "J", 20116, None, '["open_trailing_dashes"]', "06"))
        self.assertEqual(mounting, ("machine", "J", None, "03-2"))

    def test_continuation_mismatch_multifigure_and_duplicates(self):
        continued = run("--part-number", "07283-36163", "--verify")
        self.assertEqual(continued["found"], 1)
        self.assertEqual((continued["candidates"][0]["figure"], continued["candidates"][0]["item"],
                          continued["candidates"][0]["pdf_pages"]),
                         ("A2110-A6K1", 44, [37]))
        self.assertEqual(continued["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(source_has(37, "07283-36163", "A2110-A6K1", "030371"))
        mismatch = run("--part-number", "6240-51-1100", "--verify", "--auto-render-verified")
        self.assertEqual(mismatch["found"], 1)
        self.assertEqual(mismatch["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual(mismatch["candidates"][0]["view_pages"], [])
        self.assertNotIn("rendered", mismatch)
        self.assertTrue(source_has(48, "6240-51-1100", "A3010-A6A3", "A3010-A6A8"))
        many = run("--part-number", "07000-73038", "--limit", "40",
                   "--verify", "--auto-render-verified")
        figures = {row["figure"] for row in many["candidates"]}
        self.assertGreater(len(figures), 2)
        self.assertIn("A1010-A6B8", figures)
        self.assertNotIn("rendered", many)
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        copies = dict(con.execute(
            "SELECT fig_no, count(1) FROM part_occurrences WHERE book_id='HD465-7R' "
            "AND fig_no IN ('F4400-08A1', 'F4400-18A1') GROUP BY fig_no").fetchall())
        con.close()
        self.assertGreater(copies["F4400-08A1"], 0)
        self.assertGreater(copies["F4400-18A1"], 0)

    def test_icon_and_control_character_do_not_invent_part_numbers(self):
        con = sqlite3.connect(f"file:{DB.resolve().as_posix()}?mode=ro", uri=True)
        icon = con.execute(
            "SELECT pn_norm, status, flags FROM part_occurrences "
            "WHERE book_id='HD465-7R' AND pdf_page=12 AND item_raw='1'").fetchone()
        con.close()
        self.assertEqual(icon[0], None)
        self.assertEqual(icon[1], "rejected")
        self.assertIn("pn_icon_glyph", icon[2])
        self.assertEqual(run("--part-number", "j", "--verify")["found"], 0)
        control = run("--part-number", "6502-52-3010", "--figure", "A1530-C6J2", "--verify")
        self.assertGreaterEqual(control["found"], 1)
        row = control["candidates"][0]
        self.assertEqual(row["part_number"], "6502-52-3010")
        self.assertTrue(row["part_number_raw"].startswith("("))
        self.assertIn("pn_control_char", row["flags"])
        self.assertIn("ROTOR,TURBINE", row["description"])
        self.assertEqual(row["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual(run("--part-number", "99999-99-99999", "--verify")["found"], 0)

    def test_direct_view_renders_and_models_stay_isolated(self):
        with patch.dict(os.environ, {"HERMES_SESSION_ID": "hd465-view"}):
            rendered = run("--part-number", "569-46-62810", "--verify", "--auto-render-verified")
        self.assertEqual([page["pdf_page"] for page in rendered["rendered"]], [431])
        self.assertTrue(rendered["rendered"][0]["output"].startswith("MEDIA:"))
        self.assertGreater(Path(rendered["rendered"][0]["output"][6:]).stat().st_size, 50_000)
        self.assertEqual(run("--part-number", "6218-11-5830", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "426-22-31340", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "6212-11-1103", "--verify")["found"], 0)
        self.assertEqual(run("--part-number", "6215-K1-9901", "--verify")["found"], 0)
        wa = run("--part-number", "6245-21-6110", "--verify", model="WA600-6")
        self.assertEqual({(row["book_id"], row["figure"], row["pdf_pages"][0]) for row in wa["candidates"]},
                         {("WA600-6-2010", "A1030-A6A5", 38)})
        self.assertEqual(
            run("--part-number", "569-46-62810", "--verify", model="HD785-7")["found"], 0)
        self.assertEqual(
            run("--part-number", "569-46-62810", "--verify", model="WA600-6")["found"], 0)
        self.assertEqual(
            run("--part-number", "569-46-62810", "--verify", model="HD785-5")["found"], 0)
        hd7 = run("--part-number", "6218-11-5830", "--verify", model="HD785-7")
        self.assertIn(("HD785-7-B1", "A1530-A7D6", 3, 31),
                      {(row["book_id"], row["figure"], row["item"], row["pdf_pages"][0])
                       for row in hd7["candidates"]})
        hd5 = run("--part-number", "6212-11-1103", "--verify", model="HD785-5")
        self.assertEqual(hd5["candidates"][0]["figure"], "A1010-A7A5")
        self.assertEqual(hd5["candidates"][0]["pdf_pages"], [3])


if __name__ == "__main__":
    unittest.main()
