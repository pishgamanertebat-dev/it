from pathlib import Path
from datetime import datetime,timezone
import json
ROOT=Path(__file__).resolve().parents[2]
def load(name):return json.loads((ROOT/name).read_text('utf-8-sig'))
def save(name,obj):(ROOT/name).write_text(json.dumps(obj,ensure_ascii=False,indent=2),encoding='utf8')
data=load('04-DATA-DICTIONARY.json')
keys={
 'K-SCHEDULE':'schedule.id; publication version=YAML sha256',
 'K-SYNC':'singleton scope(source,destination); observation key=(scope,observed_at)',
 'K-CRON':'job.id',
 'K-GATEWAY':'singleton runtime identity=(pid,start_time); state observation=updated_at',
 'K-ART-MAINT':'(target_date,manifest_sha256,artifact_sha256)',
 'K-ART-PERF':'(target_date,report manifest version,pdf_sha256)',
 'K-LOG':'source_log_version + bounded record offset/time; no durable business PK',
 'H-RULE':'(group,version,personnel_scope,effective_from,effective_to)',
 'H-REG-SNAPSHOT':'registry_receipt.revision + snapshot generation hash',
 'H-HR-INPUT':'(pinned_source_sha256,row); logical person/date/shift keys depend on reader contract',
 'H-WISMAN':'(source_sha256,sheet,excel_row); ingestion identity preserves explicit person/date/shift',
 'H-OUTPUT':'(run_id,content_id); daily row key includes person/date/shift and preserved source row identity',
 'H-HEALTH':'singleton process identity and heartbeat observation timestamp',
 'H-ARCHIVE':'(job_id,event_key,artifact_sha256) derived from source receipts'}
relations={
 'K-SCHED':['K-SCHEDULE(schedule_id)','K-AUTH(recipient_id, historical scope only)','K-ART-MAINT(manifest/hash)','K-ART-PERF(manifest/hash)'],
 'K-AUTH':['K-SCHEDULE(recipient_capability)','K-SCHED(recipient_id)'],
 'K-FLEET':['K-X-DRIVERS(ingest_sources.sha256)','K-X-MAINT(ingest_sources.sha256)'],
 'K-ENTRY':['K-X-DRIVERS(source,before_hash,after_hash)','K-FLEET(canonical_code)'],
 'K-MAINT-ENTRY':['K-X-MAINT(source,before_hash,after_hash)','K-SYNC(accepted local/mine hashes)'],
 'H-PERSONNEL':['H-REG-SNAPSHOT(revision)','H-RULE(extra_rules)','H-OUTPUT(personnel revision)'],
 'H-BOT':['H-WISMAN(job/source)','H-OUTPUT(job.result/run_id/content_id)','H-ARCHIVE(event_key/hash)'],
 'H-OUTPUT':['H-BOT(job lineage)','H-RULE(rule_version/input sha)','H-REG-SNAPSHOT(revision)']}
for source in data['sources']:
    key=source['source_id']
    source['primary_key']=('per-table PK from database_dictionary; absent declared PK remains absent' if key in data['database_dictionary'] else '(source_sha256,sheet,excel_row,column); date/device uniqueness requires domain validation' if key.startswith('K-X-') else keys.get(key,'source-specific identifier; no inferred business PK'))
    source['relationships']=relations.get(key,['see source-to-entity mapping and source-of-truth matrix'])
    raw=source['path']
    if not source.get('last_modified_utc') and not any(c in raw for c in ['*','<','{','+']):
        p=Path(raw)
        if p.is_file():source['last_modified_utc']=datetime.fromtimestamp(p.stat().st_mtime,timezone.utc).isoformat()
    source['last_modified_status']='observed_file_metadata' if source.get('last_modified_utc') else 'unknown_or_multiple_files; use per-version manifest/watermark'
save('04-DATA-DICTIONARY.json',data)
save('evidence/inventory/infrastructure.json',dict(observed_utc='2026-11-11T08:53:00+00:00',status='partial',method='CIM query',error_code='HRESULT_0x80041003',reason='Access denied; no permissions changed',fallback='infrastructure-native.json and network-processes.json'))
save('evidence/inventory/scheduled-tasks.json',dict(status='unknown',query='schtasks /Query /FO CSV /V',error='The system cannot find the path specified',saved_definitions='HRMS autostart XML and source install scripts only; live registration not verified'))
p=ROOT/'02-KOMATSOAI-AUDIT.md'
text=p.read_text('utf8')
text+="""
## پیکره‌های فنی و کانال‌های دیگر

پوشه‌های PC800، PC1250-8R، HD785-7، HD785-5، HD465-7R_HD605-7R، WA600-6 و R330LC-9S و افزونه‌های komatso-technical-docs و komatso-public-research نیز در موجودی فعلی وجود دارند. خروجی‌های فنی و تصاویر هر Session با گزارش روزانه عملیاتی یکی نیستند. این منابع می‌توانند در آینده فهرست مجاز فایل یا پیوند نتیجه داشته باشند؛ جست‌وجوی دفترچه، رندر صفحه یا فراخوانی مدل در درخواست GET داشبورد جزو MVP نیست. وجود افزونه روی دیسک، اجرای موفق یک پاسخ فنی جدید را اثبات نمی‌کند.
"""
p.write_text(text,encoding='utf8')
print('Catalog keys/relations/mtime status enriched; failed probes explicitly recorded')
