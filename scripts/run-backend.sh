#!/usr/bin/env bash
set -euo pipefail

MODE="${1:-start}"
HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-8000}"

if [[ "${MODE}" == "dev" ]]; then
  uv run uvicorn backend.main:app --reload --host "${HOST}" --port "${PORT}"
else
  uv run uvicorn backend.main:app --host "${HOST}" --port "${PORT}"
fi
