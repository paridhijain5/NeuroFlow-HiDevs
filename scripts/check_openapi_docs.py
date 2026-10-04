#!/usr/bin/env python3
"""Audits OpenAPI docs completeness. Exits 1 if any endpoint/field is under-documented.

  python scripts/check_openapi_docs.py --url https://<app>/openapi.json
  python scripts/check_openapi_docs.py --file openapi.json
Checks per endpoint: summary, description (>= 40 chars), tags, a real response description.
Checks per schema field: description and example/examples.
"""
from __future__ import annotations

import argparse
import json
import urllib.request
from typing import Any

METHODS = {"get", "post", "put", "patch", "delete"}
GENERIC_RESPONSE = {"Successful Response", ""}


def audit(spec: dict[str, Any]) -> list[str]:
    problems: list[str] = []
    for path, item in spec.get("paths", {}).items():
        for method, op in item.items():
            if method not in METHODS:
                continue
            where = f"{method.upper()} {path}"
            if not op.get("summary") or op["summary"] == path.strip("/").replace("_", " ").title():
                problems.append(f"{where}: missing summary")
            if len(op.get("description", "")) < 40:
                problems.append(f"{where}: description missing or too short (<40 chars)")
            if not op.get("tags"):
                problems.append(f"{where}: no tags")
            ok = [r for code, r in op.get("responses", {}).items() if str(code).startswith("2")]
            if not ok or all(r.get("description", "") in GENERIC_RESPONSE for r in ok):
                problems.append(f"{where}: no custom response_description")
    for name, schema in spec.get("components", {}).get("schemas", {}).items():
        if name in {"HTTPValidationError", "ValidationError"}:
            continue
        for field, prop in schema.get("properties", {}).items():
            if not prop.get("description"):
                problems.append(f"schema {name}.{field}: missing description")
            if "example" not in prop and "examples" not in prop and "default" not in prop:
                problems.append(f"schema {name}.{field}: missing example")
    return problems


def main() -> int:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--url")
    g.add_argument("--file")
    a = p.parse_args()
    if a.url:
        with urllib.request.urlopen(a.url, timeout=30) as r:  # noqa: S310
            spec = json.load(r)
    else:
        with open(a.file) as f:
            spec = json.load(f)
    problems = audit(spec)
    for line in problems:
        print("-", line)
    n_ops = sum(1 for i in spec.get("paths", {}).values() for m in i if m in METHODS)
    print(f"\n{n_ops} endpoints audited, {len(problems)} problem(s).")
    return 1 if problems else 0


if __name__ == "__main__":
    raise SystemExit(main())
