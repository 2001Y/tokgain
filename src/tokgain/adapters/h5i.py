from __future__ import annotations

import os
import shutil
from pathlib import Path

from .common import AdapterError, extract_records, read_json_or_jsonl, run_json_command

TOOL = "h5i"
LAYER = "audit"
DEFAULT_FILES = [
    Path("~/.h5i/savings.jsonl").expanduser(),
    Path("~/.h5i/savings.json").expanduser(),
    Path("~/.h5i/summary.json").expanduser(),
]


def _env_file() -> Path | None:
    value = os.environ.get("TOKGAIN_H5I_SUMMARY_FILE")
    return Path(value).expanduser() if value else None


def available() -> bool:
    if (_env_file() and _env_file().exists()) or any(path.exists() for path in DEFAULT_FILES):
        return True
    return shutil.which("h5i") is not None


def collect() -> list[dict]:
    candidates = [_env_file()] if _env_file() else []
    candidates.extend(DEFAULT_FILES)
    for path in candidates:
        if path and path.exists():
            payload = read_json_or_jsonl(path)
            records = extract_records(TOOL, LAYER, payload, str(path))
            if records:
                return records
    if shutil.which("h5i"):
        try:
            payload = run_json_command(["h5i", "stats", "--json"])
            records = extract_records(TOOL, LAYER, payload, "h5i stats --json")
            if records:
                return records
        except AdapterError as exc:
            raise AdapterError(f"no h5i savings source found; h5i stats failed: {exc}") from exc
    raise AdapterError("no h5i savings source found (set TOKGAIN_H5I_SUMMARY_FILE or create ~/.h5i/savings.jsonl)")
