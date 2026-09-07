import time
import pymupdf


PDF = r"E:\KomatsoAI\PC800\PC800-8R_ShopManual.pdf"
TERM = "CA111"


def search(start_page, end_page):
    doc = pymupdf.open(PDF)

    hits = []

    start_time = time.perf_counter()

    for pdf_page in range(start_page, end_page + 1):
        text = doc[pdf_page - 1].get_text("text")

        if TERM.lower() in text.lower():
            hits.append(pdf_page)

    elapsed = time.perf_counter() - start_time

    doc.close()

    return hits, elapsed


print("FULL MANUAL TEST")
hits, seconds = search(1, 1060)

print("Hits:", hits)
print("Seconds:", round(seconds, 2))
print()


print("ROUTED SECTION TEST")
hits, seconds = search(486, 557)

print("Hits:", hits)
print("Seconds:", round(seconds, 2))