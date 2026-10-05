from pathlib import Path
import asyncio,os,sys,unittest
from types import ModuleType,SimpleNamespace
from unittest.mock import AsyncMock,patch
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes');sys.path.insert(0,str(HOME/'hermes-agent'))

class ProfileReconcileSafety(unittest.IsolatedAsyncioTestCase):
    def runner(self):
        from tools.admin.core_fixture import patched_core_source
        module=ModuleType('gateway.admin1_reconcile_fixture');module.__file__=str(HOME/'hermes-agent/gateway/run_profile_reconcile.py');sys.modules[module.__name__]=module
        exec(compile(patched_core_source('gateway/run_profile_reconcile.py'),module.__file__,'exec'),module.__dict__)
        runner=module.GatewayProfileReconcileMixin()
        runner._running=True;runner._primary_profile_name='default';runner.config=SimpleNamespace()
        runner._multiplex_on=lambda:True;runner._served_profile_homes={'default':ROOT/'runtime/fixture-default'}
        runner._served_profile_signatures={};runner._live_resource_claims=lambda active:{}
        runner._start_one_profile_adapters=AsyncMock(return_value=0);runner._after_profiles_added=AsyncMock()
        runner._unserve_profile=AsyncMock()
        runner._record_served_profiles=lambda active,homes:runner._note_served_profiles(homes)
        return runner
    async def test_status_self_rpc_is_off_loop_and_hot_add_completes(self):
        runner=self.runner();loop=asyncio.get_running_loop();observed=[]
        async def self_rpc():observed.append('event-loop-served-rpc');return None
        def probe(home):return asyncio.run_coroutine_threadsafe(self_rpc(),loop).result(timeout=2)
        homes=[('default',ROOT/'runtime/fixture-default'),('admin',ROOT/'runtime/fixture-admin')]
        with patch('gateway.run._multiplex_profile_homes',return_value=homes),patch('gateway.status.live_gateway_pid_for_home',side_effect=probe):
            result=await runner.reconcile_served_profiles()
        self.assertEqual(result['added'],['admin']);self.assertEqual(observed,['event-loop-served-rpc'])
    async def test_foreign_standalone_gateway_still_blocks_hot_adoption(self):
        runner=self.runner();homes=[('default',ROOT/'runtime/fixture-default'),('admin',ROOT/'runtime/fixture-admin')]
        with patch('gateway.run._multiplex_profile_homes',return_value=homes),patch('gateway.status.live_gateway_pid_for_home',return_value=os.getpid()+1):
            result=await runner.reconcile_served_profiles()
        self.assertEqual(result['added'],[]);runner._start_one_profile_adapters.assert_not_called()
    async def test_current_host_pid_is_not_mistaken_for_a_foreign_gateway(self):
        runner=self.runner();homes=[('default',ROOT/'runtime/fixture-default'),('admin',ROOT/'runtime/fixture-admin')]
        with patch('gateway.run._multiplex_profile_homes',return_value=homes),patch('gateway.status.live_gateway_pid_for_home',return_value=os.getpid()):
            result=await runner.reconcile_served_profiles()
        self.assertEqual(result['added'],['admin'])

if __name__=='__main__':unittest.main()
