#!/usr/bin/env bash
# ==============================================================================
# HMS Victory Live Chat Responder Management
# ==============================================================================

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PID_FILE="/tmp/hms_chat_responder.pid"
LOG_DIR="${PROJECT_DIR}/logs"
LOG_FILE="${LOG_DIR}/responder.log"

mkdir -p "${LOG_DIR}"

PYTHON_BIN="python3"
if [ -f "${PROJECT_DIR}/venv/bin/python" ]; then
    PYTHON_BIN="${PROJECT_DIR}/venv/bin/python"
fi

case "$1" in
    start)
        if [ -f "${PID_FILE}" ] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
            echo "[WARN] Chat responder is already running (PID: $(cat "${PID_FILE}"))."
            exit 0
        fi
        shift
        echo "Starting HMS Victory Chat Responder in background..."
        nohup "${PYTHON_BIN}" "${PROJECT_DIR}/scripts/chat_responder.py" "$@" > "${LOG_FILE}" 2>&1 &
        echo $! > "${PID_FILE}"
        echo "[OK] Started with PID: $(cat "${PID_FILE}")"
        echo "Logs available at: ${LOG_FILE}"
        ;;

    stop)
        if [ -f "${PID_FILE}" ]; then
            PID="$(cat "${PID_FILE}")"
            if kill -0 "${PID}" 2>/dev/null; then
                echo "Stopping Chat Responder (PID: ${PID})..."
                kill -TERM "${PID}" 2>/dev/null
                sleep 1
                if kill -0 "${PID}" 2>/dev/null; then
                    kill -9 "${PID}" 2>/dev/null
                fi
                echo "[OK] Stopped."
            else
                echo "[INFO] Process ${PID} was not running."
            fi
            rm -f "${PID_FILE}"
        else
            echo "[INFO] No PID file found. Checking running processes..."
            pkill -f "scripts/chat_responder.py" && echo "[OK] Stopped lingering processes." || echo "No responder process running."
        fi
        ;;

    status)
        if [ -f "${PID_FILE}" ] && kill -0 "$(cat "${PID_FILE}")" 2>/dev/null; then
            echo "[RUNNING] Chat responder is running (PID: $(cat "${PID_FILE}"))."
        else
            echo "[STOPPED] Chat responder is not running."
        fi
        ;;

    logs)
        if [ -f "${LOG_FILE}" ]; then
            tail -n 30 -f "${LOG_FILE}"
        else
            echo "Log file ${LOG_FILE} does not exist yet."
        fi
        ;;

    run)
        shift
        exec "${PYTHON_BIN}" "${PROJECT_DIR}/scripts/chat_responder.py" "$@"
        ;;

    *)
        echo "Usage: $0 {start|stop|status|logs|run} [optional args for script]"
        echo ""
        echo "Examples:"
        echo "  $0 start                 # Start background daemon in #general"
        echo "  $0 start --channel vip   # Start background daemon in #vip-lounge"
        echo "  $0 stop                  # Stop background daemon"
        echo "  $0 status                # Check status"
        echo "  $0 logs                  # Follow live logs"
        echo "  $0 run                   # Run interactively in foreground (Ctrl+C to stop)"
        exit 1
        ;;
esac
