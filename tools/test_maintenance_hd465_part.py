"""HD465-7R direct Part-only routing: auto-image only when a view is verified."""
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
    "hd465_part_plugin", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class HD465MaintenancePart(unittest.TestCase):
    def setUp(self):
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: Path(
            "C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
        modules = patch.dict(sys.modules, {"hermes_constants": constants})
        modules.start()
        self.addCleanup(modules.stop)

    def test_direct_part_with_view_returns_media_without_manual(self):
        question = "HD465-7R Part Number 569-46-62810"
        with patch.object(plugin, "run_batch", side_effect=AssertionError("Manual/web must not run")):
            result = json.loads(plugin.part_lookup(
                {"model": "HD465-7R", "question": question, "part_number": "569-46-62810"},
                session_id="hd465-direct-part-test"))
        self.assertEqual(result["source_intent"], "part")
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual([page["pdf_page"] for page in result["rendered"]], [431])
        self.assertEqual(len(result["media"]), 1)

    def test_ref_mismatch_returns_text_without_media(self):
        result = json.loads(plugin.part_lookup({
            "model": "HD465-7R", "question": "HD465-7R Part Number 6240-51-1100",
            "part_number": "6240-51-1100"},
            session_id="hd465-no-media-test"))
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual(result["media"], [])
        self.assertNotIn("rendered", result)
        self.assertEqual(result["candidates"][0]["view_pages"], [])

    def test_enrichment_does_not_request_render(self):
        started = threading.Event()
        release = threading.Event()

        def part(model, query, session):
            self.assertEqual(model, "HD465-7R")
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
                    [], "hd465-enrichment-test", "HD465-7R", "cylinder head")
                self.assertLess(time.perf_counter() - before, 0.5)
                self.assertIn("manual_packet", manual_result)
                self.assertEqual(metrics["waiting_ms"], 0)
            finally:
                release.set()
        class Complete:
            returncode = 0
            stdout = b'{"found":0,"candidates":[]}'
            stderr = b''
        with patch.object(plugin.subprocess, "run", return_value=Complete()) as call:
            plugin.enrichment_lookup("HD465-7R", "cylinder head", "hd465-enrichment-test")
        command = call.call_args.args[0]
        self.assertIn("--verify", command)
        self.assertNotIn("--render", command)
        self.assertNotIn("--auto-render-verified", command)


if __name__ == "__main__":
    unittest.main()
