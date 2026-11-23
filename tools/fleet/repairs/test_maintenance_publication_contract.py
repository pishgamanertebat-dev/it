"""Owner lunch contract: real private Writer path; existing failure tests remain."""
from contextlib import closing
import json
from pathlib import Path
import sqlite3
import unittest
from unittest.mock import patch
import uuid

from . import maintenance_publication as pub, maintenance_publication_alerts as notices
from . import test_maintenance_publication as previous
from .test_maintenance_1230 import at


class ContractTests(unittest.TestCase):
    def fixture(self):
        root=pub.CANDIDATE_ROOT/'c1717'/'u'/uuid.uuid4().hex[:8]
        with patch.object(previous,'RUN',root): f=previous.Fixture('f')
        cfg=json.loads(f.config.read_text('utf8'))
        cfg['outbound_publication'].update(policy='daily_1230',publication_window={'start':'12:30','end':'12:55'},
                                          operating_contract=pub.OWNER_LUNCH_CONTRACT)
        f.config.write_text(json.dumps(cfg),'utf8');f.ai()
        with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db,db:
            db.execute("update edits set created='2026-11-17T08:00:00+00:00'")
        return f,cfg

    def tick(self,f,clock='12:30:00'):
        with patch.object(notices,'now',return_value=at(clock,'2026-11-17')): return f.sync()

    def receipt(self,f):
        with closing(sqlite3.connect(f.runtime/'audit.sqlite3')) as db:
            return db.execute('select status from maintenance_publications_v1 order by created desc limit 1').fetchone()[0]

    def test_contract_normal_writer_receipt_and_duplicate_without_exclusion_mock(self):
        f,cfg=self.fixture();result=self.tick(f)
        self.assertEqual(result['status'],'merged');self.assertEqual(self.receipt(f),'acked')
        self.assertEqual(f.count('AI-1',f.mine),1);before=f.hashes()
        with patch.object(pub,'stage_bytes',wraps=pub.stage_bytes) as stage,patch.object(pub,'replace_source',wraps=pub.replace_source) as replace:
            self.assertEqual(self.tick(f)['status'],'unchanged')
            stage.assert_not_called();replace.assert_not_called()
        self.assertEqual(f.hashes(),before)

    def test_missing_contract_retains_original_guard(self):
        f,cfg=self.fixture();cfg['outbound_publication'].pop('operating_contract')
        f.config.write_text(json.dumps(cfg),'utf8');before=f.hashes()
        with patch.object(pub,'stage_bytes') as stage,self.assertRaisesRegex(Exception,'SMB_EXCLUSION_UNVERIFIED'):self.tick(f)
        stage.assert_not_called();self.assertEqual(f.hashes(),before)

    def test_unknown_or_wrong_policy_contract_rejected(self):
        f,cfg=self.fixture()
        for changes in ({'operating_contract':'free_text_yes'},{'policy':None}):
            trial=json.loads(json.dumps(cfg));trial['outbound_publication'].update(changes)
            with self.assertRaisesRegex(Exception,'CONFIG_INVALID'):pub.options(trial)

    def test_contract_does_not_extend_publication_window(self):
        f,cfg=self.fixture();before=f.hashes()
        for clock in ('12:00:00','12:29:59','12:55:00','13:00:00'):
            self.assertEqual(self.tick(f,clock)['status'],'publication_pending')
            self.assertEqual(f.hashes()[1],before[1])

    def test_owner_file_stops_before_stage(self):
        f,cfg=self.fixture();owner=f.mine.with_name('~$'+f.mine.name);owner.write_bytes(b'OWNER')
        before=f.hashes()
        with patch.object(pub,'stage_bytes') as stage,self.assertRaisesRegex(Exception,'EXCEL_OWNER_FILE'):self.tick(f)
        stage.assert_not_called();self.assertEqual(f.hashes(),before)

    def test_unexpected_hash_change_stops_before_replace(self):
        f,cfg=self.fixture()
        def change(stage):
            if stage=='after_stage':f.alter(lambda b:setattr(b['851']['E3'],'value','UNEXPECTED-MINE'))
        with patch.object(pub,'checkpoint',change),patch.object(pub,'replace_source',wraps=pub.replace_source) as replace:
            with self.assertRaisesRegex(Exception,'DESTINATION_CHANGED'):self.tick(f)
            replace.assert_not_called()
        self.assertEqual(self.receipt(f),'deferred');self.assertEqual(f.count('UNEXPECTED-MINE',f.mine),1)
        self.assertEqual(f.count('AI-1',f.mine),0)

    def test_stage_smb_timeout_preserves_both_inputs(self):
        f,cfg=self.fixture();before=f.hashes()
        with patch.object(pub,'stage_bytes',side_effect=RuntimeError('PUBLICATION_STAGE_TIMEOUT')):
            with self.assertRaisesRegex(Exception,'STAGE_TIMEOUT'):self.tick(f)
        self.assertEqual(f.hashes(),before);self.assertEqual(self.receipt(f),'deferred')

    def test_uncertain_result_cannot_be_blindly_retried(self):
        f,cfg=self.fixture()
        def crash(stage):
            if stage=='after_replace':raise OSError('INJECTED_DISCONNECT_AFTER_REPLACE')
        with patch.object(pub,'checkpoint',crash),self.assertRaisesRegex(Exception,'DISCONNECT_AFTER'):self.tick(f)
        self.assertEqual(self.receipt(f),'uncertain')
        with patch.object(pub,'stage_bytes') as stage,patch.object(pub,'replace_source') as replace:
            self.assertEqual(self.tick(f)['status'],'unchanged')
            stage.assert_not_called();replace.assert_not_called()
        self.assertEqual(self.receipt(f),'acked');self.assertEqual(f.count('AI-1',f.mine),1)

    def test_test_share_allowlist_keeps_production_denied(self):
        f,cfg=self.fixture();value=cfg['outbound_publication']
        exact=pub.SMB_UAT_ROOT/('komatso_contract_'+uuid.uuid4().hex)/pub.WORKBOOK
        self.assertTrue(pub.contract_test_upstream(exact,value))
        for path in (pub.SMB_UAT_ROOT.parent/pub.WORKBOOK,exact.parent/'other.xlsx',pub.SMB_UAT_ROOT/pub.WORKBOOK):
            self.assertFalse(pub.contract_test_upstream(path,value))
        for path in (exact.parent/'..'/pub.WORKBOOK,):self.assertFalse(pub.contract_test_upstream(path,value))
        with self.assertRaisesRegex(Exception,'PRODUCTION_PATH_DENIED'):
            pub.isolated_paths(cfg,Path(__file__),f.runtime,f.staged,f.config)


if __name__=='__main__':unittest.main()
