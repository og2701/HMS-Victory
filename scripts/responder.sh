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

    help|--help|-h)
        echo "=============================================================================="
        echo " HMS Victory Live Chat Responder Management"
        echo "=============================================================================="
        echo "Usage: $0 {run|start|stop|status|logs|help} [options]"
        echo ""
        echo "Commands:"
        echo "  run      Run in foreground (interactive menu for channel, duration & topic)"
        echo "  start    Launch in background as a daemon"
        echo "  stop     Stop the background daemon immediately"
        echo "  status   Check if the responder is currently running"
        echo "  logs     Tail the live logs in real time (Ctrl+C to exit logs)"
        echo "  help     Display this guide"
        echo ""
        echo "Options (pass to 'run' or 'start'):"
        echo "  --channel <name|id>   Target: general, vip, commons, politics, or channel ID"
        echo "  --duration <time>     Auto-stop timer: 15m, 30m, 1h, 2h (default: unlimited)"
        echo "  --topic <text>        Optional starting premise/grievance to kick off chat"
        echo "  --model <model>       OpenAI model: gpt-4o (default), gpt-4o-mini"
        echo "  --cooldown <sec>      Min seconds between replies (default: 3.0)"
        echo ""
        echo "Examples:"
        echo "  $0 run                                            # Interactive wizard"
        echo "  $0 run --channel vip --duration 30m               # Foreground in VIP for 30m"
        echo "  $0 start --duration 45m                           # Background in General for 45m"
        echo "  $0 start --topic \"pub quiz promo\" --duration 1h   # Background with starting topic"
        echo "  $0 stop                                           # Manually kill background process"
        echo "  $0 logs                                           # Follow live chat responses"
        echo "=============================================================================="
        exit 0
        ;;

    *)
        echo "Unknown command: '$1'"
        echo "Run '$0 help' to see usage instructions and options."
        exit 1
        ;;
esac
