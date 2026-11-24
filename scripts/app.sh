#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

PORT="${PARTER_PORT:-8522}"
URL="http://127.0.0.1:$PORT"
PATTERN="uvicorn parter.main:app --host 0.0.0.0 --port $PORT"
LOG=".run/app.log"

get_lan_ip() {
  ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1"
}

pids() { pgrep -f "$PATTERN" || true; }

healthy() { curl -fsS "$URL/api/state" >/dev/null 2>&1; }

status() {
  local p
  p="$(pids | tr '\n' ' ')"
  if [[ -z "$p" ]]; then
    echo "stopped"
  elif healthy; then
    local lan
    lan="$(get_lan_ip)"
    echo "running http://$lan:$PORT (local: http://localhost:$PORT, pid $p)"
  else
    echo "starting or broken (pid $p), see $LOG"
  fi
}

start() {
  if [[ -n "$(pids)" ]]; then status; return; fi
  mkdir -p .run
  uv sync --quiet
  nohup perl -MPOSIX=setsid -e 'setsid(); exec @ARGV' uv run $PATTERN >"$LOG" 2>&1 </dev/null &
  for _ in $(seq 1 40); do
    if healthy; then status; return; fi
    sleep 0.3
  done
  echo "did not start, see $LOG" >&2
  exit 1
}

stop() {
  local p
  p="$(pids)"
  if [[ -z "$p" ]]; then echo "stopped"; return; fi
  kill $p 2>/dev/null || true
  for _ in $(seq 1 20); do
    [[ -z "$(pids)" ]] && { echo "stopped"; return; }
    sleep 0.2
  done
  kill -9 $(pids) 2>/dev/null || true
  echo "stopped"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop >/dev/null; start ;;
  status) status ;;
  *) echo "usage: scripts/app.sh start|stop|restart|status" >&2; exit 2 ;;
esac
