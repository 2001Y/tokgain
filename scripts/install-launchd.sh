#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_SRC="$PROJECT_DIR/scripts/com.tokgain.daily.plist.template"
PLIST_DST="$HOME/Library/LaunchAgents/com.tokgain.daily.plist"

python3 -m venv "$PROJECT_DIR/.venv"
"$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/state/tokgain/logs"
sed   -e "s#__PROJECT_DIR__#$PROJECT_DIR#g"   -e "s#__HOME__#$HOME#g"   "$PLIST_SRC" > "$PLIST_DST"

launchctl unload "$PLIST_DST" >/dev/null 2>&1 || true
launchctl load "$PLIST_DST"
echo "installed: $PLIST_DST"
launchctl list | grep com.tokgain.daily || true
