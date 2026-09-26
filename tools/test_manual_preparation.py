"""Scope, deduplication and source-policy checks for the native preparation hook."""
import importlib.util
import json
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manual_preparation", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)
BALE_RETARDER = "ریتارد کار نمیکنه ضعیفه هر جفتش، واسه دستگاه 465 چه کنم ؟"

class FakeRootTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.original_root = hook.ROOT
        hook.ROOT = Path(self.tmp.name)
        (hook.ROOT / "tools").mkdir()
        (hook.ROOT / "tools/manual_models.json").write_text(json.dumps({"HD785-7":"truck"}))
        (hook.ROOT / "AGENTS.md").write_text("Shared root rule",encoding="utf-8")
        (hook.ROOT / "truck").mkdir()
        (hook.ROOT / "truck/AGENTS.md").write_text("# Device\n\nSerial 7001-UP\n\n## ایمنی\n\nShared root rule\n\nRelease stored pressure.\n\n## IMAGE DELIVERY\n\nDuplicate presentation.\n",encoding="utf-8")
        self.home = hook.ROOT / "profiles/maintenance"
        skill = self.home / "skills/maintenance-two-stream-evidence/SKILL.md"
        skill.parent.mkdir(parents=True)
        skill.write_text("1. **Technical** — Correct Shop Manual; no invented measurements.\n2. Fleet",encoding="utf-8")
        self.question = "Why does this device fail?"
        self.fleet = {"goal":"Fleet history","context":"Existing fleet contract"}
        self.args = {"tasks":[{"goal":"Manual evidence", "context":hook.MARKER+json.dumps({"model":"HD785-7","question":self.question})+"\nVerified unit"},self.fleet]}
    def tearDown(self):
        hook.ROOT = self.original_root
        self.tmp.cleanup()

class PreparationTests(FakeRootTest):
    def test_prepares_only_technical_preserves_question_once_and_safety(self):
        result = hook.prepare_args(self.args,self.home,"session")
        context = result["tasks"][0]["context"]
        self.assertIs(result["tasks"][1],self.fleet)
        self.assertEqual(context.count(self.question),1)
        self.assertIn("Serial 7001-UP",context)
        self.assertIn("Release stored pressure",context)
        self.assertNotIn("Shared root rule",context)
        self.assertNotIn("Duplicate presentation",context)
        self.assertEqual(self.args["tasks"][0]["context"].count(hook.MARKER),1)
        self.assertIsNone(hook.prepare_args(result,self.home))
    def test_profiles_and_unmarked_tasks_are_untouched(self):
        self.assertIsNone(hook.prepare_args(self.args,self.home.parent/"default"))
        self.assertIsNone(hook.prepare_args({"tasks":[self.fleet,self.fleet]},self.home))
        self.assertIsNone(hook.prepare_args({"tasks":[self.args["tasks"][0]]},self.home))
    def test_existing_question_and_device_rules_are_not_repeated(self):
        text = (hook.ROOT/"truck/AGENTS.md").read_text(encoding="utf-8")
        self.args["tasks"][0]["context"] += "\n"+self.question+"\n"+text
        context = hook.prepare_args(self.args,self.home)["tasks"][0]["context"]
        self.assertEqual(context.count(self.question),1)
        self.assertEqual(context.count("Release stored pressure"),1)
    def test_system_policy_requires_authenticated_prepared_child(self):
        info = {"platform":"subagent", "profile_name":"maintenance", "session_id":"technical-test"}
        self.assertEqual(hook.prepared_policy(info), "")
        result = hook.prepare_args(self.args,self.home)
        hook.subagent_start(child_session_id="technical-test",child_goal=result["tasks"][0]["goal"])
        self.assertIn("prerequisite is satisfied",hook.prepared_policy(info))
        self.assertEqual(hook.prepared_policy(dict(info,session_id="fleet-test")), "")
        self.assertEqual(hook.prepared_policy(dict(info,profile_name="default")), "")
        hook.subagent_stop(child_session_id="technical-test")
        self.assertEqual(hook.prepared_policy(info), "")

    def test_unverified_model_fails_before_retrieval(self):
        self.args["tasks"][0]["context"] = hook.MARKER+json.dumps({"model":"unknown","question":self.question})
        with self.assertRaises(ValueError):hook.prepare_args(self.args,self.home)
    def test_test_policy_selected_by_general_intent(self):
        text = "# Device\n\nScope.\n\n### تست فشار\n\nGauge rating.\n\n### تست برقی\n\nProbe position."
        self.assertIn("Gauge rating",hook.select_rules(text,"","pressure test")[0])
        self.assertNotIn("Gauge rating",hook.select_rules(text,"","serial range")[0])


class ParentFastPathTests(FakeRootTest):
    def setUp(self):
        super().setUp()
        self.calls = []
        def fake_batch(command, session_id):
            self.calls.append(([str(part) for part in command], session_id))
            return {"manual_packet": {"evidence": []}, "web_search": {"success": True}}
        patcher = patch.object(hook, "run_batch", side_effect=fake_batch)
        patcher.start()
        self.addCleanup(patcher.stop)
        constants = types.ModuleType("hermes_constants")
        constants.get_hermes_home = lambda: self.home
        modules = patch.dict(sys.modules, {"hermes_constants": constants})
        modules.start()
        self.addCleanup(modules.stop)
        hook._requests.clear(); hook._retrieves.clear(); hook._child_sessions.clear()
    def call(self, session="parent", **args):
        return json.loads(hook.manual_evidence(args, session_id=session))
    def retrieve(self, session="parent", **extra):
        return self.call(session, phase="retrieve", model="HD785-7", question=self.question,
                         keywords="steering heavy", **extra)

    def test_retrieve_reads_device_rules_keeps_presentation_and_runs_one_batch(self):
        result = self.retrieve()
        policy = result["prepared"]["applicable_device_policy"]
        self.assertIn("Release stored pressure", policy)
        self.assertIn("Duplicate presentation", policy)
        self.assertNotIn("Shared root rule", policy)
        self.assertTrue(result["prepared"]["device_agents_read_in_full"].endswith("AGENTS.md"))
        (command, session), = self.calls
        self.assertEqual(session, "parent")
        self.assertEqual(command[0], "retrieve")
        self.assertEqual(command[command.index("--component") + 1], "steering heavy")
        self.assertNotIn("--broad", command)
        request = json.loads(Path(command[command.index("--request-file") + 1]).read_text(encoding="utf-8"))
        self.assertEqual((request["model"], request["question"]), ("HD785-7", self.question))
        self.assertEqual(request["profile_home"], str(self.home))

    def test_exact_bale_question_requires_normalized_manual_keywords(self):
        self.question = BALE_RETARDER
        missing = self.call(phase="retrieve", model="HD785-7", question=BALE_RETARDER)
        self.assertFalse(missing["success"])
        self.assertIn("keywords", missing["error"])
        self.assertEqual(self.calls, [])
        self.assertIn("prepared", self.retrieve())

    def test_finish_is_bound_to_retrieving_session_and_batches_follow_ups(self):
        request_id = self.retrieve()["prepared"]["request_id"]
        self.assertFalse(self.call("other", phase="finish", request_id=request_id, render_pages=[3])["success"])
        self.call(phase="finish", request_id=request_id, render_pages=[3, 4], read_pages=[5], web_url="https://example.test/a")
        command = self.calls[-1][0]
        self.assertEqual(command[0], "finish")
        self.assertEqual(command[command.index("--render-pages") + 1:command.index("--read-pages")], ["3", "4"])
        self.assertEqual(command[command.index("--web-url") + 1], "https://example.test/a")
        self.assertFalse(self.call(phase="finish", request_id=request_id)["success"])

    def test_fallback_is_explicit_and_retrieval_is_bounded(self):
        self.retrieve(broad=True)
        self.assertIn("--broad", self.calls[-1][0])
        for _ in range(hook.MAX_RETRIEVES - 1):
            self.retrieve()
        limited = self.retrieve()
        self.assertFalse(limited["success"])
        self.assertIn("missing evidence", limited["error"])

    def test_delegated_workers_and_other_profiles_cannot_use_parent_tool(self):
        hook.subagent_start(child_session_id="fleet-child", child_goal="Fleet history")
        self.assertFalse(self.retrieve("fleet-child")["success"])
        hook.subagent_stop(child_session_id="fleet-child")
        self.home = self.home.parent / "default"
        self.assertIn("Maintenance", self.retrieve()["error"])
        self.assertEqual(self.calls, [])

    def test_coverage_decides_whether_another_retrieve_is_allowed(self):
        def batch(_command, _session):
            return {"manual_packet": {"evidence_coverage": self.coverage}, "web_search": {"success": True}}
        original = hook.run_batch.side_effect
        hook.run_batch.side_effect = batch
        self.addCleanup(setattr, hook.run_batch, "side_effect", original)
        self.coverage = {"status": "complete", "reason": "topic complete", "missing_component_terms": []}
        hook._retrieves.clear()
        ready = self.retrieve()
        self.assertEqual(ready["evidence_coverage"]["status"], "complete")
        self.assertIn("Do not retrieve again", ready["next"])
        self.assertTrue(ready["next"].startswith("evidence_coverage.status is complete"))
        self.coverage = {"status": "incomplete", "reason": "heading missing", "missing_component_terms": ["widget"]}
        hook._retrieves.clear()
        refine = self.retrieve()
        self.assertIn("widget", refine["next"])
        self.assertIn("broad=true", refine["next"])
        self.assertNotIn("Do not retrieve again", refine["next"])
        hook._retrieves.clear()
        exhausted = self.retrieve(broad=True)
        self.assertIn("Do not retrieve again", exhausted["next"])
        self.coverage = {"status": "truncated", "reason": "page limit", "resume_at_pdf_pages": [4]}
        hook._retrieves.clear()
        cut = self.retrieve()
        self.assertIn("read_pages", cut["next"])
        self.assertIn("Do not retrieve again", cut["next"])

    def test_schema_exposes_verified_models_and_bounded_pages(self):
        schema = hook.tool_schema({"HD785-7": "truck"})["parameters"]
        self.assertEqual(schema["required"], ["phase"])
        self.assertEqual(schema["properties"]["model"]["enum"], ["HD785-7"])
        self.assertEqual(schema["properties"]["render_pages"]["maxItems"], 8)


class RealDevicePolicyTests(unittest.TestCase):
    def test_exact_bale_retarder_policy_keeps_safety_tests_and_answer_rules(self):
        device = (ROOT / "HD465-7R_HD605-7R/AGENTS.md").read_text(encoding="utf-8")
        root = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        rules, headings = hook.select_rules(device, root, BALE_RETARDER, presentation=True)
        joined = " ".join(headings)
        for heading in ("ایمنی", "تست فشار", "قالب پاسخ", "IMAGE DELIVERY", "مدل و سریال"):
            self.assertIn(heading, joined)
        for heading in ("FAST ", "MINIMIZE TOOL", "WINDOWS EXECUTION", "Part Number"):
            self.assertNotIn(heading, joined)
        self.assertLess(len(rules), len(device))
        child, _ = hook.select_rules(device, root, BALE_RETARDER)
        self.assertNotIn("IMAGE DELIVERY", child)

if __name__ == "__main__": unittest.main()
