#!/usr/bin/env bash
# ============================================================
# agent.sh  —  Start / stop / restart the AI news agent
#
# Usage:
#   ./agent.sh start      Start agent + dashboard in background
#   ./agent.sh stop       Stop agent + dashboard
#   ./agent.sh restart    Stop then start
#   ./agent.sh status     Show running state
#   ./agent.sh logs       Tail live logs (Ctrl-C to exit)
# ============================================================

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
PID_FILE="$SCRIPT_DIR/logs/agent.pid"
DASH_PID_FILE="$SCRIPT_DIR/logs/dashboard.pid"
CAFF_PID_FILE="$SCRIPT_DIR/logs/caffeinate.pid"
LOG_FILE="$SCRIPT_DIR/logs/agent.log"
DASH_LOG_FILE="$SCRIPT_DIR/logs/dashboard.log"
MAIN="$SCRIPT_DIR/main.py"
DASHBOARD="$SCRIPT_DIR/dashboard.py"
PYTHON="${PYTHON:-python3}"

mkdir -p "$SCRIPT_DIR/logs"

# ── helpers ──────────────────────────────────────────────────

is_running() {
    if [[ -f "$PID_FILE" ]]; then
        local pid
        pid=$(cat "$PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0   # running
        fi
        rm -f "$PID_FILE"   # stale PID file
    fi
    return 1   # not running
}

is_dashboard_running() {
    if [[ -f "$DASH_PID_FILE" ]]; then
        local pid
        pid=$(cat "$DASH_PID_FILE")
        if kill -0 "$pid" 2>/dev/null; then
            return 0
        fi
        rm -f "$DASH_PID_FILE"
    fi
    return 1
}

do_start() {
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "Agent is already running (PID $pid)."
    else
        echo "Starting AI news agent..."
        # Redirect to /dev/null — Python's FileHandler already writes to $LOG_FILE,
        # so redirecting nohup output to the same file would double every log line.
        nohup "$PYTHON" "$MAIN" > /dev/null 2>&1 &
        local pid=$!
        echo "$pid" > "$PID_FILE"
        sleep 1

        if kill -0 "$pid" 2>/dev/null; then
            echo "Agent started (PID $pid). Logs: $LOG_FILE"
        else
            echo "Agent failed to start — check $LOG_FILE"
            rm -f "$PID_FILE"
            exit 1
        fi
    fi

    # Start dashboard
    if is_dashboard_running; then
        local dpid
        dpid=$(cat "$DASH_PID_FILE")
        echo "Dashboard is already running (PID $dpid) — http://localhost:3010"
    else
        nohup "$PYTHON" "$DASHBOARD" >> "$DASH_LOG_FILE" 2>&1 &
        local dpid=$!
        echo "$dpid" > "$DASH_PID_FILE"
        sleep 1
        if kill -0 "$dpid" 2>/dev/null; then
            echo "Dashboard started (PID $dpid) — http://localhost:3010"
        else
            echo "Dashboard failed to start — check $DASH_LOG_FILE"
            rm -f "$DASH_PID_FILE"
        fi
    fi

    # Start caffeinate to prevent the Mac from sleeping during scheduled runs
    if command -v caffeinate &>/dev/null; then
        if ! [[ -f "$CAFF_PID_FILE" ]] || ! kill -0 "$(cat "$CAFF_PID_FILE")" 2>/dev/null; then
            caffeinate -i &
            echo "$!" > "$CAFF_PID_FILE"
            echo "Caffeinate started — Mac will not sleep while agent is running (display can still sleep/screensaver works)."
        fi
    fi
}

do_stop() {
    # Stop agent
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "Stopping agent (PID $pid)..."
        kill "$pid" 2>/dev/null || true
        sleep 2
        if kill -0 "$pid" 2>/dev/null; then
            kill -9 "$pid" 2>/dev/null || true
            sleep 1
        fi
        rm -f "$PID_FILE"
        echo "Agent stopped."
    else
        echo "Agent is not running."
        pkill -f "python.*main\.py" 2>/dev/null || true
    fi

    # Stop dashboard
    if is_dashboard_running; then
        local dpid
        dpid=$(cat "$DASH_PID_FILE")
        echo "Stopping dashboard (PID $dpid)..."
        kill "$dpid" 2>/dev/null || true
        sleep 1
        rm -f "$DASH_PID_FILE"
        echo "Dashboard stopped."
    else
        pkill -f "python.*dashboard\.py" 2>/dev/null || true
    fi

    # Stop caffeinate
    if [[ -f "$CAFF_PID_FILE" ]]; then
        local cpid
        cpid=$(cat "$CAFF_PID_FILE")
        kill "$cpid" 2>/dev/null || true
        rm -f "$CAFF_PID_FILE"
        echo "Caffeinate stopped."
    else
        pkill -x caffeinate 2>/dev/null || true
    fi
}

do_status() {
    if is_running; then
        local pid
        pid=$(cat "$PID_FILE")
        echo "Agent:     RUNNING (PID $pid)"
    else
        echo "Agent:     STOPPED"
    fi

    if is_dashboard_running; then
        local dpid
        dpid=$(cat "$DASH_PID_FILE")
        echo "Dashboard: RUNNING (PID $dpid) — http://localhost:3010"
    else
        echo "Dashboard: STOPPED"
    fi

    if pgrep -x caffeinate &>/dev/null; then
        echo "Caffeinate: ACTIVE (Mac will not sleep)"
    else
        echo "Caffeinate: INACTIVE"
    fi

    echo ""
    echo "-- Last 5 log lines --"
    tail -5 "$LOG_FILE" 2>/dev/null || echo "(no log yet)"
}

# ── main ─────────────────────────────────────────────────────

CMD="${1:-help}"

case "$CMD" in
    start)
        do_start
        ;;
    stop)
        do_stop
        ;;
    restart)
        do_stop
        sleep 1
        do_start
        ;;
    status)
        do_status
        ;;
    logs)
        echo "Tailing $LOG_FILE (Ctrl-C to exit)..."
        tail -f "$LOG_FILE"
        ;;
    *)
        echo "Usage: $0 {start|stop|restart|status|logs}"
        exit 1
        ;;
esac
