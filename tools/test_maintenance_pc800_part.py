"""PC800-8 Maintenance Part routing, without adopting the PC800-8R Shop Manual."""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "pc800_part_plugin",
    ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)


class PC800MaintenancePart(unittest.TestCase):
    def setUp(self):
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: Path(
            "C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
        modules = patch.dict(sys.modules, {"hermes_constants": constants})
        modules.start()
        self.addCleanup(modules.stop)

    def lookup(self, pn, model="PC800-8"):
        return json.loads(plugin.part_lookup({
            "model": model, "question": model + " Part Number " + pn,
            "part_number": pn}, session_id="pc800-part-test"))

    def test_part_and_shop_model_lists_stay_separate(self):
        shop = plugin.manual_models()
        self.assertNotIn("PC800-8", shop)
        self.assertEqual(shop["PC800-8R"], "PC800")
        self.assertIn("PC800-8", plugin.INDEXED_PART_MODELS)
        self.assertNotIn("PC800-8R", plugin.INDEXED_PART_MODELS)
        self.assertNotIn("PC800LC-8", plugin.INDEXED_PART_MODELS)
        class Context:
            def __init__(self):
                self.tools = {}
            def register_hook(self, *args, **kwargs):
                pass
            def register_system_prompt_section(self, *args, **kwargs):
                pass
            def register_tool(self, **kwargs):
                self.tools[kwargs["name"]] = kwargs
        ctx = Context()
        plugin.register(ctx)
        shop_enum = ctx.tools[plugin.TOOL]["schema"]["parameters"]["properties"]["model"]["enum"]
        part_enum = ctx.tools[plugin.PART_TOOL]["schema"]["parameters"]["properties"]["model"]["enum"]
        self.assertNotIn("PC800-8", shop_enum)
        self.assertIn("PC800-8R", shop_enum)
        self.assertIn("PC800-8", part_enum)
        self.assertEqual(
            plugin.verified_device("PC800-8R", "engine does not start"),
            ROOT / "PC800" / "AGENTS.md")
        with self.assertRaises(ValueError):
            plugin.verified_device("PC800-8", "Part Number 209-01-42232")

    def test_pc800_8r_part_lookup_stays_unindexed_on_its_shop_file(self):
        result = self.lookup("209-01-42232", model="PC800-8R")
        self.assertFalse(result["indexed_lookup_available"])
        self.assertTrue(result["prepared"]["device_agents_read_in_full"].endswith(
            "PC800\\AGENTS.md") or result["prepared"]["device_agents_read_in_full"].endswith(
            "PC800/AGENTS.md"))

    def test_verified_direct_part_does_not_load_shop_agents(self):
        with patch.object(plugin, "run_batch", side_effect=AssertionError("Shop/web used")):
            result = self.lookup("209-01-42232")
        self.assertIsNone(result["prepared"]["device_agents_read_in_full"])
        self.assertIsNone(result["prepared"]["shop_manual_identity"])
        self.assertEqual(result["prepared"]["applicable_device_policy"], "")
        self.assertEqual(result["source_intent"], "part")
        self.assertEqual(result["found"], 1)
        self.assertEqual(result["candidates"][0]["pdf_verification"]["status"], "VERIFIED")
        self.assertEqual([page["pdf_page"] for page in result["rendered"]], [2])
        self.assertEqual(len(result["media"]), 1)
        self.assertEqual(self.lookup("209-06-73330")["found"], 0)

    def test_ambiguous_and_multiple_figures_have_no_media(self):
        for pn in ("209-03-41641", "08037-02512"):
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
            plugin.enrichment_lookup("PC800-8", "engine mount", "pc800-enrichment")
        command = call.call_args.args[0]
        self.assertIn("--verify", command)
        self.assertNotIn("--render", command)
        self.assertNotIn("--auto-render-verified", command)


if __name__ == "__main__":
    unittest.main()
