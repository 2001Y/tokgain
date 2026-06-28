from __future__ import annotations

import json
import selectors
import subprocess
import sys
from pathlib import Path
from typing import Any, Callable

from .observe import build_mcp_observation_record, extract_mcp_text_result

RawRecordEmitter = Callable[[dict[str, Any]], None]


class ProxyError(RuntimeError):
    pass


def run_mcp_proxy(
    *,
    server_command: list[str],
    emit_record: RawRecordEmitter,
    agent: str,
    tool: str,
    base_path: str | Path | None = None,
    tokenizer: str = "auto",
    encoding: str = "o200k_base",
    model: str | None = None,
    session_id: str | None = None,
) -> int:
    if not server_command:
        raise ProxyError("tokgain-mcpproxy requires a server command after --")
    proc = subprocess.Popen(
        server_command,
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        bufsize=1,
    )
    pending: dict[Any, dict[str, Any]] = {}
    selector = selectors.DefaultSelector()
    selector.register(sys.stdin, selectors.EVENT_READ, "client")
    if proc.stdout is not None:
        selector.register(proc.stdout, selectors.EVENT_READ, "server_stdout")
    if proc.stderr is not None:
        selector.register(proc.stderr, selectors.EVENT_READ, "server_stderr")

    client_open = True
    try:
        while selector.get_map():
            if proc.poll() is not None and not client_open:
                # Drain any final buffered output before exiting.
                pass
            for key, _ in selector.select(timeout=0.1):
                label = key.data
                stream = key.fileobj
                line = stream.readline()
                if line == "":
                    try:
                        selector.unregister(stream)
                    except Exception:
                        pass
                    if label == "client":
                        client_open = False
                        if proc.stdin is not None:
                            try:
                                proc.stdin.close()
                            except Exception:
                                pass
                    continue
                if label == "client":
                    _remember_pending_tool_call(line, pending)
                    if proc.stdin is not None:
                        proc.stdin.write(line)
                        proc.stdin.flush()
                elif label == "server_stdout":
                    _observe_server_response(
                        line=line,
                        pending=pending,
                        emit_record=emit_record,
                        agent=agent,
                        tool=tool,
                        base_path=base_path,
                        tokenizer=tokenizer,
                        encoding=encoding,
                        model=model,
                        session_id=session_id,
                    )
                    sys.stdout.write(line)
                    sys.stdout.flush()
                elif label == "server_stderr":
                    sys.stderr.write(line)
                    sys.stderr.flush()
            if proc.poll() is not None and not client_open:
                # Once the server has exited and the client is closed, any
                # remaining registered fds will soon EOF; break if none left but
                # server streams. This keeps short-lived test servers from
                # hanging on an already-finished child.
                active_labels = {key.data for key in selector.get_map().values()}
                if active_labels <= {"server_stdout", "server_stderr"}:
                    # Continue one more nonblocking drain cycle; if no output
                    # arrives, close the streams and exit.
                    events = selector.select(timeout=0)
                    if not events:
                        break
        return proc.wait(timeout=2) if proc.poll() is None else int(proc.returncode or 0)
    finally:
        for key in list(selector.get_map().values()):
            try:
                selector.unregister(key.fileobj)
            except Exception:
                pass
        if proc.poll() is None:
            try:
                proc.terminate()
                proc.wait(timeout=2)
            except Exception:
                proc.kill()


def _remember_pending_tool_call(line: str, pending: dict[Any, dict[str, Any]]) -> None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return
    if payload.get("method") != "tools/call" or "id" not in payload:
        return
    params = payload.get("params") or {}
    if not isinstance(params, dict):
        return
    pending[payload["id"]] = {
        "name": params.get("name") or "",
        "arguments": params.get("arguments") if isinstance(params.get("arguments"), dict) else {},
    }


def _observe_server_response(
    *,
    line: str,
    pending: dict[Any, dict[str, Any]],
    emit_record: RawRecordEmitter,
    agent: str,
    tool: str,
    base_path: str | Path | None,
    tokenizer: str,
    encoding: str,
    model: str | None,
    session_id: str | None,
) -> None:
    try:
        payload = json.loads(line)
    except json.JSONDecodeError:
        return
    request_id = payload.get("id")
    if request_id not in pending or "result" not in payload:
        return
    request = pending.pop(request_id)
    try:
        raw = build_mcp_observation_record(
            server_tool=tool,
            mcp_tool_name=str(request.get("name") or ""),
            arguments=request.get("arguments") or {},
            result_text=extract_mcp_text_result(payload),
            agent=agent,
            base_path=base_path,
            tokenizer=tokenizer,
            encoding=encoding,
            model=model,
            session_id=session_id,
            capture_mode="mcp_proxy",
        )
        if raw is not None:
            emit_record(raw)
    except Exception as exc:
        # The proxy must be transparent: measurement failures go to stderr but
        # never corrupt the MCP response stream.
        print(f"tokgain-mcpproxy observe failed: {type(exc).__name__}: {exc}", file=sys.stderr, flush=True)
