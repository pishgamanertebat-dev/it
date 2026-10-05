"""Hermetic ADMIN-1 tests: local fixtures, SQLite copies, fake Bale transport."""
from pathlib import Path
from contextlib import closing
import importlib.util,io,json,os,sqlite3,sys,tempfile,unittest
from datetime import datetime,timedelta
from types import SimpleNamespace
from unittest.mock import Mock,patch
from zoneinfo import ZoneInfo
import openpyxl,yaml
from tools.authorization.test_authorization import Fixture
from tools.authorization import AuthorizationStore,OFFICE_SUPERVISOR,BUSINESS_ADMIN,FUNCTION_READ,DRIVER_READ,DRIVER_RECEIVE
from tools.scheduler.runner import tick,load_config
from tools.scheduler.tasks import driver_daily
from integrations.hermes.function_domain.scoped import ScopedReader,ScopeDenied
from integrations.hermes.function_domain.extract import extract
from integrations.hermes.function_domain.worker import run
from integrations.hermes.function_domain.driver_report import load_driver,render_driver,SOURCE_NAME,SECTIONS,EMPTY,STALE
ROOT=Path('E:/KomatsoAI');HOME=Path('C:/Users/win-10/AppData/Local/hermes')

def load_module(name,path):
    spec=importlib.util.spec_from_file_location(name,path);mod=importlib.util.module_from_spec(spec);sys.modules[name]=mod;spec.loader.exec_module(mod);return mod

class AdminFixture(Fixture):
    def setUp(self):
        super().setUp();self.auth.migrate_admin(self.directory/'backups')
        self.data=self.directory/'Function';self.data.mkdir();self.reader=ScopedReader(self.data)
        self.cache=self.directory/'cache'
    def admin(self,user='101'):self.auth.assign_role(user,BUSINESS_ADMIN,actor='fixture')
    def request(self,op,args,user='101'):
        return run({'operation':op,'args':args,'identity':{'platform':'bale','chat_type':'dm','user_id':user,'chat_id':user}},store=self.auth,reader=self.reader,cache=self.cache)
    def workbook(self,days):
        wb=openpyxl.Workbook();wb.remove(wb.active)
        for title,date,mechanical,metalwork in days:
            s=wb.create_sheet(title);s.append(['گزارش روزانه رانندگان',f'تاریخ {date}'])
            s.append(['ردیف','نوع دستگاه','کد جدید',*SECTIONS.values()])
            s.append([1,'دامپتراک','HD714',mechanical,metalwork]);s.append([2,'لودر','WA601',None,None])
        wb.save(self.data/SOURCE_NAME);wb.close()

class AuthorizationTests(AdminFixture,unittest.TestCase):
    def test_additive_idempotent_migration_registration_and_overflow_unchanged(self):
        before=self.sql('SELECT * FROM channel_users');overflow=self.sql("SELECT * FROM auth_role_capabilities WHERE capability LIKE 'reports.overflow.%'")
        backup=self.auth.migrate_admin(self.directory/'backups')
        with closing(sqlite3.connect(backup)) as c:self.assertEqual(c.execute('PRAGMA integrity_check').fetchone()[0],'ok')
        self.assertEqual(before,self.sql('SELECT * FROM channel_users'));self.assertEqual(overflow,self.sql("SELECT * FROM auth_role_capabilities WHERE capability LIKE 'reports.overflow.%'"))
        self.assertEqual(self.sql('SELECT version FROM auth_migrations ORDER BY version'),[(1,),(2,)])
        self.assertEqual(set(self.auth.capabilities_for_role(BUSINESS_ADMIN)),{FUNCTION_READ})
        self.assertTrue({DRIVER_READ,DRIVER_RECEIVE}.issubset(self.auth.capabilities_for_role(OFFICE_SUPERVISOR)))
    def test_role_routing_assignments_revocation_and_registration(self):
        self.assign();self.admin();self.admin('202')
        self.assertEqual(self.auth.resolve_profile('101','101'),'admin');self.assertIsNone(self.auth.resolve_profile('202','202'))
        self.assertIsNone(self.auth.resolve_profile('303','303'));self.assertIsNone(self.auth.resolve_profile('202','101'))
        self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='202'")
        self.assertIsNone(self.auth.resolve_profile('202','202'))
        self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='101'")
        self.assertIsNone(self.auth.resolve_profile('101','101'))
    def test_unavailable_corrupt_or_bad_mapping_never_escalates(self):
        self.admin();self.assign()
        self.sql("UPDATE auth_role_profiles SET profile='../admin'");self.assertIsNone(self.auth.resolve_profile('101','101'))
        self.sql('DROP TABLE auth_role_profiles');self.assertIsNone(self.auth.resolve_profile('101','101'))
        absent=self.directory/'absent.sqlite';self.assertIsNone(AuthorizationStore(absent).resolve_profile('101','101'));self.assertFalse(absent.exists())
        invalid=self.directory/'invalid.sqlite';invalid.write_bytes(b'not sqlite');self.assertIsNone(AuthorizationStore(invalid).resolve_profile('101','101'))
    def test_ambiguous_same_priority_profile_mapping_fails_closed(self):
        self.admin();self.assign()
        self.sql("INSERT INTO auth_role_profiles VALUES ('business_admin','maintenance',100,1)")
        self.assertIsNone(self.auth.resolve_profile('101','101'))
    def test_recipient_unique_zero_multiple_and_admin_only(self):
        self.admin('202');self.assertEqual(self.auth.resolve_daily_recipient(capability=DRIVER_RECEIVE).status,'no_active_holder')
        self.assign();self.admin();self.assertEqual(self.auth.resolve_daily_recipient(capability=DRIVER_RECEIVE).recipient,'101')
        self.assertFalse(self.auth.has_capability('202',DRIVER_RECEIVE))
        self.sql("INSERT INTO auth_user_roles VALUES ('bale','202','office_supervisor',1,'fixture','fixture','fixture')")
        self.assertEqual(self.auth.resolve_daily_recipient(capability=DRIVER_RECEIVE).status,'ambiguous_holders')
    def test_bulk_assignments_rollback_on_bad_identity(self):
        with self.assertRaises(ValueError):self.auth.assign_roles([('101',BUSINESS_ADMIN),('303',BUSINESS_ADMIN)],actor='fixture')
        self.assertFalse(self.auth.has_capability('101',FUNCTION_READ))
        self.auth.assign_roles([('101',BUSINESS_ADMIN),('202',BUSINESS_ADMIN)],actor='fixture')
        self.assertTrue(self.auth.has_capability('101',FUNCTION_READ));self.assertTrue(self.auth.has_capability('202',FUNCTION_READ))

    def test_business_admin_does_not_force_a_profile_or_daily_receive(self):
        self.admin('202')
        self.assertIsNone(self.auth.resolve_profile('202','202'))
        self.assertTrue(self.auth.has_capability('202',FUNCTION_READ))
        self.assertFalse(self.auth.has_capability('202',DRIVER_RECEIVE))
        self.assertFalse(self.auth.has_capability('202','reports.overflow.daily_receive'))
        self.assertEqual(self.sql('SELECT role,profile FROM auth_role_profiles'),[('office_supervisor','admin')])

    def test_exact_overflow_command_for_business_reader_reaches_its_domain_agent(self):
        import ast
        from tools.fleet.overflow import bale
        source=ROOT/'integrations/hermes/plugins/komatso-bale-registry/__init__.py'
        tree=ast.parse(source.read_text(encoding='utf-8'))
        functions=[n for n in tree.body if isinstance(n,ast.FunctionDef) and n.name in {'_handle_overflow_report','_platform_name'}]
        scope={'_send':Mock()};exec(compile(ast.Module(body=functions,type_ignores=[]),'<real-registry-bridge>','exec'),scope)
        self.admin('202')
        handler=SimpleNamespace(authorization=self.auth)
        with patch.object(bale,'_handler',handler),patch.object(bale,'handle_overflow_message') as backend:
            self.assertIsNone(scope['_handle_overflow_report'](self.event('202',text='سرریز 1405/07/10'),SimpleNamespace()))
            backend.assert_not_called();self.assertFalse(self.auth.has_capability('202','reports.overflow.read'))
            scope['_handle_overflow_report'](self.event('303',text='سرریز 1405/07/10'),SimpleNamespace());backend.assert_called_once()
            backend.reset_mock();self.assign();self.admin()
            scope['_handle_overflow_report'](self.event('101',text='سرریز 1405/07/10'),SimpleNamespace());backend.assert_called_once()
            backend.reset_mock();scope['_handle_overflow_report'](self.event('202',text='سرریز',chat_id='group'),SimpleNamespace());backend.assert_called_once()

    def test_native_staged_resolver_routing_and_safe_fallback(self):
        sys.path.insert(0,str(HOME/'hermes-agent'))
        from tools.admin.core_fixture import patched_core_source
        from types import ModuleType
        mod=ModuleType('admin1_native_routing_fixture');sys.modules[mod.__name__]=mod
        exec(compile(patched_core_source('gateway/profile_routing.py'),'<reviewed-native-routing>','exec'),mod.__dict__)
        routes=mod.parse_profile_routes([{'name':'role','platform':'bale','resolver':'integrations.hermes.role_routing.resolve_profile'},
            {'name':'legacy','platform':'bale','user_id':'404','profile':'maintenance'},
            {'name':'expert','platform':'bale','user_id':'202','profile':'maintenance'}])
        self.assign();self.admin();self.admin('202')
        with patch('integrations.hermes.role_routing.AuthorizationStore',return_value=self.auth):
            self.assertEqual(mod.match_profile_route(routes,'bale',chat_id='101',user_id='101').profile,'admin')
            self.assertEqual(mod.match_profile_route(routes,'bale',chat_id='202',user_id='202').profile,'maintenance')
            self.assertIsNone(mod.match_profile_route(routes,'bale',chat_id='303',user_id='303'))
            self.assertEqual(mod.match_profile_route(routes,'bale',chat_id='404',user_id='404').profile,'maintenance')
            self.assertIsNone(mod.match_profile_route(routes,'bale',chat_id='group',user_id='101'))
            self.sql('DROP TABLE auth_role_profiles')
            self.assertIsNone(mod.match_profile_route(routes,'bale',chat_id='101',user_id='101'))
            self.assertEqual(mod.match_profile_route(routes,'bale',chat_id='404',user_id='404').profile,'maintenance')

class ReaderTests(AdminFixture,unittest.TestCase):
    def test_recursive_list_search_metadata_and_content_with_capability_only(self):
        self.admin();(self.data/'nested').mkdir();(self.data/'nested/file.txt').write_text('اطلاعات شرکت\nردیف دوم',encoding='utf-8')
        result=self.request('list',{'recursive':True})['result'];self.assertEqual(result['total'],2)
        self.assertEqual(self.request('search',{'query':'FILE'})['result']['entries'][0]['path'],'nested/file.txt')
        self.assertEqual(self.request('read',{'path':'nested/file.txt','limit':1})['result']['lines'],['اطلاعات شرکت'])
        self.assertIsNotNone(self.request('metadata',{'path':'nested/file.txt'})['result']['size'])
        with self.assertRaises(PermissionError):self.request('read',{'path':'nested/file.txt'},'202')
        with self.assertRaises(PermissionError):self.request('list',{},'303')
    def test_path_traversal_absolute_unc_uri_ads_devices_and_normalization_denied(self):
        self.admin()
        bad=['../x','nested/../x','C:\\Windows\\win.ini','E:\\Function\\x','\\\\server\\share\\x','file:///C:/x','/outside','x:stream','x.','NUL','COM1.txt','%2e%2e/x','nested//x','\\\\?\\E:\\Function\\x']
        for value in bad:
            with self.subTest(value=value),self.assertRaises((ScopeDenied,ValueError)):self.reader.snapshot(value)
    @unittest.skipUnless(os.name=='nt','Windows junction test')
    def test_real_junction_and_final_handle_root_reparse_denied(self):
        import _winapi
        outside=self.directory/'outside';outside.mkdir();(outside/'secret.txt').write_text('outside')
        junction=self.data/'escape';_winapi.CreateJunction(str(outside),str(junction))
        with self.assertRaises(ScopeDenied):self.reader.snapshot('escape/secret.txt')
        listed=self.reader.list(recursive=True);self.assertIn('escape',listed['skipped_unreadable_or_links']);self.assertEqual(listed['entries'],[])
        root_link=self.directory/'root-link';_winapi.CreateJunction(str(outside),str(root_link))
        with self.assertRaises(ScopeDenied):ScopedReader(root_link).snapshot('secret.txt')
    @unittest.skipUnless(os.name=='nt','Windows locking test')
    def test_parent_and_file_handles_prevent_replacement_during_read(self):
        (self.data/'nested').mkdir();p=self.data/'nested/x.txt';p.write_text('original')
        with self.reader.open('nested/x.txt') as (_,handle):
            with self.assertRaises(OSError):p.rename(self.data/'nested/moved.txt')
            with self.assertRaises(OSError):(self.data/'nested').rename(self.data/'other')
            self.assertEqual(handle.read(),b'original')
    def test_write_edit_delete_rename_move_execute_unavailable(self):
        self.admin()
        for op in ['write','edit','delete','rename','move','execute','shell','powershell']:
            with self.assertRaises(ValueError):self.request(op,{'path':'x.txt'})
        for prop in ['write','edit','delete','rename','move','execute']:self.assertFalse(hasattr(self.reader,prop))
    def test_unknown_binary_is_metadata_and_byte_identical_attachment_only(self):
        self.admin();original=b'\x00\x01\x02fakebinary';(self.data/'a.unknown').write_bytes(original)
        self.assertFalse(self.request('read',{'path':'a.unknown'})['result']['content_supported'])
        target=Path(self.request('attach',{'path':'a.unknown'})['result']['attachment']);self.assertEqual(target.read_bytes(),original)
        self.assertEqual((self.data/'a.unknown').read_bytes(),original)
    def test_xlsx_sheet_selection_paging_csv_text_docx_and_pdf(self):
        self.admin();self.workbook([('historical','1405/07/10','عطل',None)])
        self.assertEqual(extract(self.reader,SOURCE_NAME)['sheets'],['historical'])
        self.assertIn('1405/07/10',str(extract(self.reader,SOURCE_NAME,sheet='historical',limit=1)))
        (self.data/'a.csv').write_text('a,b\n1,2\n3,4',encoding='utf-8')
        self.assertEqual(extract(self.reader,'a.csv',offset=1,limit=1)['rows'],[['1','2']])
        import fitz,zipfile
        doc=fitz.open();page=doc.new_page();page.insert_text((30,30),'safe PDF fixture');doc.save(self.data/'a.pdf');doc.close()
        self.assertIn('safe PDF',extract(self.reader,'a.pdf')['pages'][0]['text'])
        with zipfile.ZipFile(self.data/'a.docx','w') as z:z.writestr('word/document.xml','<root><p>safe document fixture</p></root>')
        self.assertEqual(extract(self.reader,'a.docx')['lines'],['safe document fixture'])
    def test_owner_and_invalid_workbook_provide_metadata_and_attachment_without_guessing(self):
        self.admin()
        for name,data in [('~$locked.xlsx',b'owner binary'),('invalid.xlsx',b'\0'*100)]:
            (self.data/name).write_bytes(data)
            result=self.request('read',{'path':name})['result']
            self.assertFalse(result['content_supported']);self.assertEqual(result['size'],len(data))
            target=Path(self.request('attach',{'path':name})['result']['attachment']);self.assertEqual(target.read_bytes(),data)

    def test_revocation_during_read_blocks_content_publication(self):
        self.admin();(self.data/'a.txt').write_text('secret')
        original=self.reader.snapshot
        def revoked(*args,**kw):
            data=original(*args,**kw);self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='101'");return data
        with patch.object(self.reader,'snapshot',side_effect=revoked),self.assertRaises(PermissionError):self.request('read',{'path':'a.txt'})

class ReportTests(AdminFixture,unittest.TestCase):
    def test_exact_date_not_sheet_order_or_latest_and_two_real_images(self):
        self.workbook([('newest-first','1405/07/12','TODAY secret','TODAY metal'),('requested','1405/07/11','YESTERDAY mechanical','YESTERDAY metal'),('last-old','1405/07/10','OLD secret','OLD metal')])
        before=(self.data/SOURCE_NAME).read_bytes();report=load_driver('1405/07/11',self.reader)
        self.assertEqual(report['sheet'],'requested');self.assertIn('YESTERDAY',str(report['sections']));self.assertNotIn('TODAY',str(report['sections']));self.assertNotIn('OLD',str(report['sections']))
        paths=render_driver(report,self.directory/'images');self.assertEqual(len(paths),2)
        import fitz
        for p in paths:
            self.assertGreater(fitz.Pixmap(p).width,1000);self.assertLess(Path(p).stat().st_size,9*1024*1024)
        self.assertEqual((self.data/SOURCE_NAME).read_bytes(),before)
    def test_missing_requested_day_produces_two_stale_notices_no_old_faults(self):
        self.workbook([('old','1405/07/10','OLD mechanical','OLD metal')]);report=load_driver('1405/07/11',self.reader)
        self.assertNotIn('OLD',str(report))
        for section in report['sections'].values():self.assertEqual((section['status'],section['message']),('date_missing',STALE))
        self.assertEqual(len(render_driver(report,self.directory/'images')),2)
    def test_memory_only_render_and_upload_never_write_image_files(self):
        self.workbook([('requested','1405/07/10','fault','metal')]);report=load_driver('1405/07/10',self.reader)
        before=set(self.directory.rglob('*'))
        images=render_driver(report)
        self.assertEqual(set(self.directory.rglob('*')),before)
        self.assertEqual(len(images),2)
        from tools.scheduler.tasks import BaleSender
        sender=BaleSender.__new__(BaleSender);sender.token='fixture-token';sender.session=Mock()
        sender.session.post.return_value.status_code=200;sender.session.post.return_value.json.return_value={'ok':True}
        for i,data in enumerate(images):sender.photo_memory('fixture-recipient',data,f'fixture-{i}.png','fixture')
        self.assertEqual(sender.session.post.call_count,2)
        self.assertEqual(sender.session.post.call_args.kwargs['files']['photo'][1],images[-1])
        self.assertEqual(set(self.directory.rglob('*')),before)

    def test_empty_one_section_reports_empty_only_for_that_section(self):
        self.workbook([('requested','1405/07/11',None,'آهنگری ثبت‌شده')]);report=load_driver('1405/07/11',self.reader)
        self.assertEqual(report['sections']['mechanical']['message'],EMPTY);self.assertEqual(report['sections']['metalwork']['status'],'ready')
    def test_duplicate_date_or_missing_columns_fails_not_guess(self):
        self.workbook([('a','1405/07/11','x','y'),('b','1405/07/11','x','y')])
        with self.assertRaises(ValueError):load_driver('1405/07/11',self.reader)
        self.workbook([('a','1405/07/11','x','y')]);p=self.data/SOURCE_NAME;wb=openpyxl.load_workbook(p);wb['a']['E2']='شرح کلی';wb.save(p);wb.close()
        with self.assertRaises(ValueError):load_driver('1405/07/11',self.reader)
    def test_admin_any_requested_date_report_operation(self):
        self.admin('202');self.workbook([('old','1404/08/01','تاریخی','آهنگری')])
        result=self.request('report',{'kind':'driver','date':'1404/08/01'},'202')['result']
        self.assertEqual(result['report']['date'],'1404/08/01');self.assertEqual(len(result['images']),2)

class ScheduleTests(AdminFixture,unittest.TestCase):
    def setUp(self):
        super().setUp();self.config=self.directory/'schedule.yaml';self.state=self.directory/'runs.sqlite'
        self.job={'id':'driver_test','task':'driver_daily','recipient_role':'office_supervisor','timezone':'Asia/Tehran','trigger':{'type':'cron','hour':10,'minute':0},'params':{}}
        self.config.write_text(yaml.safe_dump({'version':1,'timezone':'UTC','schedules':[self.job]}),encoding='utf-8')
        self.now=datetime(2026,10,4,10,0,tzinfo=ZoneInfo('Asia/Tehran'));self.calls=[]
        self.registry={'driver_daily':(lambda p:None,lambda r,p:self.calls.append((r,p)))}
    def test_ten_previous_day_dedup_and_business_admin_no_push(self):
        self.assign();self.admin('202');self.assertEqual(tick(self.config,self.state,self.now,self.registry,self.auth),0)
        tick(self.config,self.state,self.now+timedelta(minutes=1),self.registry,self.auth)
        self.assertEqual(self.calls,[('101',{'date':'1405/07/11'})])
    def test_zero_multiple_unapproved_missing_receive_and_store_offline_skip(self):
        cases=['zero','multiple','unapproved','capability','offline']
        for i,case in enumerate(cases):
            with self.subTest(case=case):
                if case=='multiple':self.assign();self.sql("INSERT INTO auth_user_roles VALUES ('bale','202','office_supervisor',1,'t','t','t')")
                if case=='unapproved':self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='202'");self.sql("UPDATE channel_users SET registration_status='revoked' WHERE user_id='101'")
                if case=='capability':self.sql("UPDATE channel_users SET registration_status='approved' WHERE user_id='101'");self.sql('DELETE FROM auth_role_capabilities WHERE capability=?',(DRIVER_RECEIVE,))
                if case=='offline':self.path.rename(self.directory/'offline.sqlite')
                self.assertEqual(tick(self.config,self.state,self.now+timedelta(days=i),self.registry,self.auth),0)
                self.assertEqual(self.calls,[])
    def test_driver_backend_fixture_only_unique_holder_two_pdfs_and_revocation(self):
        self.assign();self.admin('202');self.workbook([('requested','1405/07/11','fault','metal')])
        from integrations.hermes.function_domain.driver_report import build_driver_pdf
        def fixture(date,out):
            from integrations.hermes.function_domain.driver_report import load_driver
            import fitz
            docs=[]
            for section in SECTIONS:
                p=Path(out)/(section+'.pdf');doc=fitz.open();page=doc.new_page();page.insert_text((30,30),section);doc.save(p);doc.close();docs.append(str(p))
            return {'report':load_driver(date,self.reader),'documents':docs}
        with patch('integrations.hermes.function_domain.driver_report.build_driver_pdf',side_effect=fixture),patch('tools.scheduler.tasks.BaleSender') as sender:
            driver_daily('101',{'date':'1405/07/11'},authorization=self.auth)
            self.assertEqual(sender.return_value.document.call_count,2);self.assertFalse(sender.return_value.photo.called)
            sender.reset_mock();self.assertEqual(driver_daily('202',{},authorization=self.auth)['reason'],'recipient_changed');sender.assert_not_called()
    def test_fixed_recipient_date_and_wrong_timezone_rejected(self):
        for addition in [{'recipient':'101'},{'timezone':'UTC'},{'params':{'date':'1405/07/10'}}]:
            self.config.write_text(yaml.safe_dump({'version':1,'schedules':[{**self.job,**addition}]}),encoding='utf-8')
            with self.assertRaises(ValueError):load_config(self.config,self.registry)
    def test_production_config_overflow_unchanged_and_driver_exact_ten(self):
        jobs=yaml.safe_load((ROOT/'settings/schedules.yaml').read_text(encoding='utf-8'))['schedules']
        overflow=next(j for j in jobs if j['task']=='overflow')
        self.assertEqual(overflow['trigger'],{'type':'cron','hour':9,'minute':0})
        self.assertEqual(overflow['recipient_role'],'office_supervisor');self.assertEqual(overflow['params'],{})
        driver=[j for j in jobs if j['task']=='driver_daily'];self.assertEqual(len(driver),1)
        self.assertEqual(driver[0]['trigger'],{'type':'cron','hour':10,'minute':0});self.assertNotIn('recipient',driver[0])

if __name__=='__main__':unittest.main()
