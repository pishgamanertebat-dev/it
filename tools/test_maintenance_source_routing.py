"""Maintenance source intent regression: real lookup, dispatch guard and tool registration."""
import importlib.util
import json
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("source_routing", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
A = "شماره فنی 581-91-19110 دستگاه 785-7"
B = "شماره فنی قطعه 6218-11-5830 برای HD785-7"
C = "HD785-7 ریتاردر ضعیف شده علت چیست؟"
D = "شیر ریتاردر مشکل دارد، روش تست و شماره فنی آن را بده"

class SourceRoutingTests(unittest.TestCase):
    def setUp(self):
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: Path("C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
        self.modules = patch.dict(sys.modules, {"hermes_constants": constants})
        self.modules.start()
        self.addCleanup(self.modules.stop)

    def lookup(self, question, **args):
        return json.loads(plugin.part_lookup(dict(model="HD785-7", question=question, **args)))

    def test_category_intents_not_one_pn(self):
        for question in (A, B, "785-7 600-319-3550", "PC800 20Y-60-22121",
                         "Part Number filter", "part no 12345-12-12345", "پارت نامبر فیلتر",
                         "شماره قطعه شیر", "Figure H3410-03A0 Item 10", "نمای انفجاری فیلتر"):
            with self.subTest(question=question):
                self.assertEqual(plugin.source_intent(question), "part")
        self.assertEqual(plugin.source_intent(C), "technical")
        self.assertEqual(plugin.source_intent(D), "mixed")
        self.assertEqual(plugin.source_intent("Fault and Part Number"), "mixed")
        self.assertEqual(plugin.source_intent("retarder valve pressure test"), "technical")

    def test_a_real_verified_lookup_before_any_manual_or_web(self):
        original_run = plugin.subprocess.run
        with patch.object(plugin, "run_batch", side_effect=AssertionError("Manual/web must not run")), \
             patch.object(plugin.subprocess, "run", wraps=original_run) as run:
            result = self.lookup(A)
        self.assertEqual(run.call_count, 1)
        command = run.call_args.args[0]
        self.assertTrue(command[1].endswith("partbook_lookup.py"))
        self.assertIn("--verify", command)
        self.assertIn("581-91-19110", command)
        self.assertIn("PARTBOOK_RULES_V1", result["prepared"]["applicable_device_policy"])
        candidates = result["candidates"]
        self.assertEqual({(c["figure"], c["item"]) for c in candidates},
                         {("H3410-03A0", 10), ("H3410-03B0", 5)})
        self.assertTrue(all(c["description"] == "FILTER" and c["quantity"] == "2"
                            and c["pdf_verification"]["status"] == "VERIFIED" for c in candidates))

    def test_b_other_pn_uses_same_verified_command(self):
        original_run = plugin.subprocess.run
        with patch.object(plugin.subprocess, "run", wraps=original_run) as run:
            result = self.lookup(B)
        self.assertIn("6218-11-5830", run.call_args.args[0])
        self.assertEqual(result["source_intent"], "part")

    def test_direct_verified_part_delivers_one_media_page_in_same_tool_result(self):
        result = json.loads(plugin.part_lookup(
            dict(model="HD785-7", question="????? ??? ???? 6218-11-5830 ???? HD785-7 ?? ????? ??"),
            session_id="part-direct-regression"))
        self.assertEqual(result["source_intent"], "part")
        self.assertEqual(len(result["media"]), 1)
        self.assertIn("partbook-p31.png", result["media"][0])
        self.assertTrue(Path(result["media"][0][6:]).is_file())
        self.assertEqual([page["pdf_page"] for page in result["rendered"]], [31])
        self.assertIn("same final response", result["next"])

    def test_direct_part_miss_has_no_media(self):
        result = self.lookup("HD785-7 Part Number 99999-99-99999")
        self.assertEqual(result["found"], 0)
        self.assertEqual(result["media"], [])
        self.assertNotIn("rendered", result)

    def test_part_only_manual_dispatch_stops_before_rules_filter_or_scan(self):
        with patch.object(plugin, "run_batch", side_effect=AssertionError("Manual/web must not run")):
            result = json.loads(plugin.manual_evidence(dict(phase="retrieve", model="HD785-7",
                                                           question=A, keywords="filter")))
        self.assertFalse(result["success"])
        self.assertIn("maintenance_partbook_lookup first", result["error"])

    def test_c_technical_dispatch_preserves_fast_path_and_never_part_lookup(self):
        with patch.object(plugin, "run_batch", return_value={"manual_packet": {}}) as batch, \
             patch.object(plugin, "part_lookup", side_effect=AssertionError("Unrequested parts")):
            result = plugin.retrieve(dict(model="HD785-7", question=C, keywords="retarder weak"),
                                     Path("profiles/maintenance"), "routing-technical-test")
        self.assertEqual(batch.call_count, 1)
        self.assertEqual(batch.call_args.args[0][0], "retrieve")
        self.assertIn("prepared", result)

    def test_d_mixed_preserves_both_source_contracts_and_rules(self):
        result = self.lookup(D, query="retarder valve")
        self.assertEqual(result["source_intent"], "mixed")
        self.assertIn("Shop Manual technical", result["next"])
        with patch.object(plugin, "run_batch", return_value={"manual_packet": {}}) as batch:
            manual = plugin.retrieve(dict(model="HD785-7", question=D, keywords="retarder valve test"),
                                     Path("profiles/maintenance"), "routing-mixed-test")
        self.assertEqual(batch.call_count, 1)
        self.assertIn("PARTBOOK_RULES_V1", manual["prepared"]["applicable_device_policy"])

    def test_e_miss_is_incomplete_coverage_never_absence(self):
        result = self.lookup("HD785-7 Part Number 99999-99-99999")
        self.assertEqual(result["found"], 0)
        self.assertFalse(result["coverage_complete"])
        self.assertIn("NOT evidence of absence", result["miss"])
        self.assertIn("B2", result["coverage_note"])

    def test_source_filter_keeps_part_rules_for_persian_pn_and_bare_pn(self):
        device = (ROOT / "HD785-7/AGENTS.md").read_text(encoding="utf-8")
        root = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        for question in (A, B, D, "785-7 581-91-19110"):
            rules, _ = plugin.select_rules(device, root, question, presentation=True)
            self.assertIn("PARTBOOK_RULES_V1", rules)
            self.assertIn("partbook_lookup.py", rules)

    def test_parallel_403_suppresses_same_turn_repeat_but_keeps_manual(self):
        session = "web-403-routing-regression"
        calls = []
        def retrieval(command, *_):
            calls.append(command)
            return ({"manual_packet": {"evidence_coverage": {"status": "incomplete"}},
                     "web_search": {"success": True, "backend_error":
                                    "Parallel search failed: 403 Forbidden /v1beta/search; rescued by free tier"}},
                    {"status": "skipped"}, {})
        try:
            with patch.object(plugin, "run_retrieval", side_effect=retrieval):
                first = plugin.retrieve(dict(model="HD785-7", question=C, keywords="retarder weak"),
                                        Path("profiles/maintenance"), session)
                second = plugin.retrieve(dict(model="HD785-7", question=C, keywords="rear brake ineffective"),
                                         Path("profiles/maintenance"), session)
            self.assertNotIn("--skip-web", calls[0])
            self.assertIn("--skip-web", calls[1])
            self.assertIn("manual_packet", first["retrieval"])
            self.assertIn("manual_packet", second["retrieval"])
        finally:
            key = (session, C.strip())
            plugin._web_failures.discard(key)
            plugin._retrieves.pop(key, None)
            for request_id, owner_key in list(plugin._request_keys.items()):
                if owner_key == key:
                    plugin._request_keys.pop(request_id, None)
                    plugin._requests.pop(request_id, None)

    def test_schema_is_registered_in_existing_maintenance_toolset(self):
        class Context:
            def __init__(self): self.tools = {}
            def register_hook(self, *a, **kw): pass
            def register_system_prompt_section(self, *a, **kw): pass
            def register_tool(self, **kw): self.tools[kw["name"]] = kw
        ctx = Context()
        plugin.register(ctx)
        self.assertEqual(ctx.tools[plugin.PART_TOOL]["toolset"], "komatso_maintenance")
        self.assertIs(ctx.tools[plugin.PART_TOOL]["handler"], plugin.part_lookup)
        self.assertIn(plugin.TOOL, ctx.tools)

    def test_unindexed_model_is_explicit_and_never_borrows_pilot(self):
        with patch.object(plugin.subprocess, "run", side_effect=AssertionError("No cross-model lookup")):
            result = json.loads(plugin.part_lookup(dict(model="HD465-7R", question="Part Number filter", query="filter")))
        self.assertFalse(result["indexed_lookup_available"])
        self.assertFalse(result["coverage_complete"])
        self.assertIn("HD785-5", plugin.INDEXED_PART_MODELS)

    def test_outside_serial_reports_coverage_gap(self):
        result = self.lookup(A, serial="N9000")
        self.assertTrue(any("outside indexed book coverage" in note for note in result["notes"]))

if __name__ == "__main__":
    unittest.main()