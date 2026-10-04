"""Actual plugin discovery + profile resolver, isolated homes A -> B -> A."""
import importlib.util
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
                for profile in ("default","maintenance"):
                    home=Path(tmp)/profile;home.mkdir()
                    plugins=["komatso-public-research"]
                    selected=["web","skills_readonly","komatso_public_browser","no_mcp"]
                    if profile=="maintenance":
                        plugins.append("komatso-maintenance-manual")
                        selected+=["delegation","komatso_maintenance"]
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
                    (home/"config.yaml").write_text(json.dumps(config),encoding="utf-8")
                    homes[profile]=(home,config)
                for profile,platform in [("default","bale"),("maintenance","bale"),("default","telegram"),("default","bale")]:
                    home,config=homes[profile]
                    token=set_hermes_home_override(home)
                    try:
                        with patch("hermes_constants.get_process_hermes_home",return_value=RUNTIME):
                            discover_plugins()
                            for name,schema in [("web_search",web_tools.WEB_SEARCH_SCHEMA),("web_extract",web_tools.WEB_EXTRACT_SCHEMA)]:
                                registry.register(name=name,toolset="web",schema=schema,handler=lambda args,**kw:"{}",check_fn=lambda:True)
                            enabled=_get_platform_tools(config,platform)
                            definitions=get_tool_definitions(enabled,quiet_mode=True,skip_tool_search_assembly=True)
                            actual={d["function"]["name"] for d in definitions}
                        expected=research|({"delegate_task","maintenance_manual_evidence","maintenance_partbook_lookup"} if profile=="maintenance" else set())
                        self.assertEqual(actual,expected,(profile,platform,actual))
                        self.assertFalse(actual & forbidden)
                        self.assertFalse(any(n.startswith(("browser_","mcp_")) for n in actual))
                    finally:
                        reset_hermes_home_override(token)
if __name__=="__main__":unittest.main()
