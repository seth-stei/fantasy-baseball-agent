#!/bin/bash
# Weekly Fantasy Baseball Agent Runner
# Called by launchd every Monday at 6 AM PT (9 AM ET — before first game).
# Logs to logs/weekly_YYYY-MM-DD.log

set -e
cd ~/fantasy-baseball-agent
mkdir -p logs

# Find Python — checks for a venv first, then falls back to system python3
if [ -f ".venv/bin/python3" ]; then
    PYTHON=".venv/bin/python3"
elif [ -f "venv/bin/python3" ]; then
    PYTHON="venv/bin/python3"
else
    PYTHON="$(command -v python3 || echo /usr/bin/python3)"
fi

LOG="logs/weekly_$(date +%Y-%m-%d).log"
echo "=== $(date) ===" >> "$LOG"
$PYTHON run_weekly_agent.py 2>&1 | tee -a "$LOG"
