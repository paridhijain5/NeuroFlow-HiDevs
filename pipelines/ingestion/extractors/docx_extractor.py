"""DOCX extraction with heading hierarchy preserved per section."""
from __future__ import annotations

import re

from docx import Document
from docx.table import Table
from docx.text.paragraph import Paragraph

from ..models import ExtractedPage
from ._common import rows_to_markdown

_HEADING = re.compile(r"^Heading\s+(\d)$", re.I)


def _heading_level(paragraph: Paragraph) -> int | None:
    name = (paragraph.style.name if paragraph.style is not None else "") or ""
    if name.lower() == "title":
        return 1
    m = _HEADING.match(name)
    return int(m.group(1)) if m else None


def extract_docx(path: str) -> list[ExtractedPage]:
    doc = Document(path)
    pages: list[ExtractedPage] = []

    # Page headers (deduplicated across sections), extracted separately
    seen: set[str] = set()
    for section in doc.sections:
        text = "\n".join(p.text.strip() for p in section.header.paragraphs if p.text.strip())
        if text and text not in seen:
            seen.add(text)
            pages.append(ExtractedPage(0, text, "text",
                                       {"role": "header", "level": "header", "extractor": "docx"}))

    stack: list[tuple[int, str]] = []
    meta = {"level": "body", "section": "Preamble", "heading_path": [], "extractor": "docx"}
    lines: list[str] = []

    def flush():
        nonlocal lines
        if lines:
            pages.append(ExtractedPage(0, "\n\n".join(lines), "text", dict(meta)))
            lines = []

    for child in doc.element.body.iterchildren():
        tag = child.tag.rsplit("}", 1)[-1]
        if tag == "p":
            para = Paragraph(child, doc)
            text = para.text.strip()
            level = _heading_level(para)
            if level and text:
                flush()
                while stack and stack[-1][0] >= level:
                    stack.pop()
                stack.append((level, text))
                meta = {"level": f"h{level}", "section": text,
                        "heading_path": [t for _, t in stack], "extractor": "docx"}
                lines = [text]
            elif text:
                lines.append(text)
        elif tag == "tbl":
            flush()
            rows = [[c.text for c in r.cells] for r in Table(child, doc).rows]
            md = rows_to_markdown(rows)
            if md.strip():
                pages.append(ExtractedPage(0, md, "table", dict(meta)))
    flush()

    for i, p in enumerate(pages, 1):  # DOCX has no pages: number sections sequentially
        p.page_number = i
    return pages
