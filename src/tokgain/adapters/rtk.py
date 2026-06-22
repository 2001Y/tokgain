from __future__ import annotations

import shutil

from .common import AdapterError, extract_records, run_json_command

TOOL = "rtk"
LAYER = "shell"


def available() -> bool:
    return shutil.which("rtk") is not None


def collect() -> list[dict]:
    if not available():
        raise AdapterError("rtk command not found")
    payload = run_json_command(["rtk", "gain", "--all", "--format", "json"])
    records = extract_records(TOOL, LAYER, payload, "rtk gain --all --format json")
    if not records:
        raise AdapterError("rtk output contained no savings records")
    return records
