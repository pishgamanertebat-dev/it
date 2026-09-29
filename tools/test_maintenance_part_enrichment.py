"""Optional Part Book enrichment: overlap, nonblocking collection and source isolation."""
import importlib.util
import json
import subprocess
import threading
import time
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("enrichment", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
plugin = importlib.util.module_from_spec(spec)
spec.loader.exec_module(plugin)
MANUAL = {"manual_packet": {"evidence_coverage": {"status": "complete"}, "text": "technical evidence"}}


def packet(verification="VERIFIED", description="PUMP ASS'Y", pn="705-22-45040"):
    return {"found": 1, "coverage_complete": False, "coverage_note": "B1 partial; B2 not indexed",
            "candidates": [{"figure": "J0100", "item": 1, "part_number": pn,
                            "description": description, "quantity": "1", "applicability": "N10001-",
                            "figure_title": "HYDRAULIC PUMP (STEERING AND HOIST)",
                            "pdf_verification": {"status": verification}}]}


class EnrichmentTests(unittest.TestCase):
    def setUp(self):
        plugin._request_enrichments.clear()
        plugin._retrieves.clear()

    def test_a_b_component_query_runs_in_parallel_with_manual(self):
        for query in ("boom foot pin", "steering pump"):
            barrier = threading.Barrier(2)
            def part(*_):
                barrier.wait(timeout=2)
                return packet(description="PIN" if query == "boom foot pin" else "PUMP")
            def manual(*_):
                barrier.wait(timeout=2)
                time.sleep(0.03)  # Manual work continues after the two branches started.
                return MANUAL
            with patch.object(plugin, "run_batch", side_effect=manual), patch.object(plugin, "enrichment_lookup", side_effect=part):
                result, extra, metrics = plugin.run_retrieval([], "s", "HD785-7", query)
            self.assertIs(result, MANUAL)
            self.assertEqual(extra["status"], "verified")
            self.assertGreater(metrics["overlap_ms"], 0)
            self.assertEqual(metrics["waiting_ms"], 0)

    def test_c_generic_and_unindexed_models_skip_without_subprocess(self):
        for model, query, reason in (("HD785-7", None, "no_query"), ("HD785-7", "", "no_query"),
                                     ("HD605-7R", "steering pump", "unindexed_model"),
                                     ("HD785-7", "581-91-19110", "invalid_query"),
                                     ("HD785-7", "پمپ فرمان", "invalid_query"),
                                     ("HD785-7", "x" * 81, "invalid_query")):
            with patch.object(plugin, "run_batch", return_value=MANUAL), \
                 patch.object(plugin, "enrichment_lookup", side_effect=AssertionError("Must skip")):
                result, extra, metrics = plugin.run_retrieval([], "s", model, query)
            self.assertIs(result, MANUAL)
            self.assertEqual(extra["reason"], reason)
            self.assertFalse(metrics["part_launched"])

    def test_f_part_work_never_blocks_manual_completion_and_can_arrive_at_finish(self):
        entered, release, done = threading.Event(), threading.Event(), threading.Event()
        def part(*_):
            entered.set()
            release.wait(timeout=3)
            try:
                return packet()
            finally:
                done.set()
        def manual(*_):
            self.assertTrue(entered.wait(timeout=2))
            return MANUAL
        with patch.object(plugin, "enrichment_lookup", side_effect=part), \
             patch.object(plugin, "run_batch", side_effect=manual):
            try:
                start = time.perf_counter()
                result, extra, metrics = plugin.run_retrieval([], "s", "HD785-7", "steering pump", "a" * 24)
                self.assertLess(time.perf_counter() - start, 0.2)
                self.assertIs(result, MANUAL)
                self.assertEqual(extra["status"], "not_ready")
                plugin._requests["a" * 24] = "s"
                release.set()
                # Synchronize on the actual publication event, only in the test.
                self.assertTrue(plugin._request_enrichments["a" * 24][1].wait(timeout=2))
                with patch.object(plugin, "run_batch", return_value=dict(MANUAL)):
                    finish = plugin.finish({"request_id": "a" * 24, "read_pages": [1]}, "s")
                self.assertEqual(finish["part_enrichment"]["status"], "verified")
            finally:
                release.set()
                done.wait(timeout=2)

    def test_ready_result_is_reused_at_finish_without_another_lookup(self):
        def manual(*_):
            time.sleep(0.02)
            return dict(MANUAL)
        key = "c" * 24
        with patch.object(plugin, "enrichment_lookup", return_value=packet()) as lookup, \
             patch.object(plugin, "run_batch", side_effect=manual):
            _, first, _ = plugin.run_retrieval([], "owner", "HD785-7", "steering pump", key)
            plugin._requests[key] = "owner"
            last = plugin.finish({"request_id": key, "read_pages": [1]}, "owner")
        self.assertEqual(first["status"], "verified")
        self.assertEqual(last["part_enrichment"], first)
        self.assertEqual(lookup.call_count, 1)

    def test_f_finish_also_never_waits_and_is_session_bound(self):
        ready = threading.Event()
        key = "b" * 24
        plugin._requests[key] = "owner"
        plugin._request_enrichments[key] = ("owner", ready, {}, time.monotonic())
        with patch.object(plugin, "run_batch", return_value=dict(MANUAL)):
            with self.assertRaises(ValueError):
                plugin.finish({"request_id": key, "read_pages": [1]}, "other")
            result = plugin.finish({"request_id": key, "read_pages": [1]}, "owner")
        self.assertEqual(result["part_enrichment"]["status"], "not_ready")
        self.assertNotIn(key, plugin._request_enrichments)

    def test_f_error_and_timeout_are_fail_open(self):
        for error, status in ((ValueError("private details"), "error"),
                               (subprocess.TimeoutExpired("cli", 1), "timeout"),
                               (TypeError("bad result"), "error")):
            def manual(*_):
                time.sleep(0.02)
                return MANUAL
            with patch.object(plugin, "run_batch", side_effect=manual), \
                 patch.object(plugin, "enrichment_lookup", side_effect=error):
                result, extra, _ = plugin.run_retrieval([], "s", "HD785-7", "steering pump")
            self.assertIs(result, MANUAL)
            self.assertEqual(extra["status"], status)
            self.assertNotIn("private", json.dumps(extra))

    def test_verified_only_and_no_incidental_figure_rows(self):
        for data, status in ((packet("MISMATCH"), "unusable"), (packet("needs_verification"), "unusable"),
                             (packet(description="BOLT"), "unusable"), (packet(description="STEERING VALVE"), "unusable"), (packet(description=None), "unusable"),
                             ({"found": 0, "candidates": []}, "miss")):
            result = plugin.compact_enrichment(data, "steering pump")
            self.assertEqual(result["status"], status)
            self.assertEqual(result["candidates"], [])
        irrelevant = plugin.compact_enrichment(packet(description="BRAKE COOLING OIL LINE UNIT"), "brake oil filter")
        self.assertEqual(irrelevant["status"], "unusable")
        result = plugin.compact_enrichment(packet(), "steering pump")
        row = result["candidates"][0]
        for key in ("figure", "item", "part_number", "description", "quantity", "applicability", "verification"):
            self.assertIn(key, row)
        self.assertFalse(result["coverage_complete"])
        self.assertNotIn("pdf_row_text", json.dumps(result))

    def test_enrichment_cli_is_bounded_verified_and_never_renders(self):
        with patch.object(plugin.subprocess, "run") as run:
            run.return_value.returncode = 0
            run.return_value.stdout = json.dumps(packet()).encode()
            plugin.enrichment_lookup("HD785-7", "steering pump", "session")
        command = run.call_args.args[0]
        self.assertIn("--verify", command)
        self.assertIn("--query", command)
        self.assertNotIn("--render", command)
        self.assertEqual(run.call_args.kwargs["timeout"], 1.0)
        self.assertEqual(run.call_args.kwargs["env"]["HERMES_SESSION_ID"], "session")

    def test_retrieve_supplies_parent_query_without_changing_intent(self):
        with patch.object(plugin, "run_retrieval", return_value=(MANUAL, {"status": "verified"}, {})) as run:
            result = plugin.retrieve(dict(model="HD785-7", question="HD785-7 پمپ فرمان فشار نداره",
                                          keywords="steering pump low pressure", part_query="steering pump"),
                                     Path("profiles/maintenance"), "same-parent")
        self.assertEqual(run.call_args.args[3], "steering pump")
        self.assertEqual(result["part_enrichment"]["status"], "verified")
        self.assertIn("PARTBOOK_RULES_V1", result["prepared"]["applicable_device_policy"])
        self.assertEqual(plugin.source_intent("HD785-7 پمپ فرمان فشار نداره"), "technical")
        self.assertEqual(plugin.source_intent("روش تست شیر ریتاردر و شماره فنی آن"), "mixed")

    def test_real_over_budget_subprocess_is_killed_and_manual_remains_successful(self):
        original_run = subprocess.run
        def slow_child(_command, **kwargs):
            return original_run([str(ROOT / ".venv/Scripts/python.exe"), "-c",
                                 "import time; time.sleep(5)"], **kwargs)
        def manual(*_):
            time.sleep(1.25)
            return MANUAL
        with patch.object(plugin.subprocess, "run", side_effect=slow_child), \
             patch.object(plugin, "run_batch", side_effect=manual):
            started = time.perf_counter()
            result, extra, metrics = plugin.run_retrieval([], "s", "HD785-7", "steering pump")
        self.assertIs(result, MANUAL)
        self.assertEqual(extra["status"], "timeout")
        self.assertEqual(metrics["waiting_ms"], 0)
        self.assertLess(time.perf_counter() - started, 2)

    def test_future_model_coverage_never_borrows_hd7857_pilot_note(self):
        spec = importlib.util.spec_from_file_location("lookup_coverage", ROOT / "tools/fleet/partbook_lookup.py")
        lookup = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(lookup)
        note = lookup.coverage_note("OTHER-MODEL", [{"book_id": "OTHER-B1", "index_status": "indexed_partial",
                                                   "machine_serial_prefix": "S", "machine_serial_from": 100,
                                                   "machine_serial_to": 200}])
        self.assertIn("OTHER-B1", note)
        self.assertIn("S100-S200", note)
        self.assertNotIn("HD785-7", note)
        self.assertIn("NOT evidence of absence", note)

    def test_schema_requires_no_extra_call_or_part_query(self):
        schema = plugin.tool_schema({"HD785-7": "HD785-7"})["parameters"]
        self.assertNotIn("part_query", schema["required"])
        self.assertEqual(schema["properties"]["part_query"]["type"], ["string", "null"])
        self.assertIn("SAME retrieve call", plugin.TOOL_DESCRIPTION)

if __name__ == "__main__":
    unittest.main()