from __future__ import annotations

import os
import re
import sys
from datetime import datetime
from pathlib import Path

import pymupdf


ROOT = Path(__file__).resolve().parents[1]


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return cleaned.strip("._") or "session"


def main() -> None:
    if len(sys.argv) != 4:
        raise SystemExit(
            "Usage: render_page.py PDF_PATH PAGE_NUMBER LABEL"
        )

    pdf_path = Path(sys.argv[1])
    if not pdf_path.is_absolute():
        pdf_path = ROOT / pdf_path

    page_number = int(sys.argv[2])
    label = safe_name(sys.argv[3])

    session_id = os.environ.get("HERMES_SESSION_ID")
    if not session_id:
        session_id = datetime.now().strftime(
            "session_%Y%m%d_%H%M%S_%f"
        )

    session_id = safe_name(session_id)

    output_dir = ROOT / "artifacts" / session_id
    output_dir.mkdir(parents=True, exist_ok=True)

    output_path = output_dir / f"{label}-p{page_number}.png"

    if not pdf_path.exists():
        raise FileNotFoundError(f"PDF not found: {pdf_path}")

    with pymupdf.open(pdf_path) as document:
        if page_number < 1 or page_number > document.page_count:
            raise ValueError(
                f"Page must be between 1 and {document.page_count}"
            )

        page = document.load_page(page_number - 1)

        pixmap = page.get_pixmap(
            matrix=pymupdf.Matrix(2.4, 2.4),
            colorspace=pymupdf.csRGB,
            alpha=False,
        )

        pixmap.save(str(output_path))

    if not output_path.exists():
        raise RuntimeError("PNG was not created")

    if output_path.stat().st_size < 50_000:
        raise RuntimeError(
            f"PNG is suspiciously small: {output_path.stat().st_size} bytes"
        )

    print(f"MEDIA:{output_path.as_posix()}")


if __name__ == "__main__":
    main()