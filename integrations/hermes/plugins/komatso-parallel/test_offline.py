"""Targeted offline tests. Set KOMATSO_TEST_HERMES_ROOT; run with Python -B.

Requires existing Hermes dependencies and parallel-web 1.3.2 for the MockTransport
contract test; does not install anything or use production credentials/config.
"""
import asyncio
import importlib.util
from importlib.metadata import version
import json
import os
from pathlib import Path
import socket
import subprocess
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

HERE = Path(__file__).resolve().parent
ROOT = HERE.parents[3]
sys.path.insert(0, str(Path(os.environ["KOMATSO_TEST_HERMES_ROOT"]).resolve()))


class ParallelOfflineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=ROOT / "runtime")
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name)
        self.patch(patch.dict(os.environ, {"HERMES_HOME": str(self.home),
            "PARALLEL_API_KEY": "synthetic-key", "PARALLEL_SEARCH_MODE": "advanced"}))
        self.loop = asyncio.new_event_loop()
        self.addCleanup(self.loop.close)
        denied = Mock(side_effect=AssertionError("Network forbidden"))
        self.patch(patch.object(socket.socket, "connect", denied))
        self.patch(patch.object(socket.socket, "connect_ex", denied))
        self.patch(patch.object(socket, "getaddrinfo", denied))
        from hermes_cli.plugins import PluginContext, PluginManager, parse_manifest_file
        self.manager = PluginManager()
        manifest = parse_manifest_file(HERE / "plugin.yaml", HERE, "user", "")
        self.assertEqual(manifest.name, "komatso-parallel")
        module = self.manager._load_directory_module(manifest, module_name="hermes_plugins.test_komatso_parallel")
        self.bridge = sys.modules[module.__name__ + ".provider"]
        module.register(PluginContext(manifest, self.manager))
        self.provider = self.bridge.KomatsoParallelProvider()
        spec = importlib.util.spec_from_file_location("komatso_worker_test", HERE / "worker.py")
        self.worker = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(self.worker)
        self.patch(patch("tools.interrupt.is_interrupted", return_value=False))

    def patch(self, patcher):
        value = patcher.start()
        self.addCleanup(patcher.stop)
        return value

    def test_registry_coexists_with_builtin_and_selects_explicit_name(self):
        from agent import web_search_registry as registry
        from plugins.web.parallel.provider import ParallelWebSearchProvider
        from hermes_cli.plugins import PluginContext, PluginManifest
        builtin = ParallelWebSearchProvider()
        context = PluginContext(PluginManifest(name="synthetic-builtin"), self.manager)
        context.register_web_search_provider(builtin)
        self.assertIs(registry.get_provider("parallel"), builtin)
        custom = registry.get_provider("komatso-parallel")
        self.assertEqual(custom.name, "komatso-parallel")
        self.assertIs(registry._resolve("komatso-parallel", capability="search"), custom)
        self.assertIs(registry._resolve("komatso-parallel", capability="extract"), custom)
        self.assertEqual(builtin.name, "parallel")

    def test_search_mode_and_cap(self):
        with patch.object(self.bridge, "_run", return_value={"success": True}) as run:
            for mode, expected in (("advanced", "advanced"), ("turbo", "turbo"),
                ("fast", "fast"), ("basic", "basic"), ("agentic", "advanced"), (" FAST ", "fast")):
                with self.subTest(mode=mode), patch.dict(os.environ, {"PARALLEL_SEARCH_MODE": mode}):
                    self.provider.search("synthetic query", 99)
                    run.assert_called_with("search", query="synthetic query", limit=20, mode=expected)

    def test_interruption_does_not_launch_worker(self):
        with patch("tools.interrupt.is_interrupted", return_value=True), \
             patch.object(self.bridge, "_run") as sync, patch.object(self.bridge, "_run_async") as async_run:
            self.assertEqual(self.provider.search("x")["error"], "Interrupted")
            result = self.loop.run_until_complete(self.provider.extract(["https://example.invalid"]))
            self.assertEqual(result[0]["error"], "Interrupted")
            sync.assert_not_called()
            async_run.assert_not_called()

    def test_bridge_process_boundary_and_secret_handling(self):
        python = self.home / "worker-python.exe"
        python.touch()
        with patch.dict(os.environ, {"KOMATSO_PARALLEL_PYTHON": str(python)}), \
             patch.object(self.bridge.subprocess, "run", return_value=SimpleNamespace(
                 returncode=0, stdout=b'{"ok":true,"result":{"success":true,"data":{"web":[]}}}')) as run:
            before = sys.modules.get("parallel")
            self.assertTrue(self.provider.is_available())
            run.assert_not_called()
            self.assertTrue(self.provider.search("synthetic")["success"])
            args, kwargs = run.call_args
            self.assertEqual(args[0][1:3], ["-I", "-B"])
            self.assertNotIn("synthetic-key", " ".join(args[0]))
            self.assertEqual(json.loads(kwargs["input"])["api_key"], "synthetic-key")
            self.assertNotIn("PARALLEL_API_KEY", kwargs["env"])
            self.assertEqual(kwargs["timeout"], 180)
            self.assertIs(sys.modules.get("parallel"), before)
        with patch.dict(os.environ, {"KOMATSO_PARALLEL_PYTHON": sys.executable}):
            self.assertFalse(self.provider.is_available())
        with patch.dict(os.environ, {"KOMATSO_PARALLEL_PYTHON": "relative/python"}):
            self.assertFalse(self.provider.is_available())

    def test_unset_worker_python_is_unavailable(self):
        with patch.dict(os.environ):
            os.environ.pop("KOMATSO_PARALLEL_PYTHON", None)
            self.assertFalse(self.provider.is_available())
            with patch.object(self.bridge, "get_provider_env", side_effect=os.getenv):
                self.assertFalse(self.provider.is_available())

    def test_worker_rejects_wrong_sdk_and_shared_environment(self):
        prefix = self.home / "worker"
        prefix.mkdir()
        (prefix / "pyvenv.cfg").write_text("include-system-site-packages = false\n")
        with patch.object(sys, "prefix", str(prefix)), patch.object(sys, "base_prefix", str(self.home)), \
             patch.object(self.worker, "version", return_value="1.3.2"):
            self.worker._check_environment(str(self.home / "hermes"))
            with self.assertRaises(RuntimeError):
                self.worker._check_environment(str(prefix))
            with patch.object(self.worker, "version", return_value="0.4.2"), self.assertRaises(RuntimeError):
                self.worker._check_environment(str(self.home / "hermes"))
            (prefix / "pyvenv.cfg").write_text("include-system-site-packages = true\n")
            with self.assertRaises(RuntimeError):
                self.worker._check_environment(str(self.home / "hermes"))

    def test_timeout_errors_and_extraction_shape(self):
        with patch.object(self.bridge, "_run", side_effect=subprocess.TimeoutExpired("worker", 180)):
            self.assertEqual(self.provider.search("x")["error"], "Parallel worker timed out.")
        with patch.object(self.bridge, "_run_async", new=AsyncMock(side_effect=TimeoutError)):
            rows = self.loop.run_until_complete(self.provider.extract(["https://example.invalid"], max_chars=10))
            self.assertEqual(rows[0]["error"], "Parallel worker timed out.")
        for code in ("dependency", "request", "synthetic-secret"):
            with self.assertRaises(RuntimeError) as error:
                self.bridge._decode(0, json.dumps({"ok": False, "error": code}).encode())
            self.assertNotIn("synthetic-secret", str(error.exception))

    def test_async_worker_cleanup_on_cancellation(self):
        process = SimpleNamespace(returncode=None, kill=Mock(),
            communicate=AsyncMock(side_effect=[asyncio.CancelledError(), (b"", b"")]))
        with patch.object(self.bridge, "_command", return_value=["synthetic-python"]), \
             patch.object(self.bridge.asyncio, "create_subprocess_exec", new=AsyncMock(return_value=process)):
            with self.assertRaises(asyncio.CancelledError):
                self.loop.run_until_complete(self.bridge._run_async("extract", urls=[]))
            process.kill.assert_called_once()
            self.assertEqual(process.communicate.await_count, 2)

    def test_real_132_sdk_with_mock_http_transport(self):
        # Validate serialization against the actual installed SDK, not a copy
        # of its signature. Only MockTransport receives requests.
        import httpx
        import parallel
        self.assertEqual(version("parallel-web"), "1.3.2")
        sync_class, async_class = parallel.Parallel, parallel.AsyncParallel
        calls = []
        def respond(request):
            calls.append((request.url.path, json.loads(request.content)))
            if request.url.path.endswith("/search"):
                body = {"search_id": "synthetic", "session_id": "synthetic", "results": [
                    {"url": "https://example.invalid", "title": "Synthetic", "excerpts": ["one", "two"]}]}
            else:
                body = {"extract_id": "synthetic", "session_id": "synthetic", "results": [
                    {"url": "https://example.invalid/a", "title": "Full", "full_content": "full", "excerpts": []},
                    {"url": "https://example.invalid/b", "title": "Fallback", "full_content": "", "excerpts": ["one", "two"]}],
                    "errors": [{"url": "https://example.invalid/c", "content": "", "error_type": "unavailable"}]}
            return httpx.Response(200, json=body)
        transport = httpx.MockTransport(respond)
        def sync_factory(**kwargs):
            return sync_class(**kwargs, http_client=httpx.Client(transport=transport))
        def async_factory(**kwargs):
            return async_class(**kwargs, http_client=httpx.AsyncClient(transport=transport))
        with patch.object(parallel, "Parallel", side_effect=sync_factory), \
             patch.object(parallel, "AsyncParallel", side_effect=async_factory):
            result = self.worker.search(dict(api_key="synthetic-key", query="synthetic", mode="advanced", limit=99))
            self.assertEqual(result["data"]["web"][0]["description"], "one two")
            self.assertEqual(result["data"]["web"][0]["position"], 1)
            rows = self.loop.run_until_complete(self.worker.extract(dict(api_key="synthetic-key", urls=["https://example.invalid"])))
            self.assertEqual(rows[0]["content"], "full")
            self.assertEqual(rows[1]["raw_content"], "one\n\ntwo")
            self.assertEqual(rows[2]["error"], "unavailable")
        self.assertEqual(calls[0], ("/v1/search", {"search_queries": ["synthetic"], "objective": "synthetic",
            "mode": "advanced", "advanced_settings": {"max_results": 20}}))
        self.assertEqual(calls[1][1]["advanced_settings"], {"full_content": True})


if __name__ == "__main__":
    unittest.main(verbosity=2)
