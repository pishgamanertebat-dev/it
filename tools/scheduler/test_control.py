"""Cooperative drain does not claim a run, send a message or alter a workbook."""
from pathlib import Path
import tempfile,json,unittest
from .control import worker_lock,write_record,scheduled_tick


class ControlTests(unittest.TestCase):
    def setUp(self):
        parent=Path(__file__).resolve().parents[2]/'runtime/control-tests';parent.mkdir(parents=True,exist_ok=True)
        self.temp=tempfile.TemporaryDirectory(dir=parent);self.addCleanup(self.temp.cleanup)
        self.folder=Path(self.temp.name);self.state=self.folder/'ledger.sqlite3';self.called=[]
    def run_tick(self):return scheduled_tick(lambda:self.called.append(True) or 7,self.state)
    def test_normal_tick_returns_result(self):
        self.assertEqual(self.run_tick(),7);self.assertEqual(self.called,[True])
    def test_drain_is_persistent_and_acknowledges_exact_request_without_opening_ledger(self):
        write_record(self.folder/'drain-request.json',dict(token='1'*32))
        self.assertEqual(self.run_tick(),0);self.assertEqual(self.run_tick(),0)
        self.assertEqual(self.called,[]);self.assertFalse(self.state.exists())
        ack=json.loads((self.folder/'drain-ack.json').read_text('utf8'))
        self.assertEqual(ack['token'],'1'*32);self.assertEqual(ack['state'],'drained')
    def test_invalid_request_fails_closed(self):
        (self.folder/'drain-request.json').write_text('malformed','utf8')
        self.assertEqual(self.run_tick(),0);self.assertEqual(self.called,[])
    def test_competing_worker_cannot_enter_or_send(self):
        with worker_lock(self.folder) as acquired:
            self.assertTrue(acquired);self.assertEqual(self.run_tick(),0)
        self.assertEqual(self.called,[]);self.assertEqual(self.run_tick(),7)
    def test_unlocked_old_lockfile_does_not_block_later_tick(self):
        (self.folder/'worker.lock').write_bytes(b'old')
        self.assertEqual(self.run_tick(),7)

if __name__=='__main__':unittest.main()
