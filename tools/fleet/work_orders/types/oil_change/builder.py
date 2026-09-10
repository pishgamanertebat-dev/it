"""Manual PM orders. Each model retains its own immutable source template."""
import re
from pathlib import Path
from zipfile import ZipFile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

SOURCE = Path('E:/Function/دستورکار PM-785-5.xlsx')
TEMPLATES = {
    'HD785-5': (SOURCE, 'دامپتراک کوماتسو 5-785', 'HD701'),
    'HD465-7R': (Path('E:/Function/دستورکار PM-465-7r.xlsx'), 'دامپتراک کوماتسو 465-7R', 'HD463'),
    'HD785-7': (Path('E:/Function/دستور کار PM-785-7..xlsx'), 'دامپتراک کومالتسو 785-7', 'hd708'),
    'PC800-7': (Path('E:/Function/دستور کار PM-800-7.xlsx'), 'بیل مکانیکی کوماتسو 7-800', {'1400': 'EX1251'}),
    'R330-9': (Path('E:/Function/دستورکار PM-330-9.xlsx'), 'بیل مکانیکی هیوندا 330-9', 'EX332'),
    'PC850-8': (Path('E:/Function/دستور کار PM-850-8.xlsx'), 'بیل مکانیکی کوماتسو 850-8', None),
    'WA600-6': (Path('E:/Function/دستورکار PM-600-6.xlsx'), 'لودر 6-600', 'W601'),
    'WA470-3': (Path('E:/Function/دستورکار PM-470-3.xlsx'), 'لودر 3-470', 'W472'),
}
INTERVALS = tuple(range(200, 2001, 200))
NS = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships'


def normalize_code(value, model='HD785-5'):
    if model not in TEMPLATES:
        raise ValueError('مدل حکم تعویض روغن پشتیبانی نمی‌شود.')
    prefix = 'W' if model.startswith('WA') else ('HD' if model.startswith('HD') else 'EX')
    code = str(value).strip().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789')).upper()
    if prefix == 'W' and code.startswith('WA'):
        code = 'W' + code[2:]
    if not re.fullmatch(rf'(?:{prefix})?[0-9]{{1,10}}', code):
        raise ValueError(f'کد یک دستگاه با پیشوند {prefix} یا فقط شمارهٔ آن را وارد کنید.')
    return code if code.startswith(prefix) else prefix + code


def normalize_interval(value):
    text = str(value).strip().translate(str.maketrans('۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', '01234567890123456789'))
    if text not in {str(n) for n in INTERVALS}:
        raise ValueError('نوبت سرویس باید یکی از اعداد ۲۰۰، ۴۰۰، ۶۰۰، ۸۰۰، ۱۰۰۰، ۱۲۰۰، ۱۴۰۰، ۱۶۰۰، ۱۸۰۰ یا ۲۰۰۰ باشد.')
    return int(text)


def action_for(model, interval):
    if model not in TEMPLATES:
        raise ValueError('مدل حکم تعویض روغن پشتیبانی نمی‌شود.')
    interval = normalize_interval(interval)
    # Preserve existing HD785-5 orders and stored action codes.
    return f'OIL_CHANGE_{interval}' if model == 'HD785-5' else f'OIL_CHANGE_{model}_{interval}'


def parse_action(action):
    action = str(action)
    for model in TEMPLATES:
        prefix = 'OIL_CHANGE_' if model == 'HD785-5' else f'OIL_CHANGE_{model}_'
        if action.startswith(prefix) and action[len(prefix):] in {str(n) for n in INTERVALS}:
            return model, normalize_interval(action[len(prefix):])
    raise ValueError('مدل یا نوبت سرویس حکم معتبر نیست.')


def get_items(codes, actions=None):
    codes = list(codes)
    if len(codes) != 1:
        raise ValueError('فعلاً هر حکم تعویض روغن برای یک دستگاه صادر می‌شود.')
    if not actions or len(actions) != 1:
        raise ValueError('نوبت سرویس دستگاه را مشخص کنید.')
    action = str(next(iter(actions.values())))
    model, interval = parse_action(action)
    code = normalize_code(codes[0], model)
    if set(actions) != {code}:
        raise ValueError('نوبت سرویس دستگاه را مشخص کنید.')
    return [{'machine_code': code, 'machine_name': TEMPLATES[model][1],
             'action_code': action_for(model, interval), 'action_text': f'{model} — سرویس {interval} ساعتی'}]


def build_document(*, output_path, jalali_date, items, shift='روزانه'):
    if len(items) != 1:
        raise ValueError('هر حکم باید دقیقاً یک دستگاه داشته باشد.')
    item = items[0]
    model, interval = parse_action(item['action_code'])
    code = normalize_code(item['machine_code'], model)
    interval = str(interval)
    source_path, model_title, sample_code = TEMPLATES[model]
    if isinstance(sample_code, dict):
        sample_code = sample_code.get(interval)
    output_path = Path(output_path)
    if output_path.resolve() in {t[0].resolve() for t in TEMPLATES.values()}:
        raise ValueError('فایل الگو نباید بازنویسی شود.')
    # Edit the OOXML package directly: openpyxl drops this template's header/footer.
    # Unreferenced package parts are retained to preserve printer and drawing assets.
    with ZipFile(source_path) as source:
        workbook = source.read('xl/workbook.xml').decode('utf-8')
        root = ET.fromstring(workbook)
        sheets = root.find(f'{{{NS}}}sheets')
        if {s.get('name') for s in sheets} != {str(n) for n in INTERVALS}:
            raise ValueError('شیت‌های الگوی انتخاب‌شده با نوبت‌های سرویس تطابق ندارند.')
        selected = next(s for s in sheets if s.get('name') == interval)
        rid = selected.get(f'{{{REL}}}id')
        relations = ET.fromstring(source.read('xl/_rels/workbook.xml.rels'))
        target = next(r.get('Target') for r in relations if r.get('Id') == rid)
        sheet_path = target.lstrip('/') if target.startswith('/') else 'xl/' + target
        sheet = source.read(sheet_path).decode('utf-8')
        sheet = _single_page_a4(sheet)
        cell = ET.fromstring(sheet).find(f'.//{{{NS}}}c[@r="F3"]')
        if cell is None:
            raise ValueError('محل کد دستگاه در الگو تغییر کرده است؛ الگو بررسی شود.')
        strings = ET.fromstring(source.read('xl/sharedStrings.xml'))
        if cell.get('t') == 's':
            index = int(cell.find(f'{{{NS}}}v').text)
            current_code = ''.join(strings[index].itertext()).strip()
        elif cell.get('t') is None and len(cell) == 0:
            current_code = None
        else:
            raise ValueError('ساختار سلول کد دستگاه در الگو تغییر کرده است؛ الگو بررسی شود.')
        if current_code != sample_code:
            raise ValueError('کد نمونه در الگو تغییر کرده است؛ الگو بررسی شود.')
        model_cell = ET.fromstring(sheet).find(f'.//{{{NS}}}c[@r="C3"]')
        if model_cell is None or model_cell.get('t') != 's' or ''.join(strings[int(model_cell.find(f'{{{NS}}}v').text)].itertext()).strip() != model_title:
            raise ValueError('عنوان مدل در الگو با مدل انتخاب‌شده تطابق ندارد.')
        style = cell.get('s')
        style_attr = f' s="{style}"' if style else ''
        replacement = f'<c r="F3"{style_attr} t="inlineStr"><is><t>{escape(code)}</t></is></c>'
        sheet, count = re.subn(r'<c\b(?=[^>]*\br="F3")[^>]*?(?:/>|>.*?</c>)', lambda _: replacement, sheet, flags=re.S)
        if count != 1:
            raise ValueError('محل کد دستگاه در الگو یکتا نیست.')
        workbook = re.sub(r'<sheet\b[^>]*/>', lambda m: m[0] if ET.fromstring(
            '<root xmlns:r="' + REL + '">' + m[0] + '</root>')[0].get('name') == interval else '', workbook)
        workbook = re.sub(r'\b(activeTab|firstSheet)="\d+"', r'\1="0"', workbook)
        # Future templates with scoped names need an explicit mapping, not a guess.
        if root.find(f'{{{NS}}}definedNames') is not None:
            raise ValueError('نام‌گذاری محدوده‌های الگو تغییر کرده است؛ الگو بررسی شود.')
        output_path.parent.mkdir(parents=True, exist_ok=True)
        with ZipFile(output_path, 'x') as output:
            for entry in source.infolist():
                data = source.read(entry.filename)
                if entry.filename == 'xl/workbook.xml':
                    data = workbook.encode('utf-8')
                elif entry.filename == sheet_path:
                    data = sheet.encode('utf-8')
                output.writestr(entry, data)
    return output_path


def _single_page_a4(sheet):
    """Change print settings only; preserve the template's header/drawing XML."""
    def setup(match):
        tag = re.sub(r'\s+(?:paperSize|scale|fitToWidth|fitToHeight|orientation|usePrinterDefaults)="[^"]*"', '', match[0])
        return tag[:-2] + ' paperSize="9" orientation="portrait" fitToWidth="1" fitToHeight="1" usePrinterDefaults="0"/>'

    sheet, count = re.subn(r'<pageSetup\b[^>]*/>', setup, sheet)
    if count != 1:
        raise ValueError('Expected one pageSetup in the work-order template.')
    fit = '<pageSetUpPr fitToPage="1"/>'
    if re.search(r'<pageSetUpPr\b', sheet):
        sheet = re.sub(r'<pageSetUpPr\b[^>]*/>', lambda m: re.sub(
            r'\s+fitToPage="[^"]*"', '', m[0])[:-2] + ' fitToPage="1"/>', sheet)
    elif '</sheetPr>' in sheet:
        sheet = sheet.replace('</sheetPr>', fit + '</sheetPr>', 1)
    elif re.search(r'<sheetPr\b[^>]*/>', sheet):
        sheet = re.sub(r'<sheetPr\b[^>]*/>', lambda m: m[0][:-2] + '>' + fit + '</sheetPr>', sheet, count=1)
    else:
        sheet = re.sub(r'<worksheet\b[^>]*>', lambda m: m[0] + '<sheetPr>' + fit + '</sheetPr>', sheet, count=1)
    return sheet
