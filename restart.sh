#!/usr/bin/env bash
set -Eeuo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
ENV_FILE="${ENV_FILE:-${PROJECT_DIR}/.env}"
[[ "${ENV_FILE}" == /* ]] || ENV_FILE="${PROJECT_DIR}/${ENV_FILE}"
START_SCRIPT="${PROJECT_DIR}/start.sh"
PID_FILE="${PROJECT_DIR}/annotation.pid"
LOG_FILE="${PROJECT_DIR}/annotation.log"
WAIT_SECONDS="${WAIT_SECONDS:-20}"

die() {
  echo "错误：$*" >&2
  exit 1
}

if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

DATA_DIR="${1:-${LABEL_DATA_DIR:-10-temp_label}}"
[[ "${DATA_DIR}" == /* ]] || DATA_DIR="${PROJECT_DIR}/${DATA_DIR}"
LABEL_HOST="${LABEL_HOST:-0.0.0.0}"
LABEL_PORT="${LABEL_PORT:-8577}"

[[ -d "${DATA_DIR}" ]] || die "数据目录不存在：${DATA_DIR}"
[[ "${WAIT_SECONDS}" =~ ^[1-9][0-9]*$ ]] || die "WAIT_SECONDS 必须是正整数"
[[ "${LABEL_PORT}" =~ ^[0-9]+$ && ${#LABEL_PORT} -le 5 ]] ||
  die "端口无效：${LABEL_PORT}"
LABEL_PORT="$((10#${LABEL_PORT}))"
(( LABEL_PORT >= 1 && LABEL_PORT <= 65535 )) || die "端口无效：${LABEL_PORT}"

is_our_process() {
  local pid="$1"
  [[ "${pid}" =~ ^[1-9][0-9]*$ && -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' '\n' < "/proc/${pid}/cmdline" |
    grep -Fx -- "${PROJECT_DIR}/run_annotation_ui.py" >/dev/null 2>&1
}

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(tr -d '[:space:]' < "${PID_FILE}")"
  if is_our_process "${OLD_PID}"; then
    echo "停止旧进程：${OLD_PID}"
    kill "${OLD_PID}" || die "无法停止旧进程 ${OLD_PID}"
    for _ in {1..20}; do
      [[ ! -e "/proc/${OLD_PID}" ]] && break
      sleep 0.5
    done
    [[ ! -e "/proc/${OLD_PID}" ]] || die "旧进程 ${OLD_PID} 在 10 秒内没有退出"
  elif [[ "${OLD_PID}" =~ ^[1-9][0-9]*$ && -e "/proc/${OLD_PID}" ]]; then
    die "PID 文件指向其他进程（${OLD_PID}），已停止操作"
  fi
  rm -f -- "${PID_FILE}"
fi

export ENV_FILE LABEL_DATA_DIR="${DATA_DIR}" LABEL_HOST LABEL_PORT
export PYTHONUNBUFFERED=1

{
  echo
  echo "[$(date '+%F %T')] restart.sh 启动服务"
} >> "${LOG_FILE}"

PID_TMP="${PID_FILE}.tmp.$$"
: > "${PID_TMP}" || die "无法写入 PID 文件目录"
trap 'rm -f -- "${PID_TMP}"' EXIT

cd -- "${PROJECT_DIR}"
nohup /usr/bin/env bash "${START_SCRIPT}" "${DATA_DIR}" \
  >> "${LOG_FILE}" 2>&1 < /dev/null &
NEW_PID=$!

if ! printf '%s\n' "${NEW_PID}" > "${PID_TMP}" ||
   ! mv -f -- "${PID_TMP}" "${PID_FILE}"; then
  kill "${NEW_PID}" 2>/dev/null || true
  die "无法写入 PID 文件；已停止新进程 ${NEW_PID}"
fi

HEALTH_HOST="${LABEL_HOST}"
case "${HEALTH_HOST}" in
  0.0.0.0) HEALTH_HOST=127.0.0.1 ;;
  ::|\[::\]|::1|\[::1\]) HEALTH_HOST='[::1]' ;;
  \[*\]) ;;
  *:*) HEALTH_HOST="[${HEALTH_HOST}]" ;;
esac
HEALTH_URL="http://${HEALTH_HOST}:${LABEL_PORT}/api/v1/health"

show_startup_failure() {
  rm -f -- "${PID_FILE}"
  echo "启动失败，最近日志如下：" >&2
  tail -n 40 "${LOG_FILE}" >&2 || true
  exit 1
}

if command -v curl >/dev/null 2>&1; then
  HEALTH_DEADLINE=$((SECONDS + WAIT_SECONDS))
  HEALTH_ERROR=""
  while (( SECONDS < HEALTH_DEADLINE )); do
    kill -0 "${NEW_PID}" 2>/dev/null || show_startup_failure
    if HEALTH_OUTPUT="$(curl --noproxy '*' -fsS --connect-timeout 2 --max-time 2 "${HEALTH_URL}" 2>&1)"; then
      kill -0 "${NEW_PID}" 2>/dev/null || show_startup_failure
      echo "启动成功，PID：${NEW_PID}"
      echo "日志：${LOG_FILE}"
      echo "健康检查：${HEALTH_URL}"
      exit 0
    fi
    HEALTH_ERROR="${HEALTH_OUTPUT}"
    sleep 1
  done

  kill -0 "${NEW_PID}" 2>/dev/null || show_startup_failure
  echo "进程 ${NEW_PID} 仍在运行，但健康检查超时：${HEALTH_URL}" >&2
  [[ -z "${HEALTH_ERROR}" ]] || echo "curl 错误：${HEALTH_ERROR}" >&2
  tail -n 40 "${LOG_FILE}" >&2 || true
  exit 1
fi

sleep 1
kill -0 "${NEW_PID}" 2>/dev/null || show_startup_failure
echo "启动成功，PID：${NEW_PID}（未安装 curl，已跳过健康检查）"
echo "日志：${LOG_FILE}"
