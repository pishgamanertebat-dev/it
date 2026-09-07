from pathlib import Path
import re

import pymupdf


ROOT = Path(r"E:\KomatsoAI\WA600-6")


# Ordered exactly as listed in the WA600-6 Shop Manual composition.
FORMS = [
    "SEN00235-17",
    "SEN00397-17",
    "SEN00398-17",
    "SEN00415-04",
    "SEN00399-03",
    "SEN00400-03",
    "SEN00401-08",
    "SEN00402-01",
    "SEN00403-02",
    "SEN02455-01",
    "SEN00404-03",
    "SEN00405-02",
    "SEN00406-00",
    "SEN00407-01",
    "SEN02456-01",
    "SEN00408-03",
    "SEN00409-01",
    "SEN01009-02",
    "SEN00410-02",
    "SEN00411-03",
    "SEN00566-04",
    "SEN00552-04",
    "SEN00567-07",
    "SEN00553-05",
    "SEN00554-05",
    "SEN00555-06",
    "SEN00556-05",
    "SEN00568-03",
    "SEN03364-01",
    "SEN00557-01",
    "SEN00558-02",
    "SEN00559-03",
    "SEN00560-02",
    "SEN00561-03",
    "SEN00562-03",
    "SEN00569-03",
    "SEN00570-03",
    "SEN00571-03",
    "SEN00572-03",
    "SEN00573-03",
    "SEN00574-03",
    "SEN00563-03",
    "SEN00564-02",
    "SEN00565-02",
    "SEN00583-06",
    "SEN01156-03",
    "SEN01157-02",
    "SEN01158-03",
    "SEN01160-02",
    "SEN01162-02",
    "SEN01164-03",
    "SEN01165-02",
    "SEN01167-02",
    "SEN01169-02",
    "SEN01171-02",
    "SEN01174-02",
    "SEN01175-02",
    "SEN01177-03",
    "SEN00414-07",
    "SEN00412-04",
    "SEN00413-04",
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

        if "WA600-6" in text:
            score += 15

        if "SEN00398-17" in text:
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