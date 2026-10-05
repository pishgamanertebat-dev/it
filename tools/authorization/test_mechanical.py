"""Hermetic mechanical acceptance: synthetic identities/workbooks, no production sends."""
from contextlib import closing
from datetime import datetime, date, timedelta
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, AsyncMock, patch
import asyncio, importlib.util, io, json, os, sqlite3, sys, unittest
from zoneinfo import ZoneInfo
import openpyxl, yaml
from tools.authorization.test_authorization import Fixture
from tools.authorization import *
from tools.scheduler.tasks import mechanical_overflow, mechanical_driver_daily, driver_daily
from tools.scheduler.runner import tick, load_config
from integrations.hermes.function_domain.scoped import ScopedReader, ScopeDenied
from integrations.hermes.function_domain.worker import run
from integrations.hermes.function_domain.driver_report import build_driver_pdf, SOURCE_NAME
from integrations.hermes import role_routing

STAFF=FILE_RESOURCES[MAINTENANCE_RECORDS_READ]
MANAGER_CAPS={MAINTENANCE_RECORDS_READ,DRIVER_REPORT_READ,OVERFLOW_READ,MECH_OVERFLOW_RECEIVE,MECH_DRIVER_RECEIVE}
ROOT=Path(__file__).resolve().parents[2]
HOME=Path('C:/Users/win-10/AppData/Local/hermes')

def module(name,path):
    spec=importlib.util.spec_from_file_location(name,path)
    mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

class MechanicalFixture(Fixture):
    def setUp(self):
        super().setUp()
        self.sql("INSERT INTO channel_users VALUES ('bale','654806764','654806764','approved')")
        self.sql("INSERT INTO channel_users VALUES ('bale','1732374823','1732374823','approved')")
        self.auth.migrate_admin(self.directory/'backups')
        self.auth.migrate_mechanical(self.directory/'backups',assignments=[
            ('654806764',MECHANICAL_STAFF),('654806764',MECHANICAL_MANAGER),
            ('1732374823',MECHANICAL_STAFF),('1732374823',MECHANICAL_MANAGER_DEPUTY),
            ('101',MECHANICAL_STAFF)])
        self.data=self.directory/'Function';self.data.mkdir()
        self.reader=ScopedReader(self.data);self.cache=self.directory/'cache'
        for name in [STAFF,SOURCE_NAME,'other.xlsx','سرریز روزانه.xlsx']:
            wb=openpyxl.Workbook();ws=wb.active;ws.title='history'
            ws.append(['گزارش روزانه رانندگان','1405/07/10'])
            ws.append(['ردیف','نوع دستگاه','کد جدید','شرح معایب مکانیکی','شرح معایب آهنگری'])
            ws.append([1,'دامپتراک','HD714','MECH_ONLY','METAL_ONLY'])
            old=wb.create_sheet('old');old.append(['1404/01/01','OLD'])
            wb.save(self.data/name);wb.close()
    def request(self,op,args,user='101',**who):
        identity={'platform':'bale','chat_type':'dm','user_id':user,'chat_id':user,**who}
        return run({'operation':op,'args':args,'identity':identity},store=self.auth,reader=self.reader,cache=self.cache)
    def managers(self,cap=MECH_OVERFLOW_RECEIVE):
        return self.auth.resolve_active_recipients(cap).recipients

class MechanicalAuthorization(MechanicalFixture,unittest.TestCase):
    def test_both_real_assignment_fixtures_and_base_staff_contract(self):
        for user,extra in [('654806764',MECHANICAL_MANAGER),('1732374823',MECHANICAL_MANAGER_DEPUTY)]:
            self.assertEqual(set(self.auth.roles(user)),{MECHANICAL_STAFF,extra})
            self.assertEqual(self.auth.resolve_profile(user,user),'maintenance')
            self.assertTrue(all(self.auth.has_capability(user,c) for c in MANAGER_CAPS))
            self.assertFalse(self.auth.has_capability(user,FUNCTION_READ))
            self.assertTrue(self.auth.can_read_overflow(user,user))
            self.assertEqual(set(self.auth.function_scope(user).files),{STAFF,SOURCE_NAME})
        self.assertEqual(self.auth.roles('101'),(MECHANICAL_STAFF,))
        self.assertEqual(self.auth.resolve_profile('101','101'),'maintenance')
        self.assertEqual(self.auth.function_scope('101').files,(STAFF,))
        for cap in MANAGER_CAPS-{MAINTENANCE_RECORDS_READ}|{FUNCTION_READ}:
            self.assertFalse(self.auth.has_capability('101',cap))
        self.assertEqual(self.sql("SELECT role,profile FROM auth_role_profiles ORDER BY role"),
                         [(MECHANICAL_STAFF,'maintenance'),(OFFICE_SUPERVISOR,'admin')])
    def test_idempotency_backup_registration_and_existing_roles_unchanged(self):
        users=self.sql('SELECT * FROM channel_users');roles=self.sql('SELECT * FROM auth_user_roles')
        backup=self.auth.migrate_mechanical(self.directory/'backups')
        self.assertEqual(users,self.sql('SELECT * FROM channel_users'));self.assertEqual(roles,self.sql('SELECT * FROM auth_user_roles'))
        self.assertEqual(self.sql('SELECT version FROM auth_migrations'),[(1,),(2,)])
        with closing(sqlite3.connect(backup)) as c:self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(set(self.auth.capabilities_for_role(MECHANICAL_STAFF)),{MAINTENANCE_RECORDS_READ})
    def test_atomic_migration_and_assignments_rollback(self):
        self.sql("DELETE FROM auth_user_roles WHERE role LIKE 'mechanical_%'")
        self.sql("DELETE FROM auth_role_profiles WHERE role='mechanical_staff'")
        self.sql("DELETE FROM auth_role_capabilities WHERE role LIKE 'mechanical_%'")
        self.sql("DELETE FROM auth_roles WHERE role LIKE 'mechanical_%'")
        self.sql("DELETE FROM auth_extensions")
        with self.assertRaises(ValueError):
            self.auth.migrate_mechanical(self.directory/'backups',assignments=[('101',MECHANICAL_STAFF),('303',MECHANICAL_MANAGER)])
        self.assertEqual(self.sql("SELECT role FROM auth_roles WHERE role LIKE 'mechanical_%'"),[])
        self.auth.migrate_mechanical(self.directory/'backups')
        self.auth.assign_role('101',MECHANICAL_STAFF,actor='fixture')
        self.assertEqual(self.auth.resolve_profile('101','101'),'maintenance')
    def test_conflicting_seeds_and_manager_profile_fail_transaction(self):
        for sql in ["INSERT INTO auth_role_capabilities VALUES ('mechanical_staff','function.read_all')",
                    "INSERT INTO auth_role_profiles VALUES ('mechanical_manager','admin',100,1)",
                    "UPDATE auth_capabilities SET resource='E:\\Function' WHERE capability='maintenance.records.read'"]:
            with self.subTest(sql=sql):
                backup=self.auth.backup(self.directory/'backups');self.sql(sql)
                with self.assertRaises(sqlite3.DatabaseError):self.auth.migrate_mechanical(self.directory/'backups')
                with closing(sqlite3.connect(backup)) as src,closing(sqlite3.connect(self.path)) as dest:src.backup(dest)
    def test_resource_db_cannot_widen_paths_and_private_identity_fail_closed(self):
        self.sql("UPDATE auth_capabilities SET resource='E:\\Function' WHERE capability=?",(MAINTENANCE_RECORDS_READ,))
        self.assertFalse(self.auth.function_scope('101'))
        self.sql("UPDATE channel_users SET chat_id='999' WHERE user_id='654806764'")
        self.assertFalse(self.auth.function_scope('654806764'))
        self.assertNotIn('654806764',self.managers())
        self.assertFalse(AuthorizationStore(self.directory/'missing').function_scope('101'))
    def test_multi_zero_one_many_dedup_inactive_unapproved_invalid_and_generic(self):
        self.assertEqual(set(self.managers()),{'654806764','1732374823'})
        self.auth.assign_role('654806764',MECHANICAL_MANAGER_DEPUTY,actor='fixture')
        self.assertEqual(len(self.managers()),2)
        self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='1732374823'")
        self.assertEqual(self.managers(),('654806764',))
        self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='654806764'")
        self.assertEqual(self.auth.resolve_active_recipients(MECH_OVERFLOW_RECEIVE).status,'no_eligible_recipient')
        self.sql("UPDATE auth_user_roles SET active=0 WHERE role LIKE 'mechanical_%'")
        self.assertEqual(self.auth.resolve_active_recipients(MECH_OVERFLOW_RECEIVE).status,'no_active_holder')
        self.assertEqual(self.auth.resolve_active_recipients('').status,'invalid_capability')
        self.auth.assign_role('202',BUSINESS_ADMIN,actor='fixture')
        self.assertEqual(self.auth.resolve_active_recipients(FUNCTION_READ).recipients,('202',))
    def test_legacy_supervisor_invariant_and_business_admin_remain_independent(self):
        self.assign('202');self.auth.assign_role('202',BUSINESS_ADMIN,actor='fixture')
        self.assertEqual(self.auth.resolve_daily_recipient().recipient,'202')
        self.assertEqual(self.auth.resolve_profile('202','202'),'admin')
        self.assertTrue(self.auth.function_scope('202').all)
        self.assertNotIn('202',self.managers())
        self.sql("INSERT INTO auth_user_roles VALUES ('bale','101','office_supervisor',1,'t','t','t')")
        self.assertEqual(self.auth.resolve_daily_recipient().status,'ambiguous_holders')
    def test_profile_and_actual_maintenance_registration_preserve_surfaces(self):
        cfg=yaml.safe_load((HOME/'profiles/maintenance/config.yaml').read_text(encoding='utf-8'))
        base=cfg['platform_toolsets']['bale']
        with patch.object(role_routing,'AuthorizationStore',return_value=self.auth):
            for user in ['101','654806764','1732374823']:
                tools=role_routing.resolve_toolsets(platform='bale',chat_type='dm',chat_id=user,user_id=user,base_toolsets=base)
                self.assertEqual(set(tools),set(base)|{'komatso_function'})
                self.assertTrue({'web','skills_readonly','delegation','komatso_public_browser','komatso_technical_docs','no_mcp'}<=set(tools))
            denied=role_routing.resolve_toolsets(platform='bale',chat_type='group',chat_id='101',user_id='101',base_toolsets=base)
            self.assertNotIn('komatso_function',denied)
        plugin=module('mechanical_maintenance_fixture',ROOT/'integrations/hermes/plugins/komatso-maintenance-manual/__init__.py')
        context=Mock();plugin.register(context)
        names={c.kwargs['name'] for c in context.register_tool.call_args_list}
        self.assertTrue({'maintenance_manual_evidence','maintenance_partbook_lookup'}<=names)

class MechanicalScope(MechanicalFixture,unittest.TestCase):
    def test_whole_workbook_read_history_pages_and_attachment(self):
        for user,name in [('101',STAFF),('654806764',STAFF),('1732374823',SOURCE_NAME)]:
            self.assertEqual(self.request('read',{'path':name},user)['result']['sheets'],['history','old'])
            current=self.request('read',{'path':name,'sheet':'history','offset':2,'limit':1},user)['result']
            self.assertIn('MECH_ONLY',str(current['rows']))
            self.assertIn('OLD',str(self.request('read',{'path':name,'sheet':'old'},user)))
            attached=Path(self.request('attach',{'path':name},user)['result']['attachment'])
            self.assertEqual(attached.read_bytes(),(self.data/name).read_bytes())
    def test_all_operations_are_exact_scope_no_list_search_or_error_leaks(self):
        for user,expected in [('101',{STAFF}),('654806764',{STAFF,SOURCE_NAME})]:
            for recursive in [False,True]:
                result=self.request('list',{'recursive':recursive},user)['result']
                self.assertEqual({e['path'] for e in result['entries']},expected)
                self.assertEqual(result['total'],len(expected));self.assertEqual(result['skipped_unreadable_or_links'],[])
            self.assertEqual(self.request('search',{'query':'other'},user)['result']['total'],0)
            for name in set([STAFF,SOURCE_NAME,'other.xlsx','سرریز روزانه.xlsx'])-expected:
                for op in ['read','metadata','attach']:
                    with self.subTest(user=user,op=op,name=name),self.assertRaises(ScopeDenied):
                        self.request(op,{'path':name},user)
            with self.assertRaises(ScopeDenied):self.request('metadata',{'path':''},user)
    def test_driver_capability_alone_sees_only_driver_and_never_maintenance(self):
        self.sql("INSERT INTO auth_roles VALUES ('driver_fixture','fixture')")
        self.sql("INSERT INTO auth_role_capabilities VALUES ('driver_fixture',?)",(DRIVER_REPORT_READ,))
        self.auth.assign_role('202','driver_fixture',actor='fixture')
        self.assertEqual(self.auth.function_scope('202').files,(SOURCE_NAME,))
        self.assertEqual(self.request('list',{},'202')['result']['total'],1)
        for op in ['read','metadata','attach']:
            with self.assertRaises(ScopeDenied):self.request(op,{'path':STAFF},'202')
    def test_path_escapes_and_aliases_denied_for_both_exact_capabilities(self):
        bad=['../'+STAFF,'E:\\Function\\'+STAFF,'C:/Windows/x','/x','\\\\server\\share\\x',
             'file:///E:/Function/'+STAFF,STAFF+':stream','NUL','COM1.xlsx','%2e%2e/x',
             'x/../'+STAFF,'./'+STAFF,STAFF+'.','\\\\?\\E:\\Function\\'+STAFF]
        for user in ['101','654806764']:
            for name in bad:
                for op in ['read','metadata','attach']:
                    with self.subTest(user=user,name=name,op=op),self.assertRaises((ScopeDenied,ValueError)):
                        self.request(op,{'path':name},user)
    @unittest.skipUnless(os.name=='nt','Windows junction containment')
    def test_root_junction_and_reparse_resource_denied_without_scope_leak(self):
        import _winapi
        outside=self.directory/'outside';outside.mkdir()
        junction=self.directory/'root-link';_winapi.CreateJunction(str(outside),str(junction))
        with self.assertRaises(ScopeDenied):
            ScopedReader(junction,allowed_files=[STAFF]).snapshot(STAFF)
        (self.data/STAFF).unlink();_winapi.CreateJunction(str(outside),str(self.data/STAFF))
        self.assertEqual(self.request('list',{})['result']['entries'],[])
        with self.assertRaises(PermissionError):self.request('read',{'path':STAFF})
    def test_staff_report_denied_and_nonprivate_spoof_denied(self):
        for kind in ['driver','overflow']:
            with self.assertRaises(PermissionError):self.request('report',{'kind':kind,'date':'1405/07/10'})
        for who in [{'platform':'telegram'},{'chat_type':'group'},{'chat_id':'999'}]:
            with self.assertRaises(PermissionError):self.request('list',{},'654806764',**who)
    def test_revoke_one_resource_during_read_or_attach_blocks_even_with_other_grants(self):
        original=ScopedReader.snapshot
        def revoke(reader,*args,**kw):
            data=original(reader,*args,**kw)
            self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='654806764'")
            self.auth.assign_role('654806764',MECHANICAL_STAFF,actor='fixture')
            return data
        for op in ['read','attach']:
            self.auth.assign_role('654806764',MECHANICAL_MANAGER,actor='fixture')
            with patch.object(ScopedReader,'snapshot',revoke),self.assertRaises(PermissionError):
                self.request(op,{'path':SOURCE_NAME},'654806764')
        self.assertEqual(list(self.cache.rglob('*.xlsx')),[])

class MechanicalPush(MechanicalFixture,unittest.TestCase):
    def overflow_fixture(self,day,out):
        image=Path(out)/'report.png';image.write_bytes(b'fixture')
        return {'ok':True,'report':{'date':day,'rows':[]},'images':[str(image)]}
    def pdf_fixture(self,day,out,**kw):
        p=Path(out)/'mechanical.pdf';p.write_bytes(b'fixture')
        return {'ok':True,'report':{'date':day},'documents':[str(p)]}
    def test_overflow_fanout_same_build_all_managers_no_staff_cleanup(self):
        with patch('tools.scheduler.tasks.build_report',AsyncMock(side_effect=self.overflow_fixture)) as builder,patch('tools.scheduler.tasks.BaleSender') as sender:
            result=mechanical_overflow(None,{'date':'1405/07/10'},authorization=self.auth)
            self.assertEqual(result['sent_count'],2);builder.assert_awaited_once()
            self.assertEqual({c.args[0] for c in sender.return_value.photo.call_args_list},set(self.managers()))
            self.assertFalse(sender.return_value.document.called)
            self.assertFalse(Path(builder.call_args.args[1]).exists())
    def test_zero_one_and_partial_recipient_failure_continue_without_retry(self):
        self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='1732374823'")
        with patch('tools.scheduler.tasks.build_report',AsyncMock(side_effect=self.overflow_fixture)),patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertEqual(mechanical_overflow(None,{'date':'1405/07/10'},authorization=self.auth)['sent_count'],1)
        self.sql("UPDATE auth_user_roles SET active=0 WHERE role LIKE 'mechanical_%'")
        with patch('tools.scheduler.tasks.build_report') as build,patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertEqual(mechanical_overflow(None,{},authorization=self.auth)['status'],'skipped')
            build.assert_not_called();sender.assert_not_called()
    def test_revoked_after_generation_and_between_uploads_never_sent(self):
        async def revoke(day,out):
            result=self.overflow_fixture(day,out);self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='654806764'");return result
        with patch('tools.scheduler.tasks.build_report',AsyncMock(side_effect=revoke)),patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertEqual(mechanical_overflow(None,{'date':'1405/07/10'},authorization=self.auth)['sent_count'],1)
            self.assertEqual(sender.return_value.photo.call_args.args[0],'1732374823')
        self.auth.assign_role('654806764',MECHANICAL_MANAGER,actor='fixture')
        def during(*args):
            self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='654806764'")
        with patch('tools.scheduler.tasks.build_report',AsyncMock(side_effect=self.overflow_fixture)),patch('tools.scheduler.tasks.BaleSender') as sender:
            sender.return_value.photo.side_effect=during
            mechanical_overflow(None,{'date':'1405/07/10'},authorization=self.auth)
            self.assertEqual(sender.return_value.photo.call_count,1)
    def test_only_mechanical_pdf_export_and_upload_office_still_both(self):
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=self.pdf_fixture) as build,patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertEqual(mechanical_driver_daily(None,{'date':'1405/07/10'},authorization=self.auth)['sent_count'],2)
            self.assertEqual(build.call_args.kwargs,{'sections':('mechanical',)})
            self.assertEqual(sender.return_value.document.call_count,2);self.assertFalse(sender.return_value.photo.called)
            self.assertFalse(Path(build.call_args.args[1]).exists())
        self.assign('202')
        report={'date':'1405/07/10','sections':{s:{'title':s,'status':'ready'} for s in ['mechanical','metalwork']}}
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',return_value={'report':report,'documents':['mechanical.pdf','metalwork.pdf']}) as build,patch('tools.scheduler.tasks.BaleSender') as sender:
            driver_daily('202',{'date':'1405/07/10'},authorization=self.auth)
            self.assertEqual(sender.return_value.document.call_count,2)
            self.assertEqual(build.call_args.kwargs,{})
    def test_pdf_revocation_wrong_date_or_count_no_upload(self):
        def revoke(day,out,**kw):
            result=self.pdf_fixture(day,out);self.sql("UPDATE auth_user_roles SET active=0 WHERE role LIKE 'mechanical_%'");return result
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=revoke),patch('tools.scheduler.tasks.BaleSender') as sender:
            self.assertEqual(mechanical_driver_daily(None,{'date':'1405/07/10'},authorization=self.auth)['status'],'skipped');sender.assert_not_called()
        self.auth.assign_role('654806764',MECHANICAL_MANAGER,actor='fixture')
        for data in [{'ok':True,'report':{'date':'1405/07/09'},'documents':['a']},
                     {'ok':True,'report':{'date':'1405/07/10'},'documents':['a','metal']}]:
            with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',return_value=data),patch('tools.scheduler.tasks.BaleSender') as sender:
                with self.assertRaises(ValueError):mechanical_driver_daily(None,{'date':'1405/07/10'},authorization=self.auth)
                sender.assert_not_called()
    def test_actual_exporter_mechanical_only_print_columns_searchable_exact_and_source_unchanged(self):
        import fitz
        original=(self.data/SOURCE_NAME).read_bytes()
        def export(source,sheet,output,work,columns):
            self.assertEqual(sheet,'history');self.assertEqual(columns,[1,2,3,4]);self.assertNotEqual(Path(source),self.data/SOURCE_NAME)
            doc=fitz.open();page=doc.new_page();page.insert_text((30,30),'MECH_ONLY 1405/07/10');doc.save(output);doc.close()
        with patch('tools.fleet.repairs.report.export_pdf',side_effect=export) as exporter:
            result=build_driver_pdf('1405/07/10',self.directory/'pdf',self.reader,sections=('mechanical',))
        exporter.assert_called_once();self.assertEqual(len(result['documents']),1)
        with fitz.open(result['documents'][0]) as doc:
            self.assertIn('MECH_ONLY',doc[0].get_text());self.assertNotIn('METAL_ONLY',doc[0].get_text());self.assertEqual(doc[0].get_images(),[])
        self.assertEqual((self.data/SOURCE_NAME).read_bytes(),original)
        self.assertFalse(list((self.directory/'pdf').glob('driver-excel-*')))
        with patch('tools.fleet.repairs.report.export_pdf') as exporter:
            missing=build_driver_pdf('1405/07/11',self.directory/'missing',self.reader,sections=('mechanical',))
        exporter.assert_not_called();self.assertEqual(len(missing['documents']),1)
        with fitz.open(missing['documents'][0]) as doc:self.assertIn('1405/07/11',doc[0].get_text())

class MechanicalDatesAndSchedules(MechanicalFixture,unittest.TestCase):
    def test_tehran_relative_commands_exact_and_default_mechanical_previous_day(self):
        from tools.fleet.overflow.report import parse_command
        from tools.fleet.overflow.bale import OverflowHandler
        from tools.scheduler.tasks import overflow_report_date
        today=date(2026,10,5)
        for text,expected in [('سرریز رو بده',None),('سرریز دیروز','1405/07/12'),('سرریز امروز','1405/07/13'),('سرریز 1405/07/10','1405/07/10')]:
            self.assertEqual(parse_command(text,today=today),(True,expected))
        async def check():
            handler=OverflowHandler(AsyncMock(return_value={'ok':False,'message':'fixture'}),self.auth)
            event=self.event('654806764','سرریز رو بده')
            with patch('tools.scheduler.tasks.overflow_report_date',return_value='1405/07/12'):
                gateway=SimpleNamespace(adapters={'bale':SimpleNamespace(send=AsyncMock())})
                self.assertEqual(handler.handle(event,gateway,send=Mock())['reason'],'overflow-report')
                await asyncio.gather(*handler.tasks)
            self.assertEqual(handler.worker.call_args.args[0],'1405/07/12')
        asyncio.run(check())
        self.assertEqual(overflow_report_date(datetime(2026,10,4,22,0,tzinfo=ZoneInfo('UTC'))),'1405/07/12')
    def test_schedules_fixed_capability_previous_occurrence_dedup_empty_params_and_no_ids(self):
        jobs=[]
        for name,hour,cap in [('mechanical_overflow',9,MECH_OVERFLOW_RECEIVE),('mechanical_driver_daily',10,MECH_DRIVER_RECEIVE)]:
            jobs.append({'id':name,'enabled':True,'task':name,'recipient_capability':cap,'timezone':'Asia/Tehran','trigger':{'type':'cron','hour':hour,'minute':0},'params':{}})
        cfg=self.directory/'jobs.yaml';state=self.directory/'runs.sqlite';calls=[]
        registry={n:(lambda p:None,lambda r,p,**kw:calls.append((r,p))) for n in ['mechanical_overflow','mechanical_driver_daily']}
        def write():cfg.write_text(yaml.safe_dump({'version':1,'timezone':'UTC','schedules':jobs}),encoding='utf-8')
        write()
        for hour in [9,10]:
            now=datetime(2026,10,5,hour,0,tzinfo=ZoneInfo('Asia/Tehran'))
            self.assertEqual(tick(cfg,state,now,registry,self.auth),0);tick(cfg,state,now+timedelta(minutes=1),registry,self.auth)
        self.assertEqual(len(calls),2);self.assertTrue(all(p=={'date':'1405/07/12'} for _,p in calls))
        self.assertTrue(all(r.startswith('capability:') for r,_ in calls))
        for extra in [{'recipient':'101'},{'recipient_role':'office_supervisor'},{'recipient_capability':FUNCTION_READ},{'timezone':'UTC'},{'params':{'date':'1405/07/10'}}]:
            original=dict(jobs[0]);jobs[0].update(extra);write()
            with self.assertRaises(ValueError):load_config(cfg,registry)
            jobs[0]=original
    def test_legacy_write_json_remains_saeed_only_no_deputy_or_generic_staff(self):
        cfg=json.loads((ROOT/'settings/maintenance_entry.json').read_text(encoding='utf-8'))
        self.assertEqual(set(cfg['allowed_users']),{'654806764','455740857'})
        self.assertEqual(cfg['source'],'E:\\Function\\'+STAFF)
        self.assertNotIn('1732374823',cfg['allowed_users']);self.assertNotIn('101',cfg['allowed_users'])

class MechanicalNativeRuntime(MechanicalFixture,unittest.TestCase):
    def test_real_gateway_routes_two_managers_generic_staff_and_legacy_users(self):
        from gateway.profile_routing import parse_profile_routes,match_profile_route
        cfg=yaml.safe_load((HOME/'config.yaml').read_text(encoding='utf-8'))
        routes=parse_profile_routes(cfg['gateway']['profile_routes'])
        # Baseline identities/roles are copied into a disposable synthetic DB, never assigned in production.
        for user in ['1636934401','397185913']:
            self.sql("INSERT INTO channel_users VALUES ('bale',?,?, 'approved')",(user,user))
            self.auth.assign_role(user,BUSINESS_ADMIN,actor='fixture')
        self.auth.assign_role('397185913',OFFICE_SUPERVISOR,actor='fixture')
        with patch.object(role_routing,'AuthorizationStore',return_value=self.auth):
            for user,profile in [('654806764','maintenance'),('1732374823','maintenance'),('101','maintenance'),
                                 ('1636934401','maintenance'),('455740857','maintenance'),('397185913','admin')]:
                self.assertEqual(match_profile_route(routes,'bale',chat_id=user,user_id=user).profile,profile)
            self.assertIsNone(match_profile_route(routes,'bale',chat_id='999',user_id='101'))
        self.assertFalse(any(str(r.get('user_id','')) in {'654806764','1732374823'} for r in cfg['gateway']['profile_routes']))

    def test_real_plugin_registration_tool_resolution_scope_a_b_a_and_backend_recheck(self):
        from hermes_constants import set_hermes_home_override,reset_hermes_home_override,hermes_home_key
        from hermes_cli.plugins import PluginManifest,PluginManager,PluginContext,LoadedPlugin
        from gateway.run_turn import GatewayTurnMixin
        from gateway.session_context import set_session_vars,clear_session_vars
        from toolsets import resolve_toolset
        a=self.directory/'profile-a';b=self.directory/'profile-b';a.mkdir();b.mkdir()
        cfg={'platform_toolsets':{'bale':['web','skills_readonly','komatso_public_browser','delegation','komatso_maintenance','no_mcp']},
             'known_plugin_toolsets':{'bale':['komatso_function']},
             'capability_toolsets_resolver':'integrations.hermes.role_routing.resolve_toolsets'}
        for home in [a,b]:(home/'config.yaml').write_text(yaml.safe_dump(cfg),encoding='utf-8')
        domain=module('mechanical_domain_native_fixture',ROOT/'integrations/hermes/plugins/komatso-function-domain/__init__.py')
        maintenance=module('mechanical_manual_native_fixture',ROOT/'integrations/hermes/plugins/komatso-maintenance-manual/__init__.py')
        public=module('mechanical_public_native_fixture',ROOT/'integrations/hermes/plugins/komatso-public-research/__init__.py')
        managers={}
        for home in [a,b]:
            token=set_hermes_home_override(home)
            try:
                manager=PluginManager(hermes_home_key(home));managers[home]=manager
                for name,plugin in [('mechanical-domain',domain),('mechanical-manual',maintenance),('mechanical-public',public)]:
                    manifest=PluginManifest(name=name,source='project')
                    manager._plugins[name]=LoadedPlugin(manifest=manifest,enabled=True)
                    plugin.register(PluginContext(manifest,manager))
                manager._discovered=True
            finally:reset_hermes_home_override(token)
        runner=SimpleNamespace(_delivery_adapter_for=lambda s:None)
        # Actual native filter + registered schemas, with only the identity DB substituted.
        try:
            for home,user in [(a,'654806764'),(b,'101'),(a,'1732374823')]:
                token=set_hermes_home_override(home)
                tokens=set_session_vars(platform='bale',chat_type='dm',user_id=user,chat_id=user)
                try:
                    with patch.object(role_routing,'AuthorizationStore',return_value=self.auth),patch.object(domain,'AuthorizationStore',return_value=self.auth),patch('hermes_cli.plugins.get_plugin_manager',return_value=managers[home]):
                        source=SimpleNamespace(user_id=user,chat_id=user,chat_type='dm')
                        toolsets=GatewayTurnMixin._resolve_enabled_toolsets_for_source(runner,cfg,source,'bale')
                        names=set()
                        for ts in toolsets:names.update(resolve_toolset(ts))
                        self.assertTrue({'maintenance_manual_evidence','maintenance_partbook_lookup','function_read','function_list','public_browser_navigate'}<=names, (toolsets, sorted(names)))
                        self.assertFalse(names & {'terminal','read_file','write_file','execute_code','skill_manage','manage_connections'})
                        context=domain.business_context({})
                        self.assertIn(STAFF,context)
                        if user=='101':
                            self.assertNotIn(SOURCE_NAME,context)
                            with patch.object(domain.subprocess,'run') as process:
                                self.assertFalse(json.loads(domain.execute('report',{'kind':'overflow'}))['ok']);process.assert_not_called()
                        else:
                            initial=self.auth.function_scope(user)
                            with patch.object(domain.subprocess,'run',return_value=SimpleNamespace(returncode=0,stdout='{"ok":true,"result":{"rows":["fixture"]}}')) as process:
                                self.assertTrue(json.loads(domain.execute('read',{'path':STAFF}))['ok'])
                                self.assertEqual(json.loads(process.call_args.kwargs['input'])['identity']['user_id'],user)
                                self.assertNotIn('shell',process.call_args.kwargs)
                            def revoke(*args,**kw):
                                self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id=? AND role<>?",(user,MECHANICAL_STAFF))
                                return SimpleNamespace(returncode=0,stdout='{"ok":true,"result":{"rows":["must not publish"]}}')
                            with patch.object(domain.subprocess,'run',side_effect=revoke):
                                self.assertFalse(json.loads(domain.execute('read',{'path':SOURCE_NAME}))['ok'])
                finally:clear_session_vars(tokens);reset_hermes_home_override(token)
        finally:
            for manager in managers.values():manager.unload()

if __name__=='__main__':unittest.main()
