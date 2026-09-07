from pathlib import Path
import re

import pymupdf


ROOT = Path(r"E:\KomatsoAI\PC1250-8R")


# Actual content Forms, ordered exactly as they appear
# in the PC1250-8R / PC1250SP-8R Shop Manual.
#
# Divider/header Forms such as SEN02048, SEN02049,
# SEN02052, SEN02054, etc. are intentionally omitted
# because they are section wrappers, not the technical
# content ranges we need for routing.

FORMS = [
    # 00 Index and foreword
    "SEN02050-02",
    "SEN02051-01",

    # 01 Specification
    "SEN02053-01",

    # 10 Structure, function and maintenance standard
    "SEN02055-00",
    "SEN02056-01",
    "SEN02057-00",
    "SEN02058-00",
    "SEN02059-00",
    "SEN02060-01",
    "SEN02061-00",
    "SEN02062-00",
    "SEN02063-01",

    # 20 Standard value table
    "SEN02071-00",

    # 30 Testing and adjusting
    "SEN02072-00",
    "SEN02073-00",
    "SEN02074-00",
    "SEN02075-00",
    "SEN02076-00",
    "SEN02077-00",

    # 40 Troubleshooting
    "SEN02078-00",
    "SEN02079-00",
    "SEN02080-00",
    "SEN02081-00",
    "SEN02082-00",
    "SEN02083-00",
    "SEN02085-00",
    "SEN02086-00",
    "SEN02087-00",

    # 50 Disassembly and assembly
    "SEN02785-00",
    "SEN02786-00",
    "SEN02787-00",
    "SEN02788-00",
    "SEN02789-00",
    "SEN02790-00",
    "SEN02791-00",
    "SEN02792-00",
    "SEN02793-00",

    # 90 Diagrams and drawings
    "SEN02069-01",
    "SEN02070-00",
]


def find_shop_manual():
    pdfs = sorted(ROOT.glob("*.pdf"))

    if not pdfs:
        raise SystemExit(f"No PDF found in: {ROOT}")

    candidates = []

    for pdf_path in pdfs:
        try:
            doc = pymupdf.open(pdf_path)
        except Exception:
            continue

        page_count = doc.page_count

        sample_text = []

        for i in range(min(25, page_count)):
            try:
                sample_text.append(
                    doc[i].get_text("text")
                )
            except Exception:
                pass

        text = "\n".join(sample_text).upper()

        score = 0

        if "PC1250-8R" in text:
            score += 15

        if "SEN02050-02" in text:
            score += 8

        if "SHOP MANUAL" in text:
            score += 5

        # Prefer the large technical manual if several PDFs exist.
        score += min(page_count / 1000, 3)

        candidates.append(
            (score, page_count, pdf_path)
        )

        doc.close()

    if not candidates:
        raise SystemExit(
            "Could not inspect any PDF."
        )

    candidates.sort(reverse=True)

    best_score, best_pages, best_path = candidates[0]

    if best_score < 15:
        print(
            "WARNING: Shop Manual identification "
            "confidence is low."
        )

    return best_path


def make_clusters(pages, max_gap=2):
    if not pages:
        return []

    pages = sorted(set(pages))

    clusters = [[pages[0]]]

    for page in pages[1:]:
        if page - clusters[-1][-1] <= max_gap:
            clusters[-1].append(page)
        else:
            clusters.append([page])

    return clusters


def select_main_cluster(clusters):
    if not clusters:
        return None

    # Real Form sections normally repeat their Form Number
    # across many consecutive pages.
    # TOC/composition references usually occur only once.
    return max(
        clusters,
        key=lambda c: (
            len(c),
            c[-1] - c[0],
            c[0],
        ),
    )


def short_preview(text):
    lines = [
        line.strip()
        for line in text.splitlines()
        if line.strip()
    ]

    return " | ".join(lines[:5])[:180]


def main():
    pdf_path = find_shop_manual()

    print()
    print("SELECTED PDF:")
    print(pdf_path)
    print()

    doc = pymupdf.open(pdf_path)

    print(f"PDF pages: {doc.page_count}")
    print("Scanning each PDF page once...")
    print()

    form_patterns = {
        form: re.compile(
            re.escape(form),
            re.IGNORECASE,
        )
        for form in FORMS
    }

    hits = {
        form: []
        for form in FORMS
    }

    for page_index in range(doc.page_count):
        pdf_page = page_index + 1

        try:
            text = doc[page_index].get_text(
                "text"
            )
        except Exception as exc:
            print(
                f"TEXT_ERROR page "
                f"{pdf_page}: {exc}"
            )
            continue

        upper_text = text.upper()

        for form, pattern in form_patterns.items():
            if pattern.search(upper_text):
                hits[form].append(pdf_page)

    selected = []

    print("=" * 100)
    print("FORM DETECTION")
    print("=" * 100)

    for form in FORMS:
        form_hits = hits[form]

        clusters = make_clusters(
            form_hits
        )

        chosen = select_main_cluster(
            clusters
        )

        if not chosen:
            print(
                f"{form:<13} NOT FOUND"
            )
            continue

        start = chosen[0]

        selected.append(
            {
                "form": form,
                "start": start,
                "cluster_end": chosen[-1],
                "cluster_hits": len(chosen),
                "all_hits": len(form_hits),
            }
        )

        print(
            f"{form:<13} "
            f"start={start:<5} "
            f"cluster="
            f"{chosen[0]}-{chosen[-1]} "
            f"cluster_hits="
            f"{len(chosen):<4} "
            f"all_hits="
            f"{len(form_hits)}"
        )

    print()
    print("=" * 100)
    print("ORDER CHECK")
    print("=" * 100)

    previous = None
    order_ok = True

    for item in selected:
        if (
            previous is not None
            and item["start"] <= previous["start"]
        ):
            order_ok = False

            print(
                "ORDER WARNING:",
                previous["form"],
                previous["start"],
                "->",
                item["form"],
                item["start"],
            )

        previous = item

    if order_ok:
        print(
            "Form starts are monotonic: OK"
        )

    print()
    print("=" * 100)
    print("DRAFT FORM RANGES")
    print("=" * 100)

    ordered = sorted(
        selected,
        key=lambda x: x["start"],
    )

    for i, item in enumerate(ordered):
        start = item["start"]

        if i + 1 < len(ordered):
            end = (
                ordered[i + 1]["start"]
                - 1
            )
        else:
            end = doc.page_count

        try:
            preview_text = (
                doc[start - 1]
                .get_text("text")
            )

            preview = short_preview(
                preview_text
            )
        except Exception:
            preview = ""

        print(
            f'{item["form"]:<13} => '
            f'{start}-{end} '
            f'({end - start + 1} pages)'
        )

        if preview:
            print(
                f"    {preview}"
            )

    print()
    print("=" * 100)
    print("UNRESOLVED FORMS")
    print("=" * 100)

    unresolved = [
        form
        for form in FORMS
        if not hits[form]
    ]

    if unresolved:
        for form in unresolved:
            print(form)
    else:
        print("None")

    doc.close()


if __name__ == "__main__":
    main()