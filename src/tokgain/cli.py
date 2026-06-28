from __future__ import annotations

import argparse
import hashlib
import json
import os
import sys
import tomllib
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

from .adapters import ADAPTERS
from .adapters.common import AdapterError
from .bench import BenchError, build_benchmark_record
from .measure import MeasureError, build_fff_measure_record, build_h5i_measure_record
from .mcp_proxy import ProxyError, run_mcp_proxy
from .observe import ObserveError, build_mcp_observation_record, build_terminal_observation_record
from .pricing import estimate_usd, load_prices
from .store import SCHEMA_VERSION, append_events, build_daily_summary, build_month_summary, build_week_summary, ensure_data_dir, read_events, update_state, write_daily_summary

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
        if args.command == "measure":
            return cmd_measure(args)
        if args.command == "observe":
            return cmd_observe(args)
        if args.command == "mcp-proxy":
            return cmd_mcp_proxy(args)
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
        print(f"tokgain: {exc}", file=sys.stderr)
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

    measure = sub.add_parser("measure", help="measure h5i/fff savings with built-in tool-specific runners")
    measure_sub = measure.add_subparsers(dest="measure_tool")

    h5i = measure_sub.add_parser("h5i", help="run a command raw and through h5i capture, then compare token counts")
    h5i.add_argument("--cmd", required=True, help="command to run twice: raw baseline and h5i-captured optimized output")
    h5i.add_argument("--date", default=None, help="period date YYYY-MM-DD; default yesterday")
    h5i.add_argument("--model", default=None, help="explicit model override for the measurement event")
    h5i.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    h5i.add_argument("--session-id", default=None, help="explicit session id/source run id")
    h5i.add_argument("--source-ref", default=None, help="human-readable measurement source reference")
    h5i.add_argument("--cwd", default=None, help="working directory for both command runs")
    h5i.add_argument("--timeout", type=int, default=30, help="command timeout seconds")
    h5i.add_argument("--tokenizer", choices=["auto", "regex", "tiktoken"], default=os.environ.get("TOKGAIN_TOKENIZER", "auto"))
    h5i.add_argument("--encoding", default=os.environ.get("TOKGAIN_TIKTOKEN_ENCODING", "o200k_base"))
    h5i.add_argument("--h5i-format", default="compact", choices=["compact", "structured", "yaml", "json", "summary", "text"], help="h5i capture run output format")
    h5i.add_argument("--kind", default=None, help="h5i content kind, e.g. test|log|json|diff|generic")
    h5i.add_argument("--budget", type=int, default=None, help="h5i max lines to keep in summary")
    h5i.add_argument("--token-budget", type=int, default=None, help="h5i best-effort summary token cap")
    h5i.add_argument("--min-bytes", type=int, default=0, help="h5i min bytes before capture; default 0 to force measurement")
    h5i.add_argument("--quiet", action="store_true", help="pass --quiet to h5i capture run")

    fff = measure_sub.add_parser("fff", help="call fff-mcp and compare its result against a raw baseline command")
    fff.add_argument("--query", required=True, help="query passed to fff-mcp")
    fff.add_argument("--path", default=".", help="base directory to index/search with fff-mcp")
    fff.add_argument("--fff-tool", choices=["grep", "find_files"], default="grep", help="fff MCP tool to call")
    fff.add_argument("--max-results", type=int, default=20, help="max results for fff and default baseline")
    fff.add_argument("--baseline-cmd", default=None, help="override raw baseline command; default uses rg/find")
    fff.add_argument("--output-mode", default=None, help="optional fff grep output_mode")
    fff.add_argument("--startup-wait", type=float, default=1.0, help="seconds to wait after fff-mcp initialization for indexing")
    fff.add_argument("--date", default=None, help="period date YYYY-MM-DD; default yesterday")
    fff.add_argument("--model", default=None, help="explicit model override for the measurement event")
    fff.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    fff.add_argument("--session-id", default=None, help="explicit session id/source run id")
    fff.add_argument("--source-ref", default=None, help="human-readable measurement source reference")
    fff.add_argument("--timeout", type=int, default=30, help="command/MCP timeout seconds")
    fff.add_argument("--tokenizer", choices=["auto", "regex", "tiktoken"], default=os.environ.get("TOKGAIN_TOKENIZER", "auto"))
    fff.add_argument("--encoding", default=os.environ.get("TOKGAIN_TIKTOKEN_ENCODING", "o200k_base"))

    observe = sub.add_parser("observe", help="record savings from natural agent tool invocations")
    observe_sub = observe.add_subparsers(dest="observe_source")
    observe_terminal = observe_sub.add_parser("terminal", help="observe a terminal command result from stdin")
    observe_terminal.add_argument("--agent", required=True, help="agent/runtime name, e.g. hermes")
    observe_terminal.add_argument("--command", dest="observed_command", required=True, help="command that produced stdin output")
    observe_terminal.add_argument("--exit-code", type=int, default=None)
    observe_terminal.add_argument("--cwd", default=None, help="working directory of the observed command")
    observe_terminal.add_argument("--date", default=None, help="period date YYYY-MM-DD; default today for live observations")
    observe_terminal.add_argument("--model", default=None, help="explicit model override")
    observe_terminal.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    observe_terminal.add_argument("--session-id", default=None, help="explicit session id/source run id")
    _add_runtime_context_arguments(observe_terminal)
    observe_terminal.add_argument("--tokenizer", choices=["auto", "regex", "tiktoken"], default=os.environ.get("TOKGAIN_TOKENIZER", "auto"))
    observe_terminal.add_argument("--encoding", default=os.environ.get("TOKGAIN_TIKTOKEN_ENCODING", "o200k_base"))
    observe_mcp = observe_sub.add_parser("mcp-call", help="observe an MCP tool result from stdin JSON")
    observe_mcp.add_argument("--agent", required=True, help="agent/runtime name, e.g. hermes")
    observe_mcp.add_argument("--server-tool", required=True, help="logical MCP server/tool family, e.g. fff")
    observe_mcp.add_argument("--base-path", default=None, help="base path used to build baselines")
    observe_mcp.add_argument("--date", default=None, help="period date YYYY-MM-DD; default today for live observations")
    observe_mcp.add_argument("--model", default=None, help="explicit model override")
    observe_mcp.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    observe_mcp.add_argument("--session-id", default=None, help="explicit session id/source run id")
    _add_runtime_context_arguments(observe_mcp)
    observe_mcp.add_argument("--tokenizer", choices=["auto", "regex", "tiktoken"], default=os.environ.get("TOKGAIN_TOKENIZER", "auto"))
    observe_mcp.add_argument("--encoding", default=os.environ.get("TOKGAIN_TIKTOKEN_ENCODING", "o200k_base"))

    mcp_proxy = sub.add_parser("mcp-proxy", help="transparent MCP stdio proxy that records tool savings")
    _add_mcp_proxy_arguments(mcp_proxy)

    report = sub.add_parser("report", help="print day/week/month summary")
    report.add_argument("--period", choices=["day", "week", "month"], default="day")
    report.add_argument("--date", default=None, help="date YYYY-MM-DD; day date, week end date, or any date in the month; default yesterday")
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


def _add_runtime_context_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--duration-ms", type=int, default=None, help="observed tool wall time in milliseconds")
    parser.add_argument("--turn-id", default=None, help="agent turn id for correlation")
    parser.add_argument("--tool-call-id", default=None, help="tool call id for correlation")
    parser.add_argument("--api-request-id", default=None, help="provider/API request id for correlation")


def _add_mcp_proxy_arguments(parser: argparse.ArgumentParser) -> None:
    parser.add_argument("--agent", required=True, help="agent/runtime name, e.g. codex")
    parser.add_argument("--tool", required=True, help="logical tool/server being proxied, e.g. fff")
    parser.add_argument("--base-path", default=None, help="base path used to build baselines for file-search MCPs")
    parser.add_argument("--date", default=None, help="period date YYYY-MM-DD; default today for live observations")
    parser.add_argument("--model", default=None, help="explicit model override")
    parser.add_argument("--metadata", default=None, help="session metadata JSON with model/session_id")
    parser.add_argument("--session-id", default=None, help="explicit session id/source run id")
    parser.add_argument("--tokenizer", choices=["auto", "regex", "tiktoken"], default=os.environ.get("TOKGAIN_TOKENIZER", "auto"))
    parser.add_argument("--encoding", default=os.environ.get("TOKGAIN_TIKTOKEN_ENCODING", "o200k_base"))
    parser.add_argument("server_command", nargs=argparse.REMAINDER, help="real MCP server command after --")


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
        print(json.dumps({"date": period, "tool": args.tool, "error": event.get("error")}, ensure_ascii=False), file=sys.stderr)
    return return_code


def cmd_measure(args: argparse.Namespace) -> int:
    if not getattr(args, "measure_tool", None):
        print("tokgain measure: expected subcommand 'h5i' or 'fff'", file=sys.stderr)
        return 2
    data_dir = ensure_data_dir(args.data_dir)
    period = args.date or _default_period_date()
    metadata = _load_metadata(args.metadata)
    layer = "audit" if args.measure_tool == "h5i" else "file-search-mcp"
    try:
        if args.measure_tool == "h5i":
            raw = build_h5i_measure_record(
                command=args.cmd,
                cwd=args.cwd,
                timeout=args.timeout,
                tokenizer=args.tokenizer,
                encoding=args.encoding,
                model=args.model,
                session_id=args.session_id,
                h5i_format=args.h5i_format,
                kind=args.kind,
                budget=args.budget,
                token_budget=args.token_budget,
                min_bytes=args.min_bytes,
                quiet=args.quiet,
                source_ref=args.source_ref,
            )
        else:
            raw = build_fff_measure_record(
                query=args.query,
                path=args.path,
                fff_tool=args.fff_tool,
                max_results=args.max_results,
                baseline_cmd=args.baseline_cmd,
                output_mode=args.output_mode,
                timeout=args.timeout,
                startup_wait=args.startup_wait,
                tokenizer=args.tokenizer,
                encoding=args.encoding,
                model=args.model,
                session_id=args.session_id,
                source_ref=args.source_ref,
            )
        prices = load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache)
        event = _finalize_ok_event(raw, period=period, prices=prices, cli_model=args.model, metadata=metadata)
        events = [event]
        return_code = 0
    except (BenchError, MeasureError) as exc:
        events = [_error_event(args.measure_tool, period=period, error=str(exc), cli_model=args.model, metadata=metadata, layer=layer)]
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
        print(json.dumps({"date": period, "tool": args.measure_tool, "error": event.get("error")}, ensure_ascii=False), file=sys.stderr)
    return return_code


def cmd_observe(args: argparse.Namespace) -> int:
    if getattr(args, "observe_source", None) == "terminal":
        return _cmd_observe_terminal(args)
    if getattr(args, "observe_source", None) == "mcp-call":
        return _cmd_observe_mcp_call(args)
    print("tokgain observe: expected subcommand 'terminal' or 'mcp-call'", file=sys.stderr)
    return 2


def _cmd_observe_terminal(args: argparse.Namespace) -> int:
    data_dir = ensure_data_dir(args.data_dir)
    period = args.date or datetime.now().astimezone().date().isoformat()
    metadata = _load_metadata(args.metadata)
    output = sys.stdin.read()
    try:
        raw = build_terminal_observation_record(
            command=args.observed_command,
            output=output,
            agent=args.agent,
            exit_code=args.exit_code,
            cwd=args.cwd,
            tokenizer=args.tokenizer,
            encoding=args.encoding,
            model=args.model or _agent_default_model(args.agent),
            session_id=args.session_id,
            capture_mode="hermes_hook" if args.agent == "hermes" else "terminal_observer",
        )
        if raw is None:
            print(json.dumps({"recorded": False, "reason": "command not targeted"}, ensure_ascii=False))
            return 0
        _attach_runtime_context(raw, args=args, metadata=metadata)
        event = _append_raw_event(
            data_dir=data_dir,
            raw=raw,
            period=period,
            prices=load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache),
            cli_model=args.model,
            metadata=metadata,
        )
    except ObserveError as exc:
        event = _append_error_event(data_dir=data_dir, tool="observe", period=period, error=str(exc), cli_model=args.model, metadata=metadata, layer="observer")
        print(json.dumps({"recorded": False, "error": event.get("error")}, ensure_ascii=False), file=sys.stderr)
        return 1
    measurement = (event.get("raw") or {}).get("measurement") or {}
    print(
        json.dumps(
            {
                "recorded": True,
                "date": period,
                "tool": event.get("tool"),
                "saved_tokens": event.get("saved_tokens"),
                "baseline_tokens": measurement.get("baseline_tokens"),
                "optimized_tokens": measurement.get("optimized_tokens"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def _cmd_observe_mcp_call(args: argparse.Namespace) -> int:
    data_dir = ensure_data_dir(args.data_dir)
    period = args.date or datetime.now().astimezone().date().isoformat()
    metadata = _load_metadata(args.metadata)
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            raise ObserveError("stdin JSON must be an object")
        raw = build_mcp_observation_record(
            server_tool=args.server_tool,
            mcp_tool_name=str(payload.get("tool_name") or payload.get("mcp_tool_name") or ""),
            arguments=payload.get("arguments") if isinstance(payload.get("arguments"), dict) else {},
            result_text=str(payload.get("result_text") or ""),
            agent=args.agent,
            base_path=args.base_path,
            tokenizer=args.tokenizer,
            encoding=args.encoding,
            model=args.model or _agent_default_model(args.agent),
            session_id=args.session_id,
            capture_mode="hermes_hook" if args.agent == "hermes" else "mcp_observer",
        )
        if raw is None:
            print(json.dumps({"recorded": False, "reason": "mcp call not targeted"}, ensure_ascii=False))
            return 0
        _attach_runtime_context(raw, args=args, metadata=metadata)
        event = _append_raw_event(
            data_dir=data_dir,
            raw=raw,
            period=period,
            prices=load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache),
            cli_model=args.model,
            metadata=metadata,
        )
    except (ObserveError, json.JSONDecodeError) as exc:
        event = _append_error_event(data_dir=data_dir, tool=args.server_tool, period=period, error=str(exc), cli_model=args.model, metadata=metadata, layer="mcp-observer")
        print(json.dumps({"recorded": False, "error": event.get("error")}, ensure_ascii=False), file=sys.stderr)
        return 1
    measurement = (event.get("raw") or {}).get("measurement") or {}
    print(
        json.dumps(
            {
                "recorded": True,
                "date": period,
                "tool": event.get("tool"),
                "saved_tokens": event.get("saved_tokens"),
                "baseline_tokens": measurement.get("baseline_tokens"),
                "optimized_tokens": measurement.get("optimized_tokens"),
            },
            ensure_ascii=False,
        )
    )
    return 0


def cmd_mcp_proxy(args: argparse.Namespace) -> int:
    data_dir = ensure_data_dir(args.data_dir)
    period = args.date or datetime.now().astimezone().date().isoformat()
    metadata = _load_metadata(args.metadata)
    prices = load_prices(args.prices, offline=args.offline_prices, cache_path=args.price_cache)
    server_command = list(args.server_command or [])
    if server_command and server_command[0] == "--":
        server_command = server_command[1:]

    def emit(raw: dict[str, Any]) -> None:
        _append_raw_event(
            data_dir=data_dir,
            raw=raw,
            period=period,
            prices=prices,
            cli_model=args.model,
            metadata=metadata,
        )

    try:
        return run_mcp_proxy(
            server_command=server_command,
            emit_record=emit,
            agent=args.agent,
            tool=args.tool,
            base_path=args.base_path,
            tokenizer=args.tokenizer,
            encoding=args.encoding,
            model=args.model or _agent_default_model(args.agent),
            session_id=args.session_id,
        )
    except ProxyError as exc:
        print(f"tokgain mcp-proxy: {exc}", file=sys.stderr)
        return 1


def cmd_report(args: argparse.Namespace) -> int:
    date = args.date or _default_period_date()
    if args.period == "week":
        summary = build_week_summary(args.data_dir, date)
    elif args.period == "month":
        summary = build_month_summary(args.data_dir, date)
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
        print("tokgain prices: expected subcommand 'show' or 'refresh'", file=sys.stderr)
        return 2
    print(json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True))
    return 0


def _append_raw_event(*, data_dir: Path, raw: dict[str, Any], period: str, prices: dict[str, Any], cli_model: str | None, metadata: dict[str, Any]) -> dict[str, Any]:
    event = _finalize_ok_event(raw, period=period, prices=prices, cli_model=cli_model, metadata=metadata)
    append_events(data_dir, [event])
    write_daily_summary(data_dir, period)
    update_state(data_dir, date=period, events=[event])
    return event


def _append_error_event(*, data_dir: Path, tool: str, period: str, error: str, cli_model: str | None, metadata: dict[str, Any], layer: str | None = None) -> dict[str, Any]:
    event = _error_event(tool, period=period, error=error, cli_model=cli_model, metadata=metadata, layer=layer)
    append_events(data_dir, [event])
    write_daily_summary(data_dir, period)
    update_state(data_dir, date=period, events=[event])
    return event


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
        "duration_ms": _optional_int(_runtime_field(raw, metadata, "duration_ms", "TOKGAIN_DURATION_MS")),
        "turn_id": _runtime_field(raw, metadata, "turn_id", "TOKGAIN_TURN_ID"),
        "tool_call_id": _runtime_field(raw, metadata, "tool_call_id", "TOKGAIN_TOOL_CALL_ID"),
        "api_request_id": _runtime_field(raw, metadata, "api_request_id", "TOKGAIN_API_REQUEST_ID"),
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
    event["event_id"] = _event_id(event)
    return event


def _error_event(tool: str, *, period: str, error: str, cli_model: str | None, metadata: dict[str, Any], layer: str | None = None) -> dict[str, Any]:
    model = _resolve_model(cli_model=cli_model, metadata=metadata, raw={})
    event = {
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
        "duration_ms": _optional_int(_runtime_field({}, metadata, "duration_ms", "TOKGAIN_DURATION_MS")),
        "turn_id": _runtime_field({}, metadata, "turn_id", "TOKGAIN_TURN_ID"),
        "tool_call_id": _runtime_field({}, metadata, "tool_call_id", "TOKGAIN_TOOL_CALL_ID"),
        "api_request_id": _runtime_field({}, metadata, "api_request_id", "TOKGAIN_API_REQUEST_ID"),
        "incomplete": model == MODEL_MISSING,
        "exclude_from_totals": True,
        "error": error,
    }
    event["event_id"] = _event_id(event)
    return event


def _resolve_model(*, cli_model: str | None, metadata: dict[str, Any], raw: dict[str, Any]) -> str:
    return (
        cli_model
        or metadata.get("model")
        or raw.get("model")
        or _env_first("TOKGAIN_MODEL", "CODEX_MODEL", "CLAUDE_MODEL", "OPENAI_MODEL", "ANTHROPIC_MODEL")
        or MODEL_MISSING
    )


def _attach_runtime_context(raw: dict[str, Any], *, args: argparse.Namespace, metadata: dict[str, Any]) -> None:
    for key in ("duration_ms", "turn_id", "tool_call_id", "api_request_id"):
        value = getattr(args, key, None) or metadata.get(key)
        if value not in (None, ""):
            raw[key] = value


def _runtime_field(raw: dict[str, Any], metadata: dict[str, Any], key: str, env_key: str) -> Any:
    value = raw.get(key)
    if value not in (None, ""):
        return value
    value = metadata.get(key)
    if value not in (None, ""):
        return value
    return os.environ.get(env_key)


def _optional_int(value: Any) -> int | None:
    if value in (None, ""):
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def _event_id(event: dict[str, Any]) -> str:
    event_raw = event.get("raw")
    raw: dict[str, Any] = event_raw if isinstance(event_raw, dict) else {}
    raw_measurement = raw.get("measurement")
    measurement: dict[str, Any] = raw_measurement if isinstance(raw_measurement, dict) else {}
    identity = {
        "schema_version": event.get("schema_version"),
        "status": event.get("status"),
        "period": event.get("period"),
        "tool": event.get("tool"),
        "layer": event.get("layer"),
        "model": event.get("model"),
        "source_ref": event.get("source_ref"),
        "session_id": event.get("session_id"),
        "saved_tokens": event.get("saved_tokens"),
        "saved_input_tokens": event.get("saved_input_tokens"),
        "saved_output_tokens": event.get("saved_output_tokens"),
        "saved_cache_creation_tokens": event.get("saved_cache_creation_tokens"),
        "saved_cache_read_tokens": event.get("saved_cache_read_tokens"),
        "turn_id": event.get("turn_id"),
        "tool_call_id": event.get("tool_call_id"),
        "api_request_id": event.get("api_request_id"),
        "source_event_id": raw.get("source_event_id"),
        "baseline_ref": measurement.get("baseline_ref"),
        "optimized_ref": measurement.get("optimized_ref"),
        "optimized_sha256": measurement.get("optimized_sha256"),
        "error": event.get("error"),
    }
    digest = hashlib.sha256(json.dumps(identity, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")).hexdigest()
    return f"sha256:{digest}"


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


def _agent_default_model(agent: str | None) -> str | None:
    normalized = (agent or "").strip().lower()
    if normalized == "codex":
        config_path = Path(os.environ.get("CODEX_HOME", "~/.codex")).expanduser() / "config.toml"
        try:
            data = tomllib.loads(config_path.read_text(encoding="utf-8"))
            model = data.get("model")
            return str(model) if model else None
        except Exception:
            return None
    if normalized == "hermes":
        try:
            import yaml  # type: ignore
        except Exception:
            return None
        config_path = Path(os.environ.get("HERMES_CONFIG", "~/.hermes/config.yaml")).expanduser()
        try:
            data = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            model = (data or {}).get("model", {}).get("default")
            return str(model) if model else None
        except Exception:
            return None
    return None


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
