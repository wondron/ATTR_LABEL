#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
[[ "${ENV_FILE}" == /* ]] || ENV_FILE="${PROJECT_DIR}/${ENV_FILE}"

die() {
  echo "错误：$*" >&2
  exit 2
}

if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

DATA_DIR="${1:-${LABEL_DATA_DIR:-10-temp_label}}"
[[ "${DATA_DIR}" == /* ]] || DATA_DIR="${PROJECT_DIR}/${DATA_DIR}"

HOST="${LABEL_HOST:-0.0.0.0}"
PORT="${LABEL_PORT:-8577}"
if [[ ! "${PORT}" =~ ^[0-9]+$ || ${#PORT} -gt 5 ]]; then
  die "端口无效：${PORT}"
fi
PORT="$((10#${PORT}))"
(( PORT >= 1 && PORT <= 65535 )) || die "端口无效：${PORT}"

if [[ -z "${PYTHON_BIN:-}" ]]; then
  CONDA_ENV_NAME="${CONDA_ENV_NAME-}"
  if [[ -n "${CONDA_ENV_NAME}" ]]; then
    [[ -n "${CONDA_BASE_DIR:-}" ]] ||
      die "配置了 CONDA_ENV_NAME，但 CONDA_BASE_DIR 为空"
    if [[ "${CONDA_ENV_NAME}" == "base" ]]; then
      PYTHON_BIN="${CONDA_BASE_DIR}/bin/python"
    else
      PYTHON_BIN="${CONDA_BASE_DIR}/envs/${CONDA_ENV_NAME}/bin/python"
    fi
  else
    PYTHON_BIN=python3
  fi
fi

[[ -d "${DATA_DIR}" ]] || die "数据目录不存在：${DATA_DIR}"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "找不到 Python：${PYTHON_BIN}"

exec "${PYTHON_BIN}" "${PROJECT_DIR}/run_annotation_ui.py" \
  --data-dir "${DATA_DIR}" \
  --host "${HOST}" \
  --port "${PORT}" \
  --no-browser
