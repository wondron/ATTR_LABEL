#!/usr/bin/env bash
set -euo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PYTHON_BIN="${PYTHON_BIN:-python3}"
DATA_DIR="${1:-${LABEL_DATA_DIR:-${SCRIPT_DIR}/10-temp_label}}"
HOST="${LABEL_HOST:-0.0.0.0}"
PORT="${LABEL_PORT:-8577}"

if [[ ! -d "${DATA_DIR}" ]]; then
  echo "Data directory does not exist: ${DATA_DIR}" >&2
  echo "Usage: ./start.sh /absolute/path/to/images-and-json" >&2
  exit 2
fi

exec "${PYTHON_BIN}" "${SCRIPT_DIR}/run_annotation_ui.py" \
  --data-dir "${DATA_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --no-browser
