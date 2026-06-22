from __future__ import annotations

import os
from pathlib import Path

from .common import AdapterError, extract_records, read_json_or_jsonl

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


def _candidate_files() -> list[Path]:
    paths = [_env_file()] if _env_file() else []
    paths.extend(DEFAULT_FILES)
    return [path for path in paths if path is not None]


def available() -> bool:
    # h5i's official token-reduction path is `h5i capture run -- <cmd>`.
    # Running that requires a command and must not be guessed by a collector.
    # Auto-collection is therefore file-backed only.
    return any(path.exists() for path in _candidate_files())


def collect() -> list[dict]:
    for path in _candidate_files():
        if path.exists():
            payload = read_json_or_jsonl(path)
            records = extract_records(TOOL, LAYER, payload, str(path))
            if records:
                return records
            raise AdapterError(f"h5i savings source contained no savings records: {path}")
    raise AdapterError(
        "no h5i savings source found; h5i exposes savings per `h5i capture run -- <command>` rather than a global stats command "
        "(set TOKGAIN_H5I_SUMMARY_FILE or create ~/.h5i/savings.jsonl from capture/export output)"
    )
