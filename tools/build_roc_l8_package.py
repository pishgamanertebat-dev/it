"""Reproducible ROC source manifest and bounded map; never writes source PDFs."""
from __future__ import annotations
import hashlib
import json
import re
from pathlib import Path
import pymupdf

ROOT = Path(__file__).resolve().parents[1]
IDS = ['roc-l8-trainee-9852960001', 'roc-l8-parts-9853647820d',
       'roc-l8-lf-parts-9853678620', 'roc-l8-cabin-9840042409',
       'roc-l8-electrical-9840042418']
# Boundaries from the actual PDF contents (p4), chapter headings and model labels.
RANGES = [
 ('introduction',1,9,'General / Introduction','معرفی و برنامه آموزش','general introduction'),
 ('hydraulic_general',10,15,'Hydraulic system, general','هیدرولیک عمومی','hydraulic pump'),
 ('electrical_general',16,27,'Electrical system, general','برق عمومی','electrical wiring power'),
 ('operating',28,33,'Operating','کاربری و کنترل‌ها','operating controls'),
 ('can',34,39,'CAN and control panel','شبکه CAN و پنل کنترل','can touch panel power board'),
 ('engine',40,63,'Diesel engine and starting diagrams','موتور و مدار راه‌اندازی','engine starting C13 C15'),
 ('pump_pilot',64,71,'Pumps & Pilot Pressure','پمپ و فشار پایلوت','pump pilot pressure'),
 ('cooling_fans',72,73,'Cooling fans','فن‌های خنک‌کن','cooling fans'),
 ('travel',74,81,'Tramming','حرکت دستگاه','travel tramming'),
 ('positioning',82,91,'Positioning','موقعیت‌دهی','positioning oscillation'),
 ('compressor',92,107,'Air System & Compressor','کمپرسور و سیستم هوا','compressor air flushing'),
 ('preheating',108,115,'Oil pre-heating','پیش‌گرمایش روغن','preheating oil heating'),
 ('feed',116,125,'Drilling functions / Drill feed','پیشروی حفاری','feed drilling'),
 ('rotation',126,131,'Drill rotation','چرخش حفاری','rotation threading'),
 ('rapid_feed',132,147,'Rapid feed / Rod coupling','پیشروی سریع و اتصال راد','rapid feed coupling'),
 ('impact_flushing',148,169,'Impact & Flushing air','ضربه و هوای فلاشینگ','impact flushing air'),
 ('ecl_hecl',170,175,'ECL / HECL lubrication','روانکاری ECL و HECL','ecl hecl lubrication'),
 ('damper',176,189,'Damper & DPC-I system','دمپر و کنترل ضربه','damper dpc impact'),
 ('dct',190,201,'Dust collector, DCT','غبارگیر DCT','dct dust collector'),
 ('protection',202,215,'Protective functions','حفاظت و ضدگیر','protection anti jamming rpcf'),
 ('length_measurement',216,217,'Length measurement','اندازه‌گیری طول','length measurement depth'),
 ('rhs_102',218,231,'Rod Handling System, RHS 102','رادگیر RHS 102','rhs rod handling'),
 ('rhs_140',232,251,'Rod Handling System, RHS 140','رادگیر RHS 140','rhs rod handling tube'),
 ('options',252,263,'Options','آپشن‌ها','extractor water mist greasing laser'),
 ('settings',264,265,'Adjustment/calibration','تنظیم و کالیبراسیون','settings adjustment calibration'),
 ('plc_l740',266,277,'PLC Programme ROC L740','برنامه PLC مدل L740','plc programme'),
 ('plc_l740cr',278,291,'PLC Programme ROC L740CR','برنامه PLC مدل L740CR','plc programme'),
 ('plc_l830',292,303,'PLC Programme ROC L830 and L630','برنامه PLC مدل L830 و L630','plc programme'),
 ('hydraulic_diagrams_l7',304,311,'L7 hydraulic diagrams','نقشه هیدرولیک L7','hydraulic diagram'),
 ('hydraulic_diagrams_l8',312,315,'L8 hydraulic diagrams','نقشه هیدرولیک L8','hydraulic diagram'),
 ('electrical_diagrams',316,359,'Electrical diagrams, 44 sheets','نقشه برق نسخه ۴۴ شیتی','electrical wiring diagram'),
 ('air_diagrams',360,368,'Air / Pneumatic diagrams','نقشه هوا و پنوماتیک','air pneumatic diagram'),
 ('excluded_filemarket',369,369,'FILEMARKET','صفحه غیر فنی','filemarket'),
]

def write(path, data):
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8')

def build():
    folder=ROOT/'ROC-L8'
    audit=json.loads((ROOT/'reports/technical-docs/ROC-L8-audit-20261115.evidence.json').read_text(encoding='utf-8'))
    docs=[]
    for i,s in enumerate(audit['sources']):
        path=Path(s['file']); sha=hashlib.sha256(path.read_bytes()).hexdigest()
        with pymupdf.open(path) as pdf:
            count=len(pdf)
        if sha!=s['sha256'] or count!=s['pages']:
            raise ValueError('Source changed since audit: '+s['audit_id'])
        docs.append(dict(document_id=IDS[i],audit_id=s['audit_id'],file_path=path.relative_to(ROOT).as_posix(),
            filename=path.name,sha256=sha,page_count=count,manufacturer='Atlas Copco',
            document_type=['technical_training','parts_book','parts_book','cabin_electrical_diagram','electrical_diagram'][i],
            document_number=['9852 9600 01','9853 6478 20D','9853 6786 20','9840 0424 09','9840 0424 18'][i],
            revision='UNKNOWN',copyright_year=[2008,2004,2011,'UNKNOWN','UNKNOWN'][i],
            applicable_models=[['L740','L740CR','L830','L630'],['ROC L8'],['ROC L8(30)LF'],['ROC L MKII'],['ROC L MKII']][i],
            model_scope=['Multi-model; per-page applicability required','ROC L8, actual fleet fit UNKNOWN','ROC L8(30)LF only','ROC L MKII, actual rig fit UNKNOWN','ROC L MKII, actual rig fit UNKNOWN'][i],
            cabin_scope='Bosal' if i in (0,3,4) else 'UNKNOWN',serial_scope='UNKNOWN',options_scope='UNKNOWN',
            text_extraction_mode='image' if i>2 else 'mixed' if i==0 else 'text',
            source_priority=['primary_technical','owner_default_parts','explicit_supplemental_lf','primary_cabin_electrical','primary_main_electrical'][i],
            related_documents=([IDS[4]] if i==3 else [IDS[3],IDS[0]] if i==4 else []),
            limitations=['Applicability to actual machine unconfirmed; metadata routes, PDF evidence required']+
                (['Training binder, not demonstrated complete repair manual','PDF 369 excluded; no OCR'] if i==0 else
                 ['Document number suffix 20D is preserved; its revision semantics are UNKNOWN'] if i==1 else
                 ['8992 0074 37 --> retained as unclassified cover identifier, not a machine serial'] if i==2 else [])))
    manifest=dict(schema_version=1,model='ROC-L8',page_numbering='one_based_pdf',documents=docs)
    write(folder/'document_manifest.json',manifest)
    write(ROOT/'reports/technical-docs/ROC-L8-DOCUMENT-MANIFEST.json',manifest)
    with pymupdf.open(ROOT/docs[0]['file_path']) as pdf:
        texts=[page.get_text() for page in pdf]
    # SHARED is restricted to chapters describing the common ROC L circuits;
    # exact variant claims still require the visible page/row label.
    scopes={str(n):['SHARED_ROC_L'] for n in range(1,369)}
    for n,text in enumerate(texts,1):
        named=sorted(set(re.findall(r'(?<![A-Z0-9])L(740CR|740|830|630|7TH|7|8)(?![A-Z0-9])',text.upper())))
        if named:scopes[str(n)]=['L'+v for v in named]
    def scope(start,end,models):
        for n in range(start,end+1): scopes[str(n)]=models
    scope(10,10,['L740','L740CR']);scope(11,11,['L830'])
    scope(58,58,['L830']);scope(64,64,['L740','L740CR']);scope(65,65,['L830'])
    scope(93,93,['L830']);scope(95,95,['L830']);scope(105,106,['L830'])
    scope(108,108,['L740','L740CR']);scope(109,109,['L830']);scope(116,116,['L740','L740CR'])
    scope(134,134,['L830']);scope(135,135,['L740CR'])
    scope(148,149,['L740','L740CR']);scope(150,151,['L830']);scope(152,152,['L740','L740CR'])
    scope(157,161,['L740','L740CR']);scope(163,163,['L830']);scope(164,164,['L740','L740CR'])
    scope(165,165,['L830']);scope(166,168,['L740','L740CR'])
    scope(170,170,['L740']);scope(171,171,['L830']);scope(172,172,['L740CR'])
    scope(176,188,['L740','L740CR']);scope(202,202,['L740']);scope(204,204,['L740','L740CR'])
    scope(206,206,['L740']);scope(209,209,['L740','L740CR']);scope(218,230,['L740'])
    scope(232,251,['L740CR','L830']);scope(252,254,['UNKNOWN'])
    scope(266,277,['L740']);scope(278,291,['L740CR']);scope(292,303,['L830','L630'])
    scope(304,311,['L7']);scope(312,315,['L8','L830'])
    scope(316,359,['UNKNOWN']);scope(360,362,['L7']);scope(363,365,['L740CR']);scope(366,368,['L8']);scope(369,369,['EXCLUDED'])
    sections={}
    for key,start,end,title,fa,kw in RANGES:
        models=sorted({m for p in range(start,end+1) for m in scopes[str(p)]})
        empty=[p for p in range(start,end+1) if not texts[p-1].strip()]
        kind='diagram' if 'diagrams' in key else 'training'
        sections[key]=dict(pdf_start=start,pdf_end=end,title=title,title_fa=fa,
            keywords_en=kw.split(),keywords_fa=fa.split(),topics=kw.split(),
            applicable_models=models,content_type=kind,
            extraction_mode='image' if len(empty)==end-start+1 else 'mixed',document_id=IDS[0],
            textless_pages=empty,heading_evidence=dict(pdf_page=start,text=texts[start-1][:400]),
            notes='Per-page model filter is authoritative. Shared pages can contain explicitly labelled variant alternatives.',
            limitations='Exact settings, connections and values require visible variant verification.',
            retrieval_enabled=key!='excluded_filemarket')
    write(folder/'manual_sections.json',dict(machine='ROC-L8',manual=str(ROOT/docs[0]['file_path']),pdf_pages=369,
        document_id=IDS[0],scope_policy='per_page',allowed_models=['L830','L8','SHARED_ROC_L'],
        known_models=['L740','L740CR','L830','L630','L7','L7TH','L8','SHARED_ROC_L','UNKNOWN','EXCLUDED'],
        page_models=scopes,excluded_pages=[369],sections=sections,
        limitations=['SHARED_ROC_L is common training context, not proof of universal settings',
          'UNKNOWN diagram applicability is returned only as visual discovery requiring inspection']))
    return manifest

if __name__=='__main__':
    print(json.dumps({'documents':len(build()['documents'])}))
