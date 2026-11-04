"""Fill only date, names and codes; preserve blank handwritten report columns."""
from copy import copy
import re
from pathlib import Path

from openpyxl import load_workbook
from openpyxl.worksheet.page import PageMargins

from tools.fleet.car_wash.proposal import item_for, resolve_code
from tools.fleet.car_wash.source import read_source
from tools.fleet.greasing.source import clean
from tools.fleet.work_orders.core.db import connect_db
from tools.fleet.work_orders.core.paths import WORK_ORDER_TEMPLATE_ROOT

TEMPLATE = WORK_ORDER_TEMPLATE_ROOT / 'car_wash' / 'car_wash_work_order_v1.xlsx'


def manual_identity(code, name):
    code, name = clean(code), clean(name)
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,23}', code):
        raise ValueError('کد دستگاه باید کامل و شامل حروف لاتین یا عدد باشد.')
    if not name or len(name) > 100 or name.startswith(('=', '+', '-', '@')):
        raise ValueError('نام دستگاه را به‌صورت متن و حداکثر ۱۰۰ نویسه وارد کنید.')
    return code, name


def get_items(codes, actions=None):
    codes = list(codes)
    if not 1 <= len(codes) <= 100 or len(set(codes)) != len(codes):
        raise ValueError('بین ۱ تا ۱۰۰ دستگاه متمایز انتخاب کنید.')
    if actions is not None and set(actions) != set(codes):
        raise ValueError('فهرست دستگاه‌ها کامل نیست.')
    source = read_source()
    catalog = [item_for(m) for m in source['machines'] if m['code'] and not m['duplicate']]
    result = []
    con = connect_db()
    try:
        for raw in codes:
            action = actions[raw] if actions is not None else 'CAR_WASH'
            if isinstance(action, dict) and 'source_sha256' in action:
                action = dict(action)
                expected = action.pop('source_sha256')
                if expected != source['sha256']:
                    raise ValueError('فایل ساعت‌کار تغییر کرده است؛ پیشنهاد تازه بگیرید.')
                if action == {'action_code': 'CAR_WASH'}:
                    action = 'CAR_WASH'
            if isinstance(action, dict):
                if set(action) != {'action_code', 'machine_name', 'manual'} or action['action_code'] != 'CAR_WASH' or action['manual'] is not True:
                    raise ValueError('افزودن دستی دستگاه معتبر نیست.')
                code, name = manual_identity(raw, action['machine_name'])
                # Never override an ambiguous workbook identity with typed text.
                matches = [m for m in source['machines'] if code.casefold() in
                           {m['code'].casefold(), m.get('raw_code', '').casefold()}]
                if matches:
                    raise ValueError('این کد در منبع وجود دارد؛ دستگاه را از فهرست انتخاب کنید.')
                item = {'machine_code': code, 'machine_name': name,
                        'action_code': 'CAR_WASH', 'action_text': 'کارواش'}
            else:
                if action != 'CAR_WASH':
                    raise ValueError('نوع کار این حکم فقط کارواش است.')
                item = dict(resolve_code(raw, catalog))
            matches = con.execute('SELECT id FROM machines WHERE canonical_code COLLATE BINARY=?',
                                  (item['machine_code'],)).fetchall()
            if len(matches) > 1:
                raise ValueError('هویت دستگاه در فهرست ناوگان تکراری است.')
            item['machine_id'] = matches[0]['id'] if matches else None
            result.append(item)
    finally:
        con.close()
    if len({i['machine_code'] for i in result}) != len(result):
        raise ValueError('دستگاه تکراری انتخاب شده است.')
    return result


def build_document(*, output_path, jalali_date, items, shift='روزانه', work_order=None):
    book = load_workbook(TEMPLATE)
    try:
        sheet = book.active
        count = len(items)
        if not 1 <= count <= 100:
            raise ValueError('فهرست کارواش خالی یا بیش از حد مجاز است.')
        # Keep ten rows for handwriting; insert extra rows above the signature.
        if count > 10:
            sheet.insert_rows(13, count - 10)
            for row in range(13, count + 3):
                for col in range(1, 8):
                    sheet.cell(row, col)._style = copy(sheet.cell(12, col)._style)
        end = max(10, count) + 2
        for row in range(3, end + 1):
            sheet.row_dimensions[row].height = 40
            for col in range(1, 8):
                sheet.cell(row, col).value = row - 2 if col == 1 else None
        for row, item in enumerate(items, 3):
            sheet.cell(row, 2).value = item['machine_name']
            sheet.cell(row, 3).value = item['machine_code']
            # Long operator-entered names wrap instead of shrinking the print.
            sheet.row_dimensions[row].height = max(40, 14 * ((len(item['machine_name']) + 24) // 25))
        sheet['F1'] = 'تاریخ: ' + jalali_date.translate(str.maketrans('0123456789', '۰۱۲۳۴۵۶۷۸۹'))
        if 'F1:G1' not in {str(m) for m in sheet.merged_cells.ranges}:
            sheet.merge_cells('F1:G1')
        for row in sheet:
            for cell in row:
                font = copy(cell.font)
                font.name, font.sz = 'Arial', 11
                cell.font = font
                alignment = copy(cell.alignment)
                alignment.horizontal, alignment.vertical, alignment.wrap_text = 'center', 'center', True
                cell.alignment = alignment
        font = copy(sheet['A1'].font)
        font.sz = 15
        sheet['A1'].font = font
        sheet.row_dimensions[1].height = 34
        sheet.row_dimensions[2].height = 62
        for col, width in {'A': 5.5, 'B': 25, 'C': 10.5, 'D': 15, 'E': 8, 'F': 8, 'G': 10}.items():
            sheet.column_dimensions[col].width = width
        sheet.sheet_view.rightToLeft = True
        sheet.sheet_properties.pageSetUpPr.fitToPage = True
        sheet.page_setup.paperSize = sheet.PAPERSIZE_A4
        sheet.page_setup.orientation = 'portrait'
        sheet.page_setup.scale = None
        sheet.page_setup.fitToWidth, sheet.page_setup.fitToHeight = 1, 0
        sheet.page_margins = PageMargins(left=.24, right=.24, top=.3, bottom=.3, header=0, footer=0)
        sheet.print_options.horizontalCentered = True
        sheet.print_options.verticalCentered = False
        sheet.print_area = f'A1:G{end + 3}'
        sheet.print_title_rows = '1:2'
        # Move the signature's original explicit row height after inserted rows.
        sheet.row_dimensions[end + 3].height = 20
        output_path = Path(output_path)
        output_path.parent.mkdir(parents=True, exist_ok=True)
        book.save(output_path)
        return output_path
    finally:
        book.close()
