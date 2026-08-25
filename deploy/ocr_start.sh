#!/usr/bin/env bash
# Resophy OCR microservice launcher (deploy next to ocr_server.py).
# Usage: ./ocr_start.sh {start|stop|status|restart}
# Overrides: HOST PORT PYTHON_BIN LOG PID_FILE
#
# uvicorn is invoked as "python -m uvicorn" to stay immune to broken
# console-script shebangs (e.g. conda envs cloned/moved to another prefix).
set -euo pipefail

HOST="${HOST:-0.0.0.0}"
PORT="${PORT:-6003}"
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LOG="${LOG:-${SCRIPT_DIR}/ocr_server.log}"
PID_FILE="${PID_FILE:-${SCRIPT_DIR}/ocr_server.pid}"
SELF="${BASH_SOURCE[0]}"

is_healthy() {
    curl -sf --connect-timeout 2 "http://127.0.0.1:${PORT}/health" >/dev/null 2>&1
}

find_python_bin() {
    # Pick the first python that can actually run uvicorn (the service needs
    # fastapi+uvicorn; a bare interpreter without them is useless here).
    py_has_uvicorn() {
        "$1" -c "import uvicorn" >/dev/null 2>&1
    }
    local candidate
    for candidate in \
        "${PYTHON_BIN:-}" \
        "${SCRIPT_DIR}/../.venv/bin/python" \
        "${HOME}/miniconda3/envs/Resophy/bin/python" \
        "${HOME}/anaconda3/envs/Resophy/bin/python" \
        "$(command -v python3 2>/dev/null || true)" \
        "$(command -v python 2>/dev/null || true)"; do
        [ -n "${candidate}" ] || continue
        [ -x "${candidate}" ] || continue
        if py_has_uvicorn "${candidate}"; then
            echo "${candidate}"
            return
        fi
    done
    echo ""
}

do_start() {
    if is_healthy; then
        echo "OCR service already running on port ${PORT}"
        exit 0
    fi
    local py
    py="$(find_python_bin)"
    if [ -z "${py}" ]; then
        echo "ERROR: python not found. Activate the target env first, or: export PYTHON_BIN=/path/to/bin/python" >&2
        exit 1
    fi
    nohup "${py}" -m uvicorn ocr_server:app --host "${HOST}" --port "${PORT}" --app-dir "${SCRIPT_DIR}" >>"${LOG}" 2>&1 &
    echo $! >"${PID_FILE}"
    # Cold start loads RapidOCR plus a 72MB layout ONNX model; allow up to 60s
    local i
    for i in $(seq 1 60); do
        sleep 1
        if is_healthy; then
            echo "OCR service started: http://${HOST}:${PORT} (pid $(cat "${PID_FILE}"), log ${LOG})"
            exit 0
        fi
        if [ $((i % 10)) -eq 0 ]; then
            echo "waiting for startup... (${i}s)"
        fi
    done
    echo "ERROR: service did not become healthy within 60s, last log lines:" >&2
    tail -n 15 "${LOG}" >&2 || true
    exit 1
}

do_stop() {
    if [ -f "${PID_FILE}" ]; then
        local pid
        pid="$(cat "${PID_FILE}")"
        if kill -0 "${pid}" 2>/dev/null; then
            kill "${pid}"
            local i
            for i in $(seq 1 10); do
                if ! is_healthy; then
                    break
                fi
                sleep 1
            done
            if kill -0 "${pid}" 2>/dev/null; then
                kill -9 "${pid}" 2>/dev/null || true
            fi
            rm -f "${PID_FILE}"
            echo "OCR service stopped (pid ${pid})"
            return 0
        fi
        rm -f "${PID_FILE}"
    fi
    echo "OCR service not running (no valid pid file)"
}

case "${1:-start}" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    status)
        if is_healthy; then
            echo "OCR service running on port ${PORT}"
        else
            echo "OCR service not responding on port ${PORT}"
            exit 1
        fi
        ;;
    restart)
        do_stop || true
        sleep 1
        do_start
        ;;
    *)
        echo "Usage: ${SELF} {start|stop|status|restart}" >&2
        exit 1
        ;;
esac
