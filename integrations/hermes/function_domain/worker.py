"""Fixed-operation worker; stdin JSON contains data, never code or shell instructions."""
from pathlib import Path
from tempfile import TemporaryDirectory
import json,sys,secrets,shutil
from tools.authorization import AuthorizationStore,FUNCTION_READ,OVERFLOW_READ
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
    'report':{'kind','date','machine_code','mode','output'},
}

def private_identity(identity):
    return (identity.get('platform')=='bale' and identity.get('chat_type')=='dm'
            and bool(identity.get('user_id')) and identity.get('chat_id')==identity.get('user_id'))

def authorized(identity,store):
    return private_identity(identity) and bool(store.function_scope(identity['user_id'],'bale'))

def report_authorized(identity,store,kind):
    if not private_identity(identity):return False
    if kind=='machinery_performance':
        from tools.fleet.machinery_performance.report import SOURCE_NAME
        scope=store.function_scope(identity['user_id'],'bale')
        return scope.all or SOURCE_NAME in scope.files
    if kind=='overflow':
        return (store.function_scope(identity['user_id'],'bale').all
                or store.can_read_overflow(identity['user_id'],identity['chat_id']))
    return bool(store.function_scope(identity['user_id'],'bale'))


def run(request,*,store=None,reader=None,cache=CACHE):
    store=store or AuthorizationStore();reader=reader or ScopedReader()
    identity=request.get('identity',{});op=request.get('operation');args=request.get('args',{})
    kind=args.get('kind') if isinstance(args,dict) and op=='report' else None
    check=lambda: report_authorized(identity,store,kind) if op=='report' else authorized(identity,store)
    if not check():raise PermissionError('Business resource capability required')
    scope=store.function_scope(identity['user_id'],'bale')
    # Overflow report permission grants only the existing report backend; never a raw-file tool.
    if not scope.all and not (op=='report' and kind=='overflow'):
        reader=reader.restricted(scope.files)
    out=None
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
                if args.get('date'):
                    date=validate_date(args['date'])
                elif kind=='overflow':
                    from tools.scheduler.tasks import overflow_report_date
                    date=overflow_report_date()
                else:raise ValueError('An exact date is required for driver reports')
                if args['kind']=='driver':
                    from .driver_report import build_driver
                    result=build_driver(date,out,reader)
                elif args['kind']=='overflow':
                    from tools.fleet.overflow.report import load_report,render_report
                    with TemporaryDirectory(dir=out) as tmp:
                        snapshot=Path(tmp)/'source.xlsx';snapshot.write_bytes(reader.snapshot('سرریز روزانه.xlsx'))
                        report=load_report(date,snapshot)
                    result={'ok':True,'report':report,'images':render_report(report,out)}
                elif args['kind']=='machinery_performance':
                    from tools.fleet.machinery_performance.report import load,select,TOTAL_COLUMNS
                    report=select(load(date,reader=reader),mode=args.get('mode','daily'),
                                  comparison='average_gt_limit',machine_code=args.get('machine_code'))
                    ready=bool(report['rows'] and not report['missing_codes'])
                    result=dict(status='ready' if ready else 'waiting_for_data',date=report['date'],
                                mode=report['mode'],source_sha256=report['source_sha256'],
                                missing_codes=report['missing_codes'],ambiguous_code=report['ambiguous_code'],
                                devices=[dict(name=r['name'],code=r['code'],metrics=dict(zip(TOTAL_COLUMNS,r['values'])),
                                              fuel_alert=r['alarm']) for r in report['rows']],
                                meaning='daily=exact date; monthly=saved monthly totals at preparation time; fuel=litres diesel; fuel average=litres/work hours; service counts=counts; zero work hours=undefined average')
                    if args.get('output','data') not in {'data','pdf'}:
                        raise ValueError('Unsupported performance output')
                    if ready and args.get('output')=='pdf':
                        from tools.fleet.machinery_performance.render import render,validate_pdf
                        pdf=out/'machinery-performance.pdf';render(report,pdf);validate_pdf(pdf,report)
                        result['attachment']=str(pdf)
                else:raise ValueError('Unknown report kind')
        except Exception:
            # Only this fixed private cache directory is ever cleaned; operational sources are untouched.
            for p in out.iterdir():
                if p.is_file():p.unlink()
            if not any(out.iterdir()):out.rmdir()
            raise
    # Revocation while parsing/rendering never publishes operational content.
    current=store.function_scope(identity['user_id'],'bale')
    if not check() or (not (op=='report' and kind=='overflow') and not current.all
                       and (scope.all or not set(scope.files).issubset(current.files))):
        if out is not None:
            # This random generated directory belongs to this operation.
            shutil.rmtree(out)
        raise PermissionError('Resource capability revoked during operation')
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
