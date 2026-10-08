"""Future SENT-only copies for AF/OC/GR using isolated records and fake transport."""
from contextlib import closing
import importlib
import hashlib
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import Mock, patch
from tools.authorization.test_authorization import Fixture
from tools.authorization.office_delivery import migrate, COPY_ROLE, COPY_CAPABILITY
from tools.authorization.net import migrate_net, NET_MANAGER
from tools.fleet.work_orders.core import final_copy, delivery


class FinalCopyTests(Fixture, unittest.TestCase):
    def setUp(self):
        super().setUp()
        self.auth.migrate_admin(self.directory/'backups')
        migrate_net(self.auth,self.directory/'backups',assignments=(('101',NET_MANAGER),))
        migrate(self.auth,self.directory/'backups',assignments=(('202',COPY_ROLE),))
        self.fleet=self.directory/'fleet.db'
        with self.connection() as c:
            c.execute('CREATE TABLE machines(id INTEGER PRIMARY KEY,canonical_code TEXT)')
            importlib.import_module('tools.fleet.work_orders.migrations.001_create_work_order_schema_v1').create_schema(c)
            c.execute("INSERT INTO machines VALUES (1,'HD714')")
            c.execute("INSERT INTO service_staff(id,display_name,bale_id,service_role) VALUES(1,'Fixture technician','303','AIR_FILTER')")
            final_copy.activate(c,timestamp=100)
        self.main=Mock();self.main.send_document.side_effect=lambda **kw:self.staff_send(**kw)
        self.staff=[];self.copies=[]
        self.copy_sender=Mock();self.copy_sender.document.side_effect=self.copy_send
        for target,value in [('tools.fleet.work_orders.core.db.DB_PATH',self.fleet),
                             ('tools.fleet.work_orders.core.final_copy.ARTIFACT_ROOT',self.directory/'artifacts')]:
            p=patch(target,value);p.start();self.addCleanup(p.stop)
        p=patch('tools.fleet.work_orders.core.final_copy.AuthorizationStore',return_value=self.auth);p.start();self.addCleanup(p.stop)
        p=patch('tools.fleet.work_orders.core.delivery.archive_delivered_order');self.archive=p.start();self.addCleanup(p.stop)

    def connection(self):
        c=sqlite3.connect(self.fleet);c.row_factory=sqlite3.Row;c.execute('PRAGMA foreign_keys=ON')
        return closing_context(c)

    def staff_send(self,**kw):
        self.staff.append((kw['chat_id'],Path(kw['file_path']).read_bytes()))
        return {'ok':True,'result':{'message_id':str(len(self.staff))}}

    def copy_send(self,user,path,caption,**kw):
        self.copies.append((user,Path(path).read_bytes(),caption,kw['file_name']))
        return str(len(self.copies))

    def order(self,kind='AIR_FILTER',status='APPROVED'):
        number=kind+'-'+str(len(self.all_orders())+1)
        excel=self.directory/(number+'.xlsx');excel.write_bytes(b'fixture workbook')
        pdf=excel.with_suffix('.pdf');pdf.write_bytes(b'%PDF-1.7\nFINAL '+number.encode())
        with self.connection() as c:
            cur=c.execute("""INSERT INTO service_work_orders(work_order_no,work_order_type,jalali_date,status,assigned_staff_id,
                excel_path,pdf_path,created_by,approved_by) VALUES (?,?,?, ?,1,?,?,'bale:101','bale:101')""",
                (number,kind,'1405/07/16',status,str(excel),str(pdf)))
            c.execute("""INSERT INTO service_work_order_items(work_order_id,item_no,machine_id,machine_code,action_text)
                VALUES (?,1,1,'714','fixture action')""",(cur.lastrowid,))
        return number,pdf

    def all_orders(self):
        with self.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM service_work_orders')]

    def receipts(self):
        with self.connection() as c:return [dict(r) for r in c.execute('SELECT * FROM work_order_final_copy')]

    def send(self,number):
        return delivery.send_work_order(work_order_no=number,sender=self.main)

    def drain(self,clock=200):
        final_copy.drain_pending(authorization=self.auth,sender_factory=lambda:self.copy_sender,clock=clock)

    def test_af_oc_gr_only_after_sent_exact_bytes_caption_and_once(self):
        for kind in ('AIR_FILTER','OIL_CHANGE','GREASING'):
            number,pdf=self.order(kind)
            self.drain();self.assertEqual(len(self.copies),len(self.staff))
            self.send(number)
            self.assertEqual(self.all_orders()[-1]['status'],'SENT')
            self.assertEqual(self.receipts()[-1]['status'],'pending')
            self.drain();self.drain(1200)
            self.assertEqual(self.copies[-1][1],self.staff[-1][1])
            self.assertEqual(hashlib.sha256(self.copies[-1][1]).hexdigest(),self.receipts()[-1]['artifact_sha256'])
            self.assertIn(number,self.copies[-1][2]);self.assertIn('HD714',self.copies[-1][2])
            self.assertIn('101',self.copies[-1][2]);self.assertIn('303',self.copies[-1][2])
            self.assertEqual(self.copies[-1][3],pdf.name)
            before=len(self.copies)
            self.send(number);self.drain(1300)
            self.assertEqual(len(self.copies),before)
        self.assertEqual(len(self.staff),3);self.assertEqual(len(self.copies),3)

    def test_draft_preview_file_ready_assigned_approved_only_never_copy(self):
        for state in ('DRAFT','FILE_READY','ASSIGNED','APPROVED'):
            number,pdf=self.order(status=state)
            if state!='APPROVED':
                with self.assertRaises(RuntimeError):self.send(number)
        self.drain();self.assertEqual(self.receipts(),[]);self.assertEqual(self.copies,[])

    def test_main_failed_or_uncertain_never_enqueues(self):
        for exc in (RuntimeError('definite'),TimeoutError('uncertain')):
            number,pdf=self.order()
            self.main.send_document.side_effect=exc
            with self.assertRaises(type(exc)):self.send(number)
        self.drain();self.assertEqual(self.receipts(),[]);self.assertEqual(self.copies,[])
        self.assertTrue(all(o['status']=='APPROVED' for o in self.all_orders()))

    def test_unconfirmed_transport_result_does_not_copy(self):
        number,pdf=self.order()
        self.main.send_document.return_value=None;self.main.send_document.side_effect=None
        self.send(number);self.drain()
        self.assertEqual(self.receipts(),[])

    def test_copy_failure_keeps_sent_and_no_staff_resend_or_archive(self):
        from tools.scheduler.tasks import TransportUnavailable
        number,pdf=self.order();self.send(number);self.archive.reset_mock()
        self.copy_sender.document.side_effect=TransportUnavailable()
        self.drain()
        self.assertEqual(self.receipts()[0]['status'],'failed')
        self.assertEqual(self.all_orders()[0]['status'],'SENT')
        self.assertEqual(len(self.staff),1);self.archive.assert_not_called()
        self.copy_sender.document.side_effect=self.copy_send
        self.drain(259);self.assertEqual(self.copies,[])
        self.drain(261);self.drain(1000)
        self.assertEqual(len(self.copies),1);self.assertEqual(len(self.staff),1);self.archive.assert_not_called()

    def test_uncertain_and_restart_sending_never_blind_resend(self):
        from tools.scheduler.tasks import TransportUncertain
        number,pdf=self.order();self.send(number)
        self.copy_sender.document.side_effect=TransportUncertain()
        self.drain();self.drain(1200)
        self.assertEqual(self.receipts()[0]['status'],'uncertain')
        self.assertEqual(self.copy_sender.document.call_count,1)
        with self.connection() as c:
            c.execute("UPDATE work_order_final_copy SET status='sending',claimed_at=100")
        self.copy_sender.document.side_effect=self.copy_send
        self.drain(1100);self.drain(2200)
        self.assertEqual(self.copies,[]);self.assertEqual(self.receipts()[0]['status'],'uncertain')

    def test_deleted_original_still_delivers_pinned_pdf_without_renderer(self):
        number,pdf=self.order();expected=pdf.read_bytes();self.send(number);pdf.unlink()
        Path(self.all_orders()[0]['excel_path']).write_bytes(b'changed after send')
        with patch.object(delivery,'export_staff_pdf',side_effect=AssertionError('no rerender')):
            self.drain()
        self.assertEqual(self.copies[0][1],expected)

    def test_artifact_tampering_is_blocked_not_regenerated(self):
        number,pdf=self.order();self.send(number)
        Path(self.receipts()[0]['artifact_path']).write_bytes(b'%PDF-1.7 TAMPERED')
        self.drain()
        self.assertEqual(self.copies,[]);self.assertEqual(self.receipts()[0]['status'],'blocked')

    def test_revocation_before_upload_rechecked(self):
        number,pdf=self.order();self.send(number)
        self.copy_sender.check_connection.side_effect=lambda:self.sql("UPDATE auth_user_roles SET active=0 WHERE user_id='202' AND role=?",(COPY_ROLE,))
        self.drain()
        self.assertEqual(self.copies,[]);self.assertEqual(self.receipts()[0]['status'],'revoked')

    def test_historical_sent_no_backfill_or_callback_copy(self):
        number,pdf=self.order(status='SENT')
        with self.connection() as c:final_copy.activate(c,timestamp=200)
        self.send(number);self.drain()
        self.assertEqual(self.receipts(),[]);self.assertEqual(self.staff,[]);self.assertEqual(self.copies,[])

    def test_outbox_commits_with_sent_even_if_archive_fails(self):
        from tools.fleet.work_orders.core.daily_archive import DailyArchiveError
        number,pdf=self.order()
        self.archive.side_effect=DailyArchiveError('fixture')
        with self.assertRaises(DailyArchiveError):self.send(number)
        self.assertEqual(self.all_orders()[0]['status'],'SENT')
        self.assertEqual(self.receipts()[0]['status'],'pending')
        self.archive.reset_mock();self.drain()
        self.archive.assert_not_called();self.assertEqual(len(self.copies),1)

    def test_pinning_failure_is_copy_only_blocked_receipt(self):
        number,pdf=self.order()
        with patch.object(final_copy,'ARTIFACT_ROOT',pdf):
            self.send(number)
        self.assertEqual(self.all_orders()[0]['status'],'SENT')
        self.assertEqual(self.receipts()[0]['status'],'blocked')
        self.drain();self.assertEqual(len(self.staff),1);self.assertEqual(self.copies,[])

    def test_copy_capability_does_not_create_approve_or_staff_ack(self):
        from tools.fleet.work_orders.core.permissions import check_work_order_permission
        from tools.fleet.work_orders.core import staff_dispatch
        from tools.authorization.net import domain_decision
        for cap in ('work_orders.create','work_orders.approve','work_orders.assign','work_orders.send'):
            self.assertFalse(self.auth.has_capability('202',cap))
            self.assertFalse(domain_decision('202','work_orders',(cap,),store=self.auth).allowed)
        self.assertFalse(check_work_order_permission('202',db_path=self.fleet).allowed)
        self.assertFalse(staff_dispatch.is_recipient('202'))

    def test_caption_uses_selected_dispatch_staff_name(self):
        number,pdf=self.order()
        with self.connection() as c:
            from tools.fleet.work_orders.core.staff_dispatch import create_schema
            create_schema(c)
            c.execute('INSERT INTO service_work_order_dispatch(work_order_no,manager_chat_id,recipient_id,staff_name) VALUES (?,?,?,?)',
                      (number,'101','303','Selected roster technician'))
        self.send(number);self.drain()
        self.assertIn('Selected roster technician',self.copies[0][2])
        self.assertNotIn('Fixture technician',self.copies[0][2])

    def test_no_net_issuer_no_copy(self):
        number,pdf=self.order()
        with self.connection() as c:c.execute("UPDATE service_work_orders SET created_by='bale:303',approved_by='bale:303'")
        self.send(number);self.drain()
        self.assertEqual(self.receipts(),[]);self.assertEqual(len(self.staff),1)

    def test_auth_outage_is_retryable_not_revocation(self):
        from types import SimpleNamespace
        number,pdf=self.order();self.send(number)
        with patch.object(self.auth,'resolve_active_recipients',return_value=SimpleNamespace(status='store_unavailable',recipients=())):
            self.drain()
        self.assertEqual(self.receipts()[0]['status'],'failed')
        self.drain(261)
        self.assertEqual(len(self.copies),1)

    def test_net_deputy_future_send_also_copied(self):
        from tools.authorization.net import NET_DEPUTY
        self.sql("INSERT INTO channel_users VALUES ('bale','404','404','approved')")
        migrate_net(self.auth,self.directory/'backups',assignments=(('404',NET_DEPUTY),))
        number,pdf=self.order()
        with self.connection() as c:
            c.execute("UPDATE service_work_orders SET created_by='bale:404',approved_by='bale:404'")
        self.send(number);self.drain()
        self.assertEqual(len(self.copies),1)
        self.assertIn('404',self.copies[0][2])


class closing_context:
    def __init__(self,c):self.c=c
    def __enter__(self):return self.c
    def __exit__(self,*exc):
        try:
            if exc[0] is None:self.c.commit()
            else:self.c.rollback()
        finally:self.c.close()
