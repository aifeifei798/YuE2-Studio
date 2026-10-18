#!/usr/bin/env bash
# server.sh — YuE2-Studio 一体化脚本（安装 + 服务管理，使用 uv）
# 用法：
#   ./server.sh                      # 启动服务（默认）
#   ./server.sh install [选项]       # 安装环境
#   ./server.sh start [port] [选项]   # 启动服务（后台 daemon）
#   ./server.sh run [port] [选项]     # 前台运行
#   ./server.sh stop                 # 停止服务
#   ./server.sh restart [port] [选项] # 重启服务
#   ./server.sh status               # 查看状态
#   ./server.sh -h|--help            # 帮助
#
# 安装选项：--dev --build --no-web --no-yue2 --all
# 服务选项：--build-web --dev-web --no-build
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT_DIR"

PID_FILE="$ROOT_DIR/.server.pid"
PORT_FILE="$ROOT_DIR/.server.port"
LOG_FILE="$ROOT_DIR/.server.log"
WEB_PID_FILE="$ROOT_DIR/.server.web.pid"

YUE2_WHL="${YUE2_WHL:-https://huggingface.co/m-a-p/YuE2-3B/resolve/main/yue2_infer-0.1.5-py3-none-any.whl}"

usage() {
  sed -n '2,18p' "$0" | sed 's/^# \?//'
}

# ---------------- 安装 ----------------
do_install() {
  local DEV=0 BUILD=0 WITH_WEB=1 WITH_YUE2=1
  for arg in "$@"; do
    case "$arg" in
      --dev) DEV=1 ;;
      --build) BUILD=1 ;;
      --no-web) WITH_WEB=0 ;;
      --no-yue2) WITH_YUE2=0 ;;
      --yue2) WITH_YUE2=1 ;;
      --all) WITH_YUE2=1; BUILD=1; DEV=1 ;;
      -h|--help) usage; exit 0 ;;
      *) echo "未知参数: $arg" >&2; exit 2 ;;
    esac
  done

  if ! command -v uv >/dev/null 2>&1; then
    echo "[install] 未检测到 uv，开始安装..."
    curl -LsSf https://astral.sh/uv/install.sh | sh
    export PATH="$HOME/.local/bin:$PATH"
    if ! command -v uv >/dev/null 2>&1; then
      echo "[install] uv 安装失败，请手动安装: https://docs.astral.sh/uv/" >&2
      exit 1
    fi
  fi
  echo "[install] uv: $(uv --version)"

  local PYTHON="${PYTHON:-python3.12}"
  if [ ! -d "$ROOT_DIR/.venv" ]; then
    echo "[install] 创建虚拟环境 .venv ..."
    uv venv --python "$PYTHON" "$ROOT_DIR/.venv"
  fi

  echo "[install] 安装后端依赖 ..."
  uv pip install --python "$ROOT_DIR/.venv/bin/python" -r "$ROOT_DIR/backend/requirements.txt"

  if [ "$DEV" = "1" ]; then
    echo "[install] 安装开发/测试依赖 ..."
    uv pip install --python "$ROOT_DIR/.venv/bin/python" -r "$ROOT_DIR/requirements-dev.txt"
  fi

  if [ "$WITH_YUE2" = "1" ]; then
    echo "[install] 安装 YuE2 推理包: $YUE2_WHL"
    uv pip install --python "$ROOT_DIR/.venv/bin/python" "$YUE2_WHL"
  fi

  if [ ! -f "$ROOT_DIR/.env" ]; then
    cp "$ROOT_DIR/.env.example" "$ROOT_DIR/.env"
    echo "[install] 已生成 .env（请按需编辑 ADMIN_TOKEN 等）"
  fi

  mkdir -p "$ROOT_DIR/outputs/audio" "$ROOT_DIR/outputs/artifacts"

  if [ "$WITH_WEB" = "1" ]; then
    if ! command -v npm >/dev/null 2>&1; then
      echo "[install] 未检测到 npm，跳过前端安装（后端仍可运行，但 / 会 404）" >&2
    else
      echo "[install] 安装前端依赖 ..."
      (cd "$ROOT_DIR/web" && npm install)
      if [ "$BUILD" = "1" ]; then
        echo "[install] 构建前端 ..."
        (cd "$ROOT_DIR/web" && npm run build)
      fi
    fi
  fi

  echo
  echo "[install] 完成。"
  echo "  启动服务: ./server.sh start"
  echo "  前端 dev: cd web && npm run dev  (http://127.0.0.1:5173)"
}

# ---------------- 服务管理 ----------------
is_running() {
  if [ -f "$PID_FILE" ]; then
    local pid
    pid="$(cat "$PID_FILE" 2>/dev/null || true)"
    if [ -n "$pid" ] && kill -0 "$pid" 2>/dev/null; then
      return 0
    fi
  fi
  return 1
}

read_port() {
  if [ -f "$PORT_FILE" ]; then
    cat "$PORT_FILE" 2>/dev/null || true
  else
    echo "${YUE2_PORT:-8000}"
  fi
}

healthz() {
  local port="${1:-$(read_port)}"
  local resp
  resp="$(curl -s "http://127.0.0.1:${port}/healthz" 2>/dev/null || true)"
  if echo "$resp" | grep -q '"status"'; then
    echo "$resp"
  else
    echo "unreachable (port in use or service down)"
  fi
}

do_build_web() {
  if ! command -v npm >/dev/null 2>&1; then
    echo "[server] 未检测到 npm，跳过前端构建" >&2
    return 0
  fi
  echo "[server] 构建前端 ..."
  (cd "$ROOT_DIR/web" && npm run build)
}

start_service() {
  local build_web=0 dev_web=0 port=""
  for arg in "$@"; do
    case "$arg" in
      --build-web) build_web=1 ;;
      --dev-web) dev_web=1 ;;
      --no-build) build_web=0 ;;
      -h|--help) usage; exit 0 ;;
      *)
        if [[ "$arg" =~ ^[0-9]+$ ]]; then
          port="$arg"
        else
          echo "未知参数: $arg" >&2; exit 2
        fi
        ;;
    esac
  done
  [ -z "$port" ] && port="${YUE2_PORT:-8000}"

  if [ ! -d "$ROOT_DIR/.venv" ]; then
    echo "[server] 未找到 .venv，请先运行 ./server.sh install" >&2
    exit 1
  fi

  if [ "$build_web" = "1" ]; then
    do_build_web
  fi

  if [ ! -d "$ROOT_DIR/web/dist" ]; then
    echo "[server] 警告: web/dist 不存在，后端 / 将返回 404。运行 cd web && npm run build 或加 --build-web" >&2
  fi

  mkdir -p "$ROOT_DIR/outputs/audio" "$ROOT_DIR/outputs/artifacts"

  if is_running; then
    echo "[server] 已在运行 (pid $(cat "$PID_FILE"))，端口 $(read_port)"
    exit 0
  fi

  echo "$port" > "$PORT_FILE"

  if [ "$dev_web" = "1" ]; then
    if command -v npm >/dev/null 2>&1; then
      echo "[server] 启动前端 dev server (5173) ..."
      (cd "$ROOT_DIR/web" && nohup npm run dev >"$ROOT_DIR/.server.web.log" 2>&1 & echo $! > "$WEB_PID_FILE")
    else
      echo "[server] 未检测到 npm，跳过前端 dev server" >&2
    fi
  fi

  echo "[server] 启动后端: http://0.0.0.0:$port  (workers=1)"
  nohup "$ROOT_DIR/.venv/bin/python" -m uvicorn backend.app.main:app \
    --host 0.0.0.0 --port "$port" --workers 1 >"$LOG_FILE" 2>&1 &
  local pid=$!
  echo "$pid" > "$PID_FILE"

  for i in $(seq 1 20); do
    sleep 1
    if curl -s "http://127.0.0.1:${port}/healthz" 2>/dev/null | grep -q '"status"'; then
      echo "[server] 已启动 (pid $pid)，healthz: $(healthz "$port")"
      echo "[server] 日志: $LOG_FILE"
      return 0
    fi
    if ! kill -0 "$pid" 2>/dev/null; then
      echo "[server] 启动失败，查看日志: $LOG_FILE" >&2
      tail -20 "$LOG_FILE" >&2 || true
      rm -f "$PID_FILE"
      exit 1
    fi
  done
  echo "[server] 启动超时，查看日志: $LOG_FILE" >&2
}

stop_service() {
  if is_running; then
    local pid
    pid="$(cat "$PID_FILE")"
    echo "[server] 停止后端 (pid $pid) ..."
    kill "$pid" 2>/dev/null || true
    for i in $(seq 1 10); do
      kill -0 "$pid" 2>/dev/null || break
      sleep 1
    done
    if kill -0 "$pid" 2>/dev/null; then
      echo "[server] 强制 kill" >&2
      kill -9 "$pid" 2>/dev/null || true
    fi
    rm -f "$PID_FILE"
  else
    echo "[server] 后端未运行"
    rm -f "$PID_FILE"
  fi

  pkill -f "backend.app.main:app" 2>/dev/null || true

  if [ -f "$WEB_PID_FILE" ]; then
    local wpid
    wpid="$(cat "$WEB_PID_FILE" 2>/dev/null || true)"
    if [ -n "$wpid" ] && kill -0 "$wpid" 2>/dev/null; then
      echo "[server] 停止前端 dev server (pid $wpid) ..."
      kill "$wpid" 2>/dev/null || true
    fi
    rm -f "$WEB_PID_FILE"
  fi
  echo "[server] 已停止"
}

status_service() {
  if is_running; then
    local port
    port="$(read_port)"
    echo "[server] 状态: 运行中 (pid $(cat "$PID_FILE"))"
    echo "[server] 端口: $port"
    echo "[server] healthz: $(healthz "$port")"
    echo "[server] 日志: $LOG_FILE"
  else
    echo "[server] 状态: 未运行"
  fi
}

run_foreground() {
  local port="" build_web=0
  for arg in "$@"; do
    case "$arg" in
      --build-web) build_web=1 ;;
      --dev-web) echo "[server] --dev-web 在前台模式请另开终端运行 npm run dev" >&2 ;;
      --no-build) build_web=0 ;;
      *)
        if [[ "$arg" =~ ^[0-9]+$ ]]; then port="$arg"; else echo "未知参数: $arg" >&2; exit 2; fi
        ;;
    esac
  done
  [ -z "$port" ] && port="${YUE2_PORT:-8000}"
  [ ! -d "$ROOT_DIR/.venv" ] && { echo "[server] 未找到 .venv，请先运行 ./server.sh install" >&2; exit 1; }
  [ "$build_web" = "1" ] && do_build_web
  [ ! -d "$ROOT_DIR/web/dist" ] && echo "[server] 警告: web/dist 不存在" >&2
  mkdir -p "$ROOT_DIR/outputs/audio" "$ROOT_DIR/outputs/artifacts"
  echo "[server] 前台运行: http://0.0.0.0:$port"
  exec "$ROOT_DIR/.venv/bin/python" -m uvicorn backend.app.main:app --host 0.0.0.0 --port "$port" --workers 1
}

# ---------------- 入口 ----------------
cmd="${1:-start}"
case "$cmd" in
  install)
    shift || true
    do_install "$@"
    ;;
  start)
    shift || true
    start_service "$@"
    ;;
  run|foreground)
    shift || true
    run_foreground "$@"
    ;;
  stop)
    stop_service
    ;;
  restart)
    shift || true
    stop_service
    sleep 1
    start_service "$@"
    ;;
  status)
    status_service
    ;;
  -h|--help|help)
    usage
    ;;
  *)
    # 兼容：直接传端口/选项时视为 start
    start_service "$@"
    ;;
esac