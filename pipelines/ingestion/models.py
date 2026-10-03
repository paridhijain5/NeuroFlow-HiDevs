from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class ExtractedPage:
    page_number: int
    content: str
    content_type: str  # "text" | "table" | "image_description"
    metadata: dict = field(default_factory=dict)


class ExtractionError(Exception):
    """Raised when a source cannot be extracted (bad file, robots.txt block, ...)."""
