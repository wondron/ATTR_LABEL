#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
START_SCRIPT="${SCRIPT_DIR}/start.sh"
PID_FILE="${PID_FILE:-${SCRIPT_DIR}/annotation.pid}"
LOG_FILE="${LOG_FILE:-${SCRIPT_DIR}/annotation.log}"
ENV_FILE="${ENV_FILE:-/etc/default/multi-attribute-label}"
SERVICE_NAME="${SERVICE_NAME:-multi-attribute-label.service}"
WAIT_SECONDS="${WAIT_SECONDS:-20}"

die() {
  echo "错误：$*" >&2
  exit 1
}

# 兼容原来的配置文件。该文件由服务器管理员维护，因此按 shell 环境文件加载。
if [[ -r "${ENV_FILE}" ]]; then
  set -a
  # shellcheck disable=SC1090
  source "${ENV_FILE}"
  set +a
fi

if [[ -z "${PYTHON_BIN:-}" ]]; then
  if [[ -x /root/miniconda3/envs/wondron/bin/python3 ]]; then
    PYTHON_BIN=/root/miniconda3/envs/wondron/bin/python3
  else
    PYTHON_BIN=python3
  fi
fi

DATA_DIR="${1:-${LABEL_DATA_DIR:-${SCRIPT_DIR}/10-temp_label}}"
LABEL_HOST="${LABEL_HOST:-0.0.0.0}"
LABEL_PORT="${LABEL_PORT:-8577}"

[[ -f "${START_SCRIPT}" ]] || die "找不到启动脚本：${START_SCRIPT}"
[[ -d "${DATA_DIR}" ]] || die "数据目录不存在：${DATA_DIR}"
[[ "${WAIT_SECONDS}" =~ ^[1-9][0-9]*$ ]] || die "WAIT_SECONDS 必须是正整数"
[[ "${LABEL_PORT}" =~ ^[0-9]+$ ]] &&
  (( LABEL_PORT >= 1 && LABEL_PORT <= 65535 )) || die "LABEL_PORT 无效：${LABEL_PORT}"
command -v "${PYTHON_BIN}" >/dev/null 2>&1 || die "找不到 Python：${PYTHON_BIN}"

# 第一次改用 nohup 时，避免旧 systemd 服务占用同一个端口或在重启后再次启动。
if command -v systemctl >/dev/null 2>&1 &&
   { systemctl is-active --quiet "${SERVICE_NAME}" 2>/dev/null ||
     systemctl is-enabled --quiet "${SERVICE_NAME}" 2>/dev/null; }; then
  echo "检测到旧 systemd 服务，正在一次性停用：${SERVICE_NAME}"
  if (( EUID == 0 )); then
    systemctl disable --now "${SERVICE_NAME}"
  elif command -v sudo >/dev/null 2>&1; then
    sudo systemctl disable --now "${SERVICE_NAME}"
  else
    die "请先以 root 执行：systemctl disable --now ${SERVICE_NAME}"
  fi
fi

is_our_process() {
  local pid="$1"
  [[ "${pid}" =~ ^[1-9][0-9]*$ && -r "/proc/${pid}/cmdline" ]] || return 1
  tr '\0' '\n' < "/proc/${pid}/cmdline" |
    grep -Fx -- "${SCRIPT_DIR}/run_annotation_ui.py" >/dev/null 2>&1
}

if [[ -f "${PID_FILE}" ]]; then
  OLD_PID="$(tr -d '[:space:]' < "${PID_FILE}")"
  if is_our_process "${OLD_PID}"; then
    echo "停止旧进程：${OLD_PID}"
    kill "${OLD_PID}" || die "无法停止旧进程 ${OLD_PID}；请检查进程权限"

    for _ in {1..20}; do
      [[ ! -e "/proc/${OLD_PID}" ]] && break
      sleep 0.5
    done
    [[ ! -e "/proc/${OLD_PID}" ]] || die "旧进程 ${OLD_PID} 在 10 秒内没有退出"
  elif [[ "${OLD_PID}" =~ ^[1-9][0-9]*$ && -e "/proc/${OLD_PID}" ]]; then
    die "PID 文件指向了其他进程（${OLD_PID}），为避免误杀已停止操作"
  fi
  rm -f -- "${PID_FILE}"
fi

export PYTHON_BIN LABEL_HOST LABEL_PORT
export LABEL_DATA_DIR="${DATA_DIR}"
export PYTHONUNBUFFERED=1

echo "使用 nohup 启动标注服务……"
{
  echo
  echo "[$(date '+%F %T')] restart.sh 启动服务"
} >> "${LOG_FILE}"

cd -- "${SCRIPT_DIR}"
nohup /usr/bin/env bash "${START_SCRIPT}" "${DATA_DIR}" \
  >> "${LOG_FILE}" 2>&1 < /dev/null &
NEW_PID=$!

PID_TMP="${PID_FILE}.tmp.$$"
printf '%s\n' "${NEW_PID}" > "${PID_TMP}"
mv -f -- "${PID_TMP}" "${PID_FILE}"

HEALTH_HOST="${LABEL_HOST}"
case "${HEALTH_HOST}" in
  0.0.0.0) HEALTH_HOST=127.0.0.1 ;;
  ::|\[::\]|::1|\[::1\]) HEALTH_HOST='[::1]' ;;
  *:*) HEALTH_HOST="[${HEALTH_HOST}]" ;;
esac
HEALTH_URL="http://${HEALTH_HOST}:${LABEL_PORT}/api/v1/health"

for ((second = 1; second <= WAIT_SECONDS; second++)); do
  if ! kill -0 "${NEW_PID}" 2>/dev/null; then
    rm -f -- "${PID_FILE}"
    echo "启动失败，最近日志如下：" >&2
    tail -n 40 "${LOG_FILE}" >&2 || true
    exit 1
  fi

  if command -v curl >/dev/null 2>&1; then
    if curl --noproxy '*' -fsS --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1; then
      break
    fi
  elif (( second >= 2 )); then
    break
  fi
  sleep 1
done

if command -v curl >/dev/null 2>&1 &&
   ! curl --noproxy '*' -fsS --max-time 2 "${HEALTH_URL}" >/dev/null 2>&1; then
  echo "进程 ${NEW_PID} 已在后台运行，但健康检查暂未通过：${HEALTH_URL}" >&2
  echo "请查看日志：tail -f '${LOG_FILE}'" >&2
  exit 1
fi

echo "启动成功，PID：${NEW_PID}"
echo "日志：${LOG_FILE}"
echo "健康检查：${HEALTH_URL}"
echo "SSH 断开后进程仍会继续运行。"
