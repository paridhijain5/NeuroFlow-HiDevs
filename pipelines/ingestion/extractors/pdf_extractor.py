"""PDF extraction: pypdfium2 text, OCR for scanned pages, pdfplumber tables."""
from __future__ import annotations

import logging

import pdfplumber
import pypdfium2 as pdfium

from ..models import ExtractedPage
from ._common import ocr_image, rows_to_markdown

log = logging.getLogger(__name__)

MIN_CHARS_PER_PAGE = 50  # fewer extracted characters than this => treat as scanned


def extract_pdf(path: str) -> list[ExtractedPage]:
    pages: list[ExtractedPage] = []
    pdf = pdfium.PdfDocument(path)
    try:
        with pdfplumber.open(path) as plumber:
            for i in range(len(pdf)):
                num = i + 1
                page = pdf[i]
                text = (page.get_textpage().get_text_range() or "").strip()
                scanned = len(text) < MIN_CHARS_PER_PAGE
                if scanned:
                    image = page.render(scale=2).to_pil()
                    text = ocr_image(image, "--psm 6")  # assume a uniform block of text
                if text:
                    pages.append(ExtractedPage(num, text, "text",
                                               {"page_number": num, "ocr": scanned, "extractor": "pdf"}))
                if not scanned:
                    try:
                        tables = plumber.pages[i].extract_tables()
                    except Exception as exc:
                        log.warning("Table extraction failed on page %d: %s", num, exc)
                        tables = []
                    for t_idx, rows in enumerate(tables):
                        md = rows_to_markdown(rows)
                        if md.strip():
                            pages.append(ExtractedPage(num, md, "table",
                                                       {"page_number": num, "table_index": t_idx,
                                                        "extractor": "pdf"}))
    finally:
        pdf.close()
    return pages
