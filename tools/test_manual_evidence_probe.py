"""Regression checks for indexed, bounded manual retrieval."""
import argparse
import json
import re
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import pymupdf

import manual_evidence_probe as probe


class ManualEvidenceProbeTests(unittest.TestCase):
    def test_hd785_retarder_uses_index_and_surfaces_rear_brake_diagnosis(self):
        section_map = probe.ROOT / "HD785-7" / "manual_sections.json"
        metadata = json.loads(section_map.read_text(encoding="utf-8"))
        terms = probe.search_terms("retarder not working")
        chosen = probe.route(metadata["sections"], terms, "retarder not working", limit=5)
        result = probe.probe(section_map, metadata, chosen, terms, top=8)
        pages = {hit["pdf_page"] for hit in result["evidence"]}
        self.assertFalse(result["full_manual_fallback"])
        self.assertLess(result["searched_pages"], 250)
        self.assertIn(1165, pages)  # H-11 rear brake ineffective, including retarder test
        self.assertTrue(any("testing_adjusting" in s["key"] for s in result["searched_sections"]))
        self.assertTrue(all(1 <= p <= 1336 for p in pages))

    def test_persian_retarder_routes_like_english(self):
        self.assertEqual(probe.search_terms("ریتاردر عمل نمی‌کند"),
                         probe.search_terms("retarder not working"))
    def test_batched_full_pages_stay_bounded_and_preserve_reference(self):
        section_map = probe.ROOT / "HD785-7" / "manual_sections.json"
        metadata = json.loads(section_map.read_text(encoding="utf-8"))
        result = probe.batch_pages(section_map, metadata, [1165, 364, 1165],
                                   page_chars=5000)
        self.assertEqual([p["pdf_page"] for p in result["pages"]], [1165, 364])
        self.assertIn("H-11 Rear brake is ineffective", result["pages"][0]["text"])
        self.assertTrue(all(len(p["text"]) <= 5000 for p in result["pages"]))
    def test_exact_fault_code_is_first_evidence(self):
        section_map = probe.ROOT / "HD785-7" / "manual_sections.json"
        metadata = json.loads(section_map.read_text(encoding="utf-8"))
        code = "DK51L5"
        terms = probe.search_terms("retarder fault", code=code)
        chosen = probe.route(metadata["sections"], terms, "retarder fault", code)
        existing = {path for path, _ in chosen}
        chosen += [(path, item) for path, item in probe.nodes(metadata["sections"])
                   if "failure_code" in path and not item.get("subsections")
                   and path not in existing]
        result = probe.probe(section_map, metadata, chosen, terms,
                             required_code=code)
        self.assertFalse(result["full_manual_fallback"])
        self.assertIn(code.casefold(), result["evidence"][0]["matched_terms"])
        self.assertEqual(result["evidence"][0]["pdf_page"], 928)
    def test_full_scan_only_when_index_has_no_hits(self):
        with tempfile.TemporaryDirectory() as temp:
            folder = Path(temp)
            pdf = folder / "shop.pdf"
            with pymupdf.open() as doc:
                for text in ("Index", "Hydraulic pump pressure 12 MPa", "Electrical wiring"):
                    page = doc.new_page()
                    page.insert_text((50, 50), text)
                doc.save(pdf)
            section_map = folder / "manual_sections.json"
            metadata = {
                "manual": str(pdf), "pdf_pages": 3,
                "sections": {"electrical": {"pdf_start": 3, "pdf_end": 3}},
            }
            section_map.write_text(json.dumps(metadata), encoding="utf-8")
            chosen = [("electrical", metadata["sections"]["electrical"])]
            result = probe.probe(section_map, metadata, chosen, ["hydraulic"], top=2)
            self.assertTrue(result["full_manual_fallback"])
            self.assertEqual(result["searched_pages"], 3)
            self.assertEqual(result["evidence"][0]["pdf_page"], 2)
            self.assertIn("12 MPa", result["evidence"][0]["excerpt"])


    def packet(self, model, question):
        section_map = probe.ROOT / probe.MODELS[model] / "manual_sections.json"
        metadata = json.loads(section_map.read_text(encoding="utf-8"))
        terms = probe.search_terms(question)
        chosen = probe.route(metadata["sections"], terms, question)
        result = probe.evidence_packet(section_map, metadata,
                                      probe.probe(section_map, metadata, chosen, terms, top=6))
        self.assertFalse(result["full_manual_fallback"])
        self.assertLessEqual(result["text_chars"], 22000)
        self.assertEqual(len(result["evidence"]), len({v["pdf_page"] for v in result["evidence"]}))
        self.assertEqual(Path(result["manual"]).parent, section_map.parent)
        return {item["pdf_page"]: item for item in result["evidence"]}

    def test_retarder_packet_with_model_in_question_keeps_diagnostic_topic(self):
        pages = self.packet("HD785-7", "HD785-7 ریتاردر عمل نمی‌کند؛ علت و تست ایمن چیست؟")
        self.assertIn(1165, pages)
        self.assertFalse(pages[1165]["text_truncated"])
        self.assertIn("H-11 Rear brake is ineffective", pages[1165]["heading"])

    def test_regression_hd785_heavy_steering_preserves_conditions_and_value(self):
        pages = self.packet("HD785-7", "steering wheel is heavy")
        diagnosis = pages[1166]
        self.assertFalse(diagnosis["text_truncated"])
        self.assertIn("H-12 Steering wheel is heavy", diagnosis["heading"])
        self.assertIn("high idle", diagnosis["text"])
        self.assertIn("20.6 (+0.98/0) MPa", diagnosis["text"])

    def test_regression_pc800_no_start_preserves_all_three_branches(self):
        pages = self.packet("PC800-8R", "engine does not start")
        for number in (792, 793, 794):
            self.assertIn(number, pages)
            self.assertFalse(pages[number]["text_truncated"])
        self.assertIn("Engine does not turn", pages[792]["text"])
        self.assertIn("Engine turns but no exhaust smoke", pages[793]["text"])
        self.assertIn("Exhaust smoke comes out but engine does not start", pages[794]["text"])
        self.assertIn("CA559", pages[793]["text"])

    def test_regression_pc800_slow_boom_preserves_normal_and_heavy_lift_tests(self):
        pages = self.packet("PC800-8R", "boom is slow hydraulic pressure test")
        for number in (760, 761):
            self.assertIn(number, pages)
            self.assertFalse(pages[number]["text_truncated"])
        normal = re.sub(r"\s+", " ", pages[760]["text"])
        heavy = re.sub(r"\s+", " ", pages[761]["text"])
        self.assertIn("H-5 Boom speed or power is low", normal)
        self.assertIn("P-mode", normal)
        self.assertIn("2.9 MPa", normal)
        self.assertIn("engine stopped for the preparations", normal)
        self.assertIn("heavy lift mode", heavy)

    def test_topic_continuation_stops_at_new_heading_and_reports_limit(self):
        with pymupdf.open() as doc:
            for title, size in (("Testing pump pressure", 14), ("Continue test", 10), ("Different test", 14)):
                page = doc.new_page()
                page.insert_text((45, 45), "SEN99999-01", fontsize=10, fontname="hebo")
                page.insert_text((45, 85), title, fontsize=size, fontname="hebo")
            metadata = {"sections": {"testing": {"pdf_start": 1, "pdf_end": 3}}}
            self.assertEqual(probe.topic_pages(doc, metadata, 2), ([1, 2], False))
            self.assertEqual(probe.topic_pages(doc, metadata, 1, max_pages=1), ([1], True))

    def test_cli_comma_page_list_avoids_retry_and_preserves_references(self):
        result = subprocess.run([sys.executable, str(probe.ROOT / "tools/manual_evidence_probe.py"),
                                 "--model", "HD785-7", "--pages", "1165,363"],
                                capture_output=True, text=True, check=True)
        pages = json.loads(result.stdout)["pages"]
        self.assertEqual([item["pdf_page"] for item in pages], [1165, 363])
        self.assertEqual(probe.page_numbers("12,13"), [12, 13])
        with self.assertRaises(argparse.ArgumentTypeError):
            probe.page_numbers("12,,13")


if __name__ == "__main__":
    unittest.main()
