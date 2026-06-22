from __future__ import annotations

import json
from collections import defaultdict
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Iterable

SCHEMA_VERSION = 1


def ensure_data_dir(data_dir: str | Path) -> Path:
    path = Path(data_dir).expanduser()
    (path / "daily").mkdir(parents=True, exist_ok=True)
    (path / "events.jsonl").touch(exist_ok=True)
    return path


def append_events(data_dir: str | Path, events: Iterable[dict[str, Any]]) -> None:
    path = ensure_data_dir(data_dir) / "events.jsonl"
    with path.open("a", encoding="utf-8") as fh:
        for event in events:
            fh.write(json.dumps(event, ensure_ascii=False, sort_keys=True) + "\n")


def read_events(data_dir: str | Path) -> list[dict[str, Any]]:
    path = ensure_data_dir(data_dir) / "events.jsonl"
    events: list[dict[str, Any]] = []
    with path.open("r", encoding="utf-8") as fh:
        for line in fh:
            line = line.strip()
            if not line:
                continue
            events.append(json.loads(line))
    return events


def build_daily_summary(data_dir: str | Path, date: str) -> dict[str, Any]:
    period_events = [event for event in read_events(data_dir) if event.get("period") == date]
    return summarize_events(period_events, date=date)


def summarize_events(events: list[dict[str, Any]], *, date: str | None = None, period: str = "day") -> dict[str, Any]:
    generated_at = datetime.now().astimezone().isoformat(timespec="seconds")
    by_tool: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    by_model: dict[str, dict[str, Any]] = defaultdict(_empty_bucket)
    totals = _empty_bucket()
    errors: list[dict[str, Any]] = []
    incomplete_events = 0

    for event in events:
        tool = str(event.get("tool") or "unknown")
        model = str(event.get("model") or "model_missing")
        if event.get("status") == "error":
            errors.append({
                "ts": event.get("ts"),
                "tool": tool,
                "error": event.get("error"),
                "source_ref": event.get("source_ref"),
            })
            continue
        if event.get("incomplete"):
            incomplete_events += 1
        if event.get("exclude_from_totals"):
            continue
        _add_to_bucket(totals, event)
        _add_to_bucket(by_tool[tool], event)
        _add_to_bucket(by_model[model], event)

    label = date if date is not None else period
    return {
        "schema_version": SCHEMA_VERSION,
        "period": period,
        "date": date,
        "label": label,
        "generated_at": generated_at,
        "totals": _finalize_bucket(totals),
        "reported_total": _finalize_bucket(totals),
        "net_total": None,
        "by_tool": {key: _finalize_bucket(value) for key, value in sorted(by_tool.items())},
        "by_model": {key: _finalize_bucket(value) for key, value in sorted(by_model.items())},
        "event_count": len(events),
        "incomplete_events": incomplete_events,
        "error_count": len(errors),
        "errors": errors,
    }


def write_daily_summary(data_dir: str | Path, date: str) -> dict[str, Any]:
    data_path = ensure_data_dir(data_dir)
    summary = build_daily_summary(data_path, date)
    out = data_path / "daily" / f"{date}.json"
    out.write_text(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return summary


def build_week_summary(data_dir: str | Path, end_date: str) -> dict[str, Any]:
    end = datetime.strptime(end_date, "%Y-%m-%d").date()
    days = [(end - timedelta(days=offset)).isoformat() for offset in range(6, -1, -1)]
    events = [event for event in read_events(data_dir) if event.get("period") in days]
    summary = summarize_events(events, date=None, period="week")
    summary["start_date"] = days[0]
    summary["end_date"] = days[-1]
    summary["label"] = f"{days[0]}..{days[-1]}"
    return summary


def update_state(data_dir: str | Path, *, date: str, events: list[dict[str, Any]]) -> None:
    data_path = ensure_data_dir(data_dir)
    state_path = data_path / "state.json"
    state: dict[str, Any] = {}
    if state_path.exists() and state_path.read_text(encoding="utf-8").strip():
        state = json.loads(state_path.read_text(encoding="utf-8"))
    now = datetime.now().astimezone().isoformat(timespec="seconds")
    state["schema_version"] = SCHEMA_VERSION
    state["last_run_ts"] = now
    state["last_run_date"] = date
    if any(event.get("status") == "ok" for event in events):
        state["last_success_ts"] = now
        state["last_success_date"] = date
    error_events = [event for event in events if event.get("status") == "error"]
    if error_events:
        state["last_error_ts"] = now
        state["last_error_tool"] = error_events[-1].get("tool")
        state["last_error"] = error_events[-1].get("error")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _empty_bucket() -> dict[str, Any]:
    return {
        "event_count": 0,
        "reported_saved_tokens": 0,
        "reported_saved_input_tokens": 0,
        "reported_saved_output_tokens": 0,
        "reported_saved_usd": 0.0,
    }


def _add_to_bucket(bucket: dict[str, Any], event: dict[str, Any]) -> None:
    bucket["event_count"] += 1
    bucket["reported_saved_tokens"] += int(event.get("saved_tokens") or 0)
    bucket["reported_saved_input_tokens"] += int(event.get("saved_input_tokens") or 0)
    bucket["reported_saved_output_tokens"] += int(event.get("saved_output_tokens") or 0)
    bucket["reported_saved_usd"] += float(event.get("usd_saved_estimate") or 0.0)


def _finalize_bucket(bucket: dict[str, Any]) -> dict[str, Any]:
    finalized = dict(bucket)
    finalized["reported_saved_usd"] = round(float(finalized["reported_saved_usd"]), 10)
    return finalized
