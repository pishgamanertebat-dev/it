"""Car-wash conversation on the shared work-order sessions and inline lifecycle.

Keep this module free of Excel dependencies: it also runs inside Hermes.
"""
import re

from tools.bale_ui import InlineKeyboardBuilder

STAGES = frozenset({'PROPOSAL', 'REMOVE', 'WASH_EDIT', 'WASH_NAME', 'WASH_CONFIRM', 'WASH_REVIEW', 'WASH_RESULT'})
PAGE_SIZE = 16


def render(proposal):
    lines = ['پیشنهاد کارواش', f"تاریخ حکم: {proposal['plan_date']}",
             f"آخرین داده: {proposal['cutoff']}", '', 'دستگاه‌های همین حکم:']
    for index, item in enumerate(proposal['items'], 1):
        component = item.get('components', {}).get('car_wash')
        detail = (f" | {component['value']:g} {component['unit']} از آخرین کارواش"
                  if component else ' | انتخاب دستی مسئول نت')
        lines.append(f"{index}) {item['machine_code']} — {item['machine_name']}{detail}")
        if component:
            lines.append('آخرین کارواش: ' + component['last_service'])
    if not proposal['items']:
        lines.append('فعلاً دستگاهی انتخاب نشده؛ از «ویرایش اکسل» دستگاه اضافه کنید.')
    lines.extend(['', f"نیازمند بررسی: {len(proposal.get('review', []))} مورد؛ از دکمهٔ «بررسی‌ها» ببینید."])
    lines.extend(proposal.get('source_warnings', []))
    return '\n'.join(lines)


def picker(session):
    count = len(session.proposal.get('catalog', []))
    pages = max(1, (count + PAGE_SIZE - 1) // PAGE_SIZE)
    session.edit_code_page = max(0, min(session.edit_code_page, pages - 1))
    return ('ویرایش اکسل کارواش\nدستگاه را انتخاب کنید یا کد کامل آن را بنویسید.\n'
            'برای دستگاه خارج از فهرست هم کد را بنویسید؛ سپس نام آن را می‌پرسم.\n'
            'افزودن فقط پس از «تأیید افزودن» اعمال می‌شود.\n'
            f'صفحهٔ {session.edit_code_page + 1} از {pages}')


def keyboard(session):
    from .keyboards import action
    stage = session.stage
    if stage == 'PROPOSAL':
        rows = [(action('wash_edit', '✏️ ویرایش اکسل'), action('remove', '❌ حذف دستگاه')),
                (action('wash_review', '🔎 بررسی‌ها'), action('wash_create', '✅ ساخت حکم و PDF')),
                (action('wash_menu', '↩️ بازگشت'), action('cancel', 'انصراف'))]
    elif stage == 'WASH_EDIT':
        items = session.proposal.get('catalog', [])
        selected = {i['machine_code'] for i in session.proposal['items']}
        start = session.edit_code_page * PAGE_SIZE
        buttons = [action(f'wash_unit_{i}', ('✅ ' if item['machine_code'] in selected else '➕ ') + item['machine_code'], stage)
                   for i, item in enumerate(items[start:start + PAGE_SIZE], start)]
        rows = [buttons[i:i + 2] for i in range(0, len(buttons), 2)]
        nav = []
        if start + PAGE_SIZE < len(items):
            nav.append(action('wash_next', 'صفحه بعد ➡️', stage))
        if start:
            nav.append(action('wash_prev', '⬅️ صفحه قبل', stage))
        if nav:
            rows.append(nav)
        rows.append((action('wash_back', '↩️ بازگشت به پیشنهاد', stage),))
    elif stage in {'WASH_NAME', 'WASH_CONFIRM'}:
        rows = []
        if stage == 'WASH_CONFIRM':
            rows.append((action('wash_apply', '✅ تأیید افزودن', stage),))
        rows.append((action('wash_back', '↩️ بازگشت', stage),))
    elif stage == 'WASH_REVIEW':
        rows = [(action('wash_back', '↩️ بازگشت به پیشنهاد', stage),)]
    elif stage == 'WASH_RESULT':
        rows = [(action('review_confirm', '✅ تایید بررسی فایل', stage),),
                (action('wash_preview', '📄 دریافت دوباره PDF', stage),
                 action('wash_order_edit', '✏️ ویرایش همین حکم', stage)),
                (action('wash_new', '↩️ بازگشت به کارواش', stage),)]
    else:
        return None
    return InlineKeyboardBuilder('work_order', rows)


def command(action_name, proposal=None, order_no=''):
    if action_name.startswith('wash_unit_'):
        index = int(action_name[len('wash_unit_'):])
        return proposal['catalog'][index]['machine_code']
    if action_name == 'wash_preview':
        return 'ارسال مجدد ' + order_no
    if action_name == 'wash_order_edit':
        return 'ویرایش ' + order_no
    return {'wash_edit': 'ویرایش اکسل', 'wash_review': 'بررسی ها', 'wash_create': 'ساخت حکم',
            'wash_menu': 'حکم کار', 'wash_back': 'برگشت', 'wash_apply': 'تایید افزودن',
            'wash_next': 'صفحه بعد', 'wash_prev': 'صفحه قبل', 'wash_new': 'کارواش'}[action_name]


def _clean(value):
    return ' '.join(str(value).replace('\u200c', ' ').translate(str.maketrans('كي۰۱۲۳۴۵۶۷۸۹٠١٢٣٤٥٦٧٨٩', 'کی01234567890123456789')).split())


def handle(session, text, *, accepted=False):
    """Return (reply, optional worker request); never write the Excel source."""
    proposal = session.proposal
    stage = session.stage
    if text in {'برگشت', 'بازگشت', 'بازگشت به پیشنهاد'}:
        session.additions = []
        session.removal_selection = None
        session.stage = 'WASH_EDIT' if stage in {'WASH_NAME', 'WASH_CONFIRM'} else 'PROPOSAL'
        return (picker(session) if session.stage == 'WASH_EDIT' else render(proposal)), None
    if stage == 'PROPOSAL':
        if text in {'ویرایش اکسل', 'افزودن/ویرایش', 'اضافه', 'افزودن'}:
            session.stage, session.edit_code_page = 'WASH_EDIT', 0
            return picker(session), None
        if text in {'بررسی ها', 'بررسی'}:
            session.stage = 'WASH_REVIEW'
            lines = ['نیازمند بررسی — کارواش']
            lines.extend(f"{i['code']}: {i['reason']}" for i in proposal.get('review', []))
            if len(lines) == 1:
                lines.append('موردی وجود ندارد.')
            lines.append('با وجود ابهام سابقه، افزودن دستی از «ویرایش اکسل» ممکن است.')
            return '\n'.join(lines), None
        if text == 'حذف':
            if not proposal['items']:
                raise ValueError('فهرست حکم خالی است؛ ابتدا دستگاه اضافه کنید.')
            session.stage, session.removal_selection = 'REMOVE', None
            return None, None  # Shared MultiSelect renders the removal screen.
        if text in {'ساخت حکم', 'تایید', 'تأیید'}:
            if not proposal['items']:
                raise ValueError('فهرست خالی است؛ دستگاه اضافه کنید.')
            return '', {'action': 'create', 'work_order_type': 'CAR_WASH',
                        'machine_codes': [i['machine_code'] for i in proposal['items']],
                        'jalali_date': proposal['plan_date'], 'shift': 'روزانه',
                        'proposal': {k: proposal[k] for k in ('source_sha256', 'manual_items', 'plan_date', 'cutoff')}
                        | ({'revision_of': proposal['revision_of']} if proposal.get('revision_of') else {})}
    elif stage == 'WASH_EDIT':
        if text in {'صفحه بعد', 'صفحه قبل'}:
            session.edit_code_page += 1 if text == 'صفحه بعد' else -1
            return picker(session), None
        code = _clean(text)
        catalog = proposal.get('catalog', [])
        matches = [i for i in catalog if i['machine_code'] == code]
        if not matches:
            matches = [i for i in catalog if i['machine_code'].casefold() == code.casefold()]
        if not matches and code.isdecimal():
            matches = [i for i in catalog if re.sub(r'^[A-Za-z]+', '', i['machine_code']) == code]
        if len(matches) > 1:
            raise ValueError('کد مبهم است؛ کد کامل را از دکمه‌ها انتخاب کنید.')
        if not matches and any(i.get('source_code', '').casefold() == code.casefold() for i in catalog):
            raise ValueError('کد مدل مبهم است؛ دستگاه متمایز مانند D151 یا D152 را انتخاب کنید.')
        if matches:
            item = dict(matches[0])
            evaluated = next((i for i in proposal['evaluations'] if i['machine_code'] == item['machine_code']), None)
            if evaluated:
                item = dict(evaluated)
            session.additions = [item]
            session.stage = 'WASH_CONFIRM'
            return f"افزودن {item['machine_code']} — {item['machine_name']} به همین حکم را تأیید کنید.", None
        if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,23}', code):
            raise ValueError('کد کامل دستگاه را با حروف لاتین یا عدد بنویسید.')
        session.edit_machine_code, session.stage = code, 'WASH_NAME'
        return f'کد {code} در فهرست نیست؛ نام دقیق دستگاه را بنویسید.', None
    elif stage == 'WASH_NAME':
        name = _clean(text)
        if not name or len(name) > 100 or name.startswith(('=', '+', '-', '@')):
            raise ValueError('نام دستگاه را به‌صورت متن و حداکثر ۱۰۰ نویسه وارد کنید.')
        item = {'machine_code': session.edit_machine_code, 'machine_name': name,
                'action_code': 'CAR_WASH', 'action_text': 'کارواش', 'manual': True}
        session.additions, session.stage = [item], 'WASH_CONFIRM'
        return f"افزودن دستی {item['machine_code']} — {name} به همین حکم را تأیید کنید.", None
    elif stage == 'WASH_CONFIRM' and text in {'تایید افزودن', 'تأیید افزودن'}:
        if not accepted:
            raise ValueError('افزودن را با دکمهٔ تأیید همین فرم ثبت کنید.')
        item = session.additions[0]
        if not any(i['machine_code'] == item['machine_code'] for i in proposal['items']):
            if len(proposal['items']) >= 100:
                raise ValueError('حداکثر ۱۰۰ دستگاه در یک حکم مجاز است.')
            proposal['items'].append(item)
            if item.get('manual'):
                proposal['manual_items'][item['machine_code']] = item['machine_name']
        session.additions, session.stage = [], 'WASH_EDIT'
        return 'افزودن ثبت شد.\n' + picker(session), None
    elif stage == 'REMOVE':
        from tools.bale_ui import MultiSelect
        selection = MultiSelect(**session.removal_selection)
        if text in {'تایید حذف', 'تأیید حذف'}:
            selected = selection.confirmed_ids(i['machine_code'] for i in proposal['items'])
            proposal['items'] = [i for i in proposal['items'] if i['machine_code'] not in selected]
            for code in selected:
                proposal['manual_items'].pop(code, None)
            session.stage, session.removal_selection = 'PROPOSAL', None
            return render(proposal), None
        raise ValueError('دستگاه‌ها را با دکمه‌ها انتخاب و سپس «تأیید حذف» را بزنید.')
    return 'از دکمه‌های همین مرحله انتخاب کنید.', None
