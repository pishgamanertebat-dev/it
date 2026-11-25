"""Pure report recognition and source spans; no model, workbook or identity access."""
import re
from tools.fleet.equipment_codes import canonical_input, DIGITS, ALIAS_VERSION

DEVICE = r'(?:دامپ\s*تراک|دامتراک|دامپ|تراک|بیل|لودر|بولدوزر|بولدزور|ماشین\s*آب|آبپاش)'
TOKEN = r'(?:[A-Za-z]{1,3}\s*)?[0-9]{1,4}'
DEVICE_CODE = re.compile(DEVICE + r'\s*[:：]?\s*(' + TOKEN + r')(?!\w)')
PREFIX_CODE = re.compile(r'(?<!\w)((?:EX|HD|WA|D|S|DG|MZ|TA|TR)\s*[0-9]{1,4})(?!\w)', re.I)
BARE_CODE = re.compile(r'^\s*[-_,•*\s]*(\d{3,4})\s*[:：]')
ITEM_MARKER = re.compile(r'^\s*[-•*]\s*\S')
MONTHS = ('فروردین','اردیبهشت','خرداد','تیر','مرداد','شهریور','مهر','آبان','آذر','دی','بهمن','اسفند')


def declared_report(raw):
    if not isinstance(raw, str) or not raw.strip():
        return False
    first = raw.strip().splitlines()[0].replace('ي','ی').replace('ك','ک')
    return bool(re.search(r'(?<!\w)گزارش(?!\w)', first) and '?' not in first and '؟' not in first
                and not re.search(r'(?:تایید|تأیید|تاید|قابل\s*ت)', first)
                and len(raw.strip().splitlines()) > 1)


def _source_spans(raw, *, allow_wrapping):
    spans = []
    offset = 0
    header_seen = False
    for original in raw.splitlines(keepends=True):
        line = original.rstrip('\r\n')
        if line.strip():
            if header_seen:
                spans.append((offset, offset + len(line)))
            header_seen = True
        offset += len(original)
    if not allow_wrapping or not spans:
        return spans
    first_start, first_end = spans[0]
    if not ITEM_MARKER.match(raw[first_start:first_end]):
        return spans

    # A marked report delimits bullets with markers. Keep wrapped text in
    # its original span, while still accepting a line with its own coded heading.
    blocks = [spans[0]]
    for start, end in spans[1:]:
        line = raw[start:end]
        heading = _normalized(line).lstrip()
        if (ITEM_MARKER.match(line) or DEVICE_CODE.match(heading)
                or PREFIX_CODE.match(heading) or BARE_CODE.match(heading)):
            blocks.append((start, end))
        else:
            blocks[-1] = (blocks[-1][0], end)
    return blocks


def _normalized(text):
    return text.translate(DIGITS).replace('ي','ی').replace('ك','ک').replace('\u200c',' ')


def work_items(raw, *, independent=False, karamali=False):
    items = []
    for start, end in _source_spans(raw, allow_wrapping=independent):
        line = raw[start:end]
        clean = _normalized(line)
        if karamali:
            clean = clean.replace('بولدورز', 'بولدوزر')
        matches = list(DEVICE_CODE.finditer(clean)) + list(PREFIX_CODE.finditer(clean))
        bare = BARE_CODE.search(clean)
        if bare:matches.append(bare)
        codes = {canonical_input(''.join(m[1].split())).upper() for m in matches}
        error = None
        if len(codes) > 1 and not independent:
            raise ValueError(f'بند {len(items)+1} بیش از یک کد دستگاه دارد؛ هیچ ردیفی ثبت نشد.')
        if len(codes) > 1:
            error = 'بند بیش از یک کد دستگاه دارد؛ کد و شرح همین مورد را مشخص کنید.'
        description = line
        replacements = []
        for match in matches:
            entered = ''.join(match[1].split())
            canonical = canonical_input(entered)
            if canonical != entered:
                replacement = canonical[2:] if entered.translate(DIGITS).isdecimal() else canonical
                replacements.append((match.start(1), match.end(1), replacement))
        for left, right, replacement in sorted(set(replacements), reverse=True):
            description = description[:left] + replacement + description[right:]
        if independent:
            description = description.replace('\r\n', '\n').replace('\r', '\n')
        missing_description = independent and not re.search(r'[\w]', _without_device(clean, matches))
        if missing_description or not description.strip() or len(description)>1800 or re.search(r'[\x00-\x08\x0b\x0c\x0e-\x1f]',description):
            error = 'شرح بند خالی یا نامعتبر است؛ شرح همین مورد را وارد کنید.'
            if not independent:
                raise ValueError(error)
        item = dict(code=next(iter(codes)) if len(codes) == 1 else None,description=description,raw=line,
                    span=dict(start=start,end=end))
        if independent:
            item['issue'] = error
            item['device_labels'] = [m[0][:m.start(1)-m.start()].strip(' \t\r\n:：')
                                     for m in DEVICE_CODE.finditer(clean)]
            if not codes and re.match(r'^\s*[-_,•*\s]*'+DEVICE+r'(?!\w)',clean):
                item['issue'] = 'کد دستگاه مشخص نیست؛ کد و شرح همین مورد را وارد کنید.'
        items.append(item)
    if not 1 <= len(items) <= 40:
        raise ValueError('گزارش باید بین یک تا چهل بند داشته باشد.')
    return items


def _without_device(line, matches):
    hidden = {i for match in matches for i in range(match.start(), match.end())}
    return ''.join(char for i, char in enumerate(line) if i not in hidden).strip(' -_,•*\t:：.،')


def parse(raw, current=None, *, independent=False, karamali=False):
    from . import entry_dates
    if not isinstance(raw,str) or not 1 <= len(raw) <= 16000 or not declared_report(raw):
        raise ValueError('متن کامل گزارش با عنوان و تاریخ روشن لازم است؛ متن اصلی نگه‌داری شد.')
    current = current or entry_dates.today()
    date_text = raw.strip().splitlines()[0].translate(DIGITS).replace('آ','ا')
    dates = re.findall(r'1[34]\d{2}[/.-]\d{1,2}[/.-]\d{1,2}',date_text)
    if not dates or karamali:
        for month,name in enumerate(MONTHS,1):
            for found in re.finditer(r'(?<!\d)(\d{1,2})\s*'+name.replace('آ','ا')+r'(?:\s*(1[34]\d{2}))?',date_text):
                year = found[2] or current[:4]
                if karamali and not found[2]:
                    short = re.match(r'\s+(\d{2})(?!\d)', date_text[found.end():])
                    if short:
                        year = current[:2] + short[1]
                dates.append(f'{year}/{month}/{found[1]}')
    if len(dates)!=1:
        raise ValueError('تاریخ گزارش یکتا و روشن نیست؛ هیچ ردیفی ثبت نشد.')
    date = entry_dates.validate_selected(dates[0],current)
    return dict(date=date,items=work_items(raw,independent=independent,karamali=karamali),raw_text=raw,alias_version=ALIAS_VERSION)
