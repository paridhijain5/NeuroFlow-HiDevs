#!/usr/bin/env bash
# Generates a self-signed cert for local/dev into infra/nginx/certs (gitignored).
# Run from the repo root:  bash infra/nginx/gen-self-signed-cert.sh
set -euo pipefail
OUT="$(cd "$(dirname "$0")" && pwd)/certs"
mkdir -p "$OUT"
if command -v openssl >/dev/null 2>&1; then
  MSYS_NO_PATHCONV=1 openssl req -x509 -nodes -newkey rsa:2048 -days 365 \
    -keyout "$OUT/server.key" -out "$OUT/server.crt" \
    -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
else
  echo "openssl not found - using Docker instead"
  MSYS_NO_PATHCONV=1 docker run --rm -v "$OUT:/certs" alpine/openssl req -x509 -nodes -newkey rsa:2048 \
    -days 365 -keyout /certs/server.key -out /certs/server.crt \
    -subj "/CN=localhost" -addext "subjectAltName=DNS:localhost,IP:127.0.0.1"
fi
chmod 644 "$OUT/server.crt"; chmod 600 "$OUT/server.key" 2>/dev/null || true
echo "Wrote $OUT/server.crt and server.key"
