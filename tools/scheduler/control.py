"""Cooperative CLI drain and one ledger worker at a time, without Task ACL changes."""
from contextlib import contextmanager
from datetime import datetime,timezone
from pathlib import Path
import json,os,logging,uuid

logger=logging.getLogger(__name__)


@contextmanager
def worker_lock(folder):
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    stream=(folder/'worker.lock').open('a+b');locked=False
    try:
        if stream.seek(0,2)==0:stream.write(b'0');stream.flush()
        stream.seek(0)
        try:
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_NBLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_EX|fcntl.LOCK_NB)
            locked=True
        except OSError:pass
        yield locked
    finally:
        if locked:
            stream.seek(0)
            if os.name=='nt':
                import msvcrt
                msvcrt.locking(stream.fileno(),msvcrt.LK_UNLCK,1)
            else:
                import fcntl
                fcntl.flock(stream.fileno(),fcntl.LOCK_UN)
        stream.close()


def write_record(path,payload):
    path=Path(path);temporary=path.with_name('.control-'+uuid.uuid4().hex+'.tmp')
    try:
        with temporary.open('x',encoding='utf8') as stream:
            json.dump(payload,stream,sort_keys=True);stream.flush();os.fsync(stream.fileno())
        os.replace(temporary,path)
    finally:temporary.unlink(missing_ok=True)


def scheduled_tick(callback,state):
    folder=Path(state).resolve().parent
    with worker_lock(folder) as acquired:
        if not acquired:
            logger.info('Scheduler tick deferred: another worker or deployment owns the ledger lock')
            return 0
        request=folder/'drain-request.json'
        if request.exists():
            try:
                value=json.loads(request.read_text('utf8'));token=value['token']
                if not isinstance(token,str) or len(token)!=32:raise ValueError('Invalid drain token')
            except (OSError,ValueError,KeyError,TypeError):token=None
            write_record(folder/'drain-ack.json',dict(token=token,pid=os.getpid(),
                state='drained' if token else 'invalid_request',updated=datetime.now(timezone.utc).isoformat()))
            logger.info('Scheduler drained; report execution and notification scan deferred')
            return 0
        return callback()
