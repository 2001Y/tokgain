from __future__ import annotations

import json
import select
import shlex
import subprocess
import time
from pathlib import Path
from typing import Any

from .bench import BenchError, SourceResult, build_benchmark_record, build_benchmark_record_from_sources, read_command_source


class MeasureError(BenchError):
    pass


def build_h5i_measure_record(
    *,
    command: str,
    cwd: str | None = None,
    timeout: int = 30,
    tokenizer: str = "auto",
    encoding: str = "o200k_base",
    model: str | None = None,
    session_id: str | None = None,
    h5i_format: str = "compact",
    kind: str | None = None,
    budget: int | None = None,
    token_budget: int | None = None,
    min_bytes: int = 0,
    quiet: bool = False,
    source_ref: str | None = None,
) -> dict[str, Any]:
    optimized_cmd = _h5i_capture_command(
        command,
        h5i_format=h5i_format,
        kind=kind,
        budget=budget,
        token_budget=token_budget,
        min_bytes=min_bytes,
        quiet=quiet,
    )
    return build_benchmark_record(
        tool="h5i",
        layer="audit",
        baseline_cmd=command,
        optimized_cmd=optimized_cmd,
        cwd=cwd,
        timeout=timeout,
        tokenizer=tokenizer,
        encoding=encoding,
        model=model,
        session_id=session_id,
        source_ref=source_ref or f"h5i capture run:{command}",
    )


def build_fff_measure_record(
    *,
    query: str,
    path: str = ".",
    fff_tool: str = "grep",
    max_results: int = 20,
    baseline_cmd: str | None = None,
    output_mode: str | None = None,
    timeout: int = 30,
    startup_wait: float = 1.0,
    tokenizer: str = "auto",
    encoding: str = "o200k_base",
    model: str | None = None,
    session_id: str | None = None,
    source_ref: str | None = None,
) -> dict[str, Any]:
    base_path = Path(path).expanduser()
    if fff_tool not in {"grep", "find_files"}:
        raise MeasureError(f"unsupported fff tool: {fff_tool}")
    baseline = read_command_source(
        _default_fff_baseline_cmd(query=query, path=base_path, fff_tool=fff_tool, max_results=max_results)
        if baseline_cmd is None
        else baseline_cmd,
        cwd=None,
        timeout=timeout,
        label="baseline",
    )
    optimized_text = call_fff_mcp(
        base_path=base_path,
        tool_name=fff_tool,
        arguments=_fff_arguments(query=query, fff_tool=fff_tool, max_results=max_results, output_mode=output_mode),
        timeout=timeout,
        startup_wait=startup_wait,
    )
    optimized = SourceResult(text=optimized_text, ref=f"fff-mcp:{fff_tool}", kind="mcp")
    return build_benchmark_record_from_sources(
        tool="fff",
        layer="file-search-mcp",
        baseline=baseline,
        optimized=optimized,
        tokenizer=tokenizer,
        encoding=encoding,
        model=model,
        session_id=session_id,
        source_ref=source_ref or f"fff-mcp:{fff_tool}:{query}",
    )


def call_fff_mcp(*, base_path: Path, tool_name: str, arguments: dict[str, Any], timeout: int, startup_wait: float) -> str:
    proc = subprocess.Popen(
        ["fff-mcp", "--no-update-check", str(base_path)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    try:
        _mcp_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 1,
                "method": "initialize",
                "params": {
                    "protocolVersion": "2024-11-05",
                    "capabilities": {},
                    "clientInfo": {"name": "tokgain", "version": "0.1.0"},
                },
            },
        )
        init = _mcp_read(proc, request_id=1, timeout=timeout)
        if "error" in init:
            raise MeasureError(f"fff-mcp initialize failed: {init['error']}")
        _mcp_send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})
        if startup_wait > 0:
            time.sleep(startup_wait)
        _mcp_send(
            proc,
            {
                "jsonrpc": "2.0",
                "id": 2,
                "method": "tools/call",
                "params": {"name": tool_name, "arguments": arguments},
            },
        )
        response = _mcp_read(proc, request_id=2, timeout=timeout)
        if "error" in response:
            raise MeasureError(f"fff-mcp tool call failed: {response['error']}")
        result = response.get("result") or {}
        if result.get("isError"):
            raise MeasureError(f"fff-mcp tool returned error: {result}")
        return _mcp_content_to_text(result.get("content"))
    finally:
        try:
            proc.terminate()
            proc.wait(timeout=2)
        except Exception:
            proc.kill()


def _h5i_capture_command(
    command: str,
    *,
    h5i_format: str,
    kind: str | None,
    budget: int | None,
    token_budget: int | None,
    min_bytes: int,
    quiet: bool,
) -> str:
    parts = ["h5i", "capture", "run", "--format", h5i_format, "--min-bytes", str(min_bytes)]
    if kind:
        parts.extend(["--kind", kind])
    if budget is not None:
        parts.extend(["--budget", str(budget)])
    if token_budget is not None:
        parts.extend(["--token-budget", str(token_budget)])
    if quiet:
        parts.append("--quiet")
    parts.extend(["--", "sh", "-lc", command])
    return " ".join(shlex.quote(part) for part in parts)


def _fixed_string_grep_baseline_cmd(*, query: str, path: Path, max_results: int) -> str:
    quoted_path = shlex.quote(str(path))
    quoted_query = shlex.quote(query)
    max_n = int(max_results)
    fallback_code = (
        "import os,sys; q=sys.argv[1]; root=sys.argv[2]; limit=int(sys.argv[3]); n=0; seen=0; max_files=5000\n"
        "for base,dirs,files in os.walk(root):\n"
        "    dirs[:] = [d for d in dirs if d not in {'.git','node_modules','.venv','venv','__pycache__'}]\n"
        "    for name in files:\n"
        "        seen += 1\n"
        "        if seen > max_files: raise SystemExit\n"
        "        p=os.path.join(base,name)\n"
        "        try:\n"
        "            with open(p,'r',encoding='utf-8',errors='ignore') as fh:\n"
        "                for i,line in enumerate(fh,1):\n"
        "                    if q in line:\n"
        "                        print(f'{p}:{i}:1:{line.rstrip()}'); n+=1\n"
        "                        if n>=limit: raise SystemExit\n"
        "        except (OSError,UnicodeError):\n"
        "            pass"
    )
    return (
        "if command -v rg >/dev/null 2>&1; then "
        "rg --line-number --column --no-heading --color never --fixed-strings -- "
        f"{quoted_query} {quoted_path} | head -n {max_n}; "
        "else "
        f"python3 -c {shlex.quote(fallback_code)} {quoted_query} {quoted_path} {max_n}; "
        "fi"
    )


def _default_fff_baseline_cmd(*, query: str, path: Path, fff_tool: str, max_results: int) -> str:
    quoted_path = shlex.quote(str(path))
    quoted_query = shlex.quote(query)
    if fff_tool == "grep":
        return _fixed_string_grep_baseline_cmd(query=query, path=path, max_results=max_results)
    return f"find {quoted_path} -type f | grep -i -- {quoted_query} | head -n {int(max_results)}"


def _fff_arguments(*, query: str, fff_tool: str, max_results: int, output_mode: str | None) -> dict[str, Any]:
    args: dict[str, Any] = {"query": query, "maxResults": max_results}
    if fff_tool == "grep" and output_mode:
        args["output_mode"] = output_mode
    return args


def _mcp_send(proc: subprocess.Popen[str], message: dict[str, Any]) -> None:
    if proc.stdin is None:
        raise MeasureError("fff-mcp stdin unavailable")
    proc.stdin.write(json.dumps(message, separators=(",", ":")) + "\n")
    proc.stdin.flush()


def _mcp_read(proc: subprocess.Popen[str], *, request_id: int, timeout: int) -> dict[str, Any]:
    if proc.stdout is None:
        raise MeasureError("fff-mcp stdout unavailable")
    fd = proc.stdout.fileno()
    end = time.time() + timeout
    while time.time() < end:
        ready, _, _ = select.select([fd], [], [], 0.1)
        if not ready:
            if proc.poll() is not None:
                stderr = proc.stderr.read() if proc.stderr else ""
                raise MeasureError(f"fff-mcp exited before response: {stderr.strip()}")
            continue
        line = proc.stdout.readline()
        if not line:
            continue
        try:
            payload = json.loads(line)
        except json.JSONDecodeError:
            continue
        if payload.get("id") == request_id:
            return payload
    raise MeasureError(f"fff-mcp timed out waiting for response id {request_id}")


def _mcp_content_to_text(content: Any) -> str:
    if not isinstance(content, list):
        return json.dumps(content, ensure_ascii=False, sort_keys=True)
    chunks: list[str] = []
    for item in content:
        if isinstance(item, dict) and item.get("type") == "text":
            chunks.append(str(item.get("text") or ""))
        else:
            chunks.append(json.dumps(item, ensure_ascii=False, sort_keys=True))
    return "\n".join(chunks)
