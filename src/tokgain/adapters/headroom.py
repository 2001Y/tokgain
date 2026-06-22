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
        errors: list[str] = []
        for command in (["headroom", "perf", "--format", "json"],):
            try:
                payload = run_json_command(list(command))
                records = extract_records(TOOL, LAYER, payload, " ".join(command))
                if records:
                    return records
            except AdapterError as exc:
                errors.append(str(exc))
        raise AdapterError("headroom commands returned no savings records: " + " | ".join(errors))
    raise AdapterError("no headroom savings source found (set TOKGAIN_HEADROOM_FILE or create ~/.headroom/proxy_savings.json)")
