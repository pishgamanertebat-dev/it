"""Complete PNG decoding and local resource budgets; no minimum byte size."""
from __future__ import annotations
from io import BytesIO
from pathlib import Path
from PIL import Image

MAX_DIMENSION = 10_000
MAX_PIXELS = 32_000_000
MAX_DECODED_BYTES = 128_000_000
MAX_PNG_BYTES = 64_000_000

# Upload contracts, separate from decoder budgets. Conservative decimal MB.
# https://docs.bale.ai/ (Sending files: multipart photo 10 MB, document 50 MB)
# https://core.telegram.org/bots/api#sendphoto and #senddocument
PHOTO_UPLOAD_BYTES = 10_000_000
DOCUMENT_UPLOAD_BYTES = 50_000_000


def upload_requires_document(image: dict, platform: str = "bale") -> bool:
    if image["image_bytes"] > DOCUMENT_UPLOAD_BYTES:
        raise ValueError("Technical PNG exceeds the 50 MB document upload limit")
    width, height = image["image_width"], image["image_height"]
    return (image["image_bytes"] > PHOTO_UPLOAD_BYTES
            or (platform == "telegram" and
                (width + height > 10_000 or max(width / height, height / width) > 20)))


def validate_dimensions(width: int, height: int) -> None:
    if (width <= 0 or height <= 0 or max(width, height) > MAX_DIMENSION
            or width * height > MAX_PIXELS
            or width * height * 4 > MAX_DECODED_BYTES):
        raise ValueError("Technical image exceeds pixel/dimension/memory budget")


def validate_png(data: bytes) -> dict:
    if not data or len(data) > MAX_PNG_BYTES:
        raise ValueError("Technical PNG is empty or exceeds the encoded byte budget")
    try:
        with Image.open(BytesIO(data)) as image:
            if image.format != "PNG" or getattr(image, "n_frames", 1) != 1:
                raise ValueError("Only a single-frame PNG is permitted")
            width, height = image.size
            validate_dimensions(width, height)
            image.verify()  # chunk CRCs and complete structure, not just IHDR
        with Image.open(BytesIO(data)) as image:
            image.load()  # decompress every pixel; truncated data must fail
    except (OSError, SyntaxError, Image.DecompressionBombError) as exc:
        raise ValueError("Invalid or incomplete technical PNG") from exc
    return {"image_width": width, "image_height": height, "image_bytes": len(data),
            "image_validated": True}


def read_validated_png(path: Path) -> tuple[bytes, dict]:
    if not path.is_file() or not 0 < path.stat().st_size <= MAX_PNG_BYTES:
        raise ValueError("Technical PNG is missing, empty or too large")
    data = path.read_bytes()
    return data, validate_png(data)
