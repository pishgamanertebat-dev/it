"""Exercise prepared generic Gateway boundaries against real isolated profile scopes."""
from pathlib import Path
import ast,importlib.util,json,os,sys,tempfile,unittest
from unittest.mock import patch
from types import SimpleNamespace
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes')
sys.path.insert(0,str(HOME/'hermes-agent'))

class GatewayBoundaries(unittest.TestCase):
    def test_exclusive_media_roots_admin_other_profile_admin(self):
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override
        from tools.admin.core_fixture import patched_core_source
        from types import ModuleType
        module=ModuleType('gateway.platforms.admin1_media_fixture');module.__package__='gateway.platforms';module.__file__=str(HOME/'hermes-agent/gateway/platforms/base.py');sys.modules[module.__name__]=module
        exec(compile(patched_core_source('gateway/platforms/base.py'),'<reviewed-native-media>','exec'),module.__dict__)
        with tempfile.TemporaryDirectory(dir=ROOT/'runtime') as tmp:
            root=Path(tmp);a=root/'admin';b=root/'maintenance';a.mkdir();b.mkdir()
            cache=a/'document_cache/komatso';cache.mkdir(parents=True)
            own=cache/'business.png';own.write_bytes(b'fixture')
            other=b/'document_cache';other.mkdir();otherfile=other/'other.png';otherfile.write_bytes(b'fixture')
            outside=root/'host-secret.txt';outside.write_text('fixture')
            (a/'config.yaml').write_text(json.dumps({'gateway':{'strict':True,'trust_recent_files':False,'media_delivery_exclusive_roots':True,'media_delivery_allow_dirs':[str(cache)]}}))
            (b/'config.yaml').write_text(json.dumps({'gateway':{'strict':False}}))
            for home in [a,b,a]:
                token=set_hermes_home_override(home)
                try:
                    if home==a:
                        self.assertEqual(module.validate_media_delivery_path(str(own)),str(own.resolve()))
                        self.assertIsNone(module.validate_media_delivery_path(str(outside)))
                        self.assertIsNone(module.validate_media_delivery_path(str(otherfile)))
                        filtered=module.BasePlatformAdapter.filter_media_delivery_paths([(str(own),False),(str(outside),False)])
                        self.assertEqual(filtered,[(str(own.resolve()),False)])
                    else:
                        self.assertEqual(module.validate_media_delivery_path(str(outside)),str(outside.resolve()))
                finally:reset_hermes_home_override(token)
    def test_domain_handler_uses_trusted_context_denies_spoof_and_shell(self):
        from gateway.session_context import set_session_vars,clear_session_vars
        path=ROOT/'integrations/hermes/plugins/komatso-function-domain/__init__.py'
        spec=importlib.util.spec_from_file_location('admin1_domain_fixture',path)
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        tokens=set_session_vars(platform='bale',chat_type='dm',user_id='101',chat_id='101')
        try:
            with patch.object(module,'AuthorizationStore') as store,patch.object(module.subprocess,'run') as process:
                store.return_value.has_capability.return_value=False
                self.assertEqual(module.business_context({'profile_name':'admin'}),'')
                self.assertFalse(json.loads(module.execute('list',{},user_id='999'))['ok']);process.assert_not_called()
                store.return_value.has_capability.return_value=True
                self.assertIn('Part Book',module.business_context({'profile_name':'maintenance'}))
                self.assertIn('function_*',module.business_context({'profile_name':'maintenance'}))
                self.assertFalse(json.loads(module.execute('read',{'path':'x','user_id':'999'}))['ok']);process.assert_not_called()
                process.return_value=SimpleNamespace(returncode=0,stdout='{"ok":true,"result":{"entries":[]}}')
                self.assertTrue(json.loads(module.execute('list',{},user_id='999'))['ok'])
                call=process.call_args
                self.assertNotIn('shell',call.kwargs)
                self.assertIn('integrations.hermes.function_domain.worker',call.args[0])
                request=json.loads(call.kwargs['input']);self.assertEqual(request['identity']['user_id'],'101')
                self.assertFalse(any('TOKEN' in k or 'API_KEY' in k for k in call.kwargs['env']))
        finally:clear_session_vars(tokens)

if __name__=='__main__':unittest.main()
