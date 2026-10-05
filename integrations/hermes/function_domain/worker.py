"""Fixed-operation worker; stdin JSON contains data, never code or shell instructions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sys,secrets
from tools.authorization import AuthorizationStore,FUNCTION_READ
from .scoped import ScopedReader,MAX_ATTACH_BYTES
from .extract import extract

HOME=Path('C:/Users/win-10/AppData/Local/hermes')
CACHE=HOME/'profiles/admin/document_cache/komatso'
ALLOWED={
    'list':{'path','recursive','offset','limit'},
    'search':{'path','query','offset','limit'},
    'metadata':{'path'},
    'read':{'path','sheet','offset','limit'},
    'attach':{'path'},
    'report':{'kind','date'},
}

def authorized(identity,store):
    return (identity.get('platform')=='bale' and identity.get('chat_type')=='dm'
            and bool(identity.get('user_id')) and identity.get('chat_id')==identity.get('user_id')
            and store.has_capability(identity['user_id'],FUNCTION_READ,'bale'))

def run(request,*,store=None,reader=None,cache=CACHE):
    store=store or AuthorizationStore();reader=reader or ScopedReader()
    identity=request.get('identity',{});op=request.get('operation');args=request.get('args',{})
    if not authorized(identity,store):raise PermissionError('Business resource capability required')
    if op not in ALLOWED or not isinstance(args,dict) or set(args)-ALLOWED[op]:raise ValueError('Unknown operation or fields')
    if op=='list':result=reader.list(args.get('path',''),recursive=args.get('recursive',False),offset=args.get('offset',0),limit=args.get('limit',200))
    elif op=='search':
        if not isinstance(args.get('query'),str) or not args['query'].strip():raise ValueError('Filename query is required')
        result=reader.list(args.get('path',''),recursive=True,pattern=args['query'],offset=args.get('offset',0),limit=args.get('limit',200))
    elif op=='metadata':result=reader.metadata(args['path'])
    elif op=='read':result=extract(reader,args['path'],sheet=args.get('sheet'),offset=args.get('offset',0),limit=args.get('limit',100))
    else:
        cache=Path(cache);cache.mkdir(parents=True,exist_ok=True)
        out=cache/secrets.token_hex(16);out.mkdir()
        try:
            if op=='attach':
                data=reader.snapshot(args['path'],maximum=MAX_ATTACH_BYTES)
                name=Path(args['path'].replace('\\','/')).name
                target=out/name;target.write_bytes(data)
                result={'path':args['path'],'attachment':str(target),'bytes':len(data)}
            else:
                from tools.fleet.overflow.report import validate_date
                date=validate_date(args['date'])
                if args['kind']=='driver':
                    from .driver_report import build_driver
                    result=build_driver(date,out,reader)
                elif args['kind']=='overflow':
                    from tools.fleet.overflow.report import load_report,render_report
                    with TemporaryDirectory(dir=out) as tmp:
                        snapshot=Path(tmp)/'source.xlsx';snapshot.write_bytes(reader.snapshot('سرریز روزانه.xlsx'))
                        report=load_report(date,snapshot)
                    result={'ok':True,'report':report,'images':render_report(report,out)}
                else:raise ValueError('Unknown report kind')
        except Exception:
            # Only this fixed private cache directory is ever cleaned; operational sources are untouched.
            for p in out.iterdir():
                if p.is_file():p.unlink()
            if not any(out.iterdir()):out.rmdir()
            raise
    # Revocation while parsing/rendering never publishes operational content.
    if not authorized(identity,store):raise PermissionError('Resource capability revoked during operation')
    return {'ok':True,'result':result,'source':'E:\\Function','read_only':True}

def main():
    try:
        raw=sys.stdin.buffer.read(16385)
        if len(raw)>16384:raise ValueError('Request too large')
        result=run(json.loads(raw))
        output=json.dumps(result,ensure_ascii=False,default=str)
        if len(output)>120000:raise ValueError('Result too large; request a smaller page')
    except Exception as exc:
        # Safe errors only; no raw OS paths, credentials or third-party tracebacks.
        message=str(exc) if isinstance(exc,(PermissionError,ValueError,KeyError)) else 'Source unavailable or parser failed; request metadata or attachment.'
        output=json.dumps({'ok':False,'error':type(exc).__name__,'message':message},ensure_ascii=False)
    print(output)

if __name__=='__main__':main()
