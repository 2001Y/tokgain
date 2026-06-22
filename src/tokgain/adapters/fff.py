from __future__ import annotations

import os
from pathlib import Path

from .common import AdapterError, extract_records, read_json_or_jsonl

TOOL = "fff"
LAYER = "file-search-mcp"
DEFAULT_FILES = [
    Path("~/.fff/savings.jsonl").expanduser(),
    Path("~/.fff/savings.json").expanduser(),
    Path("~/.fff/stats.json").expanduser(),
]


def _env_file() -> Path | None:
    value = os.environ.get("TOKGAIN_FFF_FILE")
    return Path(value).expanduser() if value else None


def _candidate_files() -> list[Path]:
    paths = [_env_file()] if _env_file() else []
    paths.extend(DEFAULT_FILES)
    return [path for path in paths if path is not None]


def available() -> bool:
    # Official FFF is the fff-mcp MCP server. It does not currently expose a
    # native token-savings ledger, so auto-collection is enabled only when a
    # benchmark/export file exists. Explicit `--tool fff` still records a clear
    # ERROR event if no file-backed savings source is configured.
    return any(path.exists() for path in _candidate_files())


def collect() -> list[dict]:
    for path in _candidate_files():
        if path.exists():
            payload = read_json_or_jsonl(path)
            records = extract_records(TOOL, LAYER, payload, str(path))
            if records:
                return records
            raise AdapterError(f"fff savings source has no token-savings records: {path}")
    raise AdapterError(
        "no fff savings source found; official fff is `fff-mcp` and does not expose a native savings ledger yet "
        "(set TOKGAIN_FFF_FILE or write ~/.fff/savings.jsonl from an external benchmark/export)"
    )
