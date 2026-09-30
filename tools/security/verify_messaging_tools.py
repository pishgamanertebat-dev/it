"""Verify model-callable Hermes messaging tools against a strict allowlist.

Runs installed Hermes plugin discovery and the same toolset/schema resolvers
used by Gateway turns. Output contains only toolset and tool names.
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

ALLOWED = {
    ("default", "bale"): {"web_search"},
    ("default", "telegram"): {"web_search"},
    ("maintenance", "bale"): {
        "web_search", "maintenance_manual_evidence", "maintenance_partbook_lookup"
    },
    ("maintenance", "telegram"): {"web_search"},
}
FORBIDDEN = {
    "terminal", "process_manage", "execute_code", "read_file", "write_file",
    "patch", "search_files", "manage_connections", "delegate_task",
    "skill_manage", "computer_use", "read_terminal", "close_terminal",
    "desktop_preview", "drive_preview", "read_window_below", "focus_pane",
}

def inspect(home: Path, platform: str, source: Path) -> dict:
    os.environ["HERMES_HOME"] = str(home.resolve())
    sys.path.insert(0, str(source.resolve()))
    from agent.skill_utils import parse_config_string_list
    from hermes_cli.config_effective import load_user_config_effective
    from hermes_cli.plugins import discover_plugins
    from hermes_cli.tools_config import _get_platform_tools
    from model_tools import get_tool_definitions

    discover_plugins()
    config = load_user_config_effective(home / "config.yaml")
    toolsets = sorted(_get_platform_tools(config, platform))
    disabled = parse_config_string_list(
        (config.get("agent") or {}).get("disabled_toolsets")
    ) or None
    raw = get_tool_definitions(
        toolsets, disabled, quiet_mode=True, skip_tool_search_assembly=True
    )
    visible = get_tool_definitions(toolsets, disabled, quiet_mode=True)
    names = sorted({item["function"]["name"] for item in raw})
    visible_names = sorted({item["function"]["name"] for item in visible})
    profile = "maintenance" if home.name == "maintenance" else "default"
    allowed = ALLOWED.get((profile, platform))
    forbidden = sorted(
        name for name in names
        if name in FORBIDDEN or name.startswith("browser_")
    )
    unexpected = sorted(set(names) - allowed) if allowed is not None else []
    return {
        "profile": profile,
        "platform": platform,
        "toolsets": toolsets,
        "model_tools": names,
        "visible_model_tools": visible_names,
        "forbidden_model_tools": forbidden,
        "unexpected_model_tools": unexpected,
    }

def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-home", type=Path, required=True)
    parser.add_argument("--platform", choices=("bale", "telegram", "cli"), required=True)
    parser.add_argument("--hermes-source", type=Path, required=True)
    parser.add_argument("--output", type=Path)
    parser.add_argument("--snapshot-only", action="store_true")
    args = parser.parse_args()
    result = inspect(args.profile_home, args.platform, args.hermes_source)
    rendered = json.dumps(result, ensure_ascii=False, indent=2) + "\n"
    if args.output:
        args.output.write_text(rendered, encoding="utf-8")
    print(rendered, end="")
    return 0 if args.snapshot_only or not (
        result["forbidden_model_tools"] or result["unexpected_model_tools"]
    ) else 1

if __name__ == "__main__":
    raise SystemExit(main())
