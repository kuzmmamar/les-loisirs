#!/usr/bin/env bash
set -euo pipefail

cd "$(dirname "$0")/.."
export PATH="$HOME/.local/bin:$PATH"

PORT="${PARTER_PORT:-8522}"
URL="http://127.0.0.1:$PORT"
PATTERN="uvicorn parter.main:app --host 0.0.0.0 --port $PORT"
TUNNEL_PATTERN="cloudflared tunnel --url http://127.0.0.1:$PORT"
CLOUDFLARED="${CLOUDFLARED:-$HOME/.local/bin/cloudflared}"
LOG=".run/app.log"
TUNNEL_LOG=".run/tunnel.log"
TUNNEL_URL=".run/public.url"

get_lan_ip() {
  ipconfig getifaddr en0 2>/dev/null || ipconfig getifaddr en1 2>/dev/null || echo "127.0.0.1"
}

pids() { pgrep -f "$PATTERN" || true; }
tunnel_pids() { pgrep -f "$TUNNEL_PATTERN" || true; }

healthy() {
  local key=""
  if [[ -s data/access.key ]]; then
    key="$(tr -d '[:space:]' < data/access.key)"
  fi
  curl -fsS -H "X-Parter-Key: $key" "$URL/api/state" >/dev/null 2>&1
}

public_url() {
  [[ -s "$TUNNEL_URL" ]] && tr -d '[:space:]' <"$TUNNEL_URL" || true
}

status() {
  local p lan pub
  p="$(pids | tr '\n' ' ')"
  if [[ -z "$p" ]]; then
    echo "stopped"
    return
  fi
  if ! healthy; then
    echo "starting or broken (pid $p), see $LOG"
    return
  fi
  lan="$(get_lan_ip)"
  pub="$(public_url)"
  if [[ -n "$pub" && -n "$(tunnel_pids)" ]]; then
    echo "running http://$lan:$PORT (local: http://localhost:$PORT, phone: $pub, pid $p)"
  else
    echo "running http://$lan:$PORT (local: http://localhost:$PORT, phone link down, pid $p)"
  fi
}

start_tunnel() {
  [[ -x "$CLOUDFLARED" ]] || return 0
  if [[ -n "$(tunnel_pids)" ]]; then return 0; fi
  mkdir -p .run
  rm -f "$TUNNEL_URL"
  nohup perl -MPOSIX=setsid -e 'setsid(); exec @ARGV' \
    "$CLOUDFLARED" tunnel --url "$URL" --no-autoupdate \
    >"$TUNNEL_LOG" 2>&1 </dev/null &
  local found
  for _ in $(seq 1 50); do
    found="$(grep -oE 'https://[-a-zA-Z0-9]+\.trycloudflare\.com' "$TUNNEL_LOG" 2>/dev/null | tail -1 || true)"
    if [[ -n "$found" ]]; then
      printf '%s\n' "$found" >"$TUNNEL_URL"
      return 0
    fi
    sleep 0.4
  done
}

start() {
  if [[ -n "$(pids)" ]]; then
    if healthy; then start_tunnel; status; return; fi
  fi
  mkdir -p .run
  uv sync --quiet
  nohup perl -MPOSIX=setsid -e 'setsid(); exec @ARGV' uv run $PATTERN >"$LOG" 2>&1 </dev/null &
  for _ in $(seq 1 40); do
    if healthy; then
      start_tunnel
      status
      return
    fi
    sleep 0.3
  done
  echo "did not start, see $LOG" >&2
  exit 1
}

stop() {
  local p t
  t="$(tunnel_pids)"
  if [[ -n "$t" ]]; then kill $t 2>/dev/null || true; fi
  p="$(pids)"
  if [[ -z "$p" && -z "$t" ]]; then echo "stopped"; return; fi
  if [[ -n "$p" ]]; then kill $p 2>/dev/null || true; fi
  for _ in $(seq 1 20); do
    [[ -z "$(pids)" && -z "$(tunnel_pids)" ]] && { echo "stopped"; return; }
    sleep 0.2
  done
  kill -9 $(pids) $(tunnel_pids) 2>/dev/null || true
  echo "stopped"
}

case "${1:-status}" in
  start) start ;;
  stop) stop ;;
  restart) stop >/dev/null; start ;;
  status) status ;;
  *) echo "usage: scripts/app.sh start|stop|restart|status" >&2; exit 2 ;;
esac
