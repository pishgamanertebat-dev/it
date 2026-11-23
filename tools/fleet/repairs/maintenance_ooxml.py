"""Append-only package editor for the maintenance candidate.

openpyxl is a reader and a generator of NEW rows/styles/notes only. Original
XML is patched at bounded spans; every other ZIP member is copied verbatim.
This is package evidence, not a substitute for interactive Excel acceptance.
"""
from copy import copy, deepcopy
import io
import posixpath
import re
import warnings
import zipfile
from xml.etree import ElementTree as ET
from xml.sax.saxutils import escape

import openpyxl

from .entry_service import EntryError
from .maintenance_baseline import states, xml

MAIN = 'http://schemas.openxmlformats.org/spreadsheetml/2006/main'
REL = 'http://schemas.openxmlformats.org/officeDocument/2006/relationships/'
Q = '{' + MAIN + '}'
REVISION_UID = '{http://schemas.microsoft.com/office/spreadsheetml/2014/revision}uid'
VML = '{urn:schemas-microsoft-com:vml}'
EXCEL = '{urn:schemas-microsoft-com:office:excel}'


def package(data):
    with zipfile.ZipFile(io.BytesIO(data)) as archive:
        if archive.testzip() or len(archive.namelist()) != len(set(archive.namelist())):
            raise EntryError('OOXML_PACKAGE_INVALID')
        parts = {n: archive.read(n) for n in archive.namelist()}
    for name, raw in parts.items():
        if name.endswith(('.xml', '.rels', '.vml')):
            ET.fromstring(raw)
        if name.startswith(('_xmlsignatures/', 'xl/vba')):
            raise EntryError('OOXML_SIGNED_OR_MACRO_PACKAGE_UNSUPPORTED')
    return parts


def sheet_paths(parts):
    rels = {r.attrib['Id']: r.attrib['Target'] for r in ET.fromstring(parts['xl/_rels/workbook.xml.rels'])}
    return {s.attrib['name']: resolve('xl/workbook.xml', rels[s.attrib['{' + REL[:-1] + '}id']])
            for s in ET.fromstring(parts['xl/workbook.xml']).find('{'+MAIN+'}sheets')}


def resolve(part, target):
    return target.lstrip('/') if target.startswith('/') else posixpath.normpath(posixpath.join(posixpath.dirname(part), target))


def sheet_relationship_path(sheet_path):
    return posixpath.join(posixpath.dirname(sheet_path), '_rels', posixpath.basename(sheet_path)+'.rels')


def excel_note_text(node):
    """Recognize the Excel Save As wrapper around the original identity text."""
    if (set(node.attrib) != {'ref', 'authorId', 'shapeId', REVISION_UID}
            or node.get('shapeId') != '0'
            or not re.fullmatch(r'\{[0-9A-Fa-f]{8}(?:-[0-9A-Fa-f]{4}){3}-[0-9A-Fa-f]{12}\}', node.get(REVISION_UID, ''))
            or [child.tag for child in node] != [Q + 'text']):
        return None
    text = node[0]
    if text.attrib or [child.tag for child in text] != [Q + 'r']:
        return None
    run = text[0]
    if run.attrib or [child.tag for child in run] != [Q + 'rPr', Q + 't']:
        return None
    properties, payload = run
    if (properties.attrib or payload.attrib or len(payload) or any(len(prop) for prop in properties)
            or [(p.tag, p.attrib) for p in properties] != [
                (Q + 'sz', {'val': '11'}), (Q + 'color', {'theme': '1'}),
                (Q + 'rFont', {'val': 'Calibri'}), (Q + 'family', {'val': '2'}),
                (Q + 'scheme', {'val': 'minor'})]
            or any((e.text or '').strip() for e in (node, text, run, properties, *properties))
            or any((e.tail or '').strip() for e in node.iter() if e is not node)):
        return None
    return payload.text


def excel_note_anchors(parts, sheet_path, rels):
    legacy = ET.fromstring(parts[sheet_path]).find(Q + 'legacyDrawing')
    drawings = [r for r in rels if r.get('Type') == REL + 'vmlDrawing']
    if (len(drawings) != 1 or legacy is None or drawings[0].get('TargetMode') == 'External'
            or legacy.get('{' + REL[:-1] + '}id') != drawings[0].get('Id')):
        raise EntryError('UNSUPPORTED_EXISTING_NOTE')
    anchors = {}
    for shape in ET.fromstring(parts[resolve(sheet_path, drawings[0].attrib['Target'])]).iter(VML + 'shape'):
        for client in shape.findall(EXCEL + 'ClientData'):
            if client.get('ObjectType') != 'Note':
                continue
            anchor = (client.findtext(EXCEL + 'Column'), client.findtext(EXCEL + 'Row'))
            flags = dict(re.findall(r'([\w-]+)\s*:\s*([^;]+)', shape.get('style', '')))
            anchors[anchor] = (anchor not in anchors and flags.get('visibility', '').strip() == 'hidden'
                               and client.find(EXCEL + 'Visible') is None
                               and client.find(EXCEL + 'MoveWithCells') is not None
                               and client.find(EXCEL + 'SizeWithCells') is not None)
    return anchors


def excel_notes(book, parts):
    from . import maintenance_identity as identity
    found = {}
    for title, path in sheet_paths(parts).items():
        sheet = book[title]
        candidates = {c.coordinate: c.comment.text for c in sheet._cells.values()
                      if c.comment and c.comment.author == identity.EXCEL_AUTHOR
                      and c.comment.text.startswith(identity.PREFIX)}
        if not candidates:
            continue
        rels = ET.fromstring(parts[sheet_relationship_path(path)])
        comments = [r for r in rels if r.get('Type') == REL + 'comments']
        if len(comments) != 1 or comments[0].get('TargetMode') == 'External':
            raise EntryError('UNSUPPORTED_EXISTING_NOTE')
        raw = ET.fromstring(parts[resolve(path, comments[0].attrib['Target'])])
        authors = raw.find(Q + 'authors')
        nodes = {}
        for node in raw.findall(f'{Q}commentList/{Q}comment'):
            coordinate = node.get('ref')
            if coordinate in candidates:
                if coordinate in nodes:
                    raise EntryError('IDENTITY_DUPLICATE')
                nodes[coordinate] = node
        anchors = excel_note_anchors(parts, path, rels)
        for coordinate, payload in candidates.items():
            node = nodes.get(coordinate)
            cell = sheet[coordinate]
            author = node.get('authorId', '') if node is not None else ''
            if (not re.fullmatch(r'[0-9]+', author) or int(author) >= len(authors)
                    or authors[int(author)].text != identity.EXCEL_AUTHOR or excel_note_text(node) != payload
                    or not anchors.get((str(cell.column-1), str(cell.row-1)))):
                raise EntryError('UNSUPPORTED_EXISTING_NOTE')
            found[(title, coordinate)] = payload
    return found


def read(data):
    from . import maintenance_identity as identity
    parts = package(data)
    # Unknown reader warnings mean we cannot prove the editable region's state.
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        book = openpyxl.load_workbook(io.BytesIO(data))
    try:
        # Decode cell-local evidence only. Sync matches it against the verified
        # Journal branch before accepting the workbook as input.
        book._maintenance_excel_notes = excel_notes(book, parts)
        identity.inventory(book, preserve_notes=True)
        if identity.historical(book):
            for title, path in sheet_paths(parts).items():
                cells = [c for c in book[title]._cells.values()
                         if c.comment and c.comment.text.startswith(identity.PREFIX)]
                if cells:
                    anchors = excel_note_anchors(parts, path, ET.fromstring(parts[sheet_relationship_path(path)]))
                    if any(not anchors.get((str(c.column-1), str(c.row-1))) for c in cells):
                        raise EntryError('HISTORICAL_NOTE_ANCHOR_INVALID')
        book._maintenance_package = data
        book._maintenance_before = states(book)
        book._maintenance_controls = (deepcopy(dict(book.defined_names)), xml(book.security), xml(book.calculation), xml(book.properties))
        return book
    except BaseException:
        book.close()
        raise


def once(raw, old, new):
    if raw.count(old) != 1:
        raise EntryError('OOXML_PATCH_SPAN_AMBIGUOUS')
    return raw.replace(old, new, 1)


def close_insert(raw, tag, addition):
    end = ('</'+tag+'>').encode()
    if end in raw:
        return once(raw, end, addition+end)
    match = re.search(rb'<'+tag.encode()+rb'\b[^>]*?/>', raw)
    if not match:
        raise EntryError('OOXML_CONTAINER_UNSUPPORTED: '+tag)
    old = match.group()
    return once(raw, old, old[:-2]+b'>'+addition+end)


def elements(raw, tag):
    return list(re.finditer(rb'<'+tag.encode()+rb'\b[^>]*?(?:/>|>.*?</'+tag.encode()+rb'>)', raw, re.S))


def sheet_rows(raw):
    # Parse the whole part so Excel prefixes on row/cell attributes stay in scope.
    nodes = ET.fromstring(raw).findall(f'{Q}sheetData/{Q}row')
    spans = elements(raw, 'row')
    if len(nodes) != len(spans):
        raise EntryError('OOXML_ROW_SPAN_INVALID')
    by_number = {}
    number = 0
    for node, span in zip(nodes, spans):
        number = int(node.get('r', number+1))
        by_number[number] = node, span.group()
    if len(by_number) != len(spans):
        raise EntryError('OOXML_ROW_SPAN_INVALID')
    return by_number


def cell_coordinate(raw):
    match = re.search(rb'\sr=([\'\"])([A-Z]+[0-9]+)\1', raw.split(b'>', 1)[0])
    if not match:
        raise EntryError('OOXML_CELL_SPAN_INVALID')
    return match[2].decode()


def attr(raw, name, value):
    opening, rest = raw.split(b'>', 1)
    opening = re.sub(rb'\s'+name.encode()+rb'="[^"]*"', b'', opening)
    return opening+b' '+name.encode()+b'="'+str(value).encode()+b'">'+rest


def append_styles(original, generated):
    raw = original
    for tag in ('numFmts', 'fonts', 'fills', 'borders', 'cellStyleXfs', 'cellXfs'):
        a = ET.fromstring(original).find('{'+MAIN+'}'+tag)
        b = ET.fromstring(generated).find('{'+MAIN+'}'+tag)
        old_count, new_count = len(a) if a is not None else 0, len(b) if b is not None else 0
        if new_count < old_count:
            raise EntryError('OOXML_STYLE_TABLE_SHRANK')
        if new_count == old_count:
            continue
        container = elements(generated, tag)[0].group()
        child_tag = {'numFmts':'numFmt', 'fonts':'font', 'fills':'fill', 'borders':'border'}.get(tag, 'xf')
        new = b''.join(m.group() for m in elements(container, child_tag)[old_count:])
        if a is None:
            # Number formats precede fonts in CT_Stylesheet.
            if tag != 'numFmts':
                raise EntryError('OOXML_STYLE_CONTAINER_MISSING')
            raw = once(raw, b'<fonts', b'<numFmts count="'+str(new_count).encode()+b'">'+new+b'</numFmts><fonts')
        else:
            old = elements(raw, tag)[0].group()
            changed = attr(close_insert(old, tag, new), 'count', new_count)
            raw = once(raw, old, changed)
    return raw


def notes(parts, updates, sheet_path, additions, *, row_bound=False):
    """Append new plain identity notes; keep existing rich Note and VML bytes."""
    from . import maintenance_identity as identity
    relpath = sheet_relationship_path(sheet_path)
    relraw = parts.get(relpath, b'<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships"></Relationships>')
    reltree = ET.fromstring(relraw)
    comments = [r for r in reltree if r.attrib.get('Type') == REL+'comments']
    vml = [r for r in reltree if r.attrib.get('Type') == REL+'vmlDrawing']
    if len(comments) > 1 or len(vml) > 1 or bool(comments) != bool(vml):
        raise EntryError('OOXML_NOTE_RELATIONSHIP_UNSUPPORTED')
    small = openpyxl.Workbook()
    for coordinate, comment in additions:
        small.active[coordinate].comment = copy(comment)
    stream = io.BytesIO(); small.save(stream); small.close()
    generated = package(stream.getvalue())
    template_comments = generated['xl/comments/comment1.xml']
    template_vml = generated['xl/drawings/commentsDrawing1.vml']
    if row_bound:
        drawing = ET.fromstring(template_vml)
        for client in drawing.iter(EXCEL + 'ClientData'):
            for index, tag in enumerate(('MoveWithCells', 'SizeWithCells')):
                if client.find(EXCEL + tag) is None:
                    client.insert(index, ET.Element(EXCEL + tag))
        template_vml = ET.tostring(drawing)
    if comments:
        cp, vp = resolve(sheet_path, comments[0].attrib['Target']), resolve(sheet_path, vml[0].attrib['Target'])
        cr, vr = parts[cp], parts[vp]
        if b'id="_x0000_t202"' not in vr:
            raise EntryError('OOXML_EXISTING_NOTE_SHAPETYPE_UNSUPPORTED')
        authors = ET.fromstring(cr).find('{'+MAIN+'}authors')
        index = next((i for i, a in enumerate(authors) if a.text == identity.AUTHOR), len(authors))
        if index == len(authors):
            cr = close_insert(cr, 'authors', b'<author>'+identity.AUTHOR.encode()+b'</author>')
        added_comments = b''.join(re.sub(rb'authorId="0"', b'authorId="'+str(index).encode()+b'"', m.group())
                                  for m in elements(template_comments, 'comment'))
        cr = close_insert(cr, 'commentList', added_comments)
        shapes = ET.fromstring(template_vml).findall(VML + 'shape')
        if not shapes:
            raise EntryError('OOXML_NOTE_VML_UNSUPPORTED')
        # Serialize only new shapes with their own namespace declarations.
        maximum = max([1024]+[int(n) for n in re.findall(rb'_x0000_s(\d+)', vr)])
        for index, shape in enumerate(shapes, start=maximum+1):
            shape.set('id', f'_x0000_s{index}')
        additions_vml = b''.join(ET.tostring(shape) for shape in shapes)
        vr = once(vr, b'</xml>', additions_vml+b'</xml>')
        updates[cp], updates[vp] = cr, vr
    else:
        serial = 1
        while f'xl/comments/comment{serial}.xml' in parts or f'xl/drawings/commentsDrawing{serial}.vml' in parts:
            serial += 1
        cp, vp = f'xl/comments/comment{serial}.xml', f'xl/drawings/commentsDrawing{serial}.vml'
        while cp in updates or vp in updates:
            serial += 1; cp, vp = f'xl/comments/comment{serial}.xml', f'xl/drawings/commentsDrawing{serial}.vml'
        updates[cp], updates[vp] = template_comments, template_vml
        ids = {r.attrib['Id'] for r in reltree}
        suffix = 1
        while f'komatsoNote{suffix}' in ids or f'komatsoVML{suffix}' in ids: suffix += 1
        cid, vid = f'komatsoNote{suffix}', f'komatsoVML{suffix}'
        addition = (f'<Relationship Id="{cid}" Type="{REL}comments" Target="/{cp}"/>'
                    f'<Relationship Id="{vid}" Type="{REL}vmlDrawing" Target="/{vp}"/>').encode()
        updates[relpath] = close_insert(relraw, 'Relationships', addition)
        raw = updates.get(sheet_path, parts[sheet_path])
        if ET.fromstring(raw).find('{'+MAIN+'}legacyDrawing') is not None:
            raise EntryError('OOXML_EXISTING_VML_WITHOUT_NOTES')
        # local namespace declaration works even when source uses another prefix.
        legacy = f'<legacyDrawing xmlns:r="{REL[:-1]}" r:id="{vid}"/>'.encode()
        if b'<extLst' in raw:
            raw = once(raw, b'<extLst', legacy+b'<extLst')
        else:
            raw = close_insert(raw, 'worksheet', legacy)
        updates[sheet_path] = raw
        ct = updates.get('[Content_Types].xml', parts['[Content_Types].xml'])
        if not any(e.attrib.get('Extension') == 'vml' for e in ET.fromstring(ct)):
            ct = close_insert(ct, 'Types', b'<Default Extension="vml" ContentType="application/vnd.openxmlformats-officedocument.vmlDrawing"/>')
        ct = close_insert(ct, 'Types', f'<Override PartName="/{cp}" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.comments+xml"/>'.encode())
        updates['[Content_Types].xml'] = ct


def dump(book):
    from . import maintenance_identity as identity
    original = book._maintenance_package
    parts = package(original)
    before = book._maintenance_before
    current = states(book)
    if list(before) != book.sheetnames:
        raise EntryError('OOXML_SHEET_LIST_CHANGED')
    oldnames, security, calculation, properties = book._maintenance_controls
    names = dict(book.defined_names)
    names.pop(identity.MARKER, None); oldnames.pop(identity.MARKER, None)
    if (names != oldnames or security != xml(book.security) or calculation != xml(book.calculation)
            or properties != xml(book.properties)):
        raise EntryError('OOXML_UNSUPPORTED_WORKBOOK_EDIT')
    paths = sheet_paths(parts)
    out = io.BytesIO()
    with warnings.catch_warnings():
        warnings.simplefilter('error'); book.save(out)
    generated = package(out.getvalue()); genpaths = sheet_paths(generated)
    updates = {}
    for title, previous in before.items():
        after, sheet = current[title], book[title]
        for field in previous.keys()-{'cells', 'rows', 'print_area'}:
            if previous[field] != after[field]:
                raise EntryError('OOXML_UNSUPPORTED_SHEET_EDIT: '+field)
        last = max((openpyxl.utils.cell.coordinate_from_string(c)[1] for c in previous['cells']
                    if previous['cells'][c][0] is not None), default=0)
        # Existing operational values, styles and notes must never be rewritten.
        changed = {c for c in previous['cells'].keys() | after['cells'].keys()
                   if previous['cells'].get(c) != after['cells'].get(c)}
        note_additions = []
        for coordinate in changed:
            old, new = previous['cells'].get(coordinate), after['cells'].get(coordinate)
            cell = sheet[coordinate]
            if cell.row <= last:
                # Migration may add an identity note to an exactly proven row.
                if old is None or new is None or old[-2] or not cell.comment:
                    raise EntryError('OOXML_HISTORICAL_EDIT_DENIED')
                if old[:-2]+old[-1:] != new[:-2]+new[-1:]: raise EntryError('OOXML_HISTORICAL_EDIT_DENIED')
            if cell.comment and (old is None or old[-2] != new[-2]):
                if cell.comment.author != identity.AUTHOR or not cell.comment.text.startswith(identity.PREFIX):
                    raise EntryError('OOXML_ONLY_NEW_IDENTITY_NOTES_ALLOWED')
                note_additions.append((coordinate, cell.comment))
        rows = {sheet[c].row for c in changed if sheet[c].row > last}
        path = paths[title]; raw = parts[path]
        if rows:
            original_rows = sheet_rows(raw)
            new_rows = sheet_rows(generated[genpaths[title]])
        for row in sorted(rows):
            if row not in new_rows:
                raise EntryError('OOXML_ROW_SPAN_INVALID')
            fresh_tree, fresh = new_rows[row]
            if row in original_rows:
                tree, old = original_rows[row]
                if any(e.tag not in {Q+t for t in ('row', 'c', 'v', 'is', 't')} for e in tree.iter()):
                    raise EntryError('OOXML_UNKNOWN_CONTENT_IN_APPEND_ROW')
                # Keep row attributes, including revision identifiers and spans.
                opening = old.split(b'>',1)[0].rstrip(b'/')+b'>'
                for key, val in fresh_tree.attrib.items():
                    if key in {'ht','customHeight','hidden'}: opening = attr(opening, key, val)
                # Keep existing blank cells and unknown attributes byte-for-byte;
                # replace only cells receiving new operational content/style.
                content = old.split(b'>',1)[1].rsplit(b'</row>',1)[0] if not old.rstrip().endswith(b'/>') else b''
                for cellmatch in elements(fresh, 'c'):
                    newcell = cellmatch.group()
                    coordinate = cell_coordinate(newcell)
                    if coordinate not in changed: continue
                    oldcells = [m.group() for m in elements(content, 'c') if cell_coordinate(m.group()) == coordinate]
                    if oldcells:
                        oldcell = oldcells[0]
                        head = oldcell.split(b'>',1)[0].rstrip(b'/')+b'>'
                        new_tree = ET.fromstring(newcell)
                        for key in ('s', 't'):
                            if key in new_tree.attrib: head = attr(head,key,new_tree.attrib[key])
                            else: head = re.sub(rb'\s'+key.encode()+rb'="[^"]*"', b'', head)
                        body = b'' if newcell.rstrip().endswith(b'/>') else newcell.split(b'>',1)[1].rsplit(b'</c>',1)[0]
                        content = once(content,oldcell,head+body+b'</c>')
                    else:
                        # New cells remain in column order (CT_Row.c sequence).
                        column = openpyxl.utils.cell.coordinate_from_string(coordinate)[0]
                        column_index = openpyxl.utils.cell.column_index_from_string(column)
                        later = next((m.group() for m in elements(content,'c') if openpyxl.utils.cell.column_index_from_string(
                            openpyxl.utils.cell.coordinate_from_string(cell_coordinate(m.group()))[0]) > column_index), None)
                        content = once(content,later,newcell+later) if later else content+newcell
                raw = once(raw, old, opening+content+b'</row>')
            else:
                next_row = min((r for r in original_rows if r > row), default=None)
                if next_row is None:
                    raw = close_insert(raw, 'sheetData', fresh)
                else:
                    later = original_rows[next_row][1]
                    raw = once(raw, later, fresh+later)
        if rows:
            # Dimension is an advisory range; extend using the reader's full range.
            match = re.search(rb'<dimension\b[^>]*/>', raw)
            if match:
                old = match.group()
                dimension = sheet.calculate_dimension().encode()
                updated = re.sub(rb'(\sref=)([\'\"])[^\'\"]*\2',
                                 lambda m: m[1]+m[2]+dimension+m[2], old)
                raw = once(raw, old, updated)
        if raw != parts[path]: updates[path] = raw
        if note_additions:
            notes(parts, updates, path, note_additions, row_bound=identity.historical(book))
    wb = parts['xl/workbook.xml']
    if identity.active(book) and identity.MARKER.encode() not in wb:
        version = book.defined_names[identity.MARKER].attr_text
        marker = f'<definedName name="{identity.MARKER}" hidden="1">{escape(version)}</definedName>'.encode()
        if b'<definedNames' in wb: wb = close_insert(wb, 'definedNames', marker)
        else: wb = once(wb, b'</sheets>', b'</sheets><definedNames>'+marker+b'</definedNames>')
    for index, title in enumerate(book.sheetnames):
        if before[title]['print_area'] == current[title]['print_area']: continue
        matches = [m for m in elements(wb, 'definedName') if ET.fromstring(m.group()).attrib.get('name') == '_xlnm.Print_Area'
                   and ET.fromstring(m.group()).attrib.get('localSheetId') == str(index)]
        if len(matches) != 1: raise EntryError('OOXML_PRINT_AREA_SPAN_INVALID')
        old = matches[0].group(); opening = old.split(b'>',1)[0]+b'>'
        wb = once(wb, old, opening+escape(str(book[title].print_area)).encode()+b'</definedName>')
    if wb != parts['xl/workbook.xml']: updates['xl/workbook.xml'] = wb
    styles = append_styles(parts['xl/styles.xml'], generated['xl/styles.xml'])
    if styles != parts['xl/styles.xml']: updates['xl/styles.xml'] = styles
    output = io.BytesIO()
    with zipfile.ZipFile(io.BytesIO(original)) as src, zipfile.ZipFile(output, 'w') as dst:
        dst.comment = src.comment
        for info in src.infolist(): dst.writestr(info, updates.get(info.filename, parts[info.filename]))
        for name, raw in updates.items():
            if name not in parts:
                # Retrying the same append against the same snapshot must
                # produce the same bytes, including newly added ZIP headers.
                info = zipfile.ZipInfo(name, date_time=(1980,1,1,0,0,0))
                dst.writestr(info, raw, compress_type=zipfile.ZIP_DEFLATED)
    result = output.getvalue()
    check = read(result)
    try:
        if states(check) != current or identity.inventory(check, preserve_notes=True) != identity.inventory(book, preserve_notes=True):
            raise EntryError('OOXML_READBACK_STATE_CHANGED')
    finally: check.close()
    return result
