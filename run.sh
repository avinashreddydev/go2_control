#!/usr/bin/env bash
#
# Start the Go2 control stack.
#
#   ./run.sh             backend + frontend
#   ./run.sh backend     FastAPI only (connects to the robot over WebRTC)
#   ./run.sh frontend    Next.js only
#
# Creates .venv and installs the backend deps on first run. YOLOE detection
# (torch + ultralytics + weights/) is optional; the backend runs without it.
set -euo pipefail
cd "$(dirname "$0")"

HOST=${HOST:-0.0.0.0}
PORT=${PORT:-8000}
VENV=${VENV:-.venv}
PY="$VENV/bin/python"

# Off by default: the Go2 accepts a single peer connection, so every reload
# drops the WebRTC link. RELOAD=1 ./run.sh to opt in.
RELOAD_FLAG=""
if [ "${RELOAD:-0}" = "1" ]; then
    RELOAD_FLAG="--reload"
fi

log() { printf '\033[36m[run]\033[0m %s\n' "$*"; }
die() { printf '\033[31m[run]\033[0m %s\n' "$*" >&2; exit 1; }

ensure_python() {
    if [ ! -x "$PY" ]; then
        log "creating $VENV"
        if command -v uv >/dev/null 2>&1; then
            uv venv "$VENV"
        else
            python3 -m venv "$VENV"
        fi
    fi
    if ! "$PY" -c "import fastapi, uvicorn, aiortc, cv2, openai, dotenv, webrtcvad" >/dev/null 2>&1; then
        log "installing backend dependencies"
        if command -v uv >/dev/null 2>&1; then
            uv pip install --python "$PY" -r requirements.txt
        else
            "$PY" -m pip install -r requirements.txt
        fi
    fi
    [ -f .env ] || log "no .env - chat/VLA need OPENAI_API_KEY"
}

ensure_port_free() {
    command -v lsof >/dev/null 2>&1 || return 0
    local pids
    pids=$(lsof -nP -tiTCP:"$PORT" -sTCP:LISTEN 2>/dev/null || true)
    [ -z "$pids" ] || die "port $PORT already in use by PID(s) $(echo "$pids" | tr '\n' ' ')"
}

ensure_node() {
    command -v npm >/dev/null 2>&1 || die "npm not found"
    [ -d frontend/node_modules ] || { log "npm install"; npm --prefix frontend install; }
}

backend_cmd() { "$PY" -m uvicorn backend.main:app $RELOAD_FLAG --host "$HOST" --port "$PORT"; }
frontend_cmd() { npm --prefix frontend run dev; }

case "${1:-all}" in
    backend)  ensure_port_free; ensure_python; log "backend on http://$HOST:$PORT"; backend_cmd ;;
    frontend) ensure_node; log "frontend on http://localhost:3000"; frontend_cmd ;;
    all)
        ensure_port_free
        ensure_python
        ensure_node
        trap 'trap - EXIT INT TERM; kill 0' EXIT INT TERM
        log "backend on http://$HOST:$PORT"
        backend_cmd &
        log "frontend on http://localhost:3000"
        frontend_cmd &
        wait
        ;;
    *) die "usage: ./run.sh [backend|frontend|all]" ;;
esac
