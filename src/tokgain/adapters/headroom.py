from __future__ import annotations

import os
import shutil
from pathlib import Path

from .common import AdapterError, extract_records, read_json_or_jsonl, run_json_command

TOOL = "headroom"
LAYER = "proxy"
DEFAULT_FILE = Path("~/.headroom/proxy_savings.json").expanduser()


def _env_file() -> Path | None:
    value = os.environ.get("TOKGAIN_HEADROOM_FILE")
    return Path(value).expanduser() if value else None


def available() -> bool:
    source = _env_file() or DEFAULT_FILE
    return source.exists() or shutil.which("headroom") is not None


def collect() -> list[dict]:
    source = _env_file() or DEFAULT_FILE
    if source.exists():
        payload = read_json_or_jsonl(source)
        records = extract_records(TOOL, LAYER, payload, str(source))
        if not records:
            raise AdapterError(f"headroom source contained no savings records: {source}")
        return records
    if shutil.which("headroom"):
        payload = run_json_command(["headroom", "stats", "--json"])
        records = extract_records(TOOL, LAYER, payload, "headroom stats --json")
        if records:
            return records
    raise AdapterError("no headroom savings source found (set TOKGAIN_HEADROOM_FILE or create ~/.headroom/proxy_savings.json)")
