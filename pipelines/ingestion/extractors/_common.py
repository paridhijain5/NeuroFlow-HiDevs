from __future__ import annotations

import logging

log = logging.getLogger(__name__)


def _cell(value) -> str:
    s = "" if value is None else str(value)
    return s.replace("|", "\\|").replace("\n", " ").strip()


def rows_to_markdown(rows: list[list]) -> str:
    """First row is the header. Returns a GitHub-style markdown table."""
    if not rows:
        return ""
    width = max(len(r) for r in rows)
    norm = [[_cell(c) for c in r] + [""] * (width - len(r)) for r in rows]
    lines = ["| " + " | ".join(norm[0]) + " |", "| " + " | ".join("---" for _ in range(width)) + " |"]
    lines += ["| " + " | ".join(r) + " |" for r in norm[1:]]
    return "\n".join(lines)


def ocr_image(img, config: str = "") -> str:
    """Run Tesseract; degrade gracefully (return '') if it is not installed."""
    try:
        import pytesseract

        return pytesseract.image_to_string(img, config=config).strip()
    except Exception as exc:  # TesseractNotFoundError, import errors, bad images
        log.warning("OCR unavailable or failed: %s", exc)
        return ""
