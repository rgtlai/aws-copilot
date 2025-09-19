#!/usr/bin/env bash
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "${PROJECT_ROOT}"

if ! command -v docker >/dev/null 2>&1; then
  echo "[dev-up] docker is required but not installed. Please install Docker Desktop or the Docker CLI." >&2
  exit 1
fi

if command -v docker compose >/dev/null 2>&1; then
  COMPOSE=(docker compose)
elif command -v docker-compose >/dev/null 2>&1; then
  COMPOSE=(docker-compose)
else
  echo "[dev-up] docker compose plugin is required." >&2
  exit 1
fi

SERVICE="mongodb"
COMPOSE_FILE="${PROJECT_ROOT}/docker-compose.yml"

if [[ ! -f "${COMPOSE_FILE}" ]]; then
  echo "[dev-up] docker-compose.yml not found at ${COMPOSE_FILE}" >&2
  exit 1
fi

echo "[dev-up] Ensuring MongoDB container is running..."
"${COMPOSE[@]}" -f "${COMPOSE_FILE}" up -d "${SERVICE}"

if ! command -v pnpm >/dev/null 2>&1; then
  echo "[dev-up] pnpm is required but not installed." >&2
  exit 1
fi

exec pnpm dev
