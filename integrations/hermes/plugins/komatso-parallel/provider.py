"""Hermes-side bridge. No SDK imports, lazy installs or shared client caches."""
from __future__ import annotations

import asyncio
import json
import os
from pathlib import Path
import subprocess
import sys

from agent.web_search_provider import WebSearchProvider, get_provider_env

WORKER = Path(__file__).with_name("worker.py")
WORKER_TIMEOUT = 180


def _mode():
    mode = os.getenv("PARALLEL_SEARCH_MODE", "advanced").strip().lower()
    return mode if mode in {"turbo", "fast", "basic", "advanced"} else "advanced"


def _command():
    value = get_provider_env("KOMATSO_PARALLEL_PYTHON")
    if not value:
        raise ValueError("Set KOMATSO_PARALLEL_PYTHON to the dedicated worker venv's absolute Python path.")
    path = Path(value)
    if not path.is_absolute() or not path.is_file():
        raise ValueError("Set KOMATSO_PARALLEL_PYTHON to the dedicated worker venv's absolute Python path.")
    if path.resolve() == Path(sys.executable).resolve():
        raise ValueError("Parallel 1.3.2 must use a separate venv, not the Hermes interpreter.")
    return [str(path), "-I", "-B", str(WORKER)]


def _payload(operation, **kwargs):
    key = get_provider_env("PARALLEL_API_KEY")
    if not key:
        raise ValueError("PARALLEL_API_KEY is not configured.")
    return json.dumps(dict(operation=operation, api_key=key,
                           parent_prefix=sys.prefix, **kwargs)).encode("utf-8")


def _process_options():
    # -I ignores PYTHONPATH/user-site; do not pass credentials in argv or logs.
    env = dict(os.environ)
    env.pop("PARALLEL_API_KEY", None)
    return dict(env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))


def _decode(returncode, stdout):
    if returncode:
        # SDK tracebacks/HTTP response bodies may contain credentials. Never
        # expose worker stderr or arbitrary exception messages to the caller.
        raise RuntimeError("Isolated Parallel worker failed; verify its venv and parallel-web==1.3.2.")
    response = json.loads(stdout)
    if not response.get("ok"):
        code = response.get("error")
        messages = {
            "dependency": "Worker requires an isolated venv with parallel-web==1.3.2.",
            "request": "Parallel request failed in the isolated worker.",
        }
        raise RuntimeError(messages.get(code, "Invalid Parallel worker response."))
    return response["result"]


def _run(operation, **kwargs):
    result = subprocess.run(_command(), input=_payload(operation, **kwargs),
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, timeout=WORKER_TIMEOUT,
        check=False, **_process_options())
    return _decode(result.returncode, result.stdout)


async def _run_async(operation, **kwargs):
    command, payload = _command(), _payload(operation, **kwargs)
    process = await asyncio.create_subprocess_exec(*command, stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.PIPE, **_process_options())
    try:
        stdout, _ = await asyncio.wait_for(process.communicate(payload), WORKER_TIMEOUT)
        return _decode(process.returncode, stdout)
    finally:
        if process.returncode is None:
            process.kill()
            await process.communicate()


class KomatsoParallelProvider(WebSearchProvider):
    @property
    def name(self):
        return "komatso-parallel"

    @property
    def display_name(self):
        return "Komatso Parallel (advanced)"

    def is_available(self):
        if not get_provider_env("PARALLEL_API_KEY"):
            return False
        try:
            _command()  # cheap path check; no process, network or installation
        except ValueError:
            return False
        return True

    def supports_extract(self):
        return True

    def search(self, query, limit=5):
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return {"success": False, "error": "Interrupted"}
        try:
            return _run("search", query=query, limit=min(limit, 20), mode=_mode())
        except (subprocess.TimeoutExpired, TimeoutError):
            return {"success": False, "error": "Parallel worker timed out."}
        except (ValueError, RuntimeError) as exc:
            return {"success": False, "error": str(exc)}
        except Exception:
            return {"success": False, "error": "Could not run the isolated Parallel worker."}

    async def extract(self, urls, **kwargs):
        from tools.interrupt import is_interrupted
        if is_interrupted():
            return [{"url": u, "title": "", "error": "Interrupted"} for u in urls]
        try:
            return await _run_async("extract", urls=urls)
        except TimeoutError:
            error = "Parallel worker timed out."
        except (ValueError, RuntimeError) as exc:
            error = str(exc)
        except Exception:
            error = "Could not run the isolated Parallel worker."
        return [{"url": u, "title": "", "content": "", "error": error} for u in urls]

    def get_setup_schema(self):
        return {"name": self.display_name, "badge": "paid",
            "tag": "Parallel 1.3.2 in a separate Python environment.",
            "env_vars": [
                {"key": "PARALLEL_API_KEY", "prompt": "Parallel API key", "url": "https://parallel.ai"},
                {"key": "KOMATSO_PARALLEL_PYTHON", "prompt": "Dedicated worker venv Python (absolute path)"},
            ]}
