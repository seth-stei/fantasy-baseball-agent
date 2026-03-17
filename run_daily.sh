#!/bin/bash
# Daily Fantasy Baseball Agent Runner
# Called by launchd every morning at 7 AM PT (10 AM ET).
# Logs to logs/daily_YYYY-MM-DD.log

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

LOG="logs/daily_$(date +%Y-%m-%d).log"
echo "=== $(date) ===" >> "$LOG"
$PYTHON run_fantasy_agent.py 2>&1 | tee -a "$LOG"
