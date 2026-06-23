from __future__ import annotations

import os
import shutil
from pathlib import Path
from typing import Any

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
        records = _extract_headroom_records(payload, str(source))
        if not records:
            raise AdapterError(f"headroom source contained no savings records: {source}")
        return records
    if shutil.which("headroom"):
        errors: list[str] = []
        for command in (["headroom", "perf", "--format", "json"],):
            try:
                payload = run_json_command(list(command))
                records = _extract_headroom_records(payload, " ".join(command))
                if records:
                    return records
            except AdapterError as exc:
                errors.append(str(exc))
        raise AdapterError("headroom commands returned no savings records: " + " | ".join(errors))
    raise AdapterError("no headroom savings source found (set TOKGAIN_HEADROOM_FILE or create ~/.headroom/proxy_savings.json)")


def _extract_headroom_records(payload: Any, source_ref: str) -> list[dict]:
    """Extract Headroom savings without double-counting summary mirrors.

    ``~/.headroom/proxy_savings.json`` v3 stores the same latest request in
    ``history`` and in ``display_session`` / ``lifetime`` summaries. Use the
    per-request history as the canonical record when present; summaries are a
    fallback for older or command-derived payloads.
    """

    if isinstance(payload, dict):
        history = payload.get("history")
        if isinstance(history, list) and history:
            return extract_records(TOOL, LAYER, history, source_ref)
        display_session = payload.get("display_session")
        if isinstance(display_session, dict):
            return extract_records(TOOL, LAYER, display_session, source_ref)
        lifetime = payload.get("lifetime")
        if isinstance(lifetime, dict):
            return extract_records(TOOL, LAYER, lifetime, source_ref)
    return extract_records(TOOL, LAYER, payload, source_ref)
