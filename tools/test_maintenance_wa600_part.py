"""WA600 direct Part-only and optional enrichment routing regressions."""
import importlib.util
import json
import sys
import threading
import time
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "wa600_part_plugin", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class WA600MaintenancePart(unittest.TestCase):
    def setUp(self):
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: Path(
            "C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
        modules = patch.dict(sys.modules, {"hermes_constants": constants})
        modules.start()
        self.addCleanup(modules.stop)

    def test_direct_part_only_auto_image_without_manual_or_web(self):
        question = "WA600-6 Part Number 6240-19-1810"
        with patch.object(plugin, "run_batch", side_effect=AssertionError("Manual/web must not run")):
            result = json.loads(plugin.part_lookup(
                {"model": "WA600-6", "question": question, "part_number": "6240-19-1810"},
                session_id="wa600-direct-part-test"))
        self.assertEqual(result["source_intent"], "part")
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual(result["candidates"][0]["view_pages"], [36])
        self.assertEqual([page["pdf_page"] for page in result["rendered"]], [36])
        self.assertEqual(len(result["media"]), 1)
        self.assertTrue(Path(result["media"][0][6:]).is_file())

    def test_miss_has_no_media_and_no_other_model_part(self):
        result = json.loads(plugin.part_lookup({
            "model": "WA600-6", "question": "WA600-6 Part Number 6218-11-5830",
            "part_number": "6218-11-5830"}, session_id="wa600-part-miss-test"))
        self.assertEqual(result["found"], 0)
        self.assertEqual(result["media"], [])
        self.assertNotIn("rendered", result)

    def test_wa600_enrichment_is_nonblocking_and_does_not_request_render(self):
        started = threading.Event()
        release = threading.Event()
        def part(model, query, session):
            self.assertEqual(model, "WA600-6")
            started.set()
            release.wait(timeout=2)
            return {"found": 0, "candidates": [], "coverage_complete": False}
        def manual(*_):
            self.assertTrue(started.wait(timeout=2))
            return {"manual_packet": {"evidence_coverage": {"status": "complete"}}}
        with patch.object(plugin, "enrichment_lookup", side_effect=part), \
             patch.object(plugin, "run_batch", side_effect=manual):
            try:
                before = time.perf_counter()
                manual_result, enrichment, metrics = plugin.run_retrieval(
                    [], "wa600-enrichment-test", "WA600-6", "water pump")
                self.assertLess(time.perf_counter() - before, 0.5)
                self.assertIn("manual_packet", manual_result)
                self.assertTrue(metrics["part_launched"])
                self.assertEqual(metrics["waiting_ms"], 0)
                self.assertNotEqual(enrichment.get("status"), "skipped")
            finally:
                release.set()
        class Complete:
            returncode = 0
            stdout = b'{"found":0,"candidates":[]}'
            stderr = b''
        with patch.object(plugin.subprocess, "run", return_value=Complete()) as call:
            plugin.enrichment_lookup("WA600-6", "water pump", "wa600-enrichment-test")
        command = call.call_args.args[0]
        self.assertIn("--verify", command)
        self.assertNotIn("--render", command)
        self.assertNotIn("--auto-render-verified", command)


if __name__ == "__main__":
    unittest.main()