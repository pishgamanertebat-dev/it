"""Candidate acceptance: real Writer/Journal, isolated NTFS workbooks, fake Bale.

Run with --evidence PATH to retain complete synthetic workbook/journal evidence.
No installed job, operational workbook, Share, sender or Gateway is invoked.
"""
from contextlib import closing
from copy import copy
from datetime import datetime, timezone
import importlib.util
import io
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import unittest
from unittest.mock import patch
import uuid

import openpyxl

from . import entry_service as writer, maintenance_baseline as baseline
from . import maintenance_report as reports, maintenance_identity as identity
from . import maintenance_publication as publication
from .maintenance_sync import guard_snapshot

ROOT = writer.ROOT
RUN = publication.CANDIDATE_ROOT / ('run-' + uuid.uuid4().hex)
DAY = '1405/08/25'
EVIDENCE = []


class Crash(BaseException):
    pass


class Fixture:
    def __init__(self, name, *, bootstrap=True):
        self.root = RUN / name
        self.local = self.root / 'function' / publication.WORKBOOK
        self.mine = self.root / 'mine' / publication.WORKBOOK
        self.runtime = self.root / 'journal'
        self.config = self.root / 'entry.json'
        self.staged = self.root / 'incoming.xlsx'
        for folder in (self.local.parent, self.mine.parent, self.runtime):
            folder.mkdir(parents=True)
        book = openpyxl.Workbook()
        book.remove(book.active)
        for code, name in [('710','دامپ 710'), ('851','بیل 851'), ('متفرقه','متفرقه')]:
            sheet = book.create_sheet(code)
            sheet['A1'] = name
            sheet.append(['تاریخ','ثبت کننده','نام مکانیک','کد مکانیزم' if code != 'متفرقه' else 'قسمت',
                          'نوع خرابی','قطعات مصرفی'])
            sheet.append(['1405/08/20','SYNTHETIC-MINE','TEST',code,'BASE-'+code,None])
            for column in 'ABCDEF':
                sheet.column_dimensions[column].width = 45
            sheet.print_area = 'A1:F3'
        book.save(self.local)
        book.close()
        self.mine.write_bytes(self.local.read_bytes())
        settings = dict(source=str(self.local), allowed_users=['synthetic'], sync_policy='upstream_copy',
                        outbound_publication=dict(enabled=True,mode='isolated_candidate',
                            isolation_root=str(self.root),publication_window=dict(start='00:00',end='23:59')))
        self.config.write_text(json.dumps(settings), 'utf8')
        with closing(writer.open_audit(self.runtime)):
            pass
        baseline.initialize(self.mine, expected_hash=self.hashes()[1], approved_by='project-owner',
                            decision='synthetic isolated owner baseline', config=self.config, runtime=self.runtime)
        self.settings = dict(source=str(self.local),recorder='SYNTHETIC-BALE',department='تعمیرگاه',
                             mechanic='TEST',scope='all')
        self.outcomes = []
        if bootstrap:
            self.sync()

    def ai(self, text='AI-1', source='test:1', devices=('دامپتراک710',)):
        raw = 'گزارش مکانیکی 24 آبان 1405\n' + '\n'.join('-'+d+': '+text+'.' for d in devices)
        with patch.object(reports, 'permission', return_value=self.settings):
            return reports.receive('synthetic','synthetic',source,raw,config=self.root/'policy.json',
                                   entry_config=self.config,runtime=self.runtime,day=DAY,
                                   ledger=self.root/'absent.sqlite3')

    def sync(self):
        try:
            self.staged.write_bytes(self.mine.read_bytes())
            result = guard_snapshot(self.staged, self.local, upstream=self.mine,
                                    config=self.config,runtime=self.runtime)
            self.outcomes.append(result)
            return result
        except BaseException as exc:
            self.outcomes.append(dict(error=type(exc).__name__+': '+str(exc)))
            raise

    def alter(self, fn, path=None):
        target = path or self.mine
        book = openpyxl.load_workbook(target)
        try:
            fn(book)
            book.save(target)
        finally:
            book.close()

    def hashes(self):
        return tuple(baseline.digest(p.read_bytes()) for p in (self.local,self.mine))

    def inspect(self, path):
        book = openpyxl.load_workbook(io.BytesIO(path.read_bytes()))
        try:
            rows, ids = {}, []
            for sheet in book:
                rows[sheet.title] = []
                for row in sheet.iter_rows(min_row=3):
                    if any(c.value is not None for c in row):
                        rows[sheet.title].append(dict(row=row[0].row, values=[c.value for c in row]))
                    for cell in row:
                        if cell.comment and cell.comment.text.startswith(identity.PREFIX):
                            ids.append(json.loads(cell.comment.text[len(identity.PREFIX):]))
            try:
                identity.inventory(book)
                validation = 'valid'
            except Exception as exc:
                validation = str(exc)
            return dict(sha256=baseline.digest(path.read_bytes()),sheets=book.sheetnames,
                        rows=rows,record_count=sum(map(len,rows.values())),
                        identities=ids,uuid_count=len(ids),unique_uuid_count=len({i['id'] for i in ids}),
                        validation=validation)
        finally:
            book.close()

    def count(self, text, path=None):
        state = self.inspect(path or self.local)
        return sum(text in str(r['values'][4]) for rows in state['rows'].values() for r in rows)

    def evidence(self, name, result):
        with closing(sqlite3.connect(self.runtime/'audit.sqlite3')) as db:
            db.row_factory = sqlite3.Row
            receipts = [dict(r) for r in db.execute('SELECT * FROM maintenance_publications_v1')]
            events = [dict(r) for r in db.execute('SELECT * FROM maintenance_publication_events_v1')]
            edits = [dict(r) for r in db.execute('SELECT operation,status,before_hash,after_hash FROM edits')]
        return dict(id=name,verdict=result,artifacts=str(self.root.relative_to(ROOT)),
                    local=self.inspect(self.local),mine=self.inspect(self.mine),outcomes=self.outcomes,
                    publications=receipts,events=events,journal=edits)


class CandidateTests(unittest.TestCase):
    def fixture(self, **kwargs):
        self.f = Fixture(self._testMethodName, **kwargs)
        return self.f

    def tearDown(self):
        if hasattr(self, 'f'):
            failed = any(test is self for test, _ in self._outcome.result.failures + self._outcome.result.errors)
            item=self.f.evidence(self._testMethodName, 'FAIL' if failed else getattr(self,'scenario_verdict','PASS'))
            if hasattr(self,'limitation'):item['critical_limitation']=self.limitation
            EVIDENCE.append(item)

    def preserved_conflict(self, f, pattern):
        before = f.hashes()
        with self.assertRaisesRegex(Exception, pattern):
            f.sync()
        self.assertEqual(before, f.hashes())

    def crash_after_publish(self, f, stage='after_replace'):
        def crash(name):
            if name == stage:
                raise Crash(stage)
        with patch.object(publication, 'checkpoint', crash), self.assertRaises(Crash):
            f.sync()

    def one_each(self, f, text='AI-1', count=1):
        for path in (f.mine, f.local):
            self.assertEqual(f.count(text,path), count)
            state = f.inspect(path)
            self.assertEqual(state['validation'],'valid')
            self.assertEqual(state['uuid_count'],state['unique_uuid_count'])

    def test_T1_publish_then_ack(self):
        f=self.fixture(); f.ai(); f.sync(); self.one_each(f)
        before=f.hashes();f.sync();self.assertEqual(before,f.hashes())

    def test_T2_stale_inbound_before_publication(self):
        f=self.fixture();f.ai();before=f.local.read_bytes()
        with patch.object(publication,'window_open',return_value=False):
            self.preserved_conflict(f,'WINDOW_CLOSED')
        self.assertEqual(before,f.local.read_bytes());self.assertEqual(f.count('AI-1',f.mine),0)
        f.sync();self.one_each(f)

    def test_T3_independent_mine_edit_and_append(self):
        f=self.fixture();f.ai()
        f.alter(lambda b:setattr(b['851']['E3'],'value','MINE-INDEPENDENT'))
        f.sync();self.one_each(f)
        for path in (f.local,f.mine):self.assertEqual(f.count('MINE-INDEPENDENT',path),1)

    def test_T4_edit_published_before_ack(self):
        f=self.fixture();f.ai();self.crash_after_publish(f)
        f.alter(lambda b:setattr(b['710']['E4'],'value','MINE-CORRECTED'))
        self.preserved_conflict(f,'IDENTITY_ROW_CHANGED')
        self.assertEqual(f.count('AI-1'),1);self.assertEqual(f.count('MINE-CORRECTED',f.mine),1)
        self.assertEqual(f.inspect(f.mine)['uuid_count'],1)

    def test_T5_network_outage_before_publication(self):
        f=self.fixture();f.ai()
        with patch.object(publication,'stage_bytes',side_effect=OSError('SIMULATED_NETWORK_UNAVAILABLE')):
            self.preserved_conflict(f,'NETWORK_UNAVAILABLE')
        f.sync();self.one_each(f)

    def test_T6_reconnect_and_retry(self):
        f=self.fixture();f.ai()
        with patch.object(publication,'stage_bytes',side_effect=OSError('NETWORK_DISCONNECT')):
            self.preserved_conflict(f,'NETWORK_DISCONNECT')
        f.sync();before=f.hashes();f.sync();self.assertEqual(before,f.hashes());self.one_each(f)

    @unittest.skipUnless(os.name=='nt','Requires Windows NTFS sharing handles')
    def test_T7_destination_lock_and_retry(self):
        f=self.fixture();f.ai()
        with baseline.baseline_reader(f.mine):
            self.preserved_conflict(f,'ذخیره|WinError|denied|being used')
        f.sync();self.one_each(f)

    def test_T8_disconnect_during_staging(self):
        f=self.fixture();f.ai()
        def broken(path,data):
            path.write_bytes(data[:100]);raise OSError('DISCONNECTED_DURING_STAGE')
        with patch.object(publication,'stage_bytes',broken):
            self.preserved_conflict(f,'DISCONNECTED')
        self.assertFalse(list(f.mine.parent.glob('*.partial')))
        f.sync();self.one_each(f)

    def test_T9_repeat_same_operation(self):
        f=self.fixture();first=f.ai();before=f.hashes();self.assertEqual(first,f.ai());self.assertEqual(before,f.hashes())
        f.sync();before=f.hashes();self.assertEqual(first,f.ai());f.sync();self.assertEqual(before,f.hashes());self.one_each(f)

    def test_T10_multiple_reports_multiple_devices(self):
        f=self.fixture()
        for n in range(3):f.ai('AI-BATCH-'+str(n),'test:'+str(n),('دامپتراک710','بیل851'))
        f.alter(lambda b:b['851'].append(['1405/08/24','MINE','TEST','851','MINE-NEW',None]))
        f.sync();self.one_each(f,'AI-BATCH-',6);self.assertEqual(f.inspect(f.mine)['uuid_count'],6)
        for path in (f.mine,f.local):self.assertEqual(f.count('MINE-NEW',path),1)
        before=f.hashes();f.sync();self.assertEqual(before,f.hashes())

    def test_T11_external_unguarded_overwrite(self):
        f=self.fixture();saved=f.ai();f.local.write_bytes(f.mine.read_bytes())
        self.preserved_conflict(f,'UNJOURNALED_LOCAL_CHANGE')
        with self.assertRaises(Exception):f.ai()
        with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db:
            after=db.execute('SELECT after_hash FROM edits WHERE operation=?',(saved['operation'],)).fetchone()[0]
        recovered=f.root/'evidence-recovered.xlsx';recovered.write_bytes(baseline.snapshot(f.runtime,after))
        self.assertEqual(f.count('AI-1',recovered),1)
        self.assertEqual(f.count('AI-1'),0)  # Actual loss in active fixture is explicit.

    def test_T12_resave_before_ack(self):
        f=self.fixture();f.ai();self.crash_after_publish(f)
        f.alter(lambda b:setattr(b['710'].sheet_view,'zoomScale',85))
        f.sync();self.one_each(f);before=f.hashes();f.sync();self.assertEqual(before,f.hashes())

    def test_X3_delete_after_ack(self):
        f=self.fixture();f.ai();f.sync();f.alter(lambda b:b['710'].delete_rows(4))
        self.preserved_conflict(f,'PUBLISHED_RECORD_MISSING');self.assertEqual(f.count('AI-1'),1)
        self.assertEqual(f.count('AI-1',f.mine),0)  # Mine deletion remains evidence.

    def test_crash_after_readback_and_before_ack(self):
        for stage in ('after_readback','before_ack'):
            f=Fixture(self._testMethodName+'-'+stage);self.f=f
            f.ai();self.crash_after_publish(f,stage);f.sync();self.one_each(f)

    def test_restart_fresh_process_recovers_publication(self):
        f=self.fixture();f.ai();self.crash_after_publish(f)
        f.staged.write_bytes(f.mine.read_bytes())
        command=[sys.executable,'-B','-X','utf8','-c',
                 'from tools.fleet.repairs.maintenance_sync import guard_snapshot;import sys;'
                 'print(guard_snapshot(sys.argv[1],sys.argv[2],upstream=sys.argv[3],config=sys.argv[4],runtime=sys.argv[5]))',
                 str(f.staged),str(f.local),str(f.mine),str(f.config),str(f.runtime)]
        run=subprocess.run(command,cwd=ROOT,capture_output=True,text=True,encoding='utf8',timeout=60)
        self.assertEqual(run.returncode,0,run.stderr);self.one_each(f)
        f.outcomes.append(dict(fresh_process_stdout=run.stdout))

    def test_duplicate_operation_id_with_different_payload(self):
        f=self.fixture();f.ai();before=f.hashes()
        with self.assertRaises(Exception):f.ai('ALTERED-PAYLOAD')
        self.assertEqual(before,f.hashes());f.sync();self.one_each(f)

    def test_duplicate_uuid_inside_workbook(self):
        f=self.fixture();f.ai();f.sync()
        def duplicate(book):
            s=book['710']
            for column in range(1,7):
                s.cell(5,column).value=s.cell(4,column).value
                s.cell(5,column).comment=copy(s.cell(4,column).comment)
        f.alter(duplicate);self.preserved_conflict(f,'IDENTITY_DUPLICATE')

    def test_shared_cell_correction_conflicts(self):
        f=self.fixture();f.ai();f.sync()
        f.alter(lambda b:setattr(b['710']['C4'],'value','CHANGED-MECHANIC'))
        self.preserved_conflict(f,'IDENTITY_ROW_CHANGED')

    def test_remove_sheet(self):
        f=self.fixture();f.ai();f.sync();f.alter(lambda b:b.remove(b['710']))
        self.preserved_conflict(f,'SHEET_LIST_CHANGED')

    def test_remove_metadata_note(self):
        f=self.fixture();f.ai();f.sync();f.alter(lambda b:setattr(b['710']['A4'],'comment',None))
        self.preserved_conflict(f,'PUBLISHED_RECORD_MISSING')

    def test_remove_schema(self):
        f=self.fixture();f.ai();f.sync();f.alter(lambda b:b.defined_names.pop(identity.MARKER))
        self.preserved_conflict(f,'SCHEMA_MISSING')

    def test_insert_and_sort_complete_rows(self):
        f=self.fixture();f.ai('AI-B','test:1');f.ai('AI-A','test:2');f.sync()
        def reorder(book):
            s=book['710'];s.insert_rows(3)
            values=[[copy(c) for c in s[r]] for r in (5,6)]
            for target,cells in zip((6,5),values):
                for cell in cells:
                    dest=s.cell(target,cell.column);dest.value=cell.value;dest.comment=copy(cell.comment);dest._style=copy(cell._style)
        f.alter(reorder);f.sync();self.one_each(f,'AI-',2)

    def test_sort_partial_columns_conflicts(self):
        f=self.fixture();f.ai('AI-B','test:1');f.ai('AI-A','test:2');f.sync()
        def partial(book):
            s=book['710'];s['E4'].value,s['E5'].value=s['E5'].value,s['E4'].value
        f.alter(partial);self.preserved_conflict(f,'IDENTITY_ROW_CHANGED')

    def test_move_to_different_sheet_conflicts(self):
        f=self.fixture();f.ai();f.sync()
        def move(book):
            for cell in book['710'][4]:
                dest=book['851'].cell(4,cell.column);dest.value=cell.value;dest.comment=copy(cell.comment)
            book['710'].delete_rows(4)
        f.alter(move);self.preserved_conflict(f,'IDENTITY_ROW_CHANGED_OR_MOVED')

    def test_destination_changes_after_staging(self):
        f=self.fixture();f.ai();local=f.hashes()[0]
        def race(stage):
            if stage=='after_stage':f.alter(lambda b:setattr(b['851']['E3'],'value','MINE-RACE'))
        with patch.object(publication,'checkpoint',race),self.assertRaisesRegex(Exception,'DESTINATION_CHANGED'):f.sync()
        self.assertEqual(f.hashes()[0],local);self.assertEqual(f.count('MINE-RACE',f.mine),1)
        f.sync();self.one_each(f)

    def test_boundary_change_after_final_hash_check(self):
        f=self.fixture();f.ai()
        before=f.hashes()
        intermediate={}
        def race(stage):
            if stage=='before_replace':
                alternative=f.root/'concurrent-save.xlsx'
                alternative.write_bytes(f.mine.read_bytes())
                f.alter(lambda b:setattr(b['851']['E3'],'value','MINE-AFTER-FINAL-CHECK'),alternative)
                writer.replace_source(alternative,f.mine)
                proof=f.root/'concurrent-mine-before-overwrite.xlsx';proof.write_bytes(f.mine.read_bytes())
                intermediate.update(mine=f.inspect(proof),snapshot=str(proof.relative_to(ROOT)))
        with patch.object(publication,'checkpoint',race):f.sync()
        # This is evidence of a REAL LOSS in the isolated mine fixture, not a
        # successful preservation test. The overall candidate stays PARTIAL.
        self.assertEqual(f.count('MINE-AFTER-FINAL-CHECK',f.mine),0)
        self.scenario_verdict='FAIL'
        self.limitation=dict(data_loss=True,duplicate=False,before_hashes=before,
            intermediate=intermediate,
            reason='Concurrent atomic save after final hash check can be overwritten. No SMB CAS; exclusive window required.')
        self.one_each(f)

    def test_old_open_workbook_saved_after_ack(self):
        f=self.fixture();old=f.mine.read_bytes();f.ai();f.sync();local=f.hashes()[0]
        f.mine.write_bytes(old);self.preserved_conflict(f,'PUBLISHED_RECORD_MISSING')
        self.assertEqual(f.hashes()[0],local);self.assertEqual(f.count('AI-1'),1)

    def test_owner_file_defers_without_losing_local_report(self):
        f=self.fixture();f.ai();owner=f.mine.with_name('~$'+f.mine.name);owner.write_bytes(b'SYNTHETIC')
        self.preserved_conflict(f,'EXCEL_OWNER_FILE');owner.unlink();f.sync();self.one_each(f)

    def test_legacy_migration_exact_and_idempotent(self):
        f=self.fixture(bootstrap=False)
        for n in range(4):f.ai('AI-LEGACY-'+str(n),'test:'+str(n),('دامپتراک710','بیل851'))
        self.assertEqual(f.inspect(f.local)['uuid_count'],0)
        f.sync();self.one_each(f,'AI-LEGACY-',8);before=f.hashes();f.sync();self.assertEqual(before,f.hashes())

    def test_legacy_migration_ambiguous_mine_blocks(self):
        f=self.fixture(bootstrap=False);f.ai()
        f.alter(lambda b:setattr(b['851']['E3'],'value','MINE-INITIAL-UNKNOWN'))
        self.preserved_conflict(f,'MIGRATION_MINE_VERSION_AMBIGUOUS')

    def test_unknown_package_feature_is_terminal(self):
        f=self.fixture();f.ai()
        import zipfile
        raw=f.mine.read_bytes();out=io.BytesIO()
        with zipfile.ZipFile(io.BytesIO(raw)) as src,zipfile.ZipFile(out,'w') as dst:
            for name in src.namelist():dst.writestr(name,src.read(name))
            dst.writestr('xl/unsupported-feature.xml','<unsupported/>')
        f.mine.write_bytes(out.getvalue());self.preserved_conflict(f,'UNSUPPORTED_PACKAGE_PART')
        f.mine.write_bytes(raw)
        f.alter(lambda b:setattr(b['710'].page_setup,'cellComments','atEnd'))
        self.preserved_conflict(f,'NOTES_PRINTING_UNSUPPORTED')

    def test_disabled_keeps_legacy_behavior_and_creates_no_publication_state(self):
        f=self.fixture(bootstrap=False);settings=json.loads(f.config.read_text('utf8'))
        settings['outbound_publication']['enabled']=False;f.config.write_text(json.dumps(settings),'utf8')
        f.ai();mine=f.hashes()[1];f.sync()
        self.assertEqual(f.hashes()[1],mine);self.assertEqual(f.inspect(f.local)['uuid_count'],0)
        with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db:
            self.assertIsNone(db.execute("SELECT name FROM sqlite_master WHERE name='maintenance_publications_v1'").fetchone())
        # Evidence expects the new tables: create empty tables only AFTER the check.
        with closing(writer.open_audit(f.runtime)) as db:publication.schema(db)

    def test_production_paths_rejected_even_when_enabled(self):
        f=self.fixture();settings=json.loads(f.config.read_text('utf8'));settings['outbound_publication']['isolation_root']=str(ROOT)
        f.config.write_text(json.dumps(settings),'utf8');self.preserved_conflict(f,'ISOLATION_REQUIRED')

    def test_retained_four_batches_25_rows_migrate_from_exact_evidence(self):
        f=self.fixture()
        audit=json.loads((ROOT/'reports/sync/AI-EXCEL-SYNC-TESTS.json').read_text('utf8'))
        frozen=Path(audit['run_root'])/'private-operational-snapshots'
        retained=f.root/'retained-proof';retained.mkdir();(retained/'backups').mkdir()
        original=(frozen/'function.xlsx').read_bytes()
        original_hash=baseline.digest(original)
        db_path=frozen/'audit.sqlite3';db_hash=baseline.digest(db_path.read_bytes())
        with closing(sqlite3.connect(db_path.resolve().as_uri()+'?mode=ro',uri=True)) as db:
            db.row_factory=sqlite3.Row;db.execute('PRAGMA query_only=ON')
            row=db.execute('SELECT payload FROM maintenance_baseline_events').fetchone()
            state=json.loads(row[0]);source=Path(state['source'])
            edits=db.execute('SELECT * FROM edits WHERE source=? COLLATE NOCASE AND rowid>?',
                             (str(source),state['cutoff'])).fetchall()
            hashes={state['initial_hash']}
            for edit in edits:
                hashes.update((edit['before_hash'],edit['after_hash']))
                result=json.loads(edit['result'])
                if result.get('mine_hash'):hashes.add(result['mine_hash'])
            for sha in hashes:
                data=baseline.snapshot(ROOT/'runtime/maintenance-entry',sha)
                (retained/'backups'/(sha+'.xlsx')).write_bytes(data)
            state,mine_hash,known,specs,active=publication.replay_branch(db,source,original,retained)
        self.assertEqual(len(known),25)
        self.assertEqual(len({spec[0] for spec in specs}),4)
        self.assertFalse(active)
        migrated=identity.load(original)
        try:
            before=baseline.states(migrated);identity.enable(migrated)
            for operation,group,record,position in specs:identity.attach(migrated,position,operation,group)
            candidate=identity.dump(migrated);identities=identity.inventory(migrated)
            self.assertEqual(identities,known);self.assertEqual(len(migrated.sheetnames),63)
            (retained/'migrated.xlsx').write_bytes(candidate)
            check=identity.load(candidate)
            try:
                # Repeating migration must return unchanged IDs and bytes,
                # never call append_record or bind by date/text.
                self.assertEqual(identity.inventory(check),known)
                self.assertEqual(len(check.sheetnames),63)
                for sheet in check:
                    for cell in sheet._cells.values():
                        if cell.comment and cell.comment.author==identity.AUTHOR:cell.comment=None
                check.defined_names.pop(identity.MARKER)
                self.assertEqual(baseline.states(check),before)
            finally:check.close()
            from tools.fleet.maintenance_daily import report
            old=report.scan(original,'1405/08/24');new=report.scan(candidate,'1405/08/24')
            self.assertEqual(old['devices'],new['devices'])
            # Compare all target dates represented by the protected operations.
            scan_dates=sorted({record['date'] for _,_,record,_ in specs})
            for date in scan_dates:
                self.assertEqual(report.scan(original,date)['devices'],report.scan(candidate,date)['devices'])
            self.f.outcomes.append(dict(retained_migration=dict(protected_rows=25,batches=4,
                sheet_count=63,original_sha256=original_hash,candidate_sha256=baseline.digest(candidate),
                uuid_ids=sorted(known),all_operational_states_unchanged=True,repeat_identity_unchanged=True,
                journal_sha256=db_hash,journal_unchanged=baseline.digest(db_path.read_bytes())==db_hash,
                immutable_snapshots_verified=len(hashes),report_dates_unchanged=scan_dates,
                artifacts=str(retained.relative_to(ROOT)))))
        finally:migrated.close()
        self.assertEqual(baseline.digest((frozen/'function.xlsx').read_bytes()),original_hash)
        self.assertEqual(baseline.digest(db_path.read_bytes()),db_hash)

    def test_other_workbooks_use_existing_inbound_copy(self):
        f=self.fixture();f.ai();other=f.mine.parent/'other.xlsx';other.write_bytes(b'OTHER-BYTES')
        spec=importlib.util.spec_from_file_location('candidate_sync',ROOT/'integrations/hermes/mine_sync/mine_file_sync.py')
        sync=importlib.util.module_from_spec(spec);spec.loader.exec_module(sync)
        def bridge(staged,destination,**kwargs):
            return guard_snapshot(staged,destination,upstream=kwargs['upstream'],config=f.config,runtime=f.runtime)
        with patch.object(sync,'MAINTENANCE_REPORT',f.local),patch.object(sync,'publish_fault_report',bridge):
            result=sync.empty_result();sync.sync_tree(f.mine.parent,f.local.parent,result,lambda **kw:None)
        self.assertEqual((f.local.parent/'other.xlsx').read_bytes(),b'OTHER-BYTES')
        self.assertEqual(other.read_bytes(),b'OTHER-BYTES');self.one_each(f)
        self.assertEqual(result['status'],'success');f.outcomes.append(result)


if __name__=='__main__':
    evidence_path=None
    if '--evidence' in sys.argv:
        n=sys.argv.index('--evidence');evidence_path=Path(sys.argv[n+1]);del sys.argv[n:n+2]
    result=unittest.main(exit=False)
    preservation_failures=sum(t['verdict']=='FAIL' for t in EVIDENCE)
    acceptance_ok=result.result.wasSuccessful() and preservation_failures==0
    if evidence_path:
        evidence_path.write_text(json.dumps(dict(schema='komatso-coordinated-candidate-tests-v1',
            captured_at_utc=datetime.now(timezone.utc).isoformat(),run_root=str(RUN),
            synthetic_only=True,tests=EVIDENCE,summary=dict(tests=result.result.testsRun,
            failures=len(result.result.failures),errors=len(result.result.errors),skips=len(result.result.skipped),
            preservation_failures=preservation_failures,acceptance_exit_code=0 if acceptance_ok else 1,
            scenario_verdicts={v:sum(t['verdict']==v for t in EVIDENCE) for v in ('PASS','FAIL','BLOCKED')})),
            ensure_ascii=False,indent=2)+'\n','utf8')
    if not acceptance_ok:
        print('CANDIDATE_PARTIAL: preservation acceptance failed',file=sys.stderr)
    sys.exit(not acceptance_ok)
