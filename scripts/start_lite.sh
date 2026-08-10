#!/usr/bin/env bash
# Arranque rápido perfil lite (PC casa)
set -euo pipefail
cd "$(dirname "$0")/.."

if [[ ! -d .venv ]]; then
  python3 -m venv .venv
fi
# shellcheck disable=SC1091
source .venv/bin/activate
pip install -q -U pip
pip install -q -e ".[dev]"

export SHIPRAG_PROFILE=lite
echo "==> Ingesta sample (lite)"
shiprag --profile lite ingest data/sample
echo "==> Smoke"
shiprag --profile lite smoke
echo "==> UI en http://127.0.0.1:8080  (Ctrl+C para parar)"
shiprag --profile lite serve --host 127.0.0.1 --port 8080
