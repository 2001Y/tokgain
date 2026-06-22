from __future__ import annotations

import argparse
import json
import os
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .adapters import ADAPTERS
from .adapters.common import AdapterError
from .bench import BenchError, build_benchmark_record
from .pricing import estimate_usd, load_prices
from .store import SCHEMA_VERSION, append_events, build_daily_summary, build_week_summary, ensure_data_dir, read_events, update_state, write_daily_summary

DEFAULT_DATA_DIR = Path("~/.local/state/tokgain").expanduser()
MODEL_MISSING = "model_missing"


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if not getattr(args, "command", None):
        parser.print_help()
        return 2
    try:
        if args.command == "collect":
            return cmd_collect(args)
        if args.command == "bench":
            return cmd_bench(args)
        if args.command == "report":
            return cmd_report(args)
        if args.command == "show":
            return cmd_show(args)
        if args.command == "export":
            return cmd_export(args)
        if args.command == "doctor":
            return cmd_doctor(args)
        if args.command == "prices":
            return cmd_prices(args)
    except BrokenPipeError:
        return 1
    except Exception as exc:  # pragma: no cover - defensive CLI boundary
        print(f"tokgain: {exc}", file=os.sys.stderr)
        return 1
    return 2


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="tokgain",
        description="Collect reported token savings from coding-agent compression tools into local JSONL.",
    )
    parser.add_argument("--data-dir", default=os.environ.get("TOKGAIN_DATA_DIR", str(DEFAULT_DATA_DIR)), help="state directory (default: ~/.local/state/tokgain)")
    parser.add_argument("--prices", default=os.environ.get("TOKGAIN_PRICES"), help="manual prices.json path; when omitted, refresh LiteLLM + models.dev like ccusage")
    parser.add_argument("--offline-prices", action="store_true", default=_env_truthy("TOKGAIN_PRICES_OFFLINE"), help="do not fetch live pricing; use --prices, cache, or packaged fallback")
    parser.add_argument("--price-cache", default=os.environ.get("TOKGAIN_PRICE_CACHE"), help="price cache path (default: ~/.cache/tokgain/prices.json)")

    sub = parser.add_subparsers(dest="command")

    collect = sub.add_parser("collect", help="collect savings events")
    collect.add_argument("--tool", action="append", choices=["auto", "all", *ADAPTERS.keys()], default=None, help="tool to collect; repeatable; default auto")
    collect.add_argument("--date", default=None, help="period date YYYY-MM-DD; default yesterday")
    collect.add_argument("--model", default=None, help="explicit model override for all collected events")
    collect.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    collect.add_argument("--allow-errors", action="store_true", help="return 0 even when selected adapters fail")

    bench = sub.add_parser("bench", help="measure one baseline vs optimized output pair and append a savings event")
    bench.add_argument("--tool", required=True, help="tool or service being measured, e.g. rg, ast-grep, fff, contextmode")
    bench.add_argument("--layer", default="benchmark", help="measurement layer, e.g. search-output, agent-context, code-review")
    bench.add_argument("--date", default=None, help="period date YYYY-MM-DD; default yesterday")
    bench.add_argument("--model", default=None, help="explicit model override for the benchmark event")
    bench.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    bench.add_argument("--session-id", default=None, help="explicit session id/source run id")
    bench.add_argument("--source-ref", default=None, help="human-readable benchmark source reference")
    bench.add_argument("--baseline-file", default=None, help="raw/baseline output file")
    bench.add_argument("--optimized-file", default=None, help="compressed/optimized output file")
    bench.add_argument("--baseline-cmd", default=None, help="command that prints raw/baseline output")
    bench.add_argument("--optimized-cmd", default=None, help="command that prints compressed/optimized output")
    bench.add_argument("--cwd", default=None, help="working directory for --*-cmd")
    bench.add_argument("--timeout", type=int, default=30, help="command timeout seconds")
    bench.add_argument("--tokenizer", choices=["auto", "regex", "tiktoken"], default=os.environ.get("TOKGAIN_TOKENIZER", "auto"))
    bench.add_argument("--encoding", default=os.environ.get("TOKGAIN_TIKTOKEN_ENCODING", "o200k_base"))

    report = sub.add_parser("report", help="print day/week summary")
    report.add_argument("--period", choices=["day", "week"], default="day")
    report.add_argument("--date", default=None, help="day or week end date YYYY-MM-DD; default yesterday")
    report.add_argument("--json", action="store_true", help="print JSON summary")

    show = sub.add_parser("show", help="print recent JSONL events")
    show.add_argument("--tool", default=None, help="filter by tool name; accepts adapter or benchmark-only names")
    show.add_argument("--status", choices=["ok", "error"], default=None)
    show.add_argument("--limit", type=int, default=20)

    export = sub.add_parser("export", help="export collected events")
    export.add_argument("--format", choices=["jsonl", "json"], default="jsonl")

    doctor = sub.add_parser("doctor", help="show adapter/source availability")
    doctor.add_argument("--json", action="store_true")

    prices = sub.add_parser("prices", help="inspect price table")
    prices_sub = prices.add_subparsers(dest="prices_command")
    prices_sub.add_parser("show", help="print active prices.json")
    prices_sub.add_parser("refresh", help="fetch LiteLLM + models.dev pricing and update cache")

    return parser


def cmd_collect(args: argparse.Namespace) -> int:
    data_dir = ensure_data_dir(args.data_dir)
    period = args.date or _default_period_date()
    prices = load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache)
    metadata = _load_metadata(args.metadata)
    tools = _resolve_tools(args.tool)

    events: list[dict[str, Any]] = []
    for tool in tools:
        adapter = ADAPTERS[tool]
        try:
            raw_records = adapter.collect()
            period_records = _filter_records_for_period(raw_records, period)
            if not period_records:
                events.append(
                    _error_event(
                        tool,
                        period=period,
                        error=f"no records for period {period} from {tool}",
                        cli_model=args.model,
                        metadata=metadata,
                        layer=_adapter_layer(adapter),
                    )
                )
                continue
            for raw in period_records:
                events.append(_finalize_ok_event(raw, period=period, prices=prices, cli_model=args.model, metadata=metadata))
        except AdapterError as exc:
            events.append(_error_event(tool, period=period, error=str(exc), cli_model=args.model, metadata=metadata, layer=_adapter_layer(adapter)))

    append_events(data_dir, events)
    write_daily_summary(data_dir, period)
    update_state(data_dir, date=period, events=events)

    ok_count = sum(1 for event in events if event.get("status") == "ok")
    error_count = sum(1 for event in events if event.get("status") == "error")
    print(json.dumps({"date": period, "ok": ok_count, "errors": error_count, "events": len(events)}, ensure_ascii=False))
    if error_count and not args.allow_errors:
        return 1
    return 0


def cmd_bench(args: argparse.Namespace) -> int:
    data_dir = ensure_data_dir(args.data_dir)
    period = args.date or _default_period_date()
    metadata = _load_metadata(args.metadata)
    try:
        raw = build_benchmark_record(
            tool=args.tool,
            layer=args.layer,
            baseline_file=args.baseline_file,
            optimized_file=args.optimized_file,
            baseline_cmd=args.baseline_cmd,
            optimized_cmd=args.optimized_cmd,
            cwd=args.cwd,
            timeout=args.timeout,
            tokenizer=args.tokenizer,
            encoding=args.encoding,
            source_ref=args.source_ref,
            model=args.model,
            session_id=args.session_id,
        )
        prices = load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache)
        event = _finalize_ok_event(raw, period=period, prices=prices, cli_model=args.model, metadata=metadata)
        events = [event]
        return_code = 0
    except BenchError as exc:
        events = [_error_event(args.tool, period=period, error=str(exc), cli_model=args.model, metadata=metadata, layer=args.layer)]
        return_code = 1

    append_events(data_dir, events)
    write_daily_summary(data_dir, period)
    update_state(data_dir, date=period, events=events)

    event = events[0]
    if event.get("status") == "ok":
        measurement = (event.get("raw") or {}).get("measurement") or {}
        print(
            json.dumps(
                {
                    "date": period,
                    "tool": event.get("tool"),
                    "baseline_tokens": measurement.get("baseline_tokens"),
                    "optimized_tokens": measurement.get("optimized_tokens"),
                    "saved_tokens": event.get("saved_tokens"),
                    "saved_pct": measurement.get("saved_pct"),
                    "token_count_mode": measurement.get("token_count_mode"),
                },
                ensure_ascii=False,
            )
        )
    else:
        print(json.dumps({"date": period, "tool": args.tool, "error": event.get("error")}, ensure_ascii=False), file=os.sys.stderr)
    return return_code


def cmd_report(args: argparse.Namespace) -> int:
    date = args.date or _default_period_date()
    if args.period == "week":
        summary = build_week_summary(args.data_dir, date)
    else:
        summary = build_daily_summary(args.data_dir, date)
    if args.json:
        print(json.dumps(summary, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        totals = summary["totals"]
        print(f"{summary['label']} saved={totals['reported_saved_tokens']} tokens usd=${totals['reported_saved_usd']:.6f} events={summary['event_count']} errors={summary['error_count']}")
        for tool, bucket in summary.get("by_tool", {}).items():
            print(f"  {tool}: {bucket['reported_saved_tokens']} tokens ${bucket['reported_saved_usd']:.6f} ({bucket['event_count']} events)")
    return 0


def cmd_show(args: argparse.Namespace) -> int:
    events = read_events(args.data_dir)
    if args.tool:
        events = [event for event in events if event.get("tool") == args.tool]
    if args.status:
        events = [event for event in events if event.get("status") == args.status]
    if args.limit >= 0:
        events = events[-args.limit:]
    for event in events:
        print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    events = read_events(args.data_dir)
    if args.format == "json":
        print(json.dumps(events, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        for event in events:
            print(json.dumps(event, ensure_ascii=False, sort_keys=True))
    return 0


def cmd_doctor(args: argparse.Namespace) -> int:
    payload = {
        "data_dir": str(Path(args.data_dir).expanduser()),
        "prices": str(Path(args.prices).expanduser()) if args.prices else "live:LiteLLM+models.dev (cache fallback)",
        "adapters": {tool: {"available": adapter.available()} for tool, adapter in ADAPTERS.items()},
    }
    if args.json:
        print(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True))
    else:
        print(f"data_dir: {payload['data_dir']}")
        print(f"prices: {payload['prices']}")
        for tool, info in payload["adapters"].items():
            print(f"{tool}: {'ok' if info['available'] else 'missing'}")
    return 0


def cmd_prices(args: argparse.Namespace) -> int:
    if args.prices_command == "show":
        table = load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache)
    elif args.prices_command == "refresh":
        table = load_prices(None, offline=False, cache_path=args.price_cache, refresh=True)
    else:
        print("tokgain prices: expected subcommand 'show' or 'refresh'", file=os.sys.stderr)
        return 2
    print(json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _resolve_tools(requested: list[str] | None) -> list[str]:
    if not requested or "auto" in requested:
        available = [tool for tool, adapter in ADAPTERS.items() if adapter.available()]
        return available or ["rtk"]
    if "all" in requested:
        return list(ADAPTERS.keys())
    # Preserve order and remove duplicates.
    seen: set[str] = set()
    result: list[str] = []
    for tool in requested:
        if tool not in seen:
            result.append(tool)
            seen.add(tool)
    return result


def _filter_records_for_period(records: list[dict[str, Any]], period: str) -> list[dict[str, Any]]:
    has_perioded_records = any(raw.get("period") not in (None, "") for raw in records)
    if has_perioded_records:
        return [raw for raw in records if raw.get("period") == period]
    return records


def _adapter_layer(adapter: Any) -> str | None:
    layer = getattr(adapter, "LAYER", None)
    return str(layer) if layer else None


def _finalize_ok_event(raw: dict[str, Any], *, period: str, prices: dict[str, Any], cli_model: str | None, metadata: dict[str, Any]) -> dict[str, Any]:
    model = _resolve_model(cli_model=cli_model, metadata=metadata, raw=raw)
    incomplete = model == MODEL_MISSING
    event: dict[str, Any] = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "ts": _now_iso(),
        "period": raw.get("period") or period,
        "tool": raw.get("tool"),
        "layer": raw.get("layer"),
        "model": model,
        "saved_tokens": int(raw.get("saved_tokens") or 0),
        "saved_input_tokens": raw.get("saved_input_tokens"),
        "saved_output_tokens": raw.get("saved_output_tokens"),
        "saved_cache_creation_tokens": raw.get("saved_cache_creation_tokens"),
        "saved_cache_read_tokens": raw.get("saved_cache_read_tokens"),
        "source_ref": raw.get("source_ref"),
        "session_id": raw.get("session_id") or metadata.get("session_id") or _env_first("CODEX_SESSION_ID", "CLAUDE_SESSION_ID", "HERMES_SESSION_ID"),
        "incomplete": incomplete,
        "exclude_from_totals": incomplete,
        "raw": raw.get("raw"),
    }
    usd, price_version, estimate_mode, price_missing = estimate_usd(event, prices)
    # Prefer source-provided USD only when the price table cannot price it and the source had a number.
    if price_missing and raw.get("usd_saved_estimate") is not None:
        usd = round(float(raw["usd_saved_estimate"]), 10)
        estimate_mode = "source_reported"
    event.update(
        {
            "estimate_mode": estimate_mode,
            "usd_saved_estimate": usd,
            "price_table_version": price_version,
            "price_missing": price_missing,
        }
    )
    return event


def _error_event(tool: str, *, period: str, error: str, cli_model: str | None, metadata: dict[str, Any], layer: str | None = None) -> dict[str, Any]:
    model = _resolve_model(cli_model=cli_model, metadata=metadata, raw={})
    return {
        "schema_version": SCHEMA_VERSION,
        "status": "error",
        "ts": _now_iso(),
        "period": period,
        "tool": tool,
        "layer": layer,
        "model": model,
        "saved_tokens": 0,
        "saved_input_tokens": None,
        "saved_output_tokens": None,
        "saved_cache_creation_tokens": None,
        "saved_cache_read_tokens": None,
        "estimate_mode": "none",
        "usd_saved_estimate": 0.0,
        "price_table_version": None,
        "price_missing": True,
        "source_ref": tool,
        "session_id": metadata.get("session_id") or _env_first("CODEX_SESSION_ID", "CLAUDE_SESSION_ID", "HERMES_SESSION_ID"),
        "incomplete": model == MODEL_MISSING,
        "exclude_from_totals": True,
        "error": error,
    }


def _resolve_model(*, cli_model: str | None, metadata: dict[str, Any], raw: dict[str, Any]) -> str:
    return (
        cli_model
        or metadata.get("model")
        or raw.get("model")
        or _env_first("TOKGAIN_MODEL", "CODEX_MODEL", "CLAUDE_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL")
        or MODEL_MISSING
    )


def _load_metadata(path: str | None) -> dict[str, Any]:
    candidate = path or os.environ.get("TOKGAIN_SESSION_METADATA")
    if not candidate:
        return {}
    metadata_path = Path(candidate).expanduser()
    if not metadata_path.exists():
        return {}
    with metadata_path.open("r", encoding="utf-8") as fh:
        payload = json.load(fh)
    return payload if isinstance(payload, dict) else {}


def _env_first(*keys: str) -> str | None:
    for key in keys:
        value = os.environ.get(key)
        if value:
            return value
    return None


def _env_truthy(key: str) -> bool:
    return os.environ.get(key, "").strip().lower() in {"1", "true", "yes", "on"}


def _default_period_date() -> str:
    return (datetime.now().astimezone().date() - timedelta(days=1)).isoformat()


def _now_iso() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())
