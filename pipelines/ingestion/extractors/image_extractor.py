"""Image extraction: vision-LLM description + OCR text."""
from __future__ import annotations

import asyncio
import base64
import io

from PIL import Image

from ..models import ExtractionError, ExtractedPage
from ._common import ocr_image

SUPPORTED_FORMATS = {"JPEG", "PNG", "WEBP"}
MAX_SIDE = 1024
PROMPT = ("Describe this image in detail for a search index: objects, people, setting, "
          "charts or diagrams (with their key values), and any visible text.")


def _prepare(img: Image.Image) -> Image.Image:
    if img.mode in ("RGBA", "LA", "P"):
        rgba = img.convert("RGBA")
        bg = Image.new("RGB", rgba.size, (255, 255, 255))
        bg.paste(rgba, mask=rgba.split()[-1])
        img = bg
    else:
        img = img.convert("RGB")
    img.thumbnail((MAX_SIDE, MAX_SIDE))  # only shrinks; keeps aspect ratio
    return img


def _data_url(img: Image.Image) -> str:
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=85)
    return "data:image/jpeg;base64," + base64.b64encode(buf.getvalue()).decode()


async def extract_image(path: str, client) -> list[ExtractedPage]:
    from providers.base import ChatMessage
    from providers.router import RoutingCriteria

    try:
        img = Image.open(path)
        fmt = img.format
        img.load()
    except Exception as exc:
        raise ExtractionError(f"Cannot open image: {exc}") from exc
    if fmt not in SUPPORTED_FORMATS:
        raise ExtractionError(f"Unsupported image format {fmt}; use JPEG, PNG or WEBP")

    original_size = img.size
    img = _prepare(img)
    ocr_text = await asyncio.to_thread(ocr_image, img)
    message = ChatMessage("user", [
        {"type": "text", "text": PROMPT},
        {"type": "image_url", "image_url": {"url": _data_url(img)}},
    ])
    result = await client.chat([message], RoutingCriteria(task_type="image_description", require_vision=True))

    content = result.content.strip()
    if ocr_text:
        content += "\n\nText found in image: " + ocr_text
    return [ExtractedPage(1, content, "image_description",
                          {"extractor": "image", "format": fmt, "original_size": list(original_size),
                           "sent_size": list(img.size), "has_ocr_text": bool(ocr_text),
                           "vision_model": result.model})]
