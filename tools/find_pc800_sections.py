import json
from pathlib import Path

import pymupdf


PDF = Path(r"E:\KomatsoAI\PC800\PC800-8R_ShopManual.pdf")
OUT = Path(r"E:\KomatsoAI\PC800\pc800_section_ranges.json")

FORMS = [
    "SEN02321-01",

    "SEN02323-00",
    "SEN02324-00",
    "SEN02325-00",
    "SEN02326-02",
    "SEN02327-01",
    "SEN02328-00",
    "SEN02329-00",
    "SEN02330-01",

    "SEN02675-00",

    "SEN02676-00",
    "SEN02677-00",
    "SEN02678-00",
    "SEN02679-00",
    "SEN02680-00",

    "SEN02681-00",
    "SEN02682-00",
    "SEN02683-00",
    "SEN02684-00",
    "SEN02685-00",
    "SEN02686-00",
    "SEN02687-00",

    "SEN02767-00",
    "SEN02768-00",
    "SEN02769-00",
    "SEN02770-00",
    "SEN02771-00",
    "SEN02772-00",
    "SEN02773-00",
    "SEN02774-00",
    "SEN02775-00",

    "SEN02336-01",
    "SEN02337-00",
]


def best_cluster(pages, max_gap=2):
    if not pages:
        return None

    pages = sorted(set(pages))

    clusters = []
    current = [pages[0]]

    for page in pages[1:]:
        if page - current[-1] <= max_gap:
            current.append(page)
        else:
            clusters.append(current)
            current = [page]

    clusters.append(current)

    # The real section normally creates a long contiguous cluster.
    # A TOC reference normally creates only an isolated hit.
    best = max(clusters, key=len)

    return {
        "pdf_start": best[0],
        "pdf_end": best[-1],
        "matched_pages": len(best),
    }


doc = pymupdf.open(PDF)

hits = {form: [] for form in FORMS}

for index in range(len(doc)):
    pdf_page = index + 1
    text = doc[index].get_text("text")

    for form in FORMS:
        if form in text:
            hits[form].append(pdf_page)

result = {}

for form in FORMS:
    result[form] = best_cluster(hits[form])

OUT.write_text(
    json.dumps(result, indent=2, ensure_ascii=False),
    encoding="utf-8",
)

print(f"PDF pages: {len(doc)}")
print()

for form, info in result.items():
    print(form, "=>", info)

print()
print("Saved:", OUT)