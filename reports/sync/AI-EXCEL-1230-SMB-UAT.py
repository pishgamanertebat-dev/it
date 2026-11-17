"""Explicit-owner-authorized SMB probe, synthetic files in one private folder.

Never writes an existing Share file, changes ACLs, or closes another handle.
Only run with --execute-synthetic-share-test after authorization. Children
can mutate only the marker-bound, UUID-named folder created by this run.
"""
from contextlib import contextmanager
import ctypes
from ctypes import wintypes
import hashlib
import io
import json
import os
from pathlib import Path
import re
import subprocess
import sys
import uuid
import traceback

ROOT = Path('E:/KomatsoAI')
sys.path.insert(0,str(ROOT))
from tools.fleet.repairs.entry_service import replace_source

SHARE = Path(r'\\192.168.20.251\share')
PROTECTED = SHARE/'تعمیرات 1405.xlsx'
EVIDENCE = ROOT/'runtime/maintenance-coordinated-candidate/daily-1230-20261116/smb-result.json'
PREFIX = '__komatso_publication_uat_'


def sha(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream,'sha256').hexdigest()


def guard(folder):
    if (folder.parent != SHARE or not re.fullmatch(PREFIX+r'[0-9a-f]{32}',folder.name)
            or (folder/'scope.json').read_text('utf8') != folder.name):
        raise ValueError('Synthetic scope denied')
    return folder


@contextmanager
def handle(path, flags):
    api=ctypes.WinDLL('kernel32',use_last_error=True)
    api.CreateFileW.argtypes=[wintypes.LPCWSTR,wintypes.DWORD,wintypes.DWORD,wintypes.LPVOID,wintypes.DWORD,wintypes.DWORD,wintypes.HANDLE]
    api.CreateFileW.restype=wintypes.HANDLE; api.CloseHandle.argtypes=[wintypes.HANDLE]
    h=api.CreateFileW(str(path),0x80000000,flags,None,3,0,None)
    if h==wintypes.HANDLE(-1).value: raise ctypes.WinError(ctypes.get_last_error())
    try: yield
    finally: api.CloseHandle(h)


def attempt(folder, mode):
    guard(folder); target=folder/'synthetic.xlsx'
    try:
        if mode=='direct': target.write_bytes(b'SYNTHETIC-COMPETING-DIRECT')
        else:
            stage=folder/'competitor.partial'; stage.write_bytes(b'SYNTHETIC-COMPETING-ATOMIC')
            replace_source(stage,target)
        return dict(success=True)
    except OSError as ex:
        return dict(success=False,winerror=getattr(ex,'winerror',None),error_type=type(ex).__name__)


def child(folder, mode):
    process=subprocess.run([sys.executable,'-B','-X','utf8',__file__,'--child',str(folder),mode],capture_output=True,text=True,encoding='utf8',timeout=20)
    if process.returncode: raise RuntimeError('Synthetic child failed: '+process.stderr[-500:])
    return json.loads(process.stdout)


def inventory():
    result={}
    for base,dirs,files in os.walk(SHARE,followlinks=False):
        dirs[:]=[n for n in dirs if not n.startswith(PREFIX) and not (Path(base)/n).is_symlink()]
        for name in files:
            if name.startswith('~$'): continue
            path=Path(base)/name; stat=path.stat()
            result[str(path.relative_to(SHARE))]=dict(size=stat.st_size,mtime_ns=stat.st_mtime_ns)
    return result


def run():
    folder=SHARE/(PREFIX+uuid.uuid4().hex)
    result=dict(scope='SYNTHETIC_FILES_ON_ACTUAL_SHARE_ONLY',production_workbook_writes=0,
                account=subprocess.check_output(['whoami'],text=True).strip(),test_folder=str(folder),
                server_exclusion_accepted=False,excel_clients_tested=False)
    result['before_inventory']=inventory(); result['before_workbook_sha256']=sha(PROTECTED)
    created=False
    try:
        result['stage']='create_private_folder'
        folder.mkdir(); created=True; (folder/'scope.json').write_text(folder.name,'utf8'); guard(folder)
        target=folder/'synthetic.xlsx'
        import openpyxl
        book=openpyxl.Workbook(); book.active['A1']='SYNTHETIC ONLY'
        stream=io.BytesIO();book.save(stream);book.close(); template=stream.getvalue()
        rows=[]
        for flags in (5,1,0):
            result['stage']='seed_synthetic_'+str(flags)
            target.write_bytes(template)
            result['stage']='open_handle_'+str(flags)
            with handle(target,flags):
                result['stage']='compete_'+str(flags)
                direct=child(folder,'direct'); atomic=child(folder,'atomic')
                own=folder/'publisher.partial'; own.write_bytes(template)
                try:
                    replace_source(own,target); publisher=dict(success=True)
                except OSError as ex:
                    publisher=dict(success=False,winerror=getattr(ex,'winerror',None))
            rows.append(dict(share_flags=flags,competitor_direct=direct,competitor_atomic=atomic,publisher_replace=publisher))
        result['share_matrix']=rows
        target.write_bytes(template)
        with handle(target,5):
            own=folder/'publisher.partial'; own.write_bytes(template); replace_source(own,target)
            result['write_after_replace_with_old_handle']=child(folder,'direct')
        target.write_bytes(template)
        with handle(target,0): result['exclusive_blocks_stale_save']=child(folder,'direct')
        result['stale_save_after_release']=child(folder,'atomic')
        result['verdict']='FAIL_EXCLUSION' if rows[0]['competitor_atomic']['success'] else 'REVIEW_REQUIRED'
    except Exception as ex:
        result.update(verdict='BLOCKED',error_type=type(ex).__name__,error=str(ex),traceback=traceback.format_exc())
    finally:
        if created:
            try:
                guard(folder)
                # No recursive deletion, only this exact run's known files.
                for name in ('synthetic.xlsx','competitor.partial','publisher.partial'):
                    (folder/name).unlink(missing_ok=True)
                (folder/'scope.json').unlink();folder.rmdir()
                result['test_folder_removed']=True
            except Exception as ex:
                result.update(test_folder_removed=False,cleanup_error=str(ex))
        try:
            result['after_inventory']=inventory();result['after_workbook_sha256']=sha(PROTECTED)
            result['operational_workbook_bytes_unchanged']=result['before_workbook_sha256']==result['after_workbook_sha256']
            result['operational_inventory_unchanged']=result['before_inventory']==result['after_inventory']
        except Exception as ex: result['comparison_error']=str(ex)
        EVIDENCE.write_text(json.dumps(result,ensure_ascii=False,indent=2),'utf8')
    print(json.dumps({k:v for k,v in result.items() if k not in {'before_inventory','after_inventory','share_matrix'}},ensure_ascii=True))


if __name__=='__main__':
    if len(sys.argv)==4 and sys.argv[1]=='--child': print(json.dumps(attempt(Path(sys.argv[2]),sys.argv[3])))
    elif sys.argv[1:]==['--execute-synthetic-share-test']: run()
    else: raise SystemExit('Explicit synthetic-share-test flag required')
