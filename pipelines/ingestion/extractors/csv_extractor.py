"""CSV extraction. <=1000 rows: markdown in 100-row blocks. >1000 rows: statistical summary + sample."""
from __future__ import annotations

import pandas as pd

from ..models import ExtractionError, ExtractedPage
from ._common import rows_to_markdown

SMALL_CSV_MAX_ROWS = 1000
BLOCK_ROWS = 100
SAMPLE_ROWS = 20


def _read(path: str) -> pd.DataFrame:
    for enc in ("utf-8-sig", "latin-1"):
        try:
            return pd.read_csv(path, encoding=enc)
        except UnicodeDecodeError:
            continue
        except (pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
            raise ExtractionError(f"Cannot parse CSV: {exc}") from exc
    raise ExtractionError("Cannot decode CSV")


def _fmt(x) -> str:
    return f"{x:.4g}" if isinstance(x, float) else str(x)


def _summary(df: pd.DataFrame) -> str:
    lines = [f"CSV summary: {len(df)} rows x {len(df.columns)} columns", "", "Columns and dtypes:"]
    lines += [f"- {c}: {df[c].dtype}" for c in df.columns]
    numeric = list(df.select_dtypes("number").columns)
    text_cols = [c for c in df.columns if c not in numeric]
    if numeric:
        lines += ["", "Numeric columns (min / max / mean):"]
        for c in numeric:
            s = df[c]
            lines.append(f"- {c}: {_fmt(s.min())} / {_fmt(s.max())} / {_fmt(float(s.mean()))}")
    if text_cols:
        lines += ["", "Categorical columns (top 5 values):"]
        for c in text_cols:
            top = df[c].value_counts().head(5)
            lines.append(f"- {c}: " + ", ".join(f"{k} ({v})" for k, v in top.items()))
    return "\n".join(lines)


def extract_csv(path: str) -> list[ExtractedPage]:
    df = _read(path)
    n = len(df)
    if n == 0:
        return []
    columns = [str(c) for c in df.columns]
    base = {"extractor": "csv", "row_count": n, "columns": columns}
    pages: list[ExtractedPage] = []
    if n <= SMALL_CSV_MAX_ROWS:
        for i, start in enumerate(range(0, n, BLOCK_ROWS), 1):
            block = df.iloc[start:start + BLOCK_ROWS].fillna("")
            md = rows_to_markdown([columns] + block.values.tolist())
            pages.append(ExtractedPage(i, md, "table",
                                       {**base, "mode": "markdown", "rows": f"{start + 1}-{start + len(block)}"}))
    else:
        pages.append(ExtractedPage(1, _summary(df), "table", {**base, "mode": "summary"}))
        sample = df.head(SAMPLE_ROWS).fillna("")
        pages.append(ExtractedPage(2, rows_to_markdown([columns] + sample.values.tolist()), "table",
                                   {**base, "mode": "sample_rows", "rows": f"1-{len(sample)}"}))
    return pages
