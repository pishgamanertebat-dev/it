"""Small shared region operation. Source IDs resolve through existing registries.

No model-supplied filesystem paths, new role, retrieval, or object detection.
The fixed project interpreter renders in a subprocess with the common engine.
"""
from __future__ import annotations
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess

from integrations.hermes.technical_docs_boundary import technical_docs_enabled
from integrations.hermes.technical_image import read_validated_png, upload_requires_document

ROOT = Path(__file__).resolve().parents[2]
REGION_TOOL = "technical_render_region"
REGION_DESCRIPTION = (
    "Render a known region DIRECTLY from an approved Parts Book or Shop/Technical Manual PDF. "
    "Use after source/page selection, only when region location is supported by a viewed page, "
    "PDF geometry or reliable evidence. Never infer exploded-view location from a PN row bbox. "
    "region=[x0,y0,x1,y1] normalized to [0,1] on the DISPLAYED page AFTER rotation/CropBox, "
    "top-left origin, x right/y down. PDF text boxes must first be transformed by page.rotation_matrix. "
    "Preserve model, serial, figure, page and applicability. Tiny pins/callouts/PNs/circuits/tables "
    "can use dpi=288 (default), up to 600 within pixel limits. No known location: send the full "
    "page through the existing tool, never guess a crop or unreadable technical data. Returns "
    "only the requested crop MEDIA; no extra full page. Upscaling scans cannot restore missing detail."
)


def region_schema():
    return {"name": REGION_TOOL, "description": REGION_DESCRIPTION, "parameters": {
        "type": "object", "additionalProperties": False,
        "required": ["model", "source", "page", "region"], "properties": {
            "model": {"type": "string", "description": "Verified model; Parts Book accepts approved fleet aliases"},
            "source": {"type": "string", "enum": ["parts_book", "shop_manual"]},
            "book_id": {"type": "string", "description": "parts_book: exact selected_part_books book_id from lookup, required; not a path"},
            "page": {"type": "integer", "minimum": 1, "description": "1-based PDF page, preserve the selected figure/page"},
            "region": {"type": "array", "minItems": 4, "maxItems": 4,
                       "items": {"type": "number", "minimum": 0, "maximum": 1}},
            "dpi": {"type": "number", "minimum": 72, "maximum": 600, "default": 288},
        }}}


def render_region(args, *, session_id, home, resolver):
    if not technical_docs_enabled(home):
        raise ValueError("Technical Docs surface is not enabled in this profile")
    if set(args) - set(region_schema()["parameters"]["properties"]):
        raise ValueError("Unknown region arguments; filesystem paths are not accepted")
    if not isinstance(session_id, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,199}", session_id):
        raise ValueError("A valid current session is required")
    region = args.get("region")
    if (not isinstance(region, list) or len(region) != 4
            or any(isinstance(v, bool) or not isinstance(v, (int, float))
                   or not math.isfinite(v) for v in region)
            or not (0 <= region[0] < region[2] <= 1 and 0 <= region[1] < region[3] <= 1)):
        raise ValueError("Region must be four finite ordered coordinates inside [0,1]")
    page, dpi = args.get("page"), args.get("dpi", 288)
    if isinstance(page, bool) or not isinstance(page, int) or page < 1:
        raise ValueError("Page must be a positive integer")
    if (isinstance(dpi, bool) or not isinstance(dpi, (int, float))
            or not math.isfinite(dpi) or not 72 <= dpi <= 600):
        raise ValueError("DPI must be between 72 and 600")
    models = json.loads((ROOT / "tools/manual_models.json").read_text(encoding="utf-8"))
    model = args.get("model")
    resolution = None
    if args.get("source") == "parts_book":
        resolution = resolver.resolve_model(model)
        if resolution["resolution_status"] != "RESOLVED":
            raise ValueError("Parts Book model is unknown or ambiguous")
        model = resolution["fleet_asset"]["display_model"]
    if model not in models:
        raise ValueError("Unknown technical model")
    folder = (ROOT / models[model]).resolve(strict=True)
    # Complete machine instructions before opening the selected source.
    (folder / "AGENTS.md").read_text(encoding="utf-8")
    if args.get("source") == "shop_manual":
        if args.get("book_id"):
            raise ValueError("book_id belongs only to Parts Book")
        metadata = json.loads((folder / "manual_sections.json").read_text(encoding="utf-8"))
        pdf = Path(metadata["manual"]).resolve(strict=True)
        label = "manual_evidence"
        source_identity = {"model": model, "source": "shop_manual"}
    elif args.get("source") == "parts_book":
        book_id = args.get("book_id")
        if not isinstance(book_id, str) or not book_id:
            raise ValueError("Select an exact book_id from Parts Book lookup")
        db = Path(os.environ.get("PARTBOOK_TEST_DB") or ROOT / "runtime/partbook/partbook_index.sqlite").resolve(strict=True)
        try:
            with sqlite3.connect(db.as_uri() + "?mode=ro", uri=True) as connection:
                connection.row_factory = sqlite3.Row
                books = resolver.select_books(connection, resolution, explicit_book=book_id)
        except sqlite3.Error as exc:
            raise ValueError("Parts Book source registry is unavailable") from exc
        book = books[0]
        pdf = (ROOT / book["source_pdf"]).resolve(strict=True)
        label = "partbook"
        source_identity = {"model": model, "requested_model": args["model"], "source": "parts_book",
                           "book_id": book_id, "book_model": book["model"],
                           "source_identity": book["source_identity"],
                           "machine_serial_prefix": book.get("machine_serial_prefix"),
                           "machine_serial_from": book.get("machine_serial_from"),
                           "machine_serial_to": book.get("machine_serial_to"),
                           "applicability_status": "PART_APPLICABILITY_UNCONFIRMED"}
    else:
        raise ValueError("source must be parts_book or shop_manual")
    if pdf.parent != folder or pdf.suffix.lower() != ".pdf":
        raise ValueError("Selected PDF is outside the selected model folder")
    command = [str(ROOT / ".venv/Scripts/python.exe"), "-B", str(ROOT / "tools/render_page.py"),
               str(pdf), str(page), label, "--region", *map(str, region), "--dpi", str(dpi)]
    result = subprocess.run(command, capture_output=True, timeout=90, cwd=ROOT,
                            env=dict(os.environ, HERMES_SESSION_ID=session_id, PYTHONIOENCODING="utf-8"),
                            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
    if result.returncode:
        raise ValueError("PDF region render failed: " + result.stderr.decode("utf-8", "replace")[-400:])
    lines = result.stdout.decode("utf-8").strip().splitlines()
    if len(lines) != 1 or not lines[0].startswith("MEDIA:"):
        raise ValueError("Renderer did not return one validated MEDIA file")
    media = Path(lines[0][6:]).resolve(strict=True)
    if media.parent != (ROOT / "artifacts" / session_id).resolve(strict=True):
        raise ValueError("Renderer returned a foreign session artifact")
    _, image = read_validated_png(media)
    upload_requires_document(image)
    return dict(source_identity, success=True, pdf_page=page, region=region, dpi=dpi,
                **image, media=["MEDIA:" + media.as_posix()],
                delivery_note="Include the MEDIA line in the final response. For lossless small technical text, "
                              "include [[as_document]] to use the existing unmodified PNG document transport. "
                              "Do not claim client receipt from rendering alone.")
