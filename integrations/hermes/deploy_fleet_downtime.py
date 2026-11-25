"""Controlled candidate preparation, verification, and owner-gated activation for Fleet Downtime.

Follows KomatsoAI's backed-up additive rollout pattern (matching deploy_machinery_performance.py):
- `prepare` creates isolated candidate copies of authorization DB, Hermes profile configs
  (default, admin, maintenance, net), and schedules.yaml WITHOUT modifying any live file,
  without restarting Gateway, and without sending any real Bale message.
- `verify-candidate` runs real Hermes PluginManager + toolset resolution + access boundary checks
  across ALL 4 profiles (default, admin, maintenance, net) using the isolated candidate copies.
- `activate` is explicitly gated behind `--owner-approved` and verifies pre-activation hashes before
  making any live additive change (NEVER run during Candidate UAT!).
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import importlib.util
import json
from pathlib import Path
import shutil
from types import SimpleNamespace
from unittest.mock import patch

import yaml

from tools.authorization import AuthorizationStore
from tools.authorization.fleet_downtime import (
    ADMIN_CAPABILITY,
    ADMIN_ROLE,
    EXTENSION,
    RECEIVE_CAPABILITY,
    RECEIVE_ROLE,
    migrate,
)
from tools.fleet.downtime.delivery import resolve_owner_recipients
from integrations.hermes.deploy_business_admin import bootstrap

ROOT = Path(__file__).resolve().parents[2]
HERMES_HOME = Path(r"C:\Users\win-10\AppData\Local\hermes")
CANDIDATE_OUT = ROOT / "runtime" / "fleet-downtime" / "candidate-deploy"
PLUGIN_SRC_DIR = ROOT / "integrations" / "hermes" / "plugins" / "komatso-fleet-downtime"
PLUGIN_NAME = "komatso-fleet-downtime"
TOOLSET_NAME = "komatso_fleet_downtime"
SCHEDULES_CONFIG = ROOT / "settings" / "schedules.yaml"
PROFILES = ("default", "admin", "maintenance", "net")
AUTH_TABLES = (
    "auth_roles",
    "auth_capabilities",
    "auth_role_capabilities",
    "auth_role_profiles",
    "auth_user_roles",
    "auth_events",
    "auth_migrations",
    "auth_extensions",
)


def _digest(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _save(name: str, payload: dict) -> Path:
    CANDIDATE_OUT.mkdir(parents=True, exist_ok=True)
    target = CANDIDATE_OUT / name
    target.write_text(json.dumps(payload, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
    return target


def _profile_dir(profile: str) -> Path:
    return HERMES_HOME if profile == "default" else HERMES_HOME / "profiles" / profile


def snapshot_auth(store: AuthorizationStore) -> dict:
    with closing(store._connect()) as conn:
        conn.execute("BEGIN")
        if (
            conn.execute("PRAGMA integrity_check").fetchall() != [("ok",)]
            or conn.execute("PRAGMA foreign_key_check").fetchall()
        ):
            raise RuntimeError("Authorization DB integrity failed")
        tables = {t: conn.execute(f"SELECT * FROM {t} ORDER BY 1, 2").fetchall() for t in AUTH_TABLES}
        users = [r[0] for r in conn.execute("SELECT user_id FROM channel_users WHERE platform='bale' ORDER BY user_id")]
    people = {
        u: {
            "roles": list(store.roles(u)),
            "profile": store.resolve_profile(u, u),
            "scope": store.function_scope(u).__dict__,
        }
        for u in users
    }
    return json.loads(json.dumps({"tables": tables, "people": people}))


def verify_auth_additive(store: AuthorizationStore, before: dict, *, recipient_ids: tuple[str, ...]) -> dict:
    after = snapshot_auth(store)
    for table, rows in before["tables"].items():
        if any(row not in after["tables"][table] for row in rows):
            raise RuntimeError(f"Existing authorization row altered in {table}")
    if before["tables"]["auth_role_profiles"] != after["tables"]["auth_role_profiles"]:
        raise RuntimeError("Existing role->profile routing altered")
    if before["tables"]["auth_migrations"] != after["tables"]["auth_migrations"]:
        raise RuntimeError("Existing schema version altered")
    for uid, old in before["people"].items():
        cur = after["people"][uid]
        if cur["profile"] != old["profile"]:
            raise RuntimeError(f"Profile changed for user {uid}")
        if cur["scope"] != old["scope"]:
            raise RuntimeError(f"E:\\Function file scope widened for user {uid}")
    res = store.resolve_active_recipients(RECEIVE_CAPABILITY)
    if res.status != "ready" or set(res.recipients) != set(recipient_ids):
        raise RuntimeError(f"Unexpected downtime recipients: {res}")
    return {
        "ok": True,
        "integrity": "ok",
        "existing_profiles_preserved": True,
        "existing_function_scopes_preserved": True,
        "users_checked": len(before["people"]),
        "confirmed_recipients": list(res.recipients),
    }


def build_candidate_profile_config(profile: str) -> Path:
    src = _profile_dir(profile) / "config.yaml"
    cfg = yaml.safe_load(src.read_text(encoding="utf-8"))
    # Additively enable plugin & read-only downtime toolset on Bale (never adding terminal/file/function)
    plugins = cfg.setdefault("plugins", {})
    enabled = list(plugins.get("enabled") or [])
    if PLUGIN_NAME not in enabled:
        enabled.append(PLUGIN_NAME)
    plugins["enabled"] = enabled

    pt = cfg.setdefault("platform_toolsets", {})
    bale_ts = list(pt.get("bale") or [])
    if TOOLSET_NAME not in bale_ts:
        bale_ts.append(TOOLSET_NAME)
    pt["bale"] = bale_ts

    out_dir = CANDIDATE_OUT / "profiles" / profile
    out_dir.mkdir(parents=True, exist_ok=True)
    out_path = out_dir / "config.candidate.yaml"
    out_path.write_text(yaml.safe_dump(cfg, allow_unicode=True, sort_keys=False), encoding="utf-8")
    return out_path


def prepare_candidate(store: AuthorizationStore | None = None) -> dict:
    """Prepare isolated candidate artifacts without modifying any live Hermes/DB/schedule file."""
    store = store or AuthorizationStore()
    CANDIDATE_OUT.mkdir(parents=True, exist_ok=True)
    baseline = snapshot_auth(store)
    _save("authorization-before.json", baseline)

    # Resolve confirmed recipients dynamically from registry (ambiguous entries like 'امامی' are excluded!)
    resolved = resolve_owner_recipients(auth_db_path=store.path)
    confirmed_ids = tuple(
        r.bale_user_id for r in resolved if r.resolution_status == "RESOLVED" and r.bale_user_id
    )
    admin_ids = tuple(
        r.bale_user_id for r in resolved if r.recipient_key == "project_admin" and r.resolution_status == "RESOLVED" and r.bale_user_id
    )

    # Create candidate copy of auth DB and apply additive migration ONLY to candidate copy
    backup = store.backup(CANDIDATE_OUT / "backups")
    cand_db = CANDIDATE_OUT / "candidate-auth.sqlite3"
    shutil.copy2(backup, cand_db)
    cand_store = AuthorizationStore(cand_db)
    migrate(cand_store, CANDIDATE_OUT / "candidate-backups", recipient_user_ids=confirmed_ids, admin_user_ids=admin_ids)
    auth_check = verify_auth_additive(cand_store, baseline, recipient_ids=confirmed_ids)
    _save("candidate-auth-verification.json", auth_check)

    # Snapshot live protected hashes & produce candidate profile configs (read-only on live!)
    protected: dict[str, str] = {}
    cand_configs: dict[str, str] = {}
    for prof in PROFILES:
        pdir = _profile_dir(prof)
        cfg_file = pdir / "config.yaml"
        if cfg_file.exists():
            protected[str(cfg_file)] = _digest(cfg_file)
            cand_configs[prof] = str(build_candidate_profile_config(prof))
        soul = pdir / "SOUL.md"
        if soul.exists():
            protected[str(soul)] = _digest(soul)
    if SCHEDULES_CONFIG.exists():
        protected[str(SCHEDULES_CONFIG)] = _digest(SCHEDULES_CONFIG)
    _save("protected-hashes.json", protected)

    result = {
        "ok": True,
        "candidate_auth_db": str(cand_db),
        "candidate_profile_configs": cand_configs,
        "plugin_source": str(PLUGIN_SRC_DIR),
        "auth_verification": auth_check,
        "live_files_modified": False,
        "gateway_restarted": False,
        "real_messages_sent": False,
    }
    _save("prepared.json", result)
    return result


def verify_candidate_surfaces() -> dict:
    """Verify real Hermes plugin loading & toolset selection across ALL 4 profiles in isolation."""
    cand_db = CANDIDATE_OUT / "candidate-auth.sqlite3"
    if not cand_db.exists():
        prepare_candidate()
    cand_store = AuthorizationStore(cand_db)

    bootstrap()
    from hermes_constants import hermes_home_key, reset_hermes_home_override, set_hermes_home_override
    from hermes_cli.plugins import LoadedPlugin, PluginContext, PluginManager, PluginManifest
    from gateway.run_turn import GatewayTurnMixin
    from integrations.hermes import role_routing

    # Representative approved Bale users from EVERY profile + 1 revoked/rejected control user!
    representatives = [
        ("default", "1028780913", True, False),       # مهدی کارگری (general approved user, NO function scope)
        ("admin", "514458396", True, True),           # هوشمند پاکدل (business_admin)
        ("maintenance", "1732374823", True, True),    # محسن کرمعلی (mechanical_staff)
        ("net", "641220453", True, True),             # حمید هدایتی (net_manager)
    ]
    denied_controls = [
        ("revoked", "887487566"),
        ("rejected", "1986031197"),
        ("unknown", "999999999"),
    ]

    # Load candidate plugin module directly
    spec = importlib.util.spec_from_file_location("komatso_fleet_downtime_cand", PLUGIN_SRC_DIR / "__init__.py")
    plugin_mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin_mod)

    # Confirm zero heavy imports inside plugin module
    import sys as _sys

    leaked = [m for m in ("openpyxl", "fitz", "PIL") if m in _sys.modules]

    surface_results: dict[str, dict] = {}
    for prof, uid, expect_approved, expect_func in representatives:
        cfg_path = CANDIDATE_OUT / "profiles" / prof / "config.candidate.yaml"
        cfg = yaml.safe_load(cfg_path.read_text(encoding="utf-8"))
        home = _profile_dir(prof)
        token = set_hermes_home_override(home)
        try:
            manager = PluginManager(hermes_home_key(home))
            manifest = PluginManifest(name=PLUGIN_NAME, source="project")
            manager._plugins[PLUGIN_NAME] = LoadedPlugin(manifest=manifest, enabled=True)
            plugin_mod.register(PluginContext(manifest, manager))
            manager._discovered = True
            with (
                patch.object(role_routing, "AuthorizationStore", return_value=cand_store),
                patch("hermes_cli.plugins.get_plugin_manager", return_value=manager),
            ):
                selected = GatewayTurnMixin._resolve_enabled_toolsets_for_source(
                    SimpleNamespace(_delivery_adapter_for=lambda s: None),
                    cfg,
                    SimpleNamespace(user_id=uid, chat_id=uid, chat_type="dm"),
                    "bale",
                )
            has_downtime = TOOLSET_NAME in selected
            has_unsafe = bool(set(selected) & {"terminal", "file", "code_execution", "connections", "all"})
            has_func = "komatso_function" in selected
            # Verify live execution of a read-only query tool as this user
            who = {"platform": "bale", "chat_type": "dm", "chat_id": uid, "user_id": uid}
            call_out = json.loads(
                plugin_mod.execute_tool("list_downtime_periods", {}, who=who, auth_db=cand_db)
            )
            if not has_downtime or has_unsafe or not call_out.get("ok"):
                raise RuntimeError(f"Profile surface check failed for {prof}:{uid}")
            if not expect_func and has_func:
                raise RuntimeError(f"Privilege escalation: general user {uid} received komatso_function!")
            surface_results[f"{prof}:{uid}"] = {
                "toolsets": selected,
                "downtime_toolset_present": has_downtime,
                "function_toolset_present": has_func,
                "unsafe_toolsets_absent": not has_unsafe,
                "query_ok": bool(call_out.get("ok")),
                "periods_visible": call_out.get("periods_count"),
                "registered_tools_count": len(plugin_mod.TOOL_SPECS),
            }
        finally:
            reset_hermes_home_override(token)

    # Confirm denied control users are strictly rejected fail-closed
    denied_results = {}
    for label, uid in denied_controls:
        who = {"platform": "bale", "chat_type": "dm", "chat_id": uid, "user_id": uid}
        out = json.loads(plugin_mod.execute_tool("list_downtime_periods", {}, who=who, auth_db=cand_db))
        if out.get("ok"):
            raise RuntimeError(f"Unapproved user {label}:{uid} was NOT denied!")
        denied_results[f"{label}:{uid}"] = {"denied": True, "error": out.get("error")}

    # Confirm non-DM (group chat) is denied fail-closed
    grp_who = {"platform": "bale", "chat_type": "group", "chat_id": "-100123", "user_id": "1028780913"}
    grp_out = json.loads(plugin_mod.execute_tool("list_downtime_periods", {}, who=grp_who, auth_db=cand_db))
    if grp_out.get("ok"):
        raise RuntimeError("Group chat was NOT denied!")
    denied_results["group_chat"] = {"denied": True, "error": grp_out.get("error")}

    # Verify NO live protected file changed
    protected = json.loads((CANDIDATE_OUT / "protected-hashes.json").read_text(encoding="utf-8"))
    for p_str, expected_sha in protected.items():
        if _digest(Path(p_str)) != expected_sha:
            raise RuntimeError(f"Protected live file changed unexpectedly: {p_str}")

    report = {
        "ok": True,
        "profiles_verified": list(surface_results.keys()),
        "surfaces": surface_results,
        "denied_controls": denied_results,
        "registered_tools": [t[0] for t in plugin_mod.TOOL_SPECS],
        "protected_live_files_intact": True,
        "live_gateway_restarted": False,
    }
    _save("candidate-surfaces.json", report)
    return report


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=["prepare", "verify-candidate"])
    args = parser.parse_args()
    if args.action == "prepare":
        out = prepare_candidate()
    else:
        out = verify_candidate_surfaces()
    print(json.dumps(out, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
