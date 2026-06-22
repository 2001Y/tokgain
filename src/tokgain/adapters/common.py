from __future__ import annotations

import json
import subprocess
from pathlib import Path
from typing import Any, Iterable


class AdapterError(RuntimeError):
    pass


SAVED_TOTAL_KEYS = (
    "saved_tokens",
    "tokens_saved",
    "savedTokens",
    "token_savings",
    "tokens_reduced",
    "reduced_tokens",
    "reported_saved_tokens",
    "total_saved_tokens",
    "total_saved",
)
SAVED_INPUT_KEYS = (
    "saved_input_tokens",
    "input_tokens_saved",
    "saved_prompt_tokens",
    "prompt_tokens_saved",
    "savedInputTokens",
    "input_saved_tokens",
)
SAVED_OUTPUT_KEYS = (
    "saved_output_tokens",
    "output_tokens_saved",
    "completion_tokens_saved",
    "savedOutputTokens",
    "output_saved_tokens",
)
SAVED_CACHE_CREATE_KEYS = (
    "saved_cache_creation_tokens",
    "saved_cache_create_tokens",
    "cache_creation_tokens_saved",
    "cache_create_tokens_saved",
    "savedCacheCreationTokens",
)
SAVED_CACHE_READ_KEYS = (
    "saved_cache_read_tokens",
    "cache_read_tokens_saved",
    "savedCacheReadTokens",
)
MODEL_KEYS = ("model", "model_name", "modelName", "llm_model")
SESSION_KEYS = ("session_id", "sessionId", "conversation_id", "conversationId")
PERIOD_KEYS = ("period", "date", "day")
SOURCE_KEYS = ("source_ref", "source", "path", "file")
LAYER_KEYS = ("layer", "compression_layer")
USD_KEYS = ("usd_saved_estimate", "estimated_usd_saved", "usd_saved", "cost_saved_usd")


def run_json_command(args: list[str], timeout: int = 30) -> Any:
    proc = subprocess.run(args, text=True, capture_output=True, timeout=timeout)
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip()
        raise AdapterError(f"command failed ({proc.returncode}): {' '.join(args)}: {stderr}")
    stdout = proc.stdout.strip()
    if not stdout:
        raise AdapterError(f"command produced no output: {' '.join(args)}")
    try:
        return json.loads(stdout)
    except json.JSONDecodeError as exc:
        raise AdapterError(f"command did not return JSON: {' '.join(args)}: {exc}") from exc


def read_json_or_jsonl(path: str | Path) -> Any:
    expanded = Path(path).expanduser()
    text = expanded.read_text(encoding="utf-8").strip()
    if not text:
        raise AdapterError(f"savings source is empty: {expanded}")
    if expanded.suffix == ".jsonl":
        return [json.loads(line) for line in text.splitlines() if line.strip()]
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        # Fallback: tolerate JSONL content in non-.jsonl files.
        return [json.loads(line) for line in text.splitlines() if line.strip()]


def extract_records(tool: str, default_layer: str, payload: Any, source_ref: str) -> list[dict[str, Any]]:
    records = list(_walk_payload(tool, default_layer, payload, source_ref))
    # If a command returned duplicate top-level/nested summaries, keep stable unique records.
    unique: list[dict[str, Any]] = []
    seen: set[str] = set()
    for record in records:
        key = json.dumps(
            {
                "tool": record.get("tool"),
                "layer": record.get("layer"),
                "model": record.get("model"),
                "session_id": record.get("session_id"),
                "period": record.get("period"),
                "saved_tokens": record.get("saved_tokens"),
                "saved_input_tokens": record.get("saved_input_tokens"),
                "saved_output_tokens": record.get("saved_output_tokens"),
                "saved_cache_creation_tokens": record.get("saved_cache_creation_tokens"),
                "saved_cache_read_tokens": record.get("saved_cache_read_tokens"),
                "source_ref": record.get("source_ref"),
            },
            sort_keys=True,
        )
        if key not in seen:
            unique.append(record)
            seen.add(key)
    return unique


def _walk_payload(tool: str, default_layer: str, payload: Any, source_ref: str) -> Iterable[dict[str, Any]]:
    if isinstance(payload, list):
        for item in payload:
            yield from _walk_payload(tool, default_layer, item, source_ref)
        return
    if not isinstance(payload, dict):
        return

    direct = _normalize_record(tool, default_layer, payload, source_ref)
    if direct is not None:
        yield direct
        # A direct record is treated as one event. Do not recurse into its children and double count.
        return

    for key in ("events", "sessions", "records", "items", "data", "totals", "summary", "daily", "days"):
        value = payload.get(key)
        if value is not None:
            yield from _walk_payload(tool, default_layer, value, source_ref)


def _normalize_record(tool: str, default_layer: str, raw: dict[str, Any], fallback_source_ref: str) -> dict[str, Any] | None:
    saved_input = _as_int(_get_any(raw, SAVED_INPUT_KEYS))
    saved_output = _as_int(_get_any(raw, SAVED_OUTPUT_KEYS))
    saved_cache_create = _as_int(_get_any(raw, SAVED_CACHE_CREATE_KEYS))
    saved_cache_read = _as_int(_get_any(raw, SAVED_CACHE_READ_KEYS))
    saved_total = _as_int(_get_any(raw, SAVED_TOTAL_KEYS))

    if saved_total is None and any(value is not None for value in (saved_input, saved_output, saved_cache_create, saved_cache_read)):
        saved_total = (saved_input or 0) + (saved_output or 0) + (saved_cache_create or 0) + (saved_cache_read or 0)
    if saved_total is None:
        return None

    return {
        "tool": str(raw.get("tool") or tool),
        "layer": str(_get_any(raw, LAYER_KEYS) or default_layer),
        "model": _get_any(raw, MODEL_KEYS),
        "period": _get_any(raw, PERIOD_KEYS),
        "saved_tokens": saved_total,
        "saved_input_tokens": saved_input,
        "saved_output_tokens": saved_output,
        "saved_cache_creation_tokens": saved_cache_create,
        "saved_cache_read_tokens": saved_cache_read,
        "usd_saved_estimate": _as_float(_get_any(raw, USD_KEYS)),
        "source_ref": str(_get_any(raw, SOURCE_KEYS) or fallback_source_ref),
        "session_id": _get_any(raw, SESSION_KEYS),
        "raw": raw,
    }


def _get_any(mapping: dict[str, Any], keys: tuple[str, ...]) -> Any:
    for key in keys:
        if key in mapping and mapping[key] not in (None, ""):
            return mapping[key]
    return None


def _as_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _as_float(value: Any) -> float | None:
    if value is None or value == "":
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None
