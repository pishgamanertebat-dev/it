import json
import sys
from pathlib import Path

import pymupdf


def validate_scoped(data, map_path):
    """Opt-in document/model map validation; page coverage alone is insufficient."""
    errors=[]
    manifest=json.loads((map_path.parent/'document_manifest.json').read_text(encoding='utf-8'))
    docs={d['document_id']:d for d in manifest['documents']}
    known=set(data['known_models']);seen=set();covered=set()
    with pymupdf.open(data['manual']) as pdf:
        if len(pdf)!=data['pdf_pages']:errors.append('page_count mismatch')
        def walk(sections,parent=None):
            ranges=[]
            for name,item in sections.items():
                start,end=item.get('pdf_start',0),item.get('pdf_end',0)
                if not 1<=start<=end<=len(pdf):errors.append(name+': invalid bounds');continue
                if parent and not parent[0]<=start<=end<=parent[1]:errors.append(name+': outside parent')
                if item.get('document_id') not in docs:errors.append(name+': unknown document_id')
                if not set(item.get('applicable_models',[]))<=known:errors.append(name+': invalid model')
                signature=(item.get('title'),start,end,item.get('document_id'))
                if signature in seen:errors.append(name+': duplicate section')
                seen.add(signature);covered.update(range(start,end+1))
                actual_empty=[p for p in range(start,end+1) if not pdf[p-1].get_text().strip()]
                if actual_empty!=item.get('textless_pages'):errors.append(name+': textless page mismatch')
                evidence=item.get('heading_evidence',{})
                ep=evidence.get('pdf_page',0)
                if not start<=ep<=end or evidence.get('text')!=pdf[ep-1].get_text()[:400]:
                    errors.append(name+': heading evidence differs from actual PDF')
                for previous,ps,pe,pi in ranges:
                    if start<=pe and ps<=end and not (item.get('overlap_reason') or pi.get('overlap_reason')):
                        errors.append(name+': overlap without reason: '+previous)
                ranges.append((name,start,end,item))
                walk(item.get('subsections',{}),(start,end))
        walk(data['sections'])
        if covered!=set(range(1,len(pdf)+1)):errors.append('incomplete page accounting')
        if set(data['page_models'])!={str(p) for p in range(1,len(pdf)+1)}:errors.append('missing per-page scope')
        for p,models in data['page_models'].items():
            if not models or not set(models)<=known:errors.append(p+': invalid page models')
        for page in data.get('excluded_pages',[]):
            if data['page_models'].get(str(page))!=['EXCLUDED']:errors.append('excluded page not excluded in model filter')
    return errors


def validate_children(parent_name, parent, errors):
    children = parent.get("subsections")

    if not isinstance(children, dict):
        return

    parent_start = parent["pdf_start"]
    parent_end = parent["pdf_end"]

    ranges = []

    for name, item in children.items():
        start = item.get("pdf_start")
        end = item.get("pdf_end")

        if start is None or end is None:
            errors.append(
                f"{parent_name}.{name}: missing pdf_start/pdf_end"
            )
            continue

        if start > end:
            errors.append(
                f"{parent_name}.{name}: invalid range {start}-{end}"
            )

        if start < parent_start or end > parent_end:
            errors.append(
                f"{parent_name}.{name}: outside parent range"
            )

        ranges.append((start, end, name))

    ranges.sort()

    for i in range(1, len(ranges)):
        prev_start, prev_end, prev_name = ranges[i - 1]
        start, end, name = ranges[i]

        if start <= prev_end:
            errors.append(
                f"{parent_name}: overlap "
                f"{prev_name} {prev_start}-{prev_end} "
                f"with {name} {start}-{end}"
            )

        elif start != prev_end + 1:
            errors.append(
                f"{parent_name}: gap between "
                f"{prev_name} ({prev_end}) "
                f"and {name} ({start})"
            )


def main():
    if len(sys.argv) != 2:
        print(
            "Usage: validate_manual_map.py "
            "<manual_sections.json>"
        )
        raise SystemExit(2)

    map_path = Path(sys.argv[1])

    data = json.loads(
        map_path.read_text(encoding="utf-8")
    )
    if data.get('scope_policy')=='per_page':
        errors=validate_scoped(data,map_path)
        print(json.dumps({'map':str(map_path),'errors':errors,'status':'FAIL' if errors else 'PASS',
            'semantic_review':'Actual heading evidence checked; visual identity review is recorded separately in diagram_index.json. No numerical coverage implies universal applicability.'}))
        raise SystemExit(bool(errors))

    manual_path = Path(data["manual"])
    expected_pages = int(data["pdf_pages"])

    print("MAP:")
    print(map_path)

    print()
    print("MANUAL:")
    print(manual_path)

    if not manual_path.exists():
        print()
        print("ERROR: Manual PDF does not exist.")
        raise SystemExit(3)

    doc = pymupdf.open(manual_path)
    actual_pages = doc.page_count
    doc.close()

    print()
    print(f"Expected PDF pages: {expected_pages}")
    print(f"Actual PDF pages:   {actual_pages}")

    errors = []

    if actual_pages != expected_pages:
        errors.append(
            f"PDF page count mismatch: "
            f"{actual_pages} != {expected_pages}"
        )

    sections = data["sections"]

    top_ranges = []

    for name, item in sections.items():
        start = item["pdf_start"]
        end = item["pdf_end"]

        top_ranges.append(
            (start, end, name)
        )

        validate_children(
            name,
            item,
            errors
        )

    top_ranges.sort()

    print()
    print("TOP LEVEL SECTIONS")
    print("=" * 70)

    for start, end, name in top_ranges:
        print(
            f"{name:<28} "
            f"{start:>4}-{end:<4} "
            f"({end - start + 1} pages)"
        )

    if top_ranges[0][0] != 1:
        errors.append(
            f"Top-level map does not start at page 1"
        )

    if top_ranges[-1][1] != expected_pages:
        errors.append(
            f"Top-level map does not end at "
            f"page {expected_pages}"
        )

    for i in range(1, len(top_ranges)):
        p_start, p_end, p_name = top_ranges[i - 1]
        start, end, name = top_ranges[i]

        if start <= p_end:
            errors.append(
                f"Top-level overlap: "
                f"{p_name} {p_start}-{p_end} "
                f"with {name} {start}-{end}"
            )

        elif start != p_end + 1:
            errors.append(
                f"Top-level gap: "
                f"{p_name} ends {p_end}, "
                f"{name} starts {start}"
            )

    print()
    print("=" * 70)

    if errors:
        print("VALIDATION FAILED")
        print()

        for error in errors:
            print("ERROR:", error)

        raise SystemExit(1)

    print("PDF PAGE COUNT: OK")
    print("TOP LEVEL COVERAGE: OK")
    print("NO TOP LEVEL GAPS: OK")
    print("NO TOP LEVEL OVERLAPS: OK")
    print("SUBSECTION RANGES: OK")
    print()
    print("MANUAL MAP VALIDATION: PASS")


if __name__ == "__main__":
    main()
