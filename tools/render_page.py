"""Render approved PDF pages or normalized regions of the displayed page.

Region [x0,y0,x1,y1] uses [0,1], top-left origin, AFTER rotation and CropBox.
get_pixmap's clip uses this displayed Page.rect space. PDF text/drawing boxes
are unrotated: multiply by page.rotation_matrix before normalizing. Do not add
the CropBox offset again. Scanned pixels do not acquire missing detail.
"""
from __future__ import annotations
import argparse
import hashlib
import json
import math
import os
import re
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from uuid import uuid4
import pymupdf

if str(Path(__file__).resolve().parents[1]) not in sys.path:
    sys.path.append(str(Path(__file__).resolve().parents[1]))
from integrations.hermes.technical_image import read_validated_png, validate_dimensions

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REGION_DPI = 288
MAX_DPI = 600


def safe_name(value: str) -> str:
    cleaned = re.sub(r"[^A-Za-z0-9_.-]+", "_", value)
    return cleaned.strip("._") or "session"


def approved_pdf(path: Path) -> Path:
    path = (path if path.is_absolute() else ROOT / path).resolve(strict=True)
    models = json.loads((ROOT / "tools/manual_models.json").read_text(encoding="utf-8"))
    folders = {(ROOT / folder).resolve() for folder in models.values()}
    if path.suffix.lower() != ".pdf" or not path.is_file() or path.parent not in folders:
        raise ValueError("PDF must be inside an approved technical document folder")
    return path


def region_clip(page, region):
    bounds = page.rect
    if not all(math.isfinite(v) for v in bounds) or bounds.is_empty or bounds.is_infinite:
        raise ValueError("Invalid PDF page bounds")
    if not isinstance(region, (list, tuple)) or len(region) != 4:
        raise ValueError("region must contain exactly x0,y0,x1,y1")
    if any(isinstance(v, bool) or not isinstance(v, (int, float))
           or not math.isfinite(v) for v in region):
        raise ValueError("Region coordinates must be finite numbers")
    x0, y0, x1, y1 = region
    if not (0 <= x0 < x1 <= 1 and 0 <= y0 < y1 <= 1):
        raise ValueError("Region must be ordered, nonempty and entirely inside [0,1]")
    clip = pymupdf.Rect(bounds.x0 + x0 * bounds.width, bounds.y0 + y0 * bounds.height,
                        bounds.x0 + x1 * bounds.width, bounds.y0 + y1 * bounds.height)
    if clip.is_empty or not clip.intersects(bounds):
        raise ValueError("Region has no valid PDF intersection")
    return clip


def render_page(pdf_path, page_number, label, *, region=None, dpi=None):
    pdf_path = approved_pdf(Path(pdf_path))
    if isinstance(page_number, bool) or not isinstance(page_number, int):
        raise ValueError("Page number must be an integer")
    if dpi is not None and (isinstance(dpi, bool) or not isinstance(dpi, (int, float))
                            or not math.isfinite(dpi) or not 72 <= dpi <= MAX_DPI):
        raise ValueError(f"DPI must be finite and between 72 and {MAX_DPI}")
    if region is None and dpi is not None:
        raise ValueError("DPI requires a region; full-page rendering stays at 2.4x")
    session_id = safe_name(os.environ.get("HERMES_SESSION_ID") or
                           datetime.now().strftime("session_%Y%m%d_%H%M%S_%f"))
    artifacts = (ROOT / "artifacts").resolve()
    output_dir = artifacts / session_id
    if output_dir.resolve() != output_dir:
        raise ValueError("Artifact session directory must not be a link")
    label = safe_name(label)
    scale = 2.4 if region is None else (dpi or DEFAULT_REGION_DPI) / 72
    with pymupdf.open(pdf_path) as document:
        if not document.is_pdf or document.needs_pass:
            raise ValueError("A readable, unencrypted PDF is required")
        if not 1 <= page_number <= document.page_count:
            raise ValueError(f"Page must be between 1 and {document.page_count}")
        page = document.load_page(page_number - 1)
        clip = region_clip(page, region) if region is not None else page.rect
        # Predict the outward-rounded raster allocation BEFORE get_pixmap.
        raster = (clip * pymupdf.Matrix(scale, scale)).irect
        validate_dimensions(raster.width, raster.height)
        arguments = dict(matrix=pymupdf.Matrix(scale, scale), colorspace=pymupdf.csRGB, alpha=False)
        if region is not None:
            arguments["clip"] = clip
        pixmap = page.get_pixmap(**arguments)
        validate_dimensions(pixmap.width, pixmap.height)
        output_dir.mkdir(parents=True, exist_ok=True)
        suffix = ""
        if region is not None:
            identity = hashlib.sha256(str(pdf_path).encode()).hexdigest()[:10]
            suffix = f"-crop-{identity}-{uuid4().hex}"
        output_path = output_dir / f"{label}-p{page_number}{suffix}.png"
        if output_path.is_symlink():
            raise ValueError("Artifact target must not be a link")
        fd, temporary = tempfile.mkstemp(prefix=".render-", suffix=".png", dir=output_dir)
        os.close(fd)
        try:
            pixmap.save(temporary)
            del pixmap
            _, metadata = read_validated_png(Path(temporary))
            os.replace(temporary, output_path)
        finally:
            Path(temporary).unlink(missing_ok=True)
    return dict(metadata, media=output_path.as_posix(), pdf_page=page_number,
                region=region, dpi=scale * 72)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("pdf_path", type=Path)
    parser.add_argument("page_number", type=int)
    parser.add_argument("label")
    parser.add_argument("--region", type=float, nargs=4, metavar=("X0", "Y0", "X1", "Y1"))
    parser.add_argument("--dpi", type=float)
    args = parser.parse_args()
    result = render_page(args.pdf_path, args.page_number, args.label, region=args.region, dpi=args.dpi)
    print("MEDIA:" + result["media"])


if __name__ == "__main__":
    main()
