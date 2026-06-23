from __future__ import annotations

import hashlib
import json
import os
import re
import shlex
from pathlib import Path
from typing import Any

from .bench import BenchError, SourceResult, build_benchmark_record_from_sources, count_tokens, read_command_source
from .measure import _default_fff_baseline_cmd, _fixed_string_grep_baseline_cmd


class ObserveError(BenchError):
    pass


_H5I_REDUCTION_RE = re.compile(r"(?P<before>\d+)\s*(?:→|->|=>)\s*(?P<after>\d+)")
_SECRET_FLAG_RE = re.compile(r"^(--?(?:api[-_]?key|token|password|secret|auth|bearer))(?:=.*)?$", re.I)
_ASSIGNMENT_SECRET_RE = re.compile(r"\b([A-Z0-9_]*(?:TOKEN|SECRET|PASSWORD|API_KEY|AUTH)[A-Z0-9_]*)=([^\s]+)", re.I)


def build_terminal_observation_record(
    *,
    command: str,
    output: str,
    agent: str,
    exit_code: int | None = None,
    cwd: str | None = None,
    tokenizer: str = "auto",
    encoding: str = "o200k_base",
    model: str | None = None,
    session_id: str | None = None,
    capture_mode: str = "hermes_hook",
) -> dict[str, Any] | None:
    """Build a tokgain event raw record from a naturally invoked terminal command.

    The command is not re-run except for deterministic baseline construction for
    search reducers such as ast-grep. The observed stdout/stderr text itself is
    never stored, only token counts and a short hash.
    """
    argv = _safe_split(command)
    if not argv:
        return None
    exe = Path(argv[0]).name
    if exe == "h5i" or " h5i " in f" {command} ":
        return _build_h5i_observation(
            command=command,
            output=output,
            agent=agent,
            exit_code=exit_code,
            tokenizer=tokenizer,
            encoding=encoding,
            model=model,
            session_id=session_id,
            capture_mode=capture_mode,
        )
    if exe in {"ast-grep", "sg"}:
        return _build_ast_grep_observation(
            command=command,
            argv=argv,
            output=output,
            agent=agent,
            exit_code=exit_code,
            cwd=cwd,
            tokenizer=tokenizer,
            encoding=encoding,
            model=model,
            session_id=session_id,
            capture_mode=capture_mode,
        )
    return None


def build_mcp_observation_record(
    *,
    server_tool: str,
    mcp_tool_name: str,
    arguments: dict[str, Any],
    result_text: str,
    agent: str,
    base_path: str | Path | None,
    tokenizer: str = "auto",
    encoding: str = "o200k_base",
    model: str | None = None,
    session_id: str | None = None,
    capture_mode: str = "mcp_proxy",
) -> dict[str, Any] | None:
    if server_tool != "fff" and "fff" not in mcp_tool_name:
        return None
    fff_tool = _normalize_fff_tool_name(mcp_tool_name)
    query = str(arguments.get("query") or "").strip()
    if not query:
        return None
    max_results = int(arguments.get("maxResults") or arguments.get("max_results") or 20)
    root = Path(arguments.get("path") or base_path or ".").expanduser()
    baseline_cmd = _default_fff_baseline_cmd(query=query, path=root, fff_tool=fff_tool, max_results=max_results)
    baseline = read_command_source(baseline_cmd, cwd=None, timeout=30, label="baseline")
    optimized = SourceResult(text=result_text, ref=f"mcp:{mcp_tool_name}", kind="mcp")
    raw = build_benchmark_record_from_sources(
        tool="fff",
        layer="file-search-mcp",
        baseline=baseline,
        optimized=optimized,
        tokenizer=tokenizer,
        encoding=encoding,
        model=model,
        session_id=session_id,
        source_ref=f"{capture_mode}:fff:{mcp_tool_name}:{query}",
    )
    measurement = raw.setdefault("raw", {}).setdefault("measurement", {})
    measurement.update(
        {
            "capture_mode": capture_mode,
            "agent": agent,
            "baseline_strategy": "rg_or_find_from_fff_arguments",
            "mcp_tool_name": mcp_tool_name,
            "arguments": _safe_json_metadata(arguments),
            "optimized_sha256": _sha256(result_text),
        }
    )
    return raw


def extract_mcp_text_result(payload: dict[str, Any]) -> str:
    result = payload.get("result") or {}
    if isinstance(result, dict) and "result" in result and not result.get("content"):
        return json.dumps(result.get("result"), ensure_ascii=False, sort_keys=True)
    content = result.get("content") if isinstance(result, dict) else None
    if isinstance(content, list):
        chunks: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                chunks.append(str(item.get("text") or ""))
            else:
                chunks.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
        return "\n".join(chunks)
    if isinstance(result, dict):
        return json.dumps(result, ensure_ascii=False, sort_keys=True)
    return str(result)


def _build_h5i_observation(
    *,
    command: str,
    output: str,
    agent: str,
    exit_code: int | None,
    tokenizer: str,
    encoding: str,
    model: str | None,
    session_id: str | None,
    capture_mode: str,
) -> dict[str, Any] | None:
    match = _H5I_REDUCTION_RE.search(output)
    if match:
        baseline_tokens = int(match.group("before"))
        optimized_tokens = int(match.group("after"))
        mode = "h5i_reported"
    else:
        optimized_tokens, mode = count_tokens(output, tokenizer=tokenizer, encoding=encoding)
        baseline_tokens = optimized_tokens
    saved_tokens = baseline_tokens - optimized_tokens
    measurement = {
        "capture_mode": capture_mode,
        "agent": agent,
        "baseline_strategy": "h5i_reported_reduction" if match else "h5i_observed_output_only",
        "baseline_ref": "h5i reported pre-summary tokens" if match else "observed h5i output",
        "baseline_kind": "reported" if match else "observed",
        "baseline_tokens": baseline_tokens,
        "optimized_ref": "h5i summarized output",
        "optimized_kind": "terminal_output",
        "optimized_tokens": optimized_tokens,
        "saved_tokens": saved_tokens,
        "saved_pct": round(saved_tokens / baseline_tokens * 100, 4) if baseline_tokens else None,
        "token_count_mode": mode,
        "optimized_exit_code": exit_code,
        "optimized_bytes": len(output.encode("utf-8")),
        "optimized_sha256": _sha256(output),
        "command": _redact_command(command),
    }
    return {
        "tool": "h5i",
        "layer": "audit",
        "model": model,
        "saved_tokens": saved_tokens,
        "saved_input_tokens": saved_tokens,
        "saved_output_tokens": None,
        "source_ref": f"{capture_mode}:h5i:{_redact_command(command)}",
        "session_id": session_id,
        "raw": {"measurement": measurement},
    }


def _build_ast_grep_observation(
    *,
    command: str,
    argv: list[str],
    output: str,
    agent: str,
    exit_code: int | None,
    cwd: str | None,
    tokenizer: str,
    encoding: str,
    model: str | None,
    session_id: str | None,
    capture_mode: str,
) -> dict[str, Any] | None:
    pattern = _option_value(argv, "--pattern", "-p")
    if not pattern:
        return None
    search_root = _ast_grep_search_root(argv) or "."
    command_cwd = Path(cwd).expanduser() if cwd else Path.cwd()
    root = Path(search_root)
    if not root.is_absolute():
        root = command_cwd / root
    baseline_cmd = _fixed_string_grep_baseline_cmd(query=pattern, path=root, max_results=200)
    baseline = read_command_source(baseline_cmd, cwd=None, timeout=30, label="baseline")
    optimized = SourceResult(text=output, ref=_redact_command(command), kind="terminal_output", exit_code=exit_code)
    raw = build_benchmark_record_from_sources(
        tool="ast-grep",
        layer="ast-search",
        baseline=baseline,
        optimized=optimized,
        tokenizer=tokenizer,
        encoding=encoding,
        model=model,
        session_id=session_id,
        source_ref=f"{capture_mode}:ast-grep:{_redact_command(command)}",
    )
    measurement = raw.setdefault("raw", {}).setdefault("measurement", {})
    measurement.update(
        {
            "capture_mode": capture_mode,
            "agent": agent,
            "baseline_strategy": "rg_fixed_string_from_ast_grep_pattern",
            "optimized_sha256": _sha256(output),
            "command": _redact_command(command),
        }
    )
    return raw


def _normalize_fff_tool_name(name: str) -> str:
    lowered = name.lower()
    if "find" in lowered:
        return "find_files"
    return "grep"


def _safe_split(command: str) -> list[str]:
    try:
        return shlex.split(command)
    except ValueError:
        return []


def _option_value(argv: list[str], *names: str) -> str | None:
    for i, token in enumerate(argv):
        for name in names:
            if token == name and i + 1 < len(argv):
                return argv[i + 1]
            prefix = name + "="
            if token.startswith(prefix):
                return token[len(prefix) :]
    return None


def _ast_grep_search_root(argv: list[str]) -> str | None:
    skip_next = False
    option_takes_value = {"--pattern", "-p", "--lang", "-l", "--config", "-c", "--selector", "--rewrite"}
    positionals: list[str] = []
    for token in argv[1:]:
        if skip_next:
            skip_next = False
            continue
        if token in option_takes_value:
            skip_next = True
            continue
        if token.startswith("-"):
            continue
        if token in {"run", "scan", "test", "new", "lsp", "outline", "completions", "help"}:
            continue
        positionals.append(token)
    return positionals[-1] if positionals else None


def _redact_command(command: str) -> str:
    command = _ASSIGNMENT_SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", command)
    try:
        parts = shlex.split(command)
    except ValueError:
        return command
    redacted: list[str] = []
    redact_next = False
    for part in parts:
        if redact_next:
            redacted.append("[REDACTED]")
            redact_next = False
            continue
        if _SECRET_FLAG_RE.match(part):
            if "=" in part:
                redacted.append(part.split("=", 1)[0] + "=[REDACTED]")
            else:
                redacted.append(part)
                redact_next = True
            continue
        redacted.append(part)
    return " ".join(shlex.quote(p) for p in redacted)


def _safe_json_metadata(value: Any) -> Any:
    try:
        text = json.dumps(value, ensure_ascii=False)
        return json.loads(_ASSIGNMENT_SECRET_RE.sub(lambda m: f"{m.group(1)}=[REDACTED]", text))
    except Exception:
        return None


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()
