"""Scope, deduplication and source-policy checks for the native preparation hook."""
import importlib.util
import json
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("manual_preparation", ROOT / "integrations/hermes/plugins/komatso-maintenance-manual/__init__.py")
hook = importlib.util.module_from_spec(spec)
spec.loader.exec_module(hook)

class PreparationTests(unittest.TestCase):
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

if __name__ == "__main__": unittest.main()
