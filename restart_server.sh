#!/usr/bin/env bash
# 在任意目录运行：bash /path/to/music_video/restart_server.sh
set -euo pipefail

PROJECT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
RUNTIME_DIR="$PROJECT_DIR/.runtime"
LOG_FILE="$RUNTIME_DIR/web-server.log"
PID_FILE="$RUNTIME_DIR/web-server.pid"
SERVER_URL="http://127.0.0.1:8765"
PYTHON_BIN="${PYTHON_BIN:-python3}"

for dependency in "$PYTHON_BIN" curl flock ss; do
  command -v "$dependency" >/dev/null || {
    echo "缺少命令：$dependency" >&2
    exit 1
  }
done
mkdir -p "$RUNTIME_DIR"
exec 9>"$RUNTIME_DIR/restart.lock"
flock -n 9 || { echo "另一个重启操作正在进行，请稍后再试。" >&2; exit 1; }
cd "$PROJECT_DIR"

# 只匹配本项目目录下直接运行 web_app.py 的 Python 进程，
# 也兼容之前手动执行 python3 web_app.py 启动的服务。
server_pids=()
for process_dir in /proc/[0-9]*; do
  [[ "$(readlink "$process_dir/cwd" 2>/dev/null || true)" == "$PROJECT_DIR" ]] || continue
  args=()
  mapfile -d '' -t args < "$process_dir/cmdline" 2>/dev/null || continue
  [[ ${#args[@]} -ge 2 ]] || continue
  [[ "${args[0]##*/}" =~ ^python([0-9]+(\.[0-9]+)*)?$ ]] || continue
  script_index=1
  if [[ "${args[1]}" == "-u" ]]; then
    script_index=2
  fi
  [[ ${#args[@]} -eq $((script_index + 1)) ]] || continue
  [[ "${args[$script_index]}" == web_app.py || "${args[$script_index]}" == "$PROJECT_DIR/web_app.py" ]] || continue
  server_pids+=("${process_dir##*/}")
done

for server_pid in "${server_pids[@]}"; do
  echo "正在停止旧服务（PID $server_pid）…"
  kill -TERM "$server_pid" 2>/dev/null || true
done
for server_pid in "${server_pids[@]}"; do
  for ((attempt=0; attempt<30; attempt++)); do
    kill -0 "$server_pid" 2>/dev/null || break
    # 僵尸进程已经退出，不必等待其父进程回收。
    state="$(awk '{print $3}' "/proc/$server_pid/stat" 2>/dev/null || true)"
    [[ "$state" == Z ]] && break
    sleep 1
  done
  if kill -0 "$server_pid" 2>/dev/null && [[ "$state" != Z ]]; then
    echo "旧服务尚未退出，可能仍有渲染任务。未强制终止，请查看日志后重试。" >&2
    exit 1
  fi
done

port_users="$(ss -H -ltnp 'sport = :8765')"
if [[ -n "$port_users" ]]; then
  echo "端口 8765 仍被占用，未启动新服务。请检查以下进程：" >&2
  echo "$port_users" >&2
  exit 1
fi

printf '\n--- Restart %s ---\n' "$(date -Is)" >> "$LOG_FILE"
# 关闭继承的锁描述符，避免后台服务一直持有重启锁。
nohup "$PYTHON_BIN" -u web_app.py >> "$LOG_FILE" 2>&1 < /dev/null 9>&- &
server_pid=$!

for ((attempt=0; attempt<30; attempt++)); do
  sleep 1
  if ! kill -0 "$server_pid" 2>/dev/null; then
    echo "启动失败，请检查端口 8765 是否被其他程序占用。最近日志：" >&2
    tail -n 30 "$LOG_FILE" >&2
    exit 1
  fi
  port_users="$(ss -H -ltnp 'sport = :8765')"
  if [[ "$port_users" == *"pid=$server_pid,"* ]] &&
     curl --noproxy '*' --fail --silent --max-time 2 "$SERVER_URL/api/config" >/dev/null; then
    printf '%s\n' "$server_pid" > "$PID_FILE"
    echo "服务已启动（PID $server_pid）"
    echo "打开：$SERVER_URL/"
    echo "日志：$LOG_FILE"
    exit 0
  fi
done
echo "服务启动检查超时。请查看：$LOG_FILE" >&2
tail -n 30 "$LOG_FILE" >&2
exit 1
