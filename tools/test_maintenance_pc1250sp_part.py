"""PC1250SP-8R Maintenance Part routing and image safety."""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pc1250sp_part_plugin",
    ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class PC1250SPMaintenancePart(unittest.TestCase):
    def setUp(self):
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: Path(
            "C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
        modules = patch.dict(sys.modules, {"hermes_constants": constants})
        modules.start()
        self.addCleanup(modules.stop)

    def lookup(self, pn):
        return json.loads(plugin.part_lookup({
            "model": "PC1250SP-8R", "question": "PC1250SP-8R Part Number " + pn,
            "part_number": pn}, session_id="pc1250sp-part-test"))

    def test_verified_direct_part_has_one_matching_view(self):
        with patch.object(plugin, "run_batch", side_effect=AssertionError("Shop/web used")):
            result = self.lookup("21N-01-11170")
        self.assertEqual(result["source_intent"], "part")
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual([p["pdf_page"] for p in result["rendered"]], [137])
        self.assertEqual(len(result["media"]), 1)

    def test_ambiguous_serial_and_multiple_figures_have_no_media(self):
        for pn in ("21N-00-41170", "08037-02512"):
            result = self.lookup(pn)
            self.assertGreater(result["found"], 0)
            self.assertEqual(result["media"], [])
            self.assertNotIn("rendered", result)

    def test_enrichment_is_non_rendering(self):
        class Complete:
            returncode = 0
            stdout = b'{"found":0,"candidates":[]}'
            stderr = b''
        with patch.object(plugin.subprocess, "run", return_value=Complete()) as call:
            plugin.enrichment_lookup("PC1250SP-8R", "engine mount", "pc1250sp-enrichment")
        command = call.call_args.args[0]
        self.assertIn("--verify", command)
        self.assertNotIn("--render", command)
        self.assertNotIn("--auto-render-verified", command)


if __name__ == "__main__":
    unittest.main()
