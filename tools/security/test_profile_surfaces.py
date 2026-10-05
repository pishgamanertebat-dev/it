"""Actual plugin discovery + profile resolver, isolated homes A -> B -> A."""
import importlib.util
import ast
from types import SimpleNamespace
import json
import os
from pathlib import Path
import shutil
import sys
import tempfile
import unittest
from unittest.mock import patch

ROOT=Path(__file__).resolve().parents[2]
RUNTIME=Path("C:/Users/win-10/AppData/Local/hermes")
sys.path.insert(0,str(RUNTIME/"hermes-agent"))
class ProfileSurfaces(unittest.TestCase):
    def test_resolver_and_skills_delegation_surface_across_profiles(self):
        with tempfile.TemporaryDirectory(dir=ROOT/"runtime") as tmp:
            with patch.dict(os.environ,{"HERMES_HOME":str(Path(tmp)/"initial")}):
                from hermes_constants import set_hermes_home_override,reset_hermes_home_override
                from hermes_cli.plugins import discover_plugins
                from hermes_cli.tools_config import _get_platform_tools
                from model_tools import get_tool_definitions
                from tools.registry import registry
                from tools import web_tools
                forbidden={"terminal","process_manage","execute_code","read_file","write_file","patch","search_files","skill_manage","manage_connections"}
                browser={"public_browser_"+a for a in ["navigate","snapshot","click","type","hover","select","press","scroll","back","tabs","switch_tab","frame","drag","screenshot","images","console","close"]}
                research={"web_search","web_extract","skills_list","skill_view"}|browser
                homes={}
                for profile in ("default","maintenance","admin"):
                    home=Path(tmp)/profile;home.mkdir()
                    plugins=["komatso-public-research"]
                    selected=["web","skills_readonly","komatso_public_browser","no_mcp"]
                    if profile=="maintenance":
                        plugins.append("komatso-maintenance-manual")
                        selected+=["delegation","komatso_maintenance"]
                    if profile in {"admin", "maintenance"}:
                        plugins.append("komatso-function-domain")
                    if profile=="admin":
                        selected += ["delegation"]
                    (home/"plugins").mkdir()
                    for plugin in plugins:
                        shutil.copytree(ROOT/"integrations/hermes/plugins"/plugin,home/"plugins"/plugin,ignore=shutil.ignore_patterns("__pycache__"))
                    (home/"skills").mkdir()
                    config={
                        "platform_toolsets":{"bale":selected,"telegram":selected if profile=="default" else ["search","no_mcp"]},
                        "known_plugin_toolsets":{"telegram":["komatso_maintenance","komatso_public_browser"] if profile=="maintenance" else ["komatso_maintenance"]},
                        "plugins":{"enabled":plugins},
                        "skills":{"inline_shell":False},
                        "tools":{"tool_search":{"enabled":"off"}},
                    }
                    if profile in {"admin", "maintenance"}:
                        config["capability_toolsets_resolver"]="integrations.hermes.role_routing.resolve_toolsets"
                        config["known_plugin_toolsets"]["bale"]=["komatso_function"]
                    (home/"config.yaml").write_text(json.dumps(config),encoding="utf-8")
                    homes[profile]=(home,config)
                for profile,platform,capable in [("default","bale",False),("maintenance","bale",False),("maintenance","bale",True),("admin","bale",True),("default","telegram",False),("maintenance","bale",False),("admin","bale",False),("default","bale",False)]:
                    home,config=homes[profile]
                    token=set_hermes_home_override(home)
                    try:
                        with patch("hermes_constants.get_process_hermes_home",return_value=RUNTIME):
                            discover_plugins()
                            for name,schema in [("web_search",web_tools.WEB_SEARCH_SCHEMA),("web_extract",web_tools.WEB_EXTRACT_SCHEMA)]:
                                registry.register(name=name,toolset="web",schema=schema,handler=lambda args,**kw:"{}",check_fn=lambda:True)
                            from tools.admin.core_fixture import patched_core_source
                            tree=ast.parse(patched_core_source("gateway/run_turn.py"))
                            node=next(n for n in ast.walk(tree) if isinstance(n,ast.FunctionDef) and n.name=="_resolve_enabled_toolsets_for_source")
                            scope={"SessionSource":object,"logger":SimpleNamespace(warning=lambda *a,**k:None)}
                            exec(compile(ast.Module(body=[node],type_ignores=[]),"<native-capability-fixture>","exec"),scope)
                            runner=SimpleNamespace(_delivery_adapter_for=lambda source:None)
                            source=SimpleNamespace(user_id="synthetic-user",chat_id="synthetic-user",chat_type="dm")
                            with patch("integrations.hermes.role_routing.AuthorizationStore") as store:
                                store.return_value.has_capability.return_value=capable
                                enabled=scope["_resolve_enabled_toolsets_for_source"](runner,config,source,platform)
                            definitions=get_tool_definitions(enabled,quiet_mode=True,skip_tool_search_assembly=True)
                            actual={d["function"]["name"] for d in definitions}
                        expected=research|({"delegate_task","maintenance_manual_evidence","maintenance_partbook_lookup"} if profile=="maintenance" else set())
                        if profile in {"admin","maintenance"} and platform=="bale" and capable:
                            expected |= {"function_list","function_search","function_metadata","function_read","function_attach","function_report"}
                        if profile=="admin":expected.add("delegate_task")
                        self.assertEqual(actual,expected,(profile,platform,actual))
                        self.assertFalse(actual & forbidden)
                        self.assertFalse(any(n.startswith(("browser_","mcp_")) for n in actual))
                    finally:
                        reset_hermes_home_override(token)
if __name__=="__main__":unittest.main()
