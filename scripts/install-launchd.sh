#!/usr/bin/env bash
set -euo pipefail

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PLIST_SRC="$PROJECT_DIR/scripts/com.tokgain.daily.plist.template"
PLIST_DST="$HOME/Library/LaunchAgents/com.tokgain.daily.plist"
TOKGAIN_MODEL_VALUE="${TOKGAIN_MODEL:-}"
if [[ -z "$TOKGAIN_MODEL_VALUE" && -f "$HOME/.hermes/config.yaml" ]]; then
  TOKGAIN_MODEL_VALUE="$(python3 - <<'PY'
from pathlib import Path
for line in Path.home().joinpath('.hermes/config.yaml').read_text(encoding='utf-8').splitlines():
    if line.startswith('  default:'):
        value = line.split(':', 1)[1].strip()
        print(value.strip(chr(34)).strip(chr(39)))
        break
PY
)"
fi
if [[ -z "$TOKGAIN_MODEL_VALUE" && -f "$HOME/.codex/config.toml" ]]; then
  TOKGAIN_MODEL_VALUE="$(python3 - <<'PY'
from pathlib import Path
for line in Path.home().joinpath('.codex/config.toml').read_text(encoding='utf-8').splitlines():
    line = line.strip()
    if line.startswith('model ='):
        value = line.split('=', 1)[1].strip()
        print(value.strip(chr(34)).strip(chr(39)))
        break
PY
)"
fi
TOKGAIN_MODEL_VALUE="${TOKGAIN_MODEL_VALUE:-model_missing}"

PYTHON_BIN="${PYTHON:-python3}"
if ! "$PYTHON_BIN" - <<'PY' >/dev/null 2>&1
import sys
raise SystemExit(0 if sys.version_info >= (3, 11) else 1)
PY
then
  if command -v uv >/dev/null 2>&1; then
    uv venv --clear --seed --python 3.11 "$PROJECT_DIR/.venv"
  else
    echo "tokgain requires Python >=3.11; set PYTHON=/path/to/python3.11 or install uv" >&2
    exit 1
  fi
else
  "$PYTHON_BIN" -m venv "$PROJECT_DIR/.venv"
fi
"$PROJECT_DIR/.venv/bin/python" -m pip install --upgrade pip setuptools wheel
"$PROJECT_DIR/.venv/bin/python" -m pip install -e "$PROJECT_DIR"
mkdir -p "$HOME/Library/LaunchAgents" "$HOME/.local/state/tokgain/logs" "$HOME/.local/bin"
ln -sf "$PROJECT_DIR/.venv/bin/tokgain" "$HOME/.local/bin/tokgain"
sed \
  -e "s#__PROJECT_DIR__#$PROJECT_DIR#g" \
  -e "s#__HOME__#$HOME#g" \
  -e "s#__TOKGAIN_MODEL__#$TOKGAIN_MODEL_VALUE#g" \
  "$PLIST_SRC" > "$PLIST_DST"

launchctl unload "$PLIST_DST" >/dev/null 2>&1 || true
launchctl load "$PLIST_DST"
echo "installed: $PLIST_DST"
launchctl list | grep com.tokgain.daily || true
