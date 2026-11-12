"""Pure report recognition and source spans; no model, workbook or identity access."""
import re
from tools.fleet.equipment_codes import canonical_input, DIGITS, ALIAS_VERSION

DEVICE = r'(?:دامپ\s*تراک|دامتراک|دامپ|تراک|بیل|لودر|بولدوزر|بولدزور|ماشین\s*آب|آبپاش)'
TOKEN = r'(?:[A-Za-z]{1,3}\s*)?[0-9]{1,4}'
DEVICE_CODE = re.compile(DEVICE + r'\s*[:：]?\s*(' + TOKEN + r')(?!\w)')
PREFIX_CODE = re.compile(r'(?<!\w)((?:EX|HD|WA|D|S|DG|MZ|TA|TR)\s*[0-9]{1,4})(?!\w)', re.I)
BARE_CODE = re.compile(r'^\s*[-_,•*\s]*(\d{3,4})\s*[:：]')
MONTHS = ('فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند')


def declared_report(raw):
    if not isinstance(raw, str) or not raw.strip():
        return False
    first = raw.strip().splitlines()[0].replace('ي','ی').replace('ك','ک')
    return bool(re.search(r'(?<!\w)گزارش(?!\w)', first) and '?' not in first and '؟' not in first
                and not re.search(r'(?:تایید|تأیید|تاید|قابل\s*ت)', first)
                and len(raw.strip().splitlines()) > 1)


def work_items(raw):
    items = []
    offset = 0
    first_line = True
    for original in raw.splitlines(keepends=True):
        line = original.rstrip('\r\n')
        if not line.strip() or first_line:
            if line.strip():first_line = False
            offset += len(original)
            continue
        clean = line.translate(DIGITS).replace('ي','ی').replace('ك','ک').replace('\u200c',' ')
        matches = list(DEVICE_CODE.finditer(clean)) + list(PREFIX_CODE.finditer(clean))
        bare = BARE_CODE.search(clean)
        if bare:matches.append(bare)
        codes = {canonical_input(m[1].replace(' ', '')).upper() for m in matches}
        if len(codes) > 1:
            raise ValueError(f'بند {len(items)+1} بیش از یک کد دستگاه دارد؛ هیچ ردیفی ثبت نشد.')
        description = line
        replacements = []
        for match in matches:
            entered = match[1].replace(' ', '')
            canonical = canonical_input(entered)
            if canonical != entered:
                replacement = canonical[2:] if entered.translate(DIGITS).isdecimal() else canonical
                replacements.append((match.start(1), match.end(1), replacement))
        for start, end, replacement in sorted(set(replacements), reverse=True):
            description = description[:start] + replacement + description[end:]
        if not description.strip() or len(description)>1800 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',description):
            raise ValueError('شرح بند معتبر نیست؛ متن اصلی محفوظ است.')
        items.append(dict(code=next(iter(codes),None),description=description,raw=line,
                          span=dict(start=offset,end=offset+len(line))))
        offset += len(original)
    if not 1 <= len(items) <= 40:
        raise ValueError('گزارش باید بین یک تا چهل بند داشته باشد.')
    return items


def parse(raw, current=None):
    from . import entry_dates
    if not isinstance(raw,str) or not 1 <= len(raw) <= 16000 or not declared_report(raw):
        raise ValueError('متن کامل گزارش با عنوان و تاریخ روشن لازم است؛ متن اصلی نگه‌داری شد.')
    current = current or entry_dates.today()
    date_text = raw.strip().splitlines()[0].translate(DIGITS).replace('آ','ا')
    dates = re.findall(r'1[34]\d{2}[/.-]\d{1,2}[/.-]\d{1,2}',date_text)
    if not dates:
        for month,name in enumerate(MONTHS,1):
            for found in re.finditer(r'(?<!\d)(\d{1,2})\s*'+name.replace('آ','ا')+r'(?:\s*(1[34]\d{2}))?',date_text):
                dates.append(f'{found[2] or current[:4]}/{month}/{found[1]}')
    if len(dates)!=1:
        raise ValueError('تاریخ گزارش یکتا و روشن نیست؛ هیچ ردیفی ثبت نشد.')
    date = entry_dates.validate_selected(dates[0],current)
    return dict(date=date,items=work_items(raw),raw_text=raw,alias_version=ALIAS_VERSION)
