from build_documents import *
import yaml

def obj(props,required=None,extra=False):
    return dict(type='object',properties=props,required=required or list(props),additionalProperties=extra)
def arr(schema):return dict(type='array',items=schema)
def ref(name):return {'$ref':'#/components/schemas/'+name}
def nullable(kind='string',**kw):return dict(type=[kind,'null'],**kw)
S={'type':'string'}
I={'type':'integer','minimum':0}
B={'type':'boolean'}
TS={'type':'string','format':'date-time'}
DATE={'type':'string','pattern':r'^1405/(0[1-9]|1[0-2])/(0[1-9]|[12][0-9]|3[01])$','description':'Persian operational date; server validates actual month/day'}
PERIOD={'type':'string','pattern':r'^1405/(0[1-9]|1[0-2])$'}
ID={'type':'string','pattern':r'^[A-Za-z0-9_-]{12,128}$'}
status=['sent_with_receipt','partial','ready','waiting_for_data','source_unavailable','source_stale','technical_error','closed_without_send','awaiting_review','recovered','uncertain','correction_required','correction_sent','not_due','stale','unknown']
schemas={}
schemas['SourceWatermark']=obj(dict(source_id=S,availability={'type':'string','enum':['available','partial','unavailable','unknown']},source_modified_at=nullable(format='date-time'),source_observed_at=nullable(format='date-time'),last_verified_at=nullable(format='date-time'),schema_fingerprint=nullable(),snapshot_version=nullable(),reason_code=nullable()))
schemas['Meta']=obj(dict(contract_version={'type':'string','const':'dashboard-read-model-1'},served_at=TS,snapshot_version=S,operational_timezone={'type':'string','const':'Asia/Tehran'},availability={'type':'string','enum':['complete','partial','unavailable']},freshness={'type':'string','enum':['fresh','stale','unknown']},age_seconds=nullable('integer',minimum=0),sources=arr(ref('SourceWatermark')),warnings=arr(S)))
schemas['EvidenceRef']=obj(dict(source_id=S,evidence_id=S,proof_scope={'type':'string','enum':['artifact_delivery','recipient_aggregate','recovery_chain','run_only','source_health','code_only','unknown']}))
schemas['Delivery']=obj(dict(id=ID,artifact_id=nullable(),recipient_ref=S,recipient_label=nullable(),delivery_kind={'type':'string','enum':['report','notice','correction','recovery']},status={'type':'string','enum':['sent_with_receipt','pending','failed','revoked','uncertain','unknown']},message_ids=arr(S),sent_at=nullable(format='date-time'),sha256=nullable(pattern='^[0-9a-f]{64}$'),proof_scope=S,bytes_verified=nullable('boolean'),human_read={'type':'string','const':'unknown'},evidence=arr(ref('EvidenceRef'))))
schemas['Artifact']=obj(dict(id=ID,occurrence_id=ID,kind=S,revision_ref=S,revision_of=nullable(),sha256=nullable(pattern='^[0-9a-f]{64}$'),source_sha256=nullable(pattern='^[0-9a-f]{64}$'),target_data_date=DATE,state={'type':'string','enum':['ready','invalid','missing','unknown']},content_validation={'type':'string','enum':['verified','failed','unknown']},retained=B,download_allowed=B,evidence=arr(ref('EvidenceRef'))))
schemas['AttemptSummary']=obj(dict(last_attempt_at=nullable(format='date-time'),last_success_at=nullable(format='date-time'),attempt_count=nullable('integer',minimum=0),last_checked_at=nullable(format='date-time'),next_attempt_at=nullable(format='date-time'),reason_code=nullable(),reason_fa=nullable()))
schemas['Occurrence']=obj(dict(id=ID,schedule_id=S,title_fa=S,data_date=DATE,original_due=TS,primary_status={'type':'string','enum':status},disposition={'type':'string','enum':['active','held','closed_owner','closed_expiry','unknown']},source_state=S,data_state=S,artifact_state=S,delivery_state=S,recovery_state=S,correction_state=S,expected_artifacts=nullable('integer',minimum=0),expected_deliveries=nullable('integer',minimum=0),recipient_set_provenance={'type':'string','enum':['historical_receipts','historical_reservation','current_registry_for_future','unknown']},attention_required=B,attempt=ref('AttemptSummary'),allowed_next_step_fa=nullable(),artifact_ids=arr(ID),evidence=arr(ref('EvidenceRef'))))
schemas['Counts']=obj(dict(occurrences=nullable('integer',minimum=0),logical_artifacts=nullable('integer',minimum=0),recipient_deliveries=nullable('integer',minimum=0),sent_with_receipt=nullable('integer',minimum=0),ready_artifacts=nullable('integer',minimum=0),needs_review=nullable('integer',minimum=0),unknown=nullable('integer',minimum=0),complete_denominator=B))
schemas['HealthComponent']=obj(dict(id=S,state={'type':'string','enum':['online','offline','degraded','unknown']},reported_state=nullable(),last_heartbeat_at=nullable(format='date-time'),last_success_at=nullable(format='date-time'),last_error_code=nullable(),reason_fa=nullable(),age_seconds=nullable('integer',minimum=0)))
schemas['Overview']=obj(dict(data_date=DATE,counts=ref('Counts'),components=arr(ref('HealthComponent'))))
schemas['SyncStatus']=obj(dict(component=ref('HealthComponent'),health_status=S,source_enumerated=nullable('boolean'),sync_healthy=nullable('boolean'),last_completed_scan_at=nullable(format='date-time'),next_attempt_at=nullable(format='date-time'),guards=arr(obj(dict(source_id=S,state=S,reason_code=nullable(),sheet=nullable(),cell=nullable(),local_sha256=nullable(),incoming_sha256=nullable(),last_seen_at=nullable(format='date-time'))))))
schemas['OperationCase']=obj(dict(id=ID,domain=S,occurrence_id=nullable(),status=S,cause_code=S,description_fa=S,operational_effect_fa=S,proposed_next_step_fa=S,attention_required=B,first_seen_at=nullable(format='date-time'),last_seen_at=nullable(format='date-time'),evidence=arr(ref('EvidenceRef'))))
schemas['AuditEvent']=obj(dict(id=ID,domain=S,event_type=S,occurred_at=nullable(format='date-time'),actor_ref=nullable(),object_ref=S,outcome=S,revision_ref=nullable(),evidence=arr(ref('EvidenceRef'))))
schemas['Personnel']=obj(dict(id=ID,display_name=S,job_title=nullable(),processing_state={'type':'string','enum':['active','inactive','ignored','unknown']},as_of=DATE,revision=S,groups=arr(S),group_state=S))
schemas['RuleRelease']=obj(dict(group=S,version=S,effective_from=DATE,effective_to=nullable(),state={'type':'string','enum':['implemented','pending','unknown']},scope_kind={'type':'string','enum':['general','personnel_scoped','revision_extra','unknown']},calculation_authority=S,rule_sha256=nullable()))
schemas['MetricValue']=obj(dict(known_minutes=nullable('integer',minimum=0),provisional_minutes=nullable('integer',minimum=0),final_minutes=nullable('integer',minimum=0),unknown_days=nullable('integer',minimum=0)))
schemas['HROverview']=obj(dict(period=PERIOD,selected_processing_id=nullable(),personnel_revision=nullable(),total_people=nullable('integer',minimum=0),active_people=nullable('integer',minimum=0),without_valid_group=nullable('integer',minimum=0),forms_generated=nullable('integer',minimum=0),last_verified_processing_at=nullable(format='date-time'),work=ref('MetricValue'),overtime=ref('MetricValue'),deduction=ref('MetricValue'),approval_state=S))
schemas['HRProcessing']=obj(dict(id=ID,period=PERIOD,state=S,content_id=S,run_id=S,source_snapshot_ref=S,personnel_revision=nullable(),started_at=nullable(format='date-time'),finished_at=nullable(format='date-time'),forms_generated=nullable('integer',minimum=0),calculation_state=S,publication_state=S,hr_approval_state=S,outbox_delivery_state=S,rule_versions=arr(S),issue_counts={'type':'object','additionalProperties':I}))
schemas['Me']=obj(dict(actor_ref=S,roles=arr(S),permissions=arr(S),expires_at=TS,operational_timezone={'type':'string','const':'Asia/Tehran'}))
schemas['Error']=obj(dict(error=obj(dict(code=S,message_fa=S,request_id=S,retryable=B)),meta=ref('Meta')))
def envelope(name,data,paginated=False):
    props=dict(data=data,meta=ref('Meta'))
    if paginated:props['page']=obj(dict(next_cursor=nullable(),limit={'type':'integer','minimum':1,'maximum':100},snapshot_version=S))
    schemas[name]=obj(props)
envelope('OverviewResponse',ref('Overview'))
envelope('ReportsResponse',arr(ref('Occurrence')),True)
envelope('ReportResponse',ref('Occurrence'))
envelope('DeliveriesResponse',arr(ref('Delivery')),True)
envelope('ArtifactsResponse',arr(ref('Artifact')),True)
envelope('OperationsResponse',arr(ref('OperationCase')),True)
envelope('AuditResponse',arr(ref('AuditEvent')),True)
envelope('HealthResponse',arr(ref('HealthComponent')))
envelope('SyncResponse',ref('SyncStatus'))
envelope('HROverviewResponse',ref('HROverview'))
envelope('PersonnelResponse',arr(ref('Personnel')),True)
envelope('PersonResponse',ref('Personnel'))
envelope('HRProcessingListResponse',arr(ref('HRProcessing')),True)
envelope('HRProcessingResponse',ref('HRProcessing'))
envelope('RulesResponse',arr(ref('RuleRelease')),True)
envelope('MeResponse',ref('Me'))

def query(name,schema,desc,required=False):
    return dict(name=name,in_='query',required=required,schema=schema,description=desc)
def q(name,schema,desc,required=False):
    x=query(name,schema,desc,required);x['in']=x.pop('in_');return x
page=[q('limit',dict(I,minimum=1,maximum=100,default=50),'حد صفحه'),q('cursor',S,'opaque signed cursor bound to snapshot/filter/permission')]
dateparams=[q('data_date_from',DATE,'ابتدای تاریخ داده، شامل مرز'),q('data_date_to',DATE,'انتهای تاریخ داده، شامل مرز؛حداکثر۹۲روز')]
reportfilters=[*dateparams,q('schedule_id',S,'شناسه واقعی برنامه'),q('status',{'type':'string','enum':status},'وضعیت نمایشی'),q('recipient_ref',S,'شناسه opaque گیرنده مجاز'),q('attention_only',B,'فقط موارد فعال نیازمند اقدام'),q('include_closed',dict(B,default=False),'نمایش آرشیو بسته'),q('corrected_only',B,'اصلاحیه‌ها'),q('sort',{'type':'string','enum':['data_date','-data_date','original_due','-original_due'],'default':'-data_date'},'sort whitelist'),*page]
period=[q('period',PERIOD,'ماه شمسی',True)]
routes=[
('/me','getMe','MeResponse','session.read','مشخصات session وscope مرورگر','dashboard auth','session','۳۰ثانیه؛permission هرrequest','کم،بدونخواندنsource',[],False,'login/expiry/rolechange'),
('/overview','getOverview','OverviewResponse','reports.read','کارت‌های مدیر برای یک تاریخ داده','K-SCHED,K-SYNC,K-GATEWAY,H-HEALTH','نوبت/فایل/ارسال جدا','cache۶۰ثانیه','SQLsnapshotcache', [q('data_date',DATE,'تاریخ داده انتخابی',True)],False,'کارت با reports reconcile شود'),
('/reports','listReports','ReportsResponse','reports.read','فهرست نوبت‌های گزارش','K-SCHEDULE,K-SCHED','occurrence','۶۰ثانیه','index query رویread model',reportfilters,True,'fixture۱/۲/۴ وhistorical unknown'),
('/reports/{id}','getReport','ReportResponse','reports.read','جزئیات،علت،تلاش وlineage نوبت','K-SCHED,K-ART-*','occurrence','۶۰ثانیه','lookup read model',[],False,'due/data_date وrecovery linkage'),
('/reports/{id}/deliveries','listDeliveries','DeliveriesResponse','reports.read','ارسال‌هایrecipient/artifact باproofscope','K-SCHED receipts/artifacts/recovery','recipient delivery','۶۰ثانیه','bounded indexed join',page,True,'notice مستقل؛sent بدونproof سبز نشود'),
('/reports/{id}/artifacts','listArtifacts','ArtifactsResponse','reports.read','فایل ونسخه‌هایگزارش','K-SCHED+retained manifests','logical artifact version','event/hash +۶۰ثانیه','metadata only',page,True,'retained=false دانلودنساختگی'),
('/artifacts/{id}/content','downloadArtifact',None,'reports.artifacts.download','خواندن فایل معتبر موجود','ArtifactResolver +exact hash+scope','bytes of one artifact version','no-store؛hashهرdownload','فقطreadفایلمجاز،بدونexport',[],False,'traversal/reparse/IDOR/hashfail'),
('/operations','listOperations','OperationsResponse','operations.read','caseهایواقعاًفعال یا آرشیوی','K-SCHED,K-SYNC,K-ENTRY,H-OUTPUT','case','۶۰ثانیه','readmodelgrouping',[q('domain',S,'komatsoai|hrms|sync'),q('include_closed',dict(B,default=False),'بسته‌ها'),*dateparams,*page],True,'D23تکرارattemptیکcaseبماند'),
('/sync/status','getSyncStatus','SyncResponse','system.health.read','سلامت جزئی منبع،guard وlastgood','K-SYNC+K-MAINT-ENTRY','source/guard','۶۰ثانیه','JSONcache',[],False,'partial باcron0سبزنشود'),
('/system/health','getSystemHealth','HealthResponse','system.health.read','وضعیتGateway/کانال/Scheduler/HRworker','K-GATEWAY,K-CRON,H-HEALTH,K-SCHED','component','۳۰ثانیه','فایلکوچکcache',[],False,'Baleonline مستقلTelegramretrying'),
('/audit','listAudit','AuditResponse','audit.operational.read','تاریخچه باredaction وscope','domain journals +dashboard accessaudit','event','۶۰ثانیه','boundedprojection',[q('domain',S,'دامنه مجاز'),q('object_ref',S,'هویت رخداد'),*page],True,'privacy/roleobjectchecks'),
('/hrms/overview','getHROverview','HROverviewResponse','hrms.overview.read','شاخص‌هاینسخهثابتدوره','H-PERSONNEL,H-BOT,H-OUTPUT','person/run/metric جدا','personnel۵دقیقه،job۶۰ثانیه','projectioncache',period+[q('processing_id',ID,'runانتخابی')],False,'null≠zero؛approval مستقل'),
('/hrms/personnel','listPersonnel','PersonnelResponse','hrms.personnel.read','فهرستپرسنل باrevision/asof','H-PERSONNEL revision projection','unique person','۵دقیقه یاrevision','pinned BLOB projection once',[q('as_of',DATE,'تاریخ مبنا',True),q('group',S,'G01..G09'),q('processing_state',S,'وضعیت پردازش'),q('search',dict(S,maxLength=80),'جست‌وجو؛درaccesslog ثبت نشود'),*page],True,'aliases تکرارنشود؛بدونcodeملی'),
('/hrms/personnel/{id}','getPerson','PersonResponse','hrms.personnel.read','پروفایلمجاز فرد','H-PERSONNEL','unique person','revision-bound','cached projection',[q('as_of',DATE,'تاریخ مبنا',True)],False,'role/fieldscope'),
('/hrms/processing','listProcessing','HRProcessingListResponse','hrms.attendance.read','وضعیتImport/پردازش/تولید/ارسال','H-BOT+H-OUTPUT','processing request/run', '۶۰ثانیه','job-bound manifests',period+page,True,'runtimefixture jobواقعی نشود'),
('/hrms/processing/{id}','getProcessing','HRProcessingResponse','hrms.attendance.read','نتیجهوهویتنسخهپردازش','H-BOT+H-OUTPUT','run','immutable content +۶۰ثانیهreceipt','cached structured result',[],False,'قواعدامروزرویگذشتهاجرا نشود'),
('/hrms/rules','listRules','RulesResponse','hrms.overview.read','نسخه‌هایقانون وسندمرجع محاسبه','H-RULE+revision_extra','rule release','bundlehash/revision','smallJSON/CSV',[q('as_of',DATE,'تاریخ اثر',True),q('group',S,'گروه'),*page],True,'currentG09calculated/historyimport'),
]
paths={}
for path,op,response,perm,purpose,source,unit,ttl,cost,params,paginated,accept in routes:
    errors={str(code):dict(description=desc,content={'application/json':{'schema':ref('Error')}}) for code,desc in [(401,'ورود لازم یا session منقضی'),(403,'scope/field permission ناکافی'),(404,'شناسه ناموجود یا نامجاز'),(409,'snapshot cursor/version conflict'),(410,'artifact retained bytes no longer available'),(422,'ورودی/تاریخ/بازه نامعتبر'),(429,'rate limited'),(503,'منبع قابل اعتماد وcacheسالموجودندارد')]}
    if response:
        responses={'200':dict(description='نسخه خواندنی؛stale/partial درmeta الزامی',content={'application/json':{'schema':ref(response)}}),**errors,
                   '304':dict(description='ETag برابر است؛بعدازauthorization دوباره')}
    else:
        responses={'200':dict(description='فایل ثابت و مجاز باhashتطبیق‌داده‌شده',headers={'ETag':{'schema':S},'Content-Disposition':{'schema':S}},content={'application/pdf':{'schema':{'type':'string','format':'binary'}}}),**errors}
    if '{id}' in path:params=[dict(name='id',**{'in':'path'},required=True,schema=ID,description='opaque resource id؛filepath نیست'),*params]
    paths['/api/v1'+path]={'get':dict(operationId=op,summary=purpose,tags=['HRMS' if path.startswith('/hrms') else 'Dashboard'],parameters=params,responses=responses,
        security=[{'cookieAuth':[]}],**{'x-required-permissions':[perm],'x-source-of-truth':source,'x-counting-unit':unit,'x-cache-policy':ttl,'x-read-cost':cost,'x-acceptance':accept})}
api=dict(openapi='3.1.0',info=dict(title='Management Dashboard Read API — DESIGN ONLY',version='0.1.0-draft',
    description='پیشنهاد قرارداد؛هیچ endpoint پیاده‌سازی یا راه‌اندازی نشده است. Unknown/null/stale وsource lineage الزامی‌اند. Auth lifecycle فقطدرذخیرهخودداشبورددرمرحلهتوسعهطراحیمی‌شود؛domainwriteایننسخهندارد.'),
    servers=[{'url':'https://dashboard.invalid','description':'Placeholder فقط؛hostواقعیتصویبنشده'}],
    paths=paths,components=dict(securitySchemes={'cookieAuth':dict(type='apiKey',**{'in':'cookie'},name='dashboard_session')},schemas=schemas),
    security=[{'cookieAuth':[]}],**{'x-audit-metadata':META})
(ROOT/'08-OPENAPI-DRAFT.yaml').write_text(yaml.safe_dump(api,allow_unicode=True,sort_keys=False,width=130),encoding='utf8')

body=r'''
## وضعیت قرارداد ورفتار مشترک

تمامendpointها PROPOSED هستند؛سرویس موجودی برایداشبوردفرض نشده است. نسخهv1 فقطdomain-read است؛session/cache/accessaudit متعلقبهdashboardمی‌توانددرمحیطخودشثبتشود. login/logoutوتأمینcredentialهنوزکارفازAuthهستند؛cookieAuthدرطرح صریح است.

Entityها: Occurrence،Artifact،Delivery،OperationCase،HealthComponent،Personnel،HRProcessing،RuleRelease. Schema ماشین‌خوان در[OpenAPI](08-OPENAPI-DRAFT.yaml)؛Source IDها در[کاتالوگ](04-DATA-SOURCE-CATALOG.md). pathidopaqueوresolvedserverside؛filepathخامپارامترنداریم.

برایتمامJSONپاسخ‌ها:data+meta؛metaشاملcontract_version،snapshot_version،served_at،availability،freshness،age_seconds وwatermarkمستقلمنبع. offsettimestampاجباری وtimezoneعملیاتAsia/Tehran؛Jalalidatesدر۱۴۰۵باvalidationواقعیماه/روز. عددنامعلومnullاست؛emptyarrayفقطqueryموفقباemptyواقعی.

فیلترفهرستگزارش: تاریخ داده شاملدوendpointبازه،job،status،recipient،attention،closed،corrected؛due_datefilterاگراضافهشدبایداسممتمایزداشتهباشد. defaultتاریخدادهامروزتهراندرUIبهAPIصریحفرستادهشود. sortingفقطwhitelist باtie-breakerid؛بهrawSQLتبدیلنشود. paginationcursorامضاشدهبهscope/filter/snapshotbind وlimit50،max100؛بازهحداکثر۹۲روز. تمامصفحاتیکversionدارند؛snapshotexpire→409+شروعفهرستدوباره.

خطاها:401session،403permission،404resource،409version/cursor،410retainedartifactgone،422validation،429rate،503sourceبدونlast-good. sourcefailباcacheسالم→200+stale/partial+warnings؛خطا بهempty200تبدیلنمی‌شود. errordetailفقطcode/فارسی/requestid؛secret/path/PIIندارد. HTTP304تنهابعدpermissionrecheck؛ETagشاملprojection/scopeversion. Cache-Control برایHR/artifactprivate,no-store؛برایعملیاتیprivateوmax-ageکوتاهپیشنهادی۱۵ثانیه.

اصلcheck: expectedrecipientsetتاریخیومدتretentionممکنunknownباشد؛APIcomplete_denominator=false می‌دهد وUIنسبتصددرصدنمی‌سازد. ReceiptبدونIDباartifactproofstrongتکمیل‌شود؛هرIDnonnullنیزبه‌معنیhash/contentverifiedنیست.

## نمونه مصنوعی پاسخ

~~~json
{
  "data": {
    "id": "occ_synthetic_001", "schedule_id": "driver_daily_office_supervisor",
    "data_date": "1405/08/17", "original_due": "2026-11-09T05:30:00+00:00",
    "primary_status": "partial", "attention_required": true,
    "expected_artifacts": 2, "expected_deliveries": 4,
    "recipient_set_provenance": "historical_reservation"
  },
  "meta": {
    "contract_version": "dashboard-read-model-1", "snapshot_version": "snap_synthetic_001",
    "served_at": "2026-11-11T09:00:00+00:00", "freshness": "stale",
    "operational_timezone": "Asia/Tehran", "availability": "partial",
    "age_seconds": 180, "warnings": ["SOURCE_READ_TIMEOUT"],
    "sources": []
  }
}
~~~

نمونهبالاsubsetتوضیحیOccurrenceاست؛فیلدهایکاملrequiredدرOpenAPIتعریفشده‌اند. مثالreadfailureبدونcache:

~~~json
{"error":{"code":"SOURCE_UNAVAILABLE","message_fa":"منبع و نسخه معتبر قبلی در دسترس نیست","request_id":"req_synthetic_001","retryable":true},"meta":{"availability":"unavailable","freshness":"unknown"}}
~~~

## Endpointها

برایهریکقراردادهمینبخش+قواعدمشترکبالااعمالمی‌شود. unitازscopeهریکجداست؛هیچaggregationضمنی بینrunها نداریم.

'''
examples={
'MeResponse':'actor_ref،roles،permissions،expires_at؛بدونtoken',
'OverviewResponse':'data_date=1405/08/17؛counts.occurrences=1,logical_artifacts=2,recipient_deliveries=4',
'ReportsResponse':'data=[Occurrence مصنوعی partial]؛page.next_cursor یاnull',
'ReportResponse':'Occurrence+attempt+evidence+correction/recovery states',
'DeliveriesResponse':'recipient_ref=recipient_synthetic_001،message_ids=["SYNTHETIC-100"]،human_read=unknown',
'ArtifactsResponse':'kind=mechanical،retained=false،sha256=null،download_allowed=false',
'OperationsResponse':'cause_code=HISTORICAL_CELL_CONFLICT،sheet705/cellD23 درsource detail مجاز',
'AuditResponse':'event_type=receipt_recorded،actor_ref opaque،evidence بدون متن',
'HealthResponse':'Bale online،Telegram degraded؛هرcomponent timestamp مستقل',
'SyncResponse':'health_status=partial،sync_healthy=false،guard blocked',
'HROverviewResponse':'total_people=null درنبودcanonicalrevision؛work.final_minutes=null',
'PersonnelResponse':'id=person_synthetic_001،revision=synthetic-7،groups=["G08"]؛بدونکدملی',
'PersonResponse':'display_name=کارمند نمونه،processing_state=active،as_of=1405/08/01',
'HRProcessingListResponse':'state=complete،hr_approval_state=not_recorded؛transport مستقل',
'HRProcessingResponse':'run_id=run_synthetic_001،period=1405/07،rule_versions=["G09-7.0.0"]',
'RulesResponse':'group=G09،version=G09-8.0.0،calculation_authority=before_and_after_shift'}
for path,op,response,perm,purpose,source,unit,ttl,cost,params,paginated,accept in routes:
    names=', '.join(x['name'] for x in params) or 'بدونquery'
    body+=f"### GET /api/v1{path}\n\nهدف: {purpose}. Permission: {perm}. Source of Truth: {source}. واحد: {unit}.\n\nپارامترها: {names}. {'Pagination مشترک؛sorting فقطاگرپارامترصریحآمده است.' if paginated else 'بدونpagination؛lookupبههمانversion.'} حداکثرحجمJSON۱MiB؛برایdownloadحدفایل۲۰MiBپیشنهادی،مواردبزرگباقراردادجدا.\n\nخروجی نمونه: {examples.get(response,'PDFهمانartifactنسخهثابت؛ETag=hash،بدونregeneration')}. Schema: {response or 'binary PDF'}.\n\nتازگی/cache: {ttl}. هزینهخواندن: {cost}. رفتارstale/503وerrorهای401/403/404/409/410/422/429طبققراردادمشترک؛منبعفقطازcacheworkerخواندهمی‌شود،نههرrequest. تستپذیرش: {accept}.\n\n"
body+=r'''
## شکاف لازم پیش از اتصال واقعی

۱. استخراج read projector رسمی receipt/recovery با حفظ semantics دامنه؛۲. schema fingerprint وlegacy migrations بدونwrite؛۳. نگاشتوبactorبهgrantخواندنی؛۴. API یاimmutableexportخواندنیHRMSدرصورتادامهSQLITE_CANTOPEN؛۵. ArtifactResolverباlineageواقعیفایل؛۶. نسخهفرمتjob.result/manifestبرایقدیمیها؛۷. محاسبهوثبتexpectedartifact/recipientsetبرایآیندهبایددرمالکدامنهتعریفشود،بدونbackfillحدسیگذشته.

اینقراردادادعانمی‌کندهمهفیلدهایاطلاعاتتاریخیهمینامروزکامل‌اند. هرrequiredobjectمی‌تواندnullablevalueداشتهباشد؛نبودمنبعباunknownنمایشدادهمی‌شود. افزودنendpointwriteمنوطبهMVP-4 وserviceهایفهرستعملیاتآیندهاست.
'''
write('08-API-CONTRACT.md','قرارداد API خواندنی، داده ناقص و نمونه‌ها',body)
print('Draft OpenAPI paths',len(paths),'schemas',len(schemas))
