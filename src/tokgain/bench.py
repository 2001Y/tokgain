from __future__ import annotations

import re
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any


class BenchError(RuntimeError):
    pass


_TOKEN_RE = re.compile(r"\w+|[^\w\s]", re.UNICODE)


@dataclass(frozen=True)
class SourceResult:
    text: str
    ref: str
    kind: str
    exit_code: int | None = None
    stderr: str | None = None


def build_benchmark_record(
    *,
    tool: str,
    layer: str,
    baseline_file: str | None = None,
    optimized_file: str | None = None,
    baseline_cmd: str | None = None,
    optimized_cmd: str | None = None,
    cwd: str | None = None,
    timeout: int = 30,
    tokenizer: str = "auto",
    encoding: str = "o200k_base",
    source_ref: str | None = None,
    model: str | None = None,
    session_id: str | None = None,
) -> dict[str, Any]:
    baseline = _read_source(
        label="baseline",
        file_path=baseline_file,
        command=baseline_cmd,
        cwd=cwd,
        timeout=timeout,
    )
    optimized = _read_source(
        label="optimized",
        file_path=optimized_file,
        command=optimized_cmd,
        cwd=cwd,
        timeout=timeout,
    )
    baseline_tokens, mode = count_tokens(baseline.text, tokenizer=tokenizer, encoding=encoding)
    optimized_tokens, optimized_mode = count_tokens(optimized.text, tokenizer=tokenizer, encoding=encoding)
    if optimized_mode != mode:
        raise BenchError(f"tokenizer mode changed during benchmark: {mode} -> {optimized_mode}")
    saved_tokens = baseline_tokens - optimized_tokens
    measurement = {
        "baseline_ref": baseline.ref,
        "baseline_kind": baseline.kind,
        "baseline_tokens": baseline_tokens,
        "baseline_bytes": len(baseline.text.encode("utf-8")),
        "baseline_exit_code": baseline.exit_code,
        "optimized_ref": optimized.ref,
        "optimized_kind": optimized.kind,
        "optimized_tokens": optimized_tokens,
        "optimized_bytes": len(optimized.text.encode("utf-8")),
        "optimized_exit_code": optimized.exit_code,
        "saved_tokens": saved_tokens,
        "saved_pct": _saved_pct(baseline_tokens, saved_tokens),
        "token_count_mode": mode,
    }
    return {
        "tool": tool,
        "layer": layer,
        "model": model,
        "saved_tokens": saved_tokens,
        "saved_input_tokens": saved_tokens,
        "saved_output_tokens": None,
        "source_ref": source_ref or f"bench:{baseline.ref}->{optimized.ref}",
        "session_id": session_id,
        "raw": {"measurement": measurement},
    }


def count_tokens(text: str, *, tokenizer: str = "auto", encoding: str = "o200k_base") -> tuple[int, str]:
    if tokenizer not in {"auto", "regex", "tiktoken"}:
        raise BenchError(f"unknown tokenizer: {tokenizer}")
    if tokenizer in {"auto", "tiktoken"}:
        try:
            import tiktoken  # type: ignore[import-not-found]
        except ModuleNotFoundError:
            if tokenizer == "tiktoken":
                raise BenchError("tiktoken tokenizer requested but tiktoken is not installed")
        else:
            enc = tiktoken.get_encoding(encoding)
            return len(enc.encode(text)), f"tiktoken:{encoding}"
    return len(_TOKEN_RE.findall(text)), "regex_v1"


def _read_source(
    *,
    label: str,
    file_path: str | None,
    command: str | None,
    cwd: str | None,
    timeout: int,
) -> SourceResult:
    if bool(file_path) == bool(command):
        raise BenchError(f"provide exactly one of --{label}-file or --{label}-cmd")
    if file_path:
        path = Path(file_path).expanduser()
        if not path.exists():
            raise BenchError(f"{label} file not found: {path}")
        try:
            text = path.read_text(encoding="utf-8")
        except OSError as exc:
            raise BenchError(f"{label} file read failed: {path}: {exc}") from exc
        return SourceResult(text=text, ref=str(path), kind="file")
    assert command is not None
    try:
        proc = subprocess.run(
            command,
            shell=True,
            cwd=str(Path(cwd).expanduser()) if cwd else None,
            text=True,
            capture_output=True,
            timeout=timeout,
        )
    except subprocess.TimeoutExpired as exc:
        raise BenchError(f"{label} command timed out after {timeout}s: {command}") from exc
    text = proc.stdout + proc.stderr
    if proc.returncode != 0:
        stderr = proc.stderr.strip() or proc.stdout.strip()
        raise BenchError(f"{label} command failed ({proc.returncode}): {command}: {stderr}")
    return SourceResult(text=text, ref=command, kind="command", exit_code=proc.returncode, stderr=proc.stderr)


def _saved_pct(baseline_tokens: int, saved_tokens: int) -> float | None:
    if baseline_tokens == 0:
        return None
    return round(saved_tokens / baseline_tokens * 100, 4)
