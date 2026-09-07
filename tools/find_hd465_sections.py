from pathlib import Path
import re

import pymupdf


ROOT = Path(r"E:\KomatsoAI\HD465-7R_HD605-7R")

# Ordered exactly as the Shop Manual composition list.
FORMS = [
    "SEN02283-10",
    "SEN02284-10",
    "SEN02285-10",
    "SEN02286-04",
    "SEN02287-01",
    "SEN02289-01",
    "SEN02290-04",
    "SEN02291-01",
    "SEN02292-02",
    "SEN02293-01",
    "SEN02294-01",
    "SEN02295-01",
    "SEN02296-01",
    "SEN02297-01",
    "SEN04680-00",
    "SEN02298-01",
    "SEN02299-02",
    "SEN02300-01",
    "SEN02301-01",
    "SEN02302-01",
    "SEN02308-02",
    "SEN02527-02",
    "SEN02309-04",
    "SEN02528-03",
    "SEN02529-02",
    "SEN02530-01",
    "SEN02531-02",
    "SEN02532-03",
    "SEN02310-04",
    "SEN02717-02",
    "SEN02718-02",
    "SEN02533-02",
    "SEN02534-02",
    "SEN02535-02",
    "SEN02536-02",
    "SEN02537-02",
    "SEN02538-03",
    "SEN02539-01",
    "SEN02540-02",
    "SEN02541-02",
    "SEN02542-01",
    "SEN02543-01",
    "SEN02544-02",
    "SEN02311-02",
    "SEN02726-02",
    "SEN02727-02",
    "SEN02728-01",
    "SEN02729-01",
    "SEN02730-01",
    "SEN02731-01",
    "SEN02732-01",
    "SEN02733-01",
    "SEN02734-01",
    "SEN02735-02",
    "SEN02736-01",
    "SEN02312-05",
    "SEN02313-01",
    "SEN02314-05",
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

        # Only inspect a small beginning sample for identification.
        sample_text = []

        for i in range(min(25, page_count)):
            try:
                sample_text.append(doc[i].get_text("text"))
            except Exception:
                pass

        text = "\n".join(sample_text).upper()

        score = 0

        if "HD465-7R" in text:
            score += 10

        if "HD605-7R" in text:
            score += 10

        if "SEN02283-10" in text:
            score += 8

        if "SEN02285-10" in text:
            score += 5

        # The Shop Manual should be much larger than a Parts Book.
        score += min(page_count / 1000, 3)

        candidates.append(
            (score, page_count, pdf_path)
        )

        doc.close()

    if not candidates:
        raise SystemExit("Could not inspect any PDF.")

    candidates.sort(reverse=True)

    best_score, best_pages, best_path = candidates[0]

    if best_score < 15:
        print("WARNING: Shop Manual identification confidence is low.")

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

    # Prefer the cluster containing the largest number of hits.
    # If tied, prefer the later cluster. This helps reject TOC references
    # near the beginning of the PDF.
    return max(
        clusters,
        key=lambda c: (len(c), c[-1] - c[0], c[0]),
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
        form: re.compile(re.escape(form), re.IGNORECASE)
        for form in FORMS
    }

    hits = {form: [] for form in FORMS}

    # Cache only previews for selected starts later.
    page_previews = {}

    for page_index in range(doc.page_count):
        pdf_page = page_index + 1

        try:
            text = doc[page_index].get_text("text")
        except Exception as exc:
            print(f"TEXT_ERROR page {pdf_page}: {exc}")
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
        clusters = make_clusters(form_hits)
        chosen = select_main_cluster(clusters)

        if not chosen:
            print(f"{form:<13} NOT FOUND")
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
            f"cluster={chosen[0]}-{chosen[-1]} "
            f"cluster_hits={len(chosen):<4} "
            f"all_hits={len(form_hits)}"
        )

    print()
    print("=" * 100)
    print("ORDER CHECK")
    print("=" * 100)

    previous = None
    order_ok = True

    for item in selected:
        if previous is not None and item["start"] <= previous["start"]:
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
        print("Form starts are monotonic: OK")

    print()
    print("=" * 100)
    print("DRAFT FORM RANGES")
    print("=" * 100)

    # Only use selected starts.
    # End boundary is next Form start - 1.
    ordered = sorted(
        selected,
        key=lambda x: x["start"]
    )

    for i, item in enumerate(ordered):
        start = item["start"]

        if i + 1 < len(ordered):
            end = ordered[i + 1]["start"] - 1
        else:
            end = doc.page_count

        try:
            preview_text = doc[start - 1].get_text("text")
            preview = short_preview(preview_text)
        except Exception:
            preview = ""

        print(
            f'{item["form"]:<13} => '
            f'{start}-{end} '
            f'({end - start + 1} pages)'
        )

        if preview:
            print(f"    {preview}")

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