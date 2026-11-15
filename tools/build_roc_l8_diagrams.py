"""Transcribe visually reviewed title blocks; unknown circuitry stays UNKNOWN.

Review artifacts: runtime/roc-l8-onboarding/sheets-1..11.png (D4/D5);
each tile was rendered directly from the registered PDF, without OCR.
"""
import json
from pathlib import Path
from build_roc_l8_package import ROOT, IDS, write

D4_TITLES=[
 '24VDC POWER SUPPLY','Power board / Touch panel - X50 layout','Power connection',
 'X50 connection','AC system and heater connection','Wiper, sprinkler and chair heat connection',
 'Radio, WORKING LIGHT, 12V out connection','Cab. light, ignition-emergency stop connection',
 'Switch layout DTH','Switch layout TH','Switch layout CR','Switch connection',
 'Drill Stick connection','Switch connection','Cable connection','Cable connection']
D5_TITLES=[
 'CROSS REFERENCES LIST','GENERAL INFORMATION','24VDC POWER SUPPLY','24VDC POWER SUPPLY','24VDC POWER SUPPLY',
 'IGNITION KEY, EMERGENCY STOP','ECM (ELECTRONIC CONTROL MODULE)','WORKLIGHTS','0V IN CABIN, SPARE WIRING',
 'A1 LIGHT, M20, H226, 24V TO X-BOXES','CLIMATE CONTROL SYSTEM','CLIMATE CONTROL SYSTEM',
 'CPU PROGRAM. CONNECTORS X31 AND X32 (A1)','CPU PROGRAMMING CONNECTOR X33 (INSIDE A1)',
 'CABIN MODULE D550','CABIN MODULE D550','CABIN MODULE D551','CABIN MODULE D551',
 'CABIN MODULE D553','CABIN MODULE D553','CABIN MODULE D552','CABIN MODULE D552',
 'CABIN MODULE D554','CABIN MODULE D554','DISPLAY D501 (PLACED IN CABIN)','D169, D171, D173',
 'CPU INPUTS - X30','CPU INPUTS - X33 - X34 - X35','CPU INPUTS - X35','CPU INPUTS - X36',
 'CPU INPUTS - X40','CPU INPUTS - X40','CPU INPUTS - X43','CPU OUTPUTS - X31','CPU OUTPUTS - X31',
 'CPU OUTPUTS - X32','CPU OUTPUTS - X32','CPU OUTPUTS - X33','CPU OUTPUTS - X34',
 'CPU OUTPUTS - X35, -X36','CPU OUTPUTS - X41','CPU OUTPUTS - X42','CPU OUTPUTS - X43',
 'CPU OUTPUTS - A1','OPTION RC (RIFFLE)','OPTION RC (CONE)','OPTION SCREW SAMPLER (HYD)']
# Independently checked against every D1 title block in d1-sheets-1..6.png.
D1_TITLES='''CROSS REFERENCES LIST|GENERAL INFORMATION|24VDC POWER SUPPLY|24VDC POWER SUPPLY|24VDC POWER SUPPLY|IGNITION KEY, EMERGENCY STOP|ECM (ELECTRONIC CONTROL MODULE)|WORKLIGHTS|0V IN CABIN, SPARE WIRING|A1 LIGHT, M20, H226, 24V TO X-BOXES|CLIMATE CONTROL SYSTEM|CLIMATE CONTROL SYSTEM|CPU PROGRAM. CONNECTORS X31 AND X32 (A1)|CPU PROGRAMMING CONNECTOR X33 (INSIDE A1)|CABIN MODULE D550|CABIN MODULE D550|CABIN MODULE D551|CABIN MODULE D551|CABIN MODULE D553|CABIN MODULE D553|CABIN MODULE D552|CABIN MODULE D552|CABIN MODULE D554|CABIN MODULE D554|DISPLAY D501 (PLACED IN CABIN)|D169, D171, D173|CPU INPUTS - X30|CPU INPUTS - X33 - X34 - X35|CPU INPUTS - X35|CPU INPUTS - X36|CPU INPUTS - X40|CPU INPUTS - X40|CPU INPUTS - X43|CPU OUTPUTS - X31|CPU OUTPUTS - X31|CPU OUTPUTS - X32|CPU OUTPUTS - X32|CPU OUTPUTS - X33|CPU OUTPUTS - X34|CPU OUTPUTS - X35, -X36|CPU OUTPUTS - X41|CPU OUTPUTS - X42|CPU OUTPUTS - X43|CPU OUTPUTS - A1'''.split('|')

def build():
    sheets=[]
    for di,titles in ((3,D4_TITLES),(4,D5_TITLES)):
        for page,title in enumerate(titles,1):
            refs=[];components=[];connectors=[]
            import re
            for token in re.findall(r'\b[A-Z]+\d+[A-Z]*\b',title):
                (connectors if token.startswith('X') else components).append(token)
            if di==4 and (page==6 or 15<=page<=24):refs=[dict(document_id=IDS[3],sheet='UNKNOWN',basis='Visible SEE DRAWING 9840 0424 09')]
            if di==4 and page==1:
                components.append('B362');refs.append(dict(document_id=IDS[4],sheet=30,basis='B362 30 visually read on cross references list'))
            if di==4 and page==30:
                components.append('B362')
            if di==3:
                revision=['D','B','B','B','B','B','C','C','C','C','C','C','C','D','C','C'][page-1]
            else:revision='UNKNOWN'
            sheets.append(dict(document_id=IDS[di],pdf_page=page,printed_sheet_number=f'{page:02}',
                sheet_total=len(titles),drawing_number='9840 0424 09' if di==3 else '9840 0424 18',
                drawing_title=title,revision=revision,applicable_model='ROC L MKII',cabin='Bosal',
                component_identifiers=components,connector_identifiers=connectors,referenced_sheets=refs,
                keywords_fa=['نقشه','برق','کابین' if di==3 else 'اصلی','سیم‌کشی','کانکتور' if connectors else 'مدار'],
                notes='Title block and sheet visually verified. Pin/wire/component completeness NOT verified; inspect actual sheet. Actual machine fit UNKNOWN.',
                verification_status='VISUAL_TITLE_AND_SHEET_VERIFIED',
                verification_artifact=f'runtime/roc-l8-onboarding/sheets-{(page-1+(16 if di==4 else 0))//6+1}.png'))
    # D1 remains independent, including its original 44-sheet electrical version.
    # Identity records are deliberately not copied from the separate 47-sheet PDF.
    for page in range(304,369):
        electrical=316<=page<=359
        air=page>=360
        sheets.append(dict(document_id=IDS[0],pdf_page=page,
            printed_sheet_number=f'{page-315:02}' if electrical else str((page-360)%3+1) if air else 'UNKNOWN',sheet_total=44 if electrical else 3 if air else 'UNKNOWN',
            drawing_number='9840 0424 18' if electrical else ('9840 0423 15' if page<363 else '9840 0424 06' if page<366 else '9840 0423 14') if air else 'UNKNOWN',
            drawing_title=(D1_TITLES[page-316] if electrical else
                'Hydraulic diagram' if page<316 else 'Air / Pneumatic diagram'),revision='UNKNOWN',
            applicable_model='L7' if page<312 or 360<=page<=362 else 'L740CR' if 363<=page<=365 else 'L8' if page<316 or page>=366 else 'ROC L MKII',
            cabin='Bosal' if page<316 or electrical else 'UNKNOWN',component_identifiers=[],connector_identifiers=[],
            referenced_sheets=[],keywords_fa=['نقشه','برق' if electrical else 'هیدرولیک' if page<316 else 'هوا','پنوماتیک'],
            notes='PDF page recovery record. Unverified printed identity fields remain UNKNOWN; source not merged with D5.',
            verification_status='VISUAL_TITLE_AND_SHEET_VERIFIED' if electrical or air else 'PDF_PAGE_REGISTERED_TEXT_MODEL_VERIFIED',
            verification_artifact=f'runtime/roc-l8-onboarding/d1-sheets-{(page-316)//8+1}.png' if page>=316 else 'actual PDF text and model label'))
    write(ROOT/'ROC-L8/diagram_index.json',dict(schema_version=1,page_numbering='one_based_pdf',sheets=sheets))
    return sheets

if __name__=='__main__':print(len(build()))
