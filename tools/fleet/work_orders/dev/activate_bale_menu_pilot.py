"""Install the reviewed plugin patch and gracefully stop the idle gateway.

The caller then launches the existing Hermes_Gateway.vbs. This script neither
edits credentials/config nor starts a second gateway or forcibly kills one.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sys
import tempfile
from datetime import datetime, timezone
from pathlib import Path


HERMES_ROOT = Path(r"C:\Users\win-10\AppData\Local\hermes")
STAGE_ROOT = Path(r"E:\KomatsoAI\runtime\work_order_bale_pilot")


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    manifest = json.loads((STAGE_ROOT / "manifest.json").read_text(encoding="utf-8"))
    target = HERMES_ROOT / "plugins" / "komatso-bale-registry" / "__init__.py"
    staged = STAGE_ROOT / "komatso-bale-registry" / "__init__.py"
    if digest(target) != manifest["original_sha256"] or digest(staged) != manifest["staged_sha256"]:
        raise RuntimeError("Plugin changed since staging; review before installing.")
    for name in (".env", "config.yaml"):
        if digest(HERMES_ROOT / name) != manifest["protected_sha256"][name]:
            raise RuntimeError("Configuration changed since review; inspect before installing.")
    compile(staged.read_text(encoding="utf-8"), str(target), "exec")
    state = json.loads((HERMES_ROOT / "gateway_state.json").read_text(encoding="utf-8"))
    if state.get("active_agents") != 0 or state.get("gateway_state") != "running":
        raise RuntimeError("Gateway is busy or not running; wait for it to be idle.")
    if not args.apply:
        print("Ready: reviewed plugin patch, idle gateway, unchanged configuration.")
        return

    os.environ["HERMES_HOME"] = str(HERMES_ROOT)
    sys.path.insert(0, str(HERMES_ROOT / "hermes-agent"))
    sys.path.append(str(HERMES_ROOT / "hermes-agent" / "venv" / "Lib" / "site-packages"))
    from gateway.status import get_running_pid
    from hermes_cli.gateway_windows import _drain_gateway_pid
    running_pid = get_running_pid()
    if running_pid is None or running_pid != state["pid"]:
        raise RuntimeError("Gateway process changed; inspect before restarting.")

    stamp = datetime.now(timezone.utc).strftime("%Y%m%d_%H%M%S")
    backup = Path(r"E:\KomatsoAI\backup\work_order_bale_pilot") / stamp / "__init__.py"
    backup.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(target, backup)
    with tempfile.NamedTemporaryFile(dir=target.parent, suffix=".tmp", delete=False) as output:
        temporary = Path(output.name)
        output.write(staged.read_bytes())
    try:
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    assert digest(target) == manifest["staged_sha256"]
    receipt = {"plugin_backup": str(backup), "old_pid": running_pid, "installed_sha256": digest(target), "installed_at_utc": stamp}
    (STAGE_ROOT / "deployment.json").write_text(json.dumps(receipt, indent=2), encoding="utf-8")
    print(f"Plugin installed; backup: {backup}", flush=True)
    print(f"Requesting graceful stop of idle gateway PID {running_pid}...", flush=True)
    if not _drain_gateway_pid(running_pid, 30):
        raise RuntimeError("Gateway has not exited yet; no force kill or duplicate launch performed.")
    print("Gateway stopped gracefully. Ready to launch the existing VBS service wrapper.", flush=True)


if __name__ == "__main__":
    main()
