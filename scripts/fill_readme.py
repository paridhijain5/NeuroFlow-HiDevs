"""Fill README placeholders with real data.

  python scripts/fill_readme.py [--metrics evaluation/quality_final.json]
                                [--arch docs/architecture.md]
                                [--openapi https://your-app/openapi.json | openapi.json]
"""
from __future__ import annotations

import argparse
import json
import re
import urllib.request
from pathlib import Path


def replace_block(text: str, name: str, body: str) -> str:
    pat = re.compile(rf"(<!-- {name}_START -->)(.*?)(<!-- {name}_END -->)", re.S)
    return pat.sub(lambda m: f"{m.group(1)}\n{body}\n{m.group(3)}", text)


def fmt(v):
    return f"{v:.3f}".rstrip("0").rstrip(".") if isinstance(v, float) else str(v)


def metrics_table(path: Path) -> str:
    data = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(data, dict) and isinstance(data.get("metrics"), dict):
        data = data["metrics"]
    rows = ["| Metric | Achieved | Target | Status |", "|---|---|---|---|"]
    keys = ("value", "achieved", "score", "actual")

    def walk(prefix, d):
        for k, v in d.items():
            name = f"{prefix}{k}"
            if isinstance(v, dict) and any(x in v for x in keys):
                val = next(v[x] for x in keys if x in v)
                tgt = v.get("target", v.get("threshold", ""))
                ok = v.get("passed", v.get("met"))
                status = "" if ok is None else ("met" if ok else "not met")
                rows.append(f"| {name} | {fmt(val)} | {fmt(tgt) if tgt != '' else ''} | {status} |")
            elif isinstance(v, dict):
                walk(f"{name}.", v)
            elif isinstance(v, (int, float, str, bool)):
                rows.append(f"| {name} | {fmt(v)} | | |")

    walk("", data)
    return "\n".join(rows)


def arch_block(path: Path) -> str:
    t = path.read_text(encoding="utf-8")
    m = re.search(r"```mermaid.*?```", t, re.S)
    return m.group(0) if m else t.strip()


def api_table(src: str) -> str:
    if src.startswith("http"):
        raw = urllib.request.urlopen(src).read().decode()
    else:
        raw = Path(src).read_text(encoding="utf-8")
    spec = json.loads(raw)
    global_sec = bool(spec.get("security"))
    rows = ["| Method | Path | Auth | Description |", "|---|---|---|---|"]
    for p, ops in sorted(spec.get("paths", {}).items()):
        for method, op in ops.items():
            if method.lower() not in {"get", "post", "put", "patch", "delete"}:
                continue
            auth = "Required" if (op.get("security") or global_sec) else "None"
            rows.append(f"| {method.upper()} | `{p}` | {auth} | {op.get('summary', '')} |")
    return "\n".join(rows)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--readme", default="README.md")
    ap.add_argument("--metrics", default="evaluation/quality_final.json")
    ap.add_argument("--arch", default="docs/architecture.md")
    ap.add_argument("--openapi")
    a = ap.parse_args()
    readme = Path(a.readme)
    text = readme.read_text(encoding="utf-8")
    if Path(a.metrics).exists():
        text = replace_block(text, "METRICS", metrics_table(Path(a.metrics)))
        print("metrics filled")
    else:
        print("SKIP metrics: file not found")
    if Path(a.arch).exists():
        text = replace_block(text, "ARCH", arch_block(Path(a.arch)))
        print("architecture filled")
    else:
        print("SKIP architecture: file not found")
    if a.openapi:
        text = replace_block(text, "API", api_table(a.openapi))
        print("API table filled")
    readme.write_text(text, encoding="utf-8")


if __name__ == "__main__":
    main()
