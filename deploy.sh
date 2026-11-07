#!/bin/bash
# Rebuild /Applications/bathero.app and relaunch it if it was running, so the
# rcmd shortcut always opens the latest code. Run after every change.
set -e
cd "$(dirname "$0")"
was_running=0
if pgrep -f "bathero.app/Contents/MacOS/bathero" >/dev/null; then
    was_running=1
    osascript -e 'tell application "bathero" to quit' >/dev/null 2>&1 || true
    for _ in $(seq 1 20); do
        pgrep -f "bathero.app/Contents/MacOS/bathero" >/dev/null || break
        sleep 0.25
    done
    pkill -f "bathero.app/Contents/MacOS/bathero" 2>/dev/null || true
fi
.venv/bin/python install_app.py "${1:-/Applications}" 2>&1 | grep -v "^pygame-ce"
if [ "$was_running" = 1 ]; then
    open -a bathero
    echo "relaunched bathero"
fi
