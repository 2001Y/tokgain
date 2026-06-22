from __future__ import annotations

import os
import shutil
from pathlib import Path

from .common import AdapterError, extract_records, read_json_or_jsonl, run_json_command

TOOL = "lean-ctx"
LAYER = "context"
DEFAULT_FILES = [
    Path("~/.lean-ctx/savings.jsonl").expanduser(),
    Path("~/.lean-ctx/savings.json").expanduser(),
]


def _env_file() -> Path | None:
    value = os.environ.get("TOKGAIN_LEAN_CTX_FILE")
    return Path(value).expanduser() if value else None


def available() -> bool:
    if (_env_file() and _env_file().exists()) or any(path.exists() for path in DEFAULT_FILES):
        return True
    return shutil.which("lean-ctx") is not None


def collect() -> list[dict]:
    candidates = [_env_file()] if _env_file() else []
    candidates.extend(DEFAULT_FILES)
    for path in candidates:
        if path and path.exists():
            payload = read_json_or_jsonl(path)
            records = extract_records(TOOL, LAYER, payload, str(path))
            if records:
                return records
    if shutil.which("lean-ctx"):
        errors: list[str] = []
        for cmd in (["lean-ctx", "gain", "--json"], ["lean-ctx", "savings", "export"]):
            try:
                payload = run_json_command(list(cmd))
                records = extract_records(TOOL, LAYER, payload, " ".join(cmd))
                if records:
                    return records
            except AdapterError as exc:
                errors.append(str(exc))
        raise AdapterError("lean-ctx commands returned no savings records: " + " | ".join(errors))
    raise AdapterError("no lean-ctx savings source found (set TOKGAIN_LEAN_CTX_FILE or install lean-ctx)")
