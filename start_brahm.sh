#!/bin/bash
# ═══════════════════════════════════════════════════════════════════
# BRAHM System Startup Script
# Starts all required services in the correct order.
# Usage: bash start_brahm.sh [--no-shani] [--no-chitragupta]
# ═══════════════════════════════════════════════════════════════════

BRAHM_ROOT="/mnt/d/brahm"
BRAHM_VENV="$BRAHM_ROOT/.venv/bin/python"
SHANI_ROOT="$BRAHM_ROOT/agents/shani"
SHANI_VENV="$SHANI_ROOT/venv/bin/python"
CHIT_ROOT="$BRAHM_ROOT/agents/chitragupta"
LOG_DIR="$BRAHM_ROOT/logs"

mkdir -p "$LOG_DIR"

NO_SHANI=0
NO_CHIT=0
for arg in "$@"; do
    [[ "$arg" == "--no-shani" ]]        && NO_SHANI=1
    [[ "$arg" == "--no-chitragupta" ]]  && NO_CHIT=1
done

echo "════════════════════════════════════════"
echo "  BRAHM System Startup — $(date '+%Y-%m-%d %H:%M:%S')"
echo "════════════════════════════════════════"

# ── 1. CHITRAGUPTA data API (:8003) ──────────────────────────────
if [[ $NO_CHIT -eq 0 ]]; then
    echo "[1/3] Starting CHITRAGUPTA data API on :8003 ..."
    cd "$CHIT_ROOT"
    nohup "$BRAHM_VENV" -m brahm_db.api \
        > "$LOG_DIR/chitragupta_db.log" 2>&1 &
    CHIT_PID=$!
    echo "      PID=$CHIT_PID — log: $LOG_DIR/chitragupta_db.log"
    sleep 4
    # Health check
    if curl -s http://localhost:8003/health > /dev/null 2>&1; then
        echo "      ✓ CHITRAGUPTA API ready"
    else
        echo "      ✗ CHITRAGUPTA API failed to start — check log"
    fi
else
    echo "[1/3] CHITRAGUPTA skipped (--no-chitragupta)"
fi

# ── 2. SHANI pipeline API (:8000) ────────────────────────────────
if [[ $NO_SHANI -eq 0 ]]; then
    echo "[2/3] Starting SHANI API on :8000 ..."
    cd "$SHANI_ROOT"
    nohup "$SHANI_VENV" -m uvicorn api:app \
        --host 0.0.0.0 --port 8000 \
        > "$LOG_DIR/shani.log" 2>&1 &
    SHANI_PID=$!
    echo "      PID=$SHANI_PID — log: $LOG_DIR/shani.log"
    sleep 3
    if curl -s http://localhost:8000/docs > /dev/null 2>&1; then
        echo "      ✓ SHANI API ready"
    else
        echo "      ✗ SHANI API failed to start — check log"
    fi
else
    echo "[2/3] SHANI skipped (--no-shani)"
fi

# ── 3. BRAHM MCP Server ──────────────────────────────────────────
echo "[3/3] BRAHM MCP server runs via Claude Desktop — not started here."
echo "      Ensure claude_desktop_config.json points to:"
echo "      $BRAHM_VENV $BRAHM_ROOT/mcp_server.py"

echo ""
echo "════════════════════════════════════════"
echo "  Services running:"
[[ $NO_CHIT -eq 0 ]] && echo "  • CHITRAGUPTA  http://localhost:8003/docs"
[[ $NO_SHANI -eq 0 ]] && echo "  • SHANI        http://localhost:8000/docs"
echo ""
echo "  Stop all:  pkill -f 'brahm_db.api'; pkill -f 'uvicorn api:app'"
echo "  Logs:      $LOG_DIR/"
echo "════════════════════════════════════════"

# Save PIDs for stop script
echo "CHIT_PID=${CHIT_PID:-0}" > "$LOG_DIR/brahm.pids"
echo "SHANI_PID=${SHANI_PID:-0}" >> "$LOG_DIR/brahm.pids"
