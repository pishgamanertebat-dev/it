"""Shared tool boundary, real curated evidence and child isolation regressions."""
from pathlib import Path
import importlib.util,json,os,sys,tempfile,unittest
from unittest.mock import patch
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')
sys.path.insert(0,str(ROOT))
from integrations.hermes.technical_docs_boundary import technical_docs_enabled,runtime_home

def load():
    source=ROOT/'integrations/hermes/plugins/komatso-technical-docs/__init__.py'
    spec=importlib.util.spec_from_file_location('technical_fixture',source)
    module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module);return module

class SharedTechnicalTests(unittest.TestCase):
    def test_only_explicit_surface_enables_curated_docs(self):
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
            home=Path(folder)
            self.assertFalse(technical_docs_enabled(home))
            for cfg,allowed in [({},False),({'plugins':{'enabled':['komatso-technical-docs']}},False),({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'bale':['komatso_technical_docs']}},True),({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'bale':['komatso_technical_docs']},'agent':{'disabled_toolsets':['komatso_technical_docs']}},False),({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'cli':['komatso_technical_docs']}},False)]:
                (home/'config.yaml').write_text(json.dumps(cfg),encoding='utf-8')
                self.assertEqual(technical_docs_enabled(home),allowed)
    def test_aliases_come_from_curated_models_and_fail_closed_on_collisions(self):
        module=load();aliases=module.model_aliases()
        self.assertEqual(aliases['465'],'HD465-7R')
        self.assertNotIn('785',aliases)
        self.assertNotIn('800',aliases)
        for model in aliases.values():self.assertIn(model,module.part_model_names())
    def test_default_and_named_worker_find_same_interpreter(self):
        self.assertEqual(runtime_home(HOME),HOME)
        for profile in ('maintenance','admin'):self.assertEqual(runtime_home(HOME/'profiles'/profile),HOME)
    def test_profile_instances_do_not_share_manual_request_ownership(self):
        first,second=load(),load();key='a'*24;first._requests[key]='same-session'
        with self.assertRaises(ValueError):second.finish({'request_id':key,'render_pages':[1]},'same-session')
        self.assertIsNot(first._retrieves,second._retrieves)
        self.assertIsNot(first._pending,second._pending)
    def test_shared_preparation_contract_never_requests_host_tools(self):
        body=(ROOT/'integrations/hermes/plugins/komatso-technical-docs/MANUAL_WORKER.md').read_text(encoding='utf-8')
        self.assertIn('ONLY tools inherited from Parent',body)
        self.assertNotIn('.exe',body);self.assertNotIn('Run this ONE terminal command',body)
    def test_real_partbook_evidence_identical_for_three_surfaces(self):
        module=load();results=[]
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as folder:
            for profile in ('default','maintenance','admin'):
                home=Path(folder)/profile;home.mkdir()
                (home/'config.yaml').write_text(json.dumps({'plugins':{'enabled':['komatso-technical-docs']},'platform_toolsets':{'bale':['komatso_technical_docs']}}),encoding='utf-8')
                import hermes_constants
                with patch.object(hermes_constants,'get_hermes_home',return_value=home):
                    answer=json.loads(module.part_lookup({'model':'HD785-7','question':'شماره فنی قطعه 6218-11-5830 برای HD785-7 را بررسی کن','part_number':'6218-11-5830'},session_id='shared-test-'+profile))
                self.assertTrue(answer.get('candidates'),answer)
                results.append(answer['candidates'])
        self.assertEqual(results[0],results[1]);self.assertEqual(results[1],results[2])
    def test_domain_child_ceiling_uses_existing_native_implementation(self):
        from types import SimpleNamespace
        from tools.registry import registry
        from tools.delegate_tool_toolsets import _resolve_child_toolsets
        from toolsets import resolve_toolset
        for name in ('shared_fixture_manual','shared_fixture_part'):
            registry.register(name=name,toolset='shared_fixture',schema={'name':name,'parameters':{}},handler=lambda a,**kw:'{}')
        parent=SimpleNamespace(platform='bale',enabled_toolsets=['shared_fixture','delegation','no_mcp'],disabled_toolsets=[],valid_tool_names={'shared_fixture_manual','shared_fixture_part','delegate_task'})
        enabled,_=_resolve_child_toolsets(parent,['shared_fixture','terminal','file','code_execution','delegation'],'orchestrator')
        actual={name for group in enabled for name in resolve_toolset(group)}
        self.assertTrue({'shared_fixture_manual','shared_fixture_part'}<=actual)
        self.assertTrue(actual<=parent.valid_tool_names)
        self.assertNotIn('delegate_task',actual)
if __name__=='__main__':unittest.main()
