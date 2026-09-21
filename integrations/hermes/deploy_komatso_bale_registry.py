"""Back up and deploy only the registry __init__.py; never restart Hermes."""
from datetime import datetime, timezone
import os
from pathlib import Path
import shutil
from uuid import uuid4


def main():
    source = Path(__file__).resolve().parent / "plugins/komatso-bale-registry/__init__.py"
    target = Path(os.environ["LOCALAPPDATA"]) / "hermes/plugins/komatso-bale-registry/__init__.py"
    if not source.is_file():
        raise FileNotFoundError("Canonical plugin file is required.")
    backup = None
    if target.exists():
        stamp = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
        backup = target.with_name(f"{target.name}.{stamp}.{uuid4().hex}.bak")
        shutil.copy2(target, backup)
        if backup.read_bytes() != target.read_bytes():
            raise RuntimeError("Backup verification failed; runtime was not overwritten.")
    else:
        target.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(source, target)
    if source.read_bytes() != target.read_bytes():
        raise RuntimeError(f"Deploy verification failed; backup: {backup}")
    print(f"Backup: {backup}\nDeployed: {target}")


if __name__ == "__main__":
    main()
