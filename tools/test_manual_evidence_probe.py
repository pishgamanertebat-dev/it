"""Regression checks for indexed, bounded manual retrieval."""
import json
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


if __name__ == "__main__":
    unittest.main()
