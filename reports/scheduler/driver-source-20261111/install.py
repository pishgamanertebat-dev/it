"""Install only the reviewed ordinal fix after focused and native acceptance."""
from datetime import datetime,timezone
import hashlib
import json
import os
from pathlib import Path
import uuid

OUT=Path(__file__).resolve().parent
ROOT=OUT.parents[2]
digest=lambda data:hashlib.sha256(data).hexdigest()
baseline=json.loads((OUT/'baseline-hashes.json').read_text(encoding='utf-8'))
native=json.loads((OUT/'native-acceptance.json').read_text(encoding='utf-8'))
assert all(v['status']=='passed' for v in native['validation'].values())
assert set(native['validation'])=={'mechanical','metalwork'}
assert native['blank_ordinals']==[{'row':28,'code':'HD718'}]
assert native['inventory_count']==36
test_log=(OUT/'acceptance-tests.log').read_bytes()
test_log=test_log.decode('utf-16' if test_log.startswith(b'\xff\xfe') else 'utf-8-sig')
assert test_log.strip().endswith('OK') and 'Ran 83 tests' in test_log
for rel,expected in baseline.items():
    assert digest((ROOT/rel).read_bytes())==expected,rel+' changed since review'
files=[*baseline,'tools/admin/test_driver_ordinal.py']
assert not (ROOT/files[-1]).exists()
changes={}
for rel in files:
    data=(OUT/'candidate'/rel).read_bytes()
    compile(data,str(ROOT/rel),'exec')
    changes[rel]=data
record={'installed_utc':datetime.now(timezone.utc).isoformat(),'files':{},
        'restart_required':False,'manual_send':False}
for rel,data in changes.items():
    target=ROOT/rel
    temporary=target.with_name('.driver-ordinal-'+uuid.uuid4().hex+'.tmp')
    temporary.write_bytes(data)
    os.replace(temporary,target)
    assert target.read_bytes()==data
    record['files'][rel]={'before_sha256':baseline.get(rel),'installed_sha256':digest(data)}
(OUT/'installed.json').write_text(json.dumps(record,indent=2),encoding='utf-8')
print(json.dumps(record,indent=2))
