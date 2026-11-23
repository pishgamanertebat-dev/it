"""Daily publication acceptance on private copies; simulated exclusion is labeled.

The real exclusion gate stays closed. No test sends Bale or touches a share.
"""
from contextlib import closing, nullcontext
from datetime import datetime
import io
import json
from pathlib import Path
import sqlite3
import zipfile
import unittest
from unittest.mock import Mock, patch
import uuid
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

from . import maintenance_publication as pub, maintenance_publication_alerts as notices
from . import maintenance_identity as identity, maintenance_ooxml as ooxml
from . import maintenance_baseline as base, maintenance_service as service
from . import test_maintenance_publication as previous
from tools.authorization.store import RecipientSetResolution
from tools.scheduler.tasks import TransportUnavailable

HERE = pub.CANDIDATE_ROOT / 'daily-1230-20261116'
HERE.mkdir(exist_ok=True)
RESULTS = []


def at(clock, day='2026-11-16'):
    return datetime.fromisoformat(day+'T'+clock).replace(tzinfo=ZoneInfo('Asia/Tehran'))


class DailyTests(unittest.TestCase):
    def fixture(self):
        f = previous.Fixture(self._testMethodName)
        cfg = json.loads(f.config.read_text('utf8'))
        cfg['outbound_publication'].update(policy='daily_1230', alert_owner='455740857',
                                          publication_window={'start':'12:30','end':'12:55'})
        f.config.write_text(json.dumps(cfg), 'utf8')
        self.f = f
        old_ai=f.ai
        def timed_ai(*args, **kwargs):
            result=old_ai(*args, **kwargs)
            with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db, db:
                db.execute("UPDATE edits SET created='2026-11-16T08:00:00+00:00' WHERE operation=?", (result['operation'],))
            return result
        f.ai=timed_ai
        return f

    def tearDown(self):
        failed = any(t is self for t, _ in self._outcome.result.failures+self._outcome.result.errors)
        item = dict(test=self._testMethodName, verdict='FAIL' if failed else 'PASS')
        if hasattr(self, 'f'):
            item['fixture'] = str(self.f.root)
            item['hashes'] = self.f.hashes()
        RESULTS.append(item)

    def tick(self, f, clock, *, simulated_exclusion=False, day='2026-11-16'):
        with patch.object(notices, 'now', return_value=at(clock,day)):
            if simulated_exclusion:
                # Pure fixture only. This is NOT evidence of SMB exclusion.
                with patch.object(pub, 'publication_exclusion', return_value=nullcontext()):
                    return f.sync()
            return f.sync()

    def test_schedule_boundaries_and_timezone(self):
        for clock, phase in [('12:29:59','before'),('12:30:00','open'),('12:54:59','open'),('12:55:00','closed'),('14:00:00','closed')]:
            self.assertEqual(notices.phase(at(clock)), phase)
        with self.assertRaisesRegex(Exception, 'TIMEZONE_REQUIRED'): notices.phase(datetime(2026,11,16))

    def test_no_publish_before_or_after_window_pending_retained(self):
        f=self.fixture(); f.ai(); before=f.hashes()
        for clock in ('12:29:59','12:55:00','14:00:00'):
            self.assertEqual(self.tick(f,clock)['status'], 'publication_pending')
            self.assertEqual(before, f.hashes())
        self.assertEqual(f.count('AI-1'),1)

    def test_1230_publish_retry_same_version_and_fresh_report(self):
        f=self.fixture(); f.ai()
        self.tick(f,'12:30:00',simulated_exclusion=True)
        before=f.hashes(); self.tick(f,'12:32:00',simulated_exclusion=True)
        self.assertEqual(before,f.hashes()); self.assertEqual(f.count('AI-1',f.mine),1)
        f.ai('NEW-REPORT','test:new')
        self.tick(f,'14:00:00'); self.assertEqual(f.count('NEW-REPORT',f.mine),0)
        self.tick(f,'12:30:00',day='2026-11-17',simulated_exclusion=True)
        self.assertEqual(f.count('NEW-REPORT',f.mine),1)

    def test_network_failure_then_1240_recovery_simulated(self):
        f=self.fixture(); f.ai(); before=f.hashes()
        with patch.object(pub,'stage_bytes',side_effect=OSError('OFFLINE')):
            with self.assertRaises(OSError): self.tick(f,'12:30:00',simulated_exclusion=True)
        self.assertEqual(before,f.hashes())
        self.tick(f,'12:40:00',simulated_exclusion=True)
        self.assertEqual(f.count('AI-1',f.mine),1)

    def test_second_changed_version_inside_window_publishes_once(self):
        f=self.fixture(); f.ai(); self.tick(f,'12:30:00',simulated_exclusion=True)
        f.ai('NEW-WITHIN-WINDOW','test:within')
        self.tick(f,'12:42:00',simulated_exclusion=True)
        before=f.hashes(); self.tick(f,'12:44:00',simulated_exclusion=True)
        self.assertEqual(before,f.hashes()); self.assertEqual(f.count('NEW-WITHIN-WINDOW',f.mine),1)

    def test_daily_policy_rejects_widened_window(self):
        f=self.fixture(); cfg=json.loads(f.config.read_text('utf8'))
        cfg['outbound_publication']['publication_window']['end']='14:00'
        f.config.write_text(json.dumps(cfg),'utf8')
        with self.assertRaisesRegex(Exception,'WINDOW_INVALID'): f.sync()

    def test_late_new_report_does_not_raise_missed_window_notice(self):
        f=self.fixture(); result=f.ai()
        with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db,db:
            db.execute("UPDATE edits SET created='2026-11-16T10:30:00+00:00' WHERE operation=?",(result['operation'],))
        self.tick(f,'14:00:00')
        with patch.object(notices,'now',return_value=at('14:00:00')), patch.object(notices,'notify'):
            self.assertEqual(pub.poll_existing_tick('success',config=f.config,runtime=f.runtime)['pending_operations'],0)
        self.assertEqual(f.count('AI-1'),1)

    def test_real_gate_blocks_stage_replace_and_lunch_is_not_exclusion(self):
        f=self.fixture(); f.ai(); before=f.hashes()
        with patch.object(pub,'replace_source') as replace, patch.object(pub,'stage_bytes') as stage:
            with self.assertRaisesRegex(Exception,'SMB_EXCLUSION_UNVERIFIED'): self.tick(f,'12:30:00')
        replace.assert_not_called(); stage.assert_not_called(); self.assertEqual(before,f.hashes())

    def test_owner_file_conflict_readback_and_crash_recovery(self):
        f=self.fixture(); f.ai(); owner=f.mine.with_name('~$'+f.mine.name); owner.write_bytes(b'OPEN')
        with self.assertRaisesRegex(Exception,'EXCEL_OWNER_FILE'): self.tick(f,'12:30:00',simulated_exclusion=True)
        owner.unlink()
        def crash(name):
            if name=='before_ack': raise previous.Crash('receipt interruption')
        with patch.object(pub,'checkpoint',crash), self.assertRaises(previous.Crash):
            self.tick(f,'12:32:00',simulated_exclusion=True)
        self.tick(f,'12:34:00',simulated_exclusion=True)
        self.assertEqual(f.count('AI-1',f.mine),1)
        f.alter(lambda b:setattr(b['710']['E4'],'value','CONFLICT'))
        with self.assertRaisesRegex(Exception,'IDENTITY_ROW_CHANGED'): self.tick(f,'12:36:00',simulated_exclusion=True)

    def test_invalid_readback_is_uncertain_never_auto_republished(self):
        f=self.fixture(); f.ai()
        def corrupt(stage):
            if stage=='after_replace': f.mine.write_bytes(b'CORRUPT')
        with patch.object(pub,'checkpoint',corrupt), self.assertRaises(Exception):
            self.tick(f,'12:30:00',simulated_exclusion=True)
        with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT status FROM maintenance_publications_v1 ORDER BY created DESC LIMIT 1').fetchone()[0],'uncertain')
        self.assertEqual(f.count('AI-1'),1)

    def test_no_pending_noop_and_ordinary_inbound(self):
        f=self.fixture(); before=f.hashes()
        self.tick(f,'14:00:00'); self.assertEqual(before,f.hashes())
        f.alter(lambda b:setattr(b['851']['E3'],'value','MINE-EDIT'))
        self.tick(f,'14:02:00'); self.assertEqual(f.count('MINE-EDIT'),1)

    def test_pending_reports_do_not_stop_ordinary_inbound_outside_window(self):
        f=self.fixture(); f.ai(); f.alter(lambda b:setattr(b['851']['E3'],'value','MINE-INBOUND-WHILE-PENDING'))
        mine_before=f.hashes()[1]
        self.assertEqual(self.tick(f,'14:00:00')['status'],'publication_pending')
        self.assertEqual(f.count('AI-1'),1); self.assertEqual(f.count('MINE-INBOUND-WHILE-PENDING'),1)
        self.assertEqual(f.hashes()[1],mine_before); before=f.hashes()
        self.tick(f,'14:02:00'); self.assertEqual(before,f.hashes())
        self.tick(f,'12:30:00',day='2026-11-17',simulated_exclusion=True)
        self.assertEqual(f.count('AI-1',f.mine),1); self.assertEqual(f.count('MINE-INBOUND-WHILE-PENDING',f.mine),1)

    def test_unmigrated_no_pending_does_not_write_remote(self):
        f=previous.Fixture(self._testMethodName, bootstrap=False); self.f=f
        cfg=json.loads(f.config.read_text('utf8')); cfg['outbound_publication'].update(policy='daily_1230',alert_owner='455740857',publication_window={'start':'12:30','end':'12:55'})
        f.config.write_text(json.dumps(cfg),'utf8'); before=f.hashes()
        self.tick(f,'12:30:00'); self.assertEqual(before,f.hashes())
        self.assertEqual(f.inspect(f.local)['uuid_count'],0)

    def test_notice_once_retry_unavailable_owner_only_and_no_data_leak(self):
        f=self.fixture(); store=Mock(); store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('455740857','999'))
        sender=Mock(); sender.message.return_value=12345
        with closing(previous.writer.open_audit(f.runtime)) as db:
            notices.observe(db,2,'SOURCE_UNAVAILABLE',moment=at('12:54:00'))
            for clock in ('12:54:00','12:55:00','12:56:00','14:00:00'):
                notices.notify(db,dict(alert_owner='455740857'),moment=at(clock),authorization=store,sender_factory=lambda:sender)
            sender.message.assert_called_once(); self.assertEqual(sender.message.call_args.args[0],'455740857')
            self.assertNotIn('AI-1',sender.message.call_args.args[1])
            notices.observe(db,2,'SOURCE_UNAVAILABLE',moment=at('12:55:00','2026-11-17'))
            sender.message.side_effect=TransportUnavailable('offline')
            notices.notify(db,dict(alert_owner='455740857'),moment=at('12:55:00','2026-11-17'),authorization=store,sender_factory=lambda:sender)
            sender.message.side_effect=None
            for clock in ('12:57:00','12:59:00'):
                notices.notify(db,dict(alert_owner='455740857'),moment=at(clock,'2026-11-17'),authorization=store,sender_factory=lambda:sender)
            self.assertEqual(sender.message.call_count,3)
            self.assertEqual(db.execute("SELECT COUNT(*) FROM delivery_alert_receipt WHERE status='sent'").fetchone()[0],2)

    def test_uncertain_notice_never_duplicate_and_revocation_blocks(self):
        f=self.fixture(); store=Mock(); store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('455740857',))
        sender=Mock(); sender.message.return_value=None
        with closing(previous.writer.open_audit(f.runtime)) as db:
            notices.observe(db,1,'PUBLICATION_READBACK_UNCERTAIN',moment=at('12:55:00'))
            for clock in ('12:55:00','12:57:00'):
                notices.notify(db,dict(alert_owner='455740857'),moment=at(clock),authorization=store,sender_factory=lambda:sender)
            sender.message.assert_called_once()
            self.assertEqual(db.execute('SELECT status FROM delivery_alert_receipt').fetchone()[0],'uncertain')
            notices.observe(db,1,'SOURCE_UNAVAILABLE',moment=at('12:55:00','2026-11-17'))
            store.resolve_active_recipients.return_value=RecipientSetResolution('ready',('999',))
            notices.notify(db,dict(alert_owner='455740857'),moment=at('12:55:00','2026-11-17'),authorization=store,sender_factory=lambda:sender)
            sender.message.assert_called_once()

    def test_poll_on_outage_backoff_and_noop_has_no_notice(self):
        f=self.fixture(); f.ai()
        with patch.object(notices,'now',return_value=at('12:55:00')), patch.object(notices,'notify') as notify:
            result=pub.poll_existing_tick('source_unavailable',config=f.config,runtime=f.runtime)
            self.assertEqual(result['pending_operations'],1); notify.assert_called_once()
        with closing(previous.writer.open_audit(f.runtime)) as db:
            self.assertEqual(db.execute('SELECT reason FROM maintenance_publication_watch_v1 ORDER BY updated DESC LIMIT 1').fetchone()[0],'SOURCE_UNAVAILABLE')

    def test_corrupt_journal_preserved_and_integrity_notice_is_durable(self):
        f=self.fixture(); f.ai(); path=f.runtime/'audit.sqlite3'; path.write_bytes(b'CORRUPT-JOURNAL')
        before=f.hashes()
        with patch.object(notices,'now',return_value=at('12:55:00')), patch.object(notices,'notify') as notify:
            result=pub.poll_existing_tick('partial',config=f.config,runtime=f.runtime)
            self.assertEqual(result['pending_operations'],-1); notify.assert_called_once()
        self.assertEqual(path.read_bytes(),b'CORRUPT-JOURNAL'); self.assertEqual(before,f.hashes())
        with closing(sqlite3.connect(f.runtime/'publication-notice-fallback.sqlite3')) as db:
            self.assertEqual(db.execute('SELECT pending,reason FROM maintenance_publication_watch_v1').fetchone(),
                             (-1,'PUBLICATION_INTEGRITY_REVIEW_REQUIRED'))

    def test_existing_cron_calls_notice_even_offline_backoff_and_notice_failure(self):
        from integrations.hermes.mine_sync.test_mine_file_sync_reliability import ReliabilityTests, sync
        fixture=ReliabilityTests('test_outage_repeated_cron_ticks_and_max_backoff'); fixture.setUp()
        try:
            with patch.object(sync,'publication_notice_tick') as notice:
                fixture.outage(); self.assertEqual(notice.call_args.args[1]['status'],'source_unavailable')
                with patch.object(sync,'utcnow',return_value=fixture.stamp(1001)):
                    notice.side_effect=RuntimeError('notification outage')
                    self.assertEqual(sync.run(fixture.args()),0)
                self.assertEqual(notice.call_count,2)
                self.assertEqual(fixture.state()['health_status'],'backoff_skip')
        finally: fixture.tearDown()

    def test_notes_unknown_parts_second_append_and_history_edit_denied(self):
        f=self.fixture()
        book=identity.load(f.mine.read_bytes(),preserve=True); identity.enable(book)
        for n in range(2):
            saved=service.append_record(book,dict(canonical='710',sheet='710',code='710',date=previous.DAY,mechanic='TEST',description='MULTI-'+str(n),parts=''),recorder='TEST')
            identity.attach(book,saved,('e' if n else 'f')*32,0)
        data=identity.dump(book); book.close()
        check=identity.load(data,preserve=True)
        saved=service.append_record(check,dict(canonical='851',sheet='851',code='851',date=previous.DAY,mechanic='TEST',description='SECOND',parts=''),recorder='TEST')
        identity.attach(check,saved,'d'*32,0); second=identity.dump(check); check.close()
        a,b=ooxml.package(data),ooxml.package(second)
        for name in a:
            if 'comment' in name.lower() or name.endswith('.vml'): self.assertEqual(a[name],b[name])
        # Append to the SAME sheet too: extend its note list and VML instead
        # of regenerating or overwriting existing notes/shapes.
        check=identity.load(second,preserve=True)
        saved=service.append_record(check,dict(canonical='710',sheet='710',code='710',date=previous.DAY,mechanic='TEST',description='THIRD',parts=''),recorder='TEST')
        identity.attach(check,saved,'c'*32,0); third=identity.dump(check); check.close()
        self.assertEqual(len(identity.inventory(check:=identity.load(third,preserve=True))),4); check.close()
        check=identity.load(second,preserve=True); check['710']['E3']='UNAUTHORIZED'
        with self.assertRaisesRegex(Exception,'HISTORICAL_EDIT_DENIED'): identity.dump(check)
        check.close()

    def test_foreign_rich_note_and_unknown_package_component_retained(self):
        f=self.fixture()
        def note(book):
            from openpyxl.comments import Comment
            book['710']['C3'].comment=Comment('KEEP-ME','Human')
        f.alter(note)
        parts=ooxml.package(f.mine.read_bytes())
        name=next(n for n in parts if n.startswith('xl/comments/'))
        parts[name]=parts[name].replace(b'<text><t>KEEP-ME</t></text>',b'<text><r><rPr><b/></rPr><t>KEEP-ME</t></r></text>')
        parts['xl/unknown-setting.xml']=b'<setting value="preserve exactly"/>'
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w') as archive:
            for n,raw in parts.items(): archive.writestr(n,raw)
        book=identity.load(stream.getvalue(),preserve=True)
        saved=service.append_record(book,dict(canonical='710',sheet='710',code='710',date=previous.DAY,mechanic='TEST',description='RICH-NOTE',parts=''),recorder='TEST')
        identity.attach(book,saved,'b'*32,0); output=identity.dump(book); book.close()
        after=ooxml.package(output)
        self.assertEqual(after['xl/unknown-setting.xml'],parts['xl/unknown-setting.xml'])
        self.assertIn(b'<r><rPr><b/></rPr><t>KEEP-ME</t></r>',after[name])

    def test_native_note_requires_exact_wrapper_anchor_and_journal(self):
        f = self.fixture()
        cfg = json.loads(f.config.read_text('utf8'))
        cfg['outbound_publication']['operating_contract'] = pub.OWNER_LUNCH_CONTRACT
        f.config.write_text(json.dumps(cfg), 'utf8')
        f.ai()
        self.assertEqual(self.tick(f, '12:30:00')['status'], 'merged')
        parts = ooxml.package(f.mine.read_bytes())
        original = 'xl/comments/comment1.xml'
        native = 'xl/comments1.xml'
        rels = 'xl/worksheets/_rels/sheet1.xml.rels'
        parts[rels] = parts[rels].replace(b'/xl/comments/comment1.xml', b'../comments1.xml')
        types = '[Content_Types].xml'
        parts[types] = parts[types].replace(b'/xl/comments/comment1.xml', b'/xl/comments1.xml')
        worksheet = 'xl/worksheets/sheet1.xml'
        unknown = b'xmlns:uat="urn:komatso:uat" uat:setting="preserve"'
        parts[worksheet] = parts[worksheet].replace(b'<dimension ', b'<dimension ' + unknown + b' ')
        comments = ET.fromstring(parts.pop(original))
        q = '{' + ooxml.MAIN + '}'
        note = comments.find(f'{q}commentList/{q}comment')
        payload = note.findtext(f'{q}text/{q}t')
        parts[native] = (
            f'<comments xmlns="{ooxml.MAIN}" xmlns:xr="http://schemas.microsoft.com/office/spreadsheetml/2014/revision">'
            '<authors><author>Author</author></authors><commentList>'
            f'<comment ref="{note.attrib["ref"]}" authorId="0" shapeId="0" '
            'xr:uid="{00000000-0006-0000-0400-000001000000}">'
            '<text><r><rPr><sz val="11"/><color theme="1"/><rFont val="Calibri"/>'
            f'<family val="2"/><scheme val="minor"/></rPr><t>{escape(payload)}</t></r></text>'
            '</comment></commentList></comments>').encode()

        def workbook(replacements):
            output = io.BytesIO()
            with zipfile.ZipFile(output, 'w') as archive:
                for name, content in (parts | replacements).items():
                    archive.writestr(name, content)
            return output.getvalue()

        native_data = workbook({})
        changed_identity = json.loads(payload[len(identity.PREFIX):])
        changed_identity['operation'] = 'f'*32
        changed_identity['id'] = uuid.uuid5(identity.NAMESPACE, 'f'*32 + ':0:710').hex
        drawing = 'xl/drawings/commentsDrawing1.vml'
        comment_xml = parts[native]
        before = f.local.read_bytes()
        for replacements, error in (
            ({native:comment_xml.replace(b'Calibri', b'Arial')}, 'UNSUPPORTED_EXISTING_NOTE'),
            ({drawing:parts[drawing].replace(b'visibility:hidden', b'visibility:visible')}, 'UNSUPPORTED_EXISTING_NOTE'),
            ({drawing:parts[drawing].replace(b':Row>3<', b':Row>4<')}, 'UNSUPPORTED_EXISTING_NOTE'),
            ({native:comment_xml.replace(payload.encode(), (identity.PREFIX + base.encoded(changed_identity)).encode())},
             'IDENTITY_UNKNOWN_OR_CHANGED'),
            ({native:comment_xml.replace(b'<commentList>', b'<commentList>' + ooxml.elements(comment_xml, 'comment')[0].group())},
             'IDENTITY_DUPLICATE'),
            ({native:comment_xml.replace(payload.encode(), b'Human note')}, 'IDENTITY_PUBLISHED_RECORD_MISSING'),
            ({native:comment_xml.replace(b'"row_sha256":"', b'"row_sha256":"0')}, 'IDENTITY_ROW_CHANGED_OR_MOVED'),
            ({native:comment_xml.replace(b'{"group":', b'{ "group":')}, 'IDENTITY_ROW_CHANGED_OR_MOVED'),
        ):
            with self.subTest(error=error):
                fake = workbook(replacements)
                self.assertNotEqual(fake, native_data)
                f.mine.write_bytes(fake)
                with self.assertRaisesRegex(Exception, error):
                    self.tick(f, '12:30:00')
                self.assertEqual(f.local.read_bytes(), before)
                self.assertEqual(f.mine.read_bytes(), fake)

        f.local.write_bytes(native_data)
        with self.assertRaisesRegex(Exception, 'UNJOURNALED_LOCAL_CHANGE'):
            f.ai('UNVERIFIED-LOCAL', 'test:unverified-local')
        self.assertEqual(f.local.read_bytes(), native_data)
        f.local.write_bytes(before)
        f.mine.write_bytes(native_data)
        self.assertEqual(self.tick(f, '12:30:00')['status'], 'imported')
        f.ai('AFTER-SAVE-AS', 'test:after-save-as')
        self.assertEqual(self.tick(f, '12:30:00')['status'], 'merged')
        saved = f.mine.read_bytes()
        self.assertEqual(self.tick(f, '12:32:00')['status'], 'unchanged')
        self.assertEqual(f.mine.read_bytes(), saved)
        after = ooxml.package(saved)
        self.assertIn(ooxml.elements(comment_xml, 'comment')[0].group(), after[native])
        self.assertIn(unknown, after[worksheet])

    def test_native_63_sheets_append_25_migrate_and_all_parts_retained(self):
        evidence=json.loads((previous.ROOT/'reports/sync/AI-EXCEL-SYNC-TESTS.json').read_text('utf8'))
        frozen=Path(evidence['run_root'])/'private-operational-snapshots'
        original=(frozen/'function.xlsx').read_bytes()
        retained=HERE/'native-history'; retained.mkdir(exist_ok=True); (retained/'backups').mkdir(exist_ok=True)
        with closing(sqlite3.connect((frozen/'audit.sqlite3').resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row
            state=json.loads(db.execute('SELECT payload FROM maintenance_baseline_events').fetchone()[0])
            rows=db.execute('SELECT * FROM edits WHERE rowid>?',(state['cutoff'],)).fetchall()
            hashes={state['initial_hash']}
            for row in rows:
                hashes.update((row['before_hash'],row['after_hash']))
                result=json.loads(row['result'])
                if result.get('mine_hash'): hashes.add(result['mine_hash'])
            for sha in hashes: (retained/'backups'/(sha+'.xlsx')).write_bytes(base.snapshot(previous.ROOT/'runtime/maintenance-entry',sha))
            _,mine_hash,known,specs,_=pub.replay_branch(db,Path(state['source']),original,retained)
        native=base.snapshot(retained,mine_hash)
        book=identity.load(native,preserve=True); identity.enable(book)
        from .maintenance_report import apply_records
        for operation in dict.fromkeys(s[0] for s in specs):
            apply_records(book,[s[2] for s in specs if s[0]==operation],operation=operation)
        self.assertEqual(identity.inventory(book),known); self.assertEqual(len(known),25)
        output=identity.dump(book); book.close(); (retained/'native-migrated.xlsx').write_bytes(output)
        a,b=ooxml.package(native),ooxml.package(output)
        self.assertTrue(set(a)<=set(b))
        protected=[n for n in a if n.startswith(('xl/printerSettings/','xl/theme/'))]
        for name in protected: self.assertEqual(a[name],b[name])
        self.assertEqual(len([n for n in protected if n.startswith('xl/printerSettings/')]),30)
        for token in (b'xr:revisionPtr',b'mc:Ignorable',b'<extLst'):
            self.assertEqual(a['xl/workbook.xml'].count(token),b['xl/workbook.xml'].count(token))
        checked=identity.load(output,preserve=True)
        self.assertEqual(len(checked.sheetnames),63); self.assertEqual(identity.inventory(checked),known); checked.close()
        (HERE/'native-package-result.json').write_text(json.dumps(dict(source_sha256=base.digest(native),output_sha256=base.digest(output),
            historical_rows=25,batches=4,sheets=63,original_parts=len(a),added_parts=sorted(set(b)-set(a)),
            changed_parts=[n for n in a if a[n]!=b[n]],unchanged_parts=[n for n in a if a[n]==b[n]],
            native_excel='UNKNOWN',SMB='UNKNOWN',artifact=str(retained/'native-migrated.xlsx')),indent=2),'utf8')


if __name__=='__main__':
    runner=unittest.TextTestRunner(verbosity=2)
    result=runner.run(unittest.defaultTestLoader.loadTestsFromTestCase(DailyTests))
    (HERE/'unit-results.json').write_text(json.dumps(dict(tests=result.testsRun,failures=len(result.failures),errors=len(result.errors),
        skipped=len(result.skipped),scenarios=RESULTS,simulated_exclusion_is_not_SMB_acceptance=True),indent=2),'utf8')
    raise SystemExit(0 if result.wasSuccessful() else 1)
