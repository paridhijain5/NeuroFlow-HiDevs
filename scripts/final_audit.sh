#!/usr/bin/env bash
# Task 20 Step 1: final codebase audit. Run from repo root (Git Bash / WSL / Linux / macOS).
set -u
fail=0
echo "== 1. TODO comments (document each in a GitHub Issue or remove) =="
grep -rnE "#\s*TODO|//\s*TODO" backend/ tests/ sdk/ --include=*.py || echo "none"
echo; echo "== 2. print() outside tests (use logger.info) =="
grep -rnE "^\s*print\(" backend/ --include=*.py || echo "none"
echo; echo "== 3. .env / mlruns tracked by git =="
tracked=$(git ls-files | grep -E "(^|/)\.env$|(^|/)mlruns/" || true)
if [ -n "$tracked" ]; then echo "$tracked"; echo "FIX: git rm -r --cached <path> and add to .gitignore"; fail=1; else echo "none"; fi
echo; echo "== 4. ruff =="; ruff check backend/ || fail=1
echo; echo "== 5. mypy =="; mypy backend/ || fail=1
echo; echo "== 6. pytest =="; pytest tests/unit/ -v || fail=1
echo; echo "== 7. detect-secrets =="
detect-secrets scan --baseline .secrets.baseline && git diff --stat .secrets.baseline
echo
[ $fail -eq 0 ] && echo "AUDIT PASSED (also resolve any TODO/print lines listed above)" || echo "AUDIT FAILED: fix the items above"
exit $fail
