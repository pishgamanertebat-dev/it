"""Validation set for the HD785-7 B1 Part Book pilot.

Every expected value below was read from the real B1 PDF page noted in the test
(not invented). Each test also re-checks the page text directly with PyMuPDF,
independent of the index.

Run:
  E:\\KomatsoAI\\.venv\\Scripts\\python.exe -m unittest E:\\KomatsoAI\\tools\\fleet\\test_partbook_lookup.py -v
Requires the index (rebuild: E:\\KomatsoAI\\.venv\\Scripts\\python.exe E:\\KomatsoAI\\tools\\partbook_index.py)
"""
import contextlib
import io
import sys
import unittest
from pathlib import Path

import pymupdf

sys.path.insert(0, str(Path(__file__).resolve().parent))
import partbook_lookup as L  # noqa: E402

B1 = L.ROOT / "HD785-7" / "KOMATSU HD785-7 DUMP TRUCK PART BOOK SERIAL NUMBERS N10001- N10560.pdf"
_DOC = pymupdf.open(B1)


def run(*args):
    with contextlib.redirect_stdout(io.StringIO()):
        return L.main(list(args))


def page_has(page, *tokens):
    words = {w[4] for w in _DOC[page - 1].get_text("words")}
    return all(t in words for t in tokens)


class PartbookB1(unittest.TestCase):
    def one(self, res, **kw):
        for c in res["candidates"]:
            if all(c.get(k) == v for k, v in kw.items()):
                return c
        self.fail(f"no candidate {kw} in {[(c['figure'], c['item_raw'], c['part_number_raw']) for c in res['candidates']]}")

    # 1 exact PN (p.31)
    def test_exact_pn(self):
        r = run("--part-number", "6218-11-5830", "--verify")
        c = self.one(r, figure="A1530-A7D6", item=3)
        self.assertEqual((c["description"], c["quantity"], c["applicability"]), ("GASKET (K1)", "2", "SN:500013-"))
        self.assertEqual(c["pdf_pages"], [31])
        self.assertEqual(c["pdf_verification"]["status"], "VERIFIED")
        self.assertTrue(page_has(31, "6218-11-5830", "GASKET"))

    # 2 exact PN appears in 2 figures (p.101 and service kit p.147)
    def test_exact_pn_two_figures(self):
        r = run("--part-number", "600-319-3550", "--verify")
        figs = {c["figure"] for c in r["candidates"]}
        self.assertEqual(figs, {"A4119-A7A1", "B9999-A7F5"})
        self.assertTrue(all(c["pdf_verification"]["status"] == "VERIFIED" for c in r["candidates"]))
        self.assertTrue(page_has(101, "600-319-3550") and page_has(147, "600-319-3550"))

    # 3 lowercase / spaced input still exact (normalized key, raw preserved)
    def test_exact_pn_case_insensitive(self):
        r = run("--part-number", " 561-54-80e00 ")
        c = self.one(r, figure="E0100-01A0")
        self.assertEqual(c["part_number_raw"], "561-54-80E00")

    # 4 prefix PN (p.172 radiator family)
    def test_prefix_pn(self):
        r = run("--part-number-prefix", "561-03-827", "--limit", "20")
        pns = {c["part_number_raw"] for c in r["candidates"]}
        self.assertTrue({"561-03-82701", "561-03-82702", "561-03-82703"} <= pns)
        self.assertTrue(all(p.startswith("561-03-827") for p in pns))

    # 5 exact description via FTS (p.31)
    def test_exact_description(self):
        r = run("--query", "TURBOCHARGER ASS'Y", "--limit", "5")
        self.one(r, figure="A1530-A7D6", part_number_raw="(6505-67-5030)")

    # 6 partial description + figure title words
    def test_partial_description(self):
        r = run("--query", "radiator air cond", "--limit", "10")
        self.assertTrue(any(c["part_number_raw"].startswith("561-03-827") for c in r["candidates"]))

    # 7 figure listing (p.250)
    def test_figure(self):
        r = run("--figure", "E0100-01A0", "--limit", "60")
        self.assertGreaterEqual(len(r["candidates"]), 30)
        self.assertEqual({c["pdf_pages"][0] for c in r["candidates"]}, {250})

    # 8 figure + item, single row (p.31)
    def test_figure_item(self):
        r = run("--figure", "A1530-A7D6", "--item", "19", "--verify")
        self.assertEqual(len(r["candidates"]), 1)
        c = r["candidates"][0]
        self.assertEqual((c["part_number_raw"], c["description"], c["quantity"]), ("6164-51-8611", "FLANGE,R.H.", "1"))
        self.assertEqual(c["pdf_verification"]["status"], "VERIFIED")

    # 9 duplicate item (1* alternatives + 1 / 1- rows on p.250)
    def test_duplicate_item(self):
        r = run("--figure", "E0100-01A0", "--item", "1", "--limit", "20")
        raws = [c["item_raw"] for c in r["candidates"]]
        self.assertIn("1*", raws)
        self.assertIn("1-", raws)
        self.assertGreaterEqual(len(raws), 9)

    # 10 duplicated PN across many figures
    def test_duplicated_pn(self):
        r = run("--part-number", "01643-31032", "--limit", "50")
        self.assertGreater(len({c["figure"] for c in r["candidates"]}), 5)
        self.one(r, figure="A1530-A7D6", item=18)

    # 11 machine-serial specific: battery 569-06-N1100 from N10177, 08000-32220 N10001-176 (p.250)
    def test_machine_serial_split(self):
        early = run("--figure", "E0100-01A0", "--item", "1", "--serial", "N10100", "--limit", "30")
        late = run("--figure", "E0100-01A0", "--item", "1", "--serial", "N10300", "--limit", "30")
        e = {c["part_number_raw"] for c in early["candidates"]}
        l_ = {c["part_number_raw"] for c in late["candidates"]}
        self.assertIn("08000-32220", e)
        self.assertNotIn("569-06-N1100", e)
        self.assertIn("569-06-N1100", l_)
        self.assertNotIn("08000-32220", l_)
        self.assertTrue(page_has(250, "569-06-N1100", "N10177", "-176"))

    # 12 abbreviated range 'N10154 - 243' (p.172)
    def test_abbreviated_serial_range(self):
        r = run("--part-number", "561-03-82702", "--figure", "C0110-01A0", "--serial", "N10200", "--verify")
        c = self.one(r, part_number_raw="561-03-82702")
        self.assertTrue(c["serial_match"])
        self.assertEqual(c["pdf_verification"]["status"], "VERIFIED")
        miss = run("--part-number", "561-03-82702", "--figure", "C0110-01A0", "--serial", "N10300")
        self.assertEqual(miss["found"], 0)
        self.assertIn("NOT evidence of absence", miss["miss"])

    # 13 engine-serial split: item 16 = 01435-01040 SN:500086- ; old 01010-81045 SN:500013-085 (p.31)
    def test_engine_serial(self):
        new = run("--figure", "A1530-A7D6", "--item", "16", "--engine-serial", "500090")
        old = run("--figure", "A1530-A7D6", "--item", "16", "--engine-serial", "500050")
        self.assertEqual([c["part_number_raw"] for c in new["candidates"]], ["01435-01040"])
        self.assertEqual([c["part_number_raw"] for c in old["candidates"]], ["01010-81045"])
        self.assertIn("item_inherited_from_previous_row", old["candidates"][0]["flags"])

    # 14 option-specific figure (large-capacity battery, p.251)
    def test_option_figure(self):
        r = run("--query", "battery large capacity", "--limit", "40")
        self.assertIn("E0100-01A1", {c["figure"] for c in r["candidates"]})

    # 15 continuation page: A1510-A7C6 spans p.29-30; first row on p.30 has no item
    def test_continuation_page(self):
        r = run("--figure", "A1510-A7C6", "--item", "43", "--verify")
        c = self.one(r, part_number_raw="6219-81-8350")
        self.assertEqual(c["pdf_pages"], [30])
        self.assertEqual(c["figure_pdf_pages"], [29, 30])
        clip = run("--part-number", "6151-53-8280", "--figure", "A1510-A7C6")
        p30 = [x for x in clip["candidates"] if x["pdf_pages"] == [30]]
        self.assertTrue(p30 and "continued_from_previous_page" in p30[0]["flags"])

    # 16 cross-reference 'SEE FIG.' (p.35)
    def test_cross_reference(self):
        r = run("--part-number", "6218-11-8102")
        c = self.one(r, figure="A1650-A7A3")
        self.assertIn("SEE FIG.A1650-B7A3", c["description"])
        self.assertIn("pn_parenthesized", c["flags"])

    # 17 replacement / alternative relation (item marker '*' linked to base row, p.250)
    def test_replacement_relation(self):
        r = run("--figure", "E0100-01A0", "--item", "12", "--limit", "5")
        star = self.one(r, item_raw="12*")
        self.assertEqual(star["part_number_raw"], "01024-81016")
        rel = star.get("relations", [])
        self.assertTrue(any(x["pn_raw"] == "01010-81016" for x in rel))

    # 18 ambiguous row: legend-glyph-only PN cell must stay flagged, not become a candidate PN
    def test_ambiguous_symbol_row(self):
        r = run("--figure", "A1010-A7B4", "--item", "1")
        c = self.one(r, description="HEAD,CYLINDER")
        self.assertIsNone(c["part_number"])
        self.assertEqual(c["status"], "needs_verification")
        self.assertIn("pn_symbol_only", c["flags"])

    # 19 symbol-prefixed PN keeps raw, exposes clean key, flagged
    def test_symbol_prefixed_pn(self):
        r = run("--part-number", "6505-67-3170")
        c = self.one(r, figure="A1530-C7D6")
        self.assertNotEqual(c["part_number_raw"], "6505-67-3170")
        self.assertTrue(any(f.startswith("pn_symbol_prefix") for f in c["flags"]))

    # 20 group filter (E0 electrical)
    def test_group(self):
        r = run("--group", "E0", "--part-number", "561-06-83264")
        self.assertTrue(r["candidates"])
        self.assertTrue(all(c["group"] == "E0" for c in r["candidates"]))

    # 21 index miss is never "does not exist"; B2 coverage reported
    def test_miss_and_coverage(self):
        r = run("--part-number", "999-99-99999")
        self.assertEqual(r["found"], 0)
        self.assertFalse(r["coverage_complete"])
        self.assertTrue(any("HD785-7-B2" in n for n in r["notes"]))

    # 22 serial outside B1 coverage is reported
    def test_serial_outside_coverage(self):
        r = run("--part-number", "6218-11-5830", "--serial", "N9000")
        self.assertTrue(any("outside indexed book coverage" in n for n in r["notes"]))

    # 23 verification reads only candidate pages
    def test_verify_reads_only_candidate_pages(self):
        r = run("--part-number", "6218-11-5830", "--verify")
        self.assertLessEqual(r["pdf_pages_read"], 2)

    # 24 read-only connection
    def test_read_only(self):
        con = L.connect(L.DEFAULT_DB)
        with self.assertRaises(Exception):
            con.execute("CREATE TABLE x(a)")


if __name__ == "__main__":
    unittest.main()
