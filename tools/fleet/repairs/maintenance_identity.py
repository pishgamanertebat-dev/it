"""Row-bound identities in default-hidden Excel notes; no helper sheets/columns.

Notes move with their anchor cell in a full-row sort. A partial sort, moved
sheet, changed row, copied identity or removed note is a conflict, never a match.
The external publication journal remains the authority for expected identities.
"""
import io
import json
import re
import uuid
import warnings
import zipfile
from xml.etree import ElementTree as ET

import openpyxl
from openpyxl.comments import Comment
from openpyxl.workbook.defined_name import DefinedName

from .entry_service import EntryError
from .maintenance_baseline import digest, encoded, states, xml

MARKER = '_KOMATSO_MAINTENANCE_RECORDS_V1'
VERSION = '"row-notes-v1"'
AUTHOR = 'KOMATSO-MAINTENANCE-V1'
PREFIX = 'KOMATSO-MAINTENANCE-V1\n'
NAMESPACE = uuid.UUID('da670f71-ecaf-4f70-87a0-d3c0de57f7ce')


def active(book):
    name = book.defined_names.get(MARKER)
    if name is None:
        return False
    if name.attr_text != VERSION or name.hidden is not True:
        raise EntryError('IDENTITY_SCHEMA_INVALID')
    return True


def enable(book):
    if not active(book):
        book.defined_names.add(DefinedName(MARKER, attr_text=VERSION, hidden=True))


def row_hash(sheet, row):
    # Include every operational cell, not just name/date/description. Ignore
    # physical row number and formatting so whole-row moves stay identifiable.
    values = [(c.column, c.value, c.data_type) for c in sheet[row]
              if c.value is not None or c.data_type == 'f']
    return digest(encoded(values).encode())


def attach(book, saved, operation, group):
    if not re.fullmatch('[0-9a-f]{32}', operation):
        raise EntryError('IDENTITY_OPERATION_INVALID')
    sheet, row = book[saved['sheet']], saved['row']
    from .maintenance_service import layout
    anchor = sheet.cell(row, layout(sheet)[1]['date'])
    if anchor.comment is not None:
        raise EntryError('IDENTITY_ANCHOR_OCCUPIED')
    identity = dict(id=uuid.uuid5(NAMESPACE, f'{operation}:{group}:{sheet.title}').hex,
                    operation=operation, group=group, sheet=sheet.title,
                    row_sha256=row_hash(sheet, row))
    anchor.comment = Comment(PREFIX + encoded(identity), AUTHOR)
    return identity


def inventory(book, *, preserve_notes=False):
    found = {}
    for sheet in book:
        if active(book) and sheet.page_setup.cellComments not in (None, 'none'):
            raise EntryError('IDENTITY_NOTES_PRINTING_UNSUPPORTED')
        from .maintenance_service import layout
        header, columns = layout(sheet)
        for cell in list(sheet._cells.values()):
            note = cell.comment
            if not note:
                continue
            if note.author != AUTHOR or not note.text.startswith(PREFIX):
                if preserve_notes and note.author != AUTHOR and not note.text.startswith(PREFIX):
                    continue
                raise EntryError('UNSUPPORTED_EXISTING_NOTE')
            try:
                item = json.loads(note.text[len(PREFIX):])
                expected = uuid.uuid5(NAMESPACE, f"{item['operation']}:{item['group']}:{item['sheet']}").hex
                valid = (set(item) == {'id','operation','group','sheet','row_sha256'}
                         and re.fullmatch('[0-9a-f]{32}', item['operation'])
                         and type(item['group']) is int and item['group'] >= 0
                         and item['id'] == expected and item['sheet'] == sheet.title
                         and cell.column == columns['date'] and cell.row > header
                         and item['row_sha256'] == row_hash(sheet, cell.row))
            except (KeyError, ValueError, TypeError):
                valid = False
            if not valid:
                raise EntryError('IDENTITY_ROW_CHANGED_OR_MOVED')
            if item['id'] in found:
                raise EntryError('IDENTITY_DUPLICATE')
            found[item['id']] = item
    if found and not active(book):
        raise EntryError('IDENTITY_SCHEMA_MISSING')
    return found


def load(data, *, preserve=False):
    """Reject package features we cannot preserve, before any reserialization.

    Candidate supports the simple inspected workbook and our own plain notes.
    Unsupported OOXML is terminal, including unknown extension namespaces.
    """
    if preserve:
        from .maintenance_ooxml import read
        return read(data)
    allowed = re.compile(r'(?:\[Content_Types\]\.xml|_rels/\.rels|docProps/(?:app|core)\.xml|'
                         r'xl/(?:workbook|styles|sharedStrings)\.xml|xl/theme/theme\d+\.xml|'
                         r'xl/_rels/workbook\.xml\.rels|xl/worksheets/sheet\d+\.xml|'
                         r'xl/worksheets/_rels/sheet\d+\.xml\.rels|'
                         r'xl/comments/comment\d+\.xml|xl/drawings/commentsDrawing\d+\.vml)')
    namespaces = {'http://schemas.openxmlformats.org/spreadsheetml/2006/main',
                  'http://schemas.openxmlformats.org/officeDocument/2006/relationships',
                  'http://www.w3.org/XML/1998/namespace'}
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() is not None or len(set(archive.namelist())) != len(archive.namelist()):
            raise EntryError('UNSUPPORTED_OR_CORRUPT_PACKAGE')
        for name in archive.namelist():
            if not allowed.fullmatch(name):
                raise EntryError('UNSUPPORTED_PACKAGE_PART: ' + name)
            if name == 'xl/workbook.xml' or re.fullmatch(r'xl/worksheets/sheet\d+\.xml', name):
                for element in ET.fromstring(archive.read(name)).iter():
                    namespace, _, tag = element.tag[1:].partition('}')
                    if namespace not in namespaces or tag in {'extLst','AlternateContent','ignoredErrors','smartTags'}:
                        raise EntryError('UNSUPPORTED_PACKAGE_FEATURE: ' + tag)
                    if any(key.startswith('{') and key[1:].partition('}')[0] not in namespaces
                           for key in element.attrib):
                        raise EntryError('UNSUPPORTED_PACKAGE_ATTRIBUTE: ' + tag)
            if name.startswith('xl/comments/'):
                for element in ET.fromstring(archive.read(name)).iter():
                    if element.tag.rsplit('}',1)[-1] not in {'comments','authors','author','commentList','comment','text','t'}:
                        raise EntryError('UNSUPPORTED_RICH_NOTE')
            if name.endswith('.rels'):
                if any(r.attrib.get('TargetMode') == 'External' for r in ET.fromstring(archive.read(name))):
                    raise EntryError('UNSUPPORTED_EXTERNAL_RELATIONSHIP')
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        book = openpyxl.load_workbook(io.BytesIO(data))
    try:
        inventory(book)
        return book
    except BaseException:
        book.close()
        raise


def dump(book):
    """Check the full supported sheet state and workbook controls after save."""
    if hasattr(book, '_maintenance_package'):
        from .maintenance_ooxml import dump as preserving_dump
        return preserving_dump(book)
    before = states(book)
    modified = book.properties.modified
    controls = (dict(book.defined_names), xml(book.security), xml(book.calculation), xml(book.properties))
    output = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        book.save(output)
    data = output.getvalue()
    check = load(data)
    try:
        after = (dict(check.defined_names), xml(check.security), xml(check.calculation), xml(check.properties))
        # openpyxl updates only the package's modified timestamp on save.
        check.properties.modified = modified
        after = (*after[:3], xml(check.properties))
        if states(check) != before or after != controls:
            raise EntryError('CANDIDATE_ROUNDTRIP_CHANGED_CONTENT')
    finally:
        check.close()
    return data
