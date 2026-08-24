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
    if [ -n "${PYTHON_BIN:-}" ]; then
        echo "${PYTHON_BIN}"
        return
    fi
    if command -v python3 >/dev/null 2>&1; then
        command -v python3
        return
    fi
    if command -v python >/dev/null 2>&1; then
        command -v python
        return
    fi
    local candidate
    for candidate in \
        "${SCRIPT_DIR}/../.venv/bin/python" \
        "${HOME}/miniconda3/envs/Resophy/bin/python" \
        "${HOME}/anaconda3/envs/Resophy/bin/python"; do
        if [ -x "${candidate}" ]; then
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
    local i
    for i in $(seq 1 20); do
        sleep 1
        if is_healthy; then
            echo "OCR service started: http://${HOST}:${PORT} (pid $(cat "${PID_FILE}"), log ${LOG})"
            exit 0
        fi
    done
    echo "ERROR: service did not become healthy within 20s, check ${LOG}" >&2
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
            exit 0
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
