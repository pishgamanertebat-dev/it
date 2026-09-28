"""Batch invariants; no network/model calls."""
import contextlib
import io
import json
import os
import sys
from pathlib import Path
import tempfile
import threading
import unittest
from unittest.mock import patch
import manual_worker_batch as worker

class BatchTests(unittest.TestCase):
    def test_independent_operations_run_concurrently_and_keep_both_results(self):
        barrier=threading.Barrier(2)
        def first():barrier.wait(timeout=2);return {"pages":[7]}
        def second():barrier.wait(timeout=2);return {"success":False,"error":"unavailable"}
        results,timings=worker.batch([("manual",first),("web",second)])
        self.assertEqual(results["manual"]["pages"],[7])
        self.assertFalse(results["web"]["success"])
        self.assertEqual(set(timings),{"manual","web"})
    def test_failure_keeps_independent_manual_result(self):
        def fail():raise ValueError("provider details must not leak")
        results,_=worker.batch([("manual",lambda:{"valid":True}),("web",fail)])
        self.assertTrue(results["manual"]["valid"])
        self.assertNotIn("provider details",str(results))
        self.assertIn("missing",results["web"]["error"])
    def test_private_request_must_be_inside_runtime(self):
        with self.assertRaises(ValueError):worker.prepared_request(worker.ROOT/"AGENTS.md")
        with tempfile.TemporaryDirectory(dir=worker.ROOT/"runtime") as folder:
            p=Path(folder)/"request.json"
            p.write_text(json.dumps({"model":"unknown","question":"x"}))
            with self.assertRaises(ValueError):worker.prepared_request(p)
    def test_skip_web_keeps_manual_packet_without_repeating_provider_call(self):
        with tempfile.TemporaryDirectory(dir=worker.ROOT/"runtime") as folder:
            request=Path(folder)/"request.json"
            request.write_text(json.dumps({
                "model":"HD785-7", "question":"retarder is weak",
                "profile_home":"C:/Users/win-10/AppData/Local/hermes/profiles/maintenance"
            }),encoding="utf-8")
            argv=["manual_worker_batch.py","retrieve","--request-file",str(request),
                  "--component","retarder","--skip-web"]
            with (
                patch.object(sys,"argv",argv),
                patch.object(worker,"run_probe",return_value={"evidence_coverage":{"status":"complete"}}),
                patch.object(worker,"web_operation",side_effect=AssertionError("repeat provider call")),
                contextlib.redirect_stdout(io.StringIO()) as output,
            ):
                worker.main()
            packet=json.loads(output.getvalue())
            self.assertEqual(packet["manual_packet"]["evidence_coverage"]["status"],"complete")
            self.assertIn("suppressed",packet["web_search"]["error"])
            self.assertEqual(packet["batch_metrics"]["backend_operations"],2)

    def test_web_uses_prepared_maintenance_profile_under_multiplex(self):
        home=Path("C:/Users/win-10/AppData/Local/hermes/profiles/maintenance")
        with patch.object(worker.subprocess,"run") as run:
            run.return_value.returncode=0
            run.return_value.stdout=b'{"success":true}'
            self.assertTrue(worker.web_operation(home,"search",query="public model")['success'])
            self.assertEqual(run.call_args.kwargs['env']['HERMES_HOME'],str(home))
            self.assertNotIn("query",str(run.call_args.args))

if __name__=="__main__":unittest.main()
