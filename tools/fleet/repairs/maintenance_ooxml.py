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


def read(data):
    from . import maintenance_identity as identity
    parts = package(data)
    # Unknown reader warnings mean we cannot prove the editable region's state.
    with warnings.catch_warnings():
        warnings.simplefilter('error')
        book = openpyxl.load_workbook(io.BytesIO(data))
    try:
        identity.inventory(book, preserve_notes=True)
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


def notes(parts, updates, sheet_path, additions):
    """Append new plain identity notes; keep existing rich Note and VML bytes."""
    from . import maintenance_identity as identity
    relpath = posixpath.join(posixpath.dirname(sheet_path), '_rels', posixpath.basename(sheet_path)+'.rels')
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
        shapes = re.findall(rb'<[^:>]+:shape\b.*?</[^:>]+:shape>', template_vml, re.S)
        if not shapes:
            raise EntryError('OOXML_NOTE_VML_UNSUPPORTED')
        # Generated shapes bring their namespace declarations; preserve originals.
        maximum = max([1024]+[int(n) for n in re.findall(rb'_x0000_s(\d+)', vr)])
        additions_vml = b''.join(re.sub(rb'_x0000_s\d+', ('_x0000_s'+str(maximum+i+1)).encode(), s)
                                 for i, s in enumerate(shapes))
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
        for row in sorted(rows):
            found = [m for m in elements(raw, 'row') if ET.fromstring(m.group()).attrib.get('r') == str(row)]
            fresh = [m.group() for m in elements(generated[genpaths[title]], 'row')
                     if ET.fromstring(m.group()).attrib.get('r') == str(row)]
            if len(fresh) != 1 or len(found) > 1: raise EntryError('OOXML_ROW_SPAN_INVALID')
            if found:
                old = found[0].group(); tree = ET.fromstring(old)
                if any(e.tag.rsplit('}', 1)[-1] not in {'row','c','v','is','t'} for e in tree.iter()):
                    raise EntryError('OOXML_UNKNOWN_CONTENT_IN_APPEND_ROW')
                # Keep row attributes, including revision identifiers and spans.
                opening = old.split(b'>',1)[0].rstrip(b'/')+b'>'
                for key, val in ET.fromstring(fresh[0]).attrib.items():
                    if key in {'ht','customHeight','hidden'}: opening = attr(opening, key, val)
                # Keep existing blank cells and unknown attributes byte-for-byte;
                # replace only cells receiving new operational content/style.
                content = old.split(b'>',1)[1].rsplit(b'</row>',1)[0] if not old.rstrip().endswith(b'/>') else b''
                for cellmatch in elements(fresh[0], 'c'):
                    newcell = cellmatch.group()
                    coordinate = ET.fromstring(newcell).attrib['r']
                    if coordinate not in changed: continue
                    oldcells = [m.group() for m in elements(content, 'c') if ET.fromstring(m.group()).attrib['r'] == coordinate]
                    if oldcells:
                        oldcell = oldcells[0]
                        head = oldcell.split(b'>',1)[0].rstrip(b'/')+b'>'
                        fresh_tree = ET.fromstring(newcell)
                        for key in ('s', 't'):
                            if key in fresh_tree.attrib: head = attr(head,key,fresh_tree.attrib[key])
                            else: head = re.sub(rb'\s'+key.encode()+rb'="[^"]*"', b'', head)
                        body = b'' if newcell.rstrip().endswith(b'/>') else newcell.split(b'>',1)[1].rsplit(b'</c>',1)[0]
                        content = once(content,oldcell,head+body+b'</c>')
                    else:
                        # New cells remain in column order (CT_Row.c sequence).
                        column = openpyxl.utils.cell.coordinate_from_string(coordinate)[0]
                        column_index = openpyxl.utils.cell.column_index_from_string(column)
                        later = next((m.group() for m in elements(content,'c') if openpyxl.utils.cell.column_index_from_string(
                            openpyxl.utils.cell.coordinate_from_string(ET.fromstring(m.group()).attrib['r'])[0]) > column_index), None)
                        content = once(content,later,newcell+later) if later else content+newcell
                raw = once(raw, old, opening+content+b'</row>')
            else:
                raw = close_insert(raw, 'sheetData', fresh[0])
        if rows:
            # Dimension is an advisory range; extend using the reader's full range.
            match = re.search(rb'<dimension\b[^>]*/>', raw)
            if match: raw = once(raw, match.group(), b'<dimension ref="'+sheet.calculate_dimension().encode()+b'"/>')
        if raw != parts[path]: updates[path] = raw
        if note_additions: notes(parts, updates, path, note_additions)
    wb = parts['xl/workbook.xml']
    if identity.active(book) and identity.MARKER.encode() not in wb:
        marker = f'<definedName name="{identity.MARKER}" hidden="1">{escape(identity.VERSION)}</definedName>'.encode()
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
