import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(args, *, env=None, input_text=None):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(SRC)
    if env:
        merged_env.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        [sys.executable, "-m", "tokgain.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
        input=input_text,
        capture_output=True,
    )


def write_executable(path: Path, body: str) -> Path:
    path.write_text(body, encoding="utf-8")
    path.chmod(path.stat().st_mode | stat.S_IXUSR)
    return path


def read_jsonl(path: Path):
    return [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines() if line.strip()]


def write_prices(path: Path):
    path.write_text(
        json.dumps(
            {
                "version": "test-prices",
                "currency": "USD",
                "models": {
                    "gpt-test": {"input_per_1m": 1.0, "output_per_1m": 2.0},
                    "claude-test": {"input_per_1m": 3.0, "output_per_1m": 6.0},
                },
            }
        ),
        encoding="utf-8",
    )
    return path


def test_collect_rtk_writes_event_daily_and_state(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "rtk",
        """#!/usr/bin/env python3
import json
print(json.dumps({
  "sessions": [
    {"session_id": "s1", "model": "gpt-test", "saved_input_tokens": 800, "saved_output_tokens": 200, "source_ref": "fake-rtk"}
  ]
}))
""",
    )
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "collect",
            "--tool",
            "rtk",
            "--date",
            "2026-06-21",
        ],
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    events = read_jsonl(data_dir / "events.jsonl")
    assert len(events) == 1
    event = events[0]
    assert event["status"] == "ok"
    assert event["tool"] == "rtk"
    assert event["layer"] == "shell"
    assert event["model"] == "gpt-test"
    assert event["saved_tokens"] == 1000
    assert event["saved_input_tokens"] == 800
    assert event["saved_output_tokens"] == 200
    assert event["estimate_mode"] == "input_output"
    assert event["usd_saved_estimate"] == 0.0012
    assert event["price_table_version"] == "test-prices"

    daily = json.loads((data_dir / "daily" / "2026-06-21.json").read_text(encoding="utf-8"))
    assert daily["totals"]["reported_saved_tokens"] == 1000
    assert daily["totals"]["reported_saved_usd"] == 0.0012
    assert daily["by_tool"]["rtk"]["reported_saved_tokens"] == 1000

    state = json.loads((data_dir / "state.json").read_text(encoding="utf-8"))
    assert state["last_success_date"] == "2026-06-21"
    assert state["schema_version"] == 1


def test_cli_model_overrides_metadata_and_payload(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "rtk",
        """#!/usr/bin/env python3
import json
print(json.dumps({"saved_tokens": 1000, "saved_input_tokens": 1000, "model": "payload-model"}))
""",
    )
    prices = write_prices(tmp_path / "prices.json")
    metadata = tmp_path / "session.json"
    metadata.write_text(json.dumps({"model": "metadata-model", "session_id": "meta-session"}), encoding="utf-8")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "collect",
            "--tool",
            "rtk",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
            "--metadata",
            str(metadata),
        ],
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["model"] == "gpt-test"
    assert event["session_id"] == "meta-session"
    assert event["usd_saved_estimate"] == 0.001


def test_collect_fff_from_env_file(tmp_path):
    fff_file = tmp_path / "fff-savings.jsonl"
    fff_file.write_text(
        json.dumps({"model": "gpt-test", "saved_tokens": 321, "source_ref": "fff fixture"}) + "\n",
        encoding="utf-8",
    )
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "collect",
            "--tool",
            "fff",
            "--date",
            "2026-06-21",
        ],
        env={"TOKGAIN_FFF_FILE": fff_file},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["tool"] == "fff"
    assert event["layer"] == "file-search-mcp"
    assert event["saved_tokens"] == 321


def test_collect_fff_without_external_ledger_records_clear_error(tmp_path):
    data_dir = tmp_path / "state"
    result = run_cli([
        "--data-dir",
        str(data_dir),
        "collect",
        "--tool",
        "fff",
        "--date",
        "2026-06-21",
        "--model",
        "gpt-test",
    ], env={"TOKGAIN_FFF_FILE": tmp_path / "missing.jsonl"})

    assert result.returncode == 1
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "error"
    assert event["tool"] == "fff"
    assert "fff-mcp" in event["error"]
    assert "savings ledger" in event["error"]


def test_model_missing_event_is_incomplete_and_excluded_from_totals(tmp_path):
    headroom_file = tmp_path / "proxy_savings.json"
    headroom_file.write_text(json.dumps({"saved_tokens": 500, "saved_input_tokens": 500}), encoding="utf-8")
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "collect",
            "--tool",
            "headroom",
            "--date",
            "2026-06-21",
        ],
        env={"TOKGAIN_HEADROOM_FILE": headroom_file},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["model"] == "model_missing"
    assert event["incomplete"] is True
    assert event["exclude_from_totals"] is True
    daily = json.loads((data_dir / "daily" / "2026-06-21.json").read_text(encoding="utf-8"))
    assert daily["totals"]["reported_saved_tokens"] == 0
    assert daily["incomplete_events"] == 1


def test_explicit_missing_adapter_records_error_and_returns_nonzero(tmp_path):
    data_dir = tmp_path / "state"
    result = run_cli([
        "--data-dir",
        str(data_dir),
        "collect",
        "--tool",
        "h5i",
        "--date",
        "2026-06-21",
        "--model",
        "gpt-test",
    ], env={"PATH": str(tmp_path / "empty-bin")})

    assert result.returncode == 1
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "error"
    assert event["tool"] == "h5i"
    assert event["layer"] == "audit"
    assert "h5i capture run" in event["error"].lower()
    daily = json.loads((data_dir / "daily" / "2026-06-21.json").read_text(encoding="utf-8"))
    assert daily["errors"][0]["tool"] == "h5i"


def test_period_filter_that_removes_all_records_is_visible_error(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "rtk",
        """#!/usr/bin/env python3
import json
print(json.dumps({
  "sessions": [
    {"period": "2026-06-20", "saved_tokens": 123, "model": "gpt-test"}
  ]
}))
""",
    )
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "collect",
            "--tool",
            "rtk",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
        ],
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 1
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "error"
    assert event["tool"] == "rtk"
    assert event["layer"] == "shell"
    assert "no records for period 2026-06-21" in event["error"]


def test_bench_file_pair_writes_measured_savings_event(tmp_path):
    baseline = tmp_path / "baseline.txt"
    optimized = tmp_path / "optimized.txt"
    baseline.write_text("alpha beta gamma delta\n", encoding="utf-8")
    optimized.write_text("alpha delta\n", encoding="utf-8")
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "bench",
            "--tool",
            "rg",
            "--layer",
            "search-output",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
            "--baseline-file",
            str(baseline),
            "--optimized-file",
            str(optimized),
            "--tokenizer",
            "regex",
        ]
    )

    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout)
    assert summary["saved_tokens"] == 2
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "ok"
    assert event["tool"] == "rg"
    assert event["layer"] == "search-output"
    assert event["saved_tokens"] == 2
    assert event["saved_input_tokens"] == 2
    assert event["estimate_mode"] == "prompt_equivalent"
    assert event["usd_saved_estimate"] == 0.000002
    assert event["raw"]["measurement"]["baseline_tokens"] == 4
    assert event["raw"]["measurement"]["optimized_tokens"] == 2
    assert event["raw"]["measurement"]["token_count_mode"] == "regex_v1"
    daily = json.loads((data_dir / "daily" / "2026-06-21.json").read_text(encoding="utf-8"))
    assert daily["by_tool"]["rg"]["reported_saved_tokens"] == 2


def test_measure_h5i_runs_raw_and_h5i_capture_then_records_savings(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "h5i",
        """#!/usr/bin/env python3
import sys
assert sys.argv[1:4] == ['capture', 'run', '--format']
print('short summary')
""",
    )
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "measure",
            "h5i",
            "--cmd",
            "printf 'alpha beta gamma delta'",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
            "--tokenizer",
            "regex",
        ],
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout)
    assert summary["tool"] == "h5i"
    assert summary["baseline_tokens"] == 4
    assert summary["optimized_tokens"] == 2
    assert summary["saved_tokens"] == 2
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "ok"
    assert event["tool"] == "h5i"
    assert event["layer"] == "audit"
    assert event["saved_tokens"] == 2
    assert "h5i capture run" in event["source_ref"]


def test_measure_fff_calls_mcp_and_compares_rg_baseline(tmp_path):
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    write_executable(
        bin_dir / "fff-mcp",
        """#!/usr/bin/env python3
import json
import sys
for line in sys.stdin:
    msg = json.loads(line)
    if msg.get('method') == 'initialize':
        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'protocolVersion': '2024-11-05', 'capabilities': {'tools': {}}, 'serverInfo': {'name': 'fff', 'version': 'test'}}}), flush=True)
    elif msg.get('method') == 'tools/call':
        args = msg['params']['arguments']
        assert msg['params']['name'] == 'grep'
        assert args['query'] == 'needle'
        print(json.dumps({'jsonrpc': '2.0', 'id': msg['id'], 'result': {'content': [{'type': 'text', 'text': 'src/a.py:1:needle'}], 'isError': False}}), flush=True)
""",
    )
    repo = tmp_path / "repo"
    repo.mkdir()
    (repo / "a.py").write_text("needle one\nneedle two\nneedle three\n", encoding="utf-8")
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"

    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "--prices",
            str(prices),
            "measure",
            "fff",
            "--path",
            str(repo),
            "--query",
            "needle",
            "--max-results",
            "3",
            "--startup-wait",
            "0",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
            "--tokenizer",
            "regex",
        ],
        env={"PATH": f"{bin_dir}:{os.environ['PATH']}"},
    )

    assert result.returncode == 0, result.stderr + result.stdout
    summary = json.loads(result.stdout)
    assert summary["tool"] == "fff"
    assert summary["saved_tokens"] > 0
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "ok"
    assert event["tool"] == "fff"
    assert event["layer"] == "file-search-mcp"
    measurement = event["raw"]["measurement"]
    assert measurement["baseline_kind"] == "command"
    assert measurement["optimized_kind"] == "mcp"
    assert measurement["optimized_ref"] == "fff-mcp:grep"



def test_bench_command_failure_records_error_event(tmp_path):
    data_dir = tmp_path / "state"
    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "bench",
            "--tool",
            "ast-grep",
            "--layer",
            "ast-search",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
            "--baseline-cmd",
            "printf ok",
            "--optimized-cmd",
            "python3 -c 'import sys; sys.exit(7)'",
            "--tokenizer",
            "regex",
        ]
    )

    assert result.returncode == 1
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "error"
    assert event["tool"] == "ast-grep"
    assert event["layer"] == "ast-search"
    assert "optimized command failed" in event["error"]


def test_bench_timeout_records_wrapped_error_event(tmp_path):
    data_dir = tmp_path / "state"
    result = run_cli(
        [
            "--data-dir",
            str(data_dir),
            "bench",
            "--tool",
            "contextmode",
            "--layer",
            "agent-context",
            "--date",
            "2026-06-21",
            "--model",
            "gpt-test",
            "--baseline-cmd",
            "printf ok",
            "--optimized-cmd",
            "python3 -c 'import time; time.sleep(2)'",
            "--timeout",
            "1",
            "--tokenizer",
            "regex",
        ]
    )

    assert result.returncode == 1
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["status"] == "error"
    assert event["tool"] == "contextmode"
    assert event["layer"] == "agent-context"
    assert "timed out" in event["error"]


def test_show_accepts_benchmark_only_tool_names(tmp_path):
    data_dir = tmp_path / "state"
    (data_dir / "daily").mkdir(parents=True)
    event = {
        "schema_version": 1,
        "status": "ok",
        "ts": "2026-06-22T00:00:00+09:00",
        "period": "2026-06-21",
        "tool": "contextmode",
        "layer": "agent-context",
        "model": "gpt-test",
        "saved_tokens": 10,
        "saved_input_tokens": 10,
        "saved_output_tokens": None,
        "estimate_mode": "prompt_equivalent",
        "usd_saved_estimate": 0.00001,
        "price_table_version": "test-prices",
        "source_ref": "manual",
        "session_id": "s1",
        "incomplete": False,
        "exclude_from_totals": False,
    }
    (data_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")

    show = run_cli(["--data-dir", str(data_dir), "show", "--tool", "contextmode", "--limit", "1"])

    assert show.returncode == 0, show.stderr + show.stdout
    assert json.loads(show.stdout)["tool"] == "contextmode"


def test_report_show_export_and_prices_commands(tmp_path):
    data_dir = tmp_path / "state"
    (data_dir / "daily").mkdir(parents=True)
    event = {
        "schema_version": 1,
        "status": "ok",
        "ts": "2026-06-22T00:00:00+09:00",
        "period": "2026-06-21",
        "tool": "rtk",
        "layer": "shell",
        "model": "gpt-test",
        "saved_tokens": 100,
        "saved_input_tokens": 100,
        "saved_output_tokens": None,
        "estimate_mode": "prompt_equivalent",
        "usd_saved_estimate": 0.0001,
        "price_table_version": "test-prices",
        "source_ref": "manual",
        "session_id": "s1",
        "incomplete": False,
        "exclude_from_totals": False,
    }
    (data_dir / "events.jsonl").write_text(json.dumps(event) + "\n", encoding="utf-8")
    prices = write_prices(tmp_path / "prices.json")

    report = run_cli(["--data-dir", str(data_dir), "report", "--period", "day", "--date", "2026-06-21", "--json"])
    assert report.returncode == 0, report.stderr + report.stdout
    payload = json.loads(report.stdout)
    assert payload["totals"]["reported_saved_tokens"] == 100

    show = run_cli(["--data-dir", str(data_dir), "show", "--tool", "rtk", "--limit", "1"])
    assert show.returncode == 0, show.stderr + show.stdout
    assert json.loads(show.stdout)["tool"] == "rtk"

    export = run_cli(["--data-dir", str(data_dir), "export", "--format", "jsonl"])
    assert export.returncode == 0
    assert export.stdout.strip().startswith("{")

    prices_result = run_cli(["--prices", str(prices), "prices", "show"])
    assert prices_result.returncode == 0
    assert json.loads(prices_result.stdout)["version"] == "test-prices"


def test_observe_h5i_terminal_hook_parses_reduction_from_stdin(tmp_path):
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"
    h5i_output = """{
  "schema_version": 1,
  "tool": "pytest",
  "kind": "test",
  "status": "ok",
  "body": "1 passed"
}

▢ h5i object abc123 · tool-output · 141 bytes · 50 lines · ~90% fewer tokens (100→10)
"""

    result = run_cli(
        [
            "--data-dir", str(data_dir),
            "--prices", str(prices),
            "observe", "terminal",
            "--agent", "hermes",
            "--command", "h5i capture run --format json -- pytest -q",
            "--exit-code", "0",
            "--date", "2026-06-21",
            "--model", "gpt-test",
            "--session-id", "hermes-session",
        ],
        input_text=h5i_output,
    )

    assert result.returncode == 0, result.stderr + result.stdout
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["tool"] == "h5i"
    assert event["layer"] == "audit"
    assert event["saved_tokens"] == 90
    assert event["session_id"] == "hermes-session"
    measurement = event["raw"]["measurement"]
    assert measurement["capture_mode"] == "hermes_hook"
    assert measurement["baseline_tokens"] == 100
    assert measurement["optimized_tokens"] == 10
    assert measurement["agent"] == "hermes"


def test_observe_ast_grep_terminal_hook_compares_rg_baseline(tmp_path):
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"
    work = tmp_path / "repo"
    work.mkdir()
    (work / "a.py").write_text("needle\n" * 20, encoding="utf-8")

    result = run_cli(
        [
            "--data-dir", str(data_dir),
            "--prices", str(prices),
            "observe", "terminal",
            "--agent", "hermes",
            "--command", "ast-grep run --pattern needle --lang python .",
            "--cwd", str(work),
            "--exit-code", "0",
            "--date", "2026-06-21",
            "--model", "gpt-test",
        ],
        input_text="a.py:1:needle\n",
    )

    assert result.returncode == 0, result.stderr + result.stdout
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["tool"] == "ast-grep"
    assert event["layer"] == "ast-search"
    measurement = event["raw"]["measurement"]
    assert measurement["capture_mode"] == "hermes_hook"
    assert measurement["baseline_strategy"] == "rg_fixed_string_from_ast_grep_pattern"
    assert measurement["baseline_tokens"] > measurement["optimized_tokens"]
    assert event["saved_tokens"] == measurement["baseline_tokens"] - measurement["optimized_tokens"]


def test_mcp_proxy_passes_through_fff_and_records_event(tmp_path):
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"
    work = tmp_path / "repo"
    work.mkdir()
    (work / "a.txt").write_text("needle\n" * 30, encoding="utf-8")
    fake_server = write_executable(
        tmp_path / "fake_fff_server.py",
        """#!/usr/bin/env python3
import json
import sys

for line in sys.stdin:
    msg = json.loads(line)
    if "id" not in msg:
        continue
    if msg.get("method") == "initialize":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"capabilities": {}}}), flush=True)
    elif msg.get("method") == "tools/call":
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {"content": [{"type": "text", "text": "a.txt:1:needle\\n"}]}}), flush=True)
    else:
        print(json.dumps({"jsonrpc": "2.0", "id": msg["id"], "result": {}}), flush=True)
""",
    )
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SRC)
    input_text = "\n".join(
        [
            json.dumps({"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}),
            json.dumps({"jsonrpc": "2.0", "id": 2, "method": "tools/call", "params": {"name": "grep", "arguments": {"query": "needle", "maxResults": 20}}}),
        ]
    ) + "\n"
    result = subprocess.run(
        [
            sys.executable, "-m", "tokgain.cli",
            "--data-dir", str(data_dir),
            "--prices", str(prices),
            "mcp-proxy",
            "--agent", "codex",
            "--tool", "fff",
            "--base-path", str(work),
            "--date", "2026-06-21",
            "--model", "gpt-test",
            "--", str(fake_server),
        ],
        cwd=ROOT,
        env=env,
        text=True,
        input=input_text,
        capture_output=True,
        timeout=5,
    )
    assert result.returncode == 0, result.stderr + result.stdout
    responses = [json.loads(line) for line in result.stdout.splitlines() if line.strip()]
    assert responses[0]["id"] == 1
    assert responses[1]["id"] == 2

    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["tool"] == "fff"
    assert event["layer"] == "file-search-mcp"
    measurement = event["raw"]["measurement"]
    assert measurement["capture_mode"] == "mcp_proxy"
    assert measurement["agent"] == "codex"
    assert measurement["baseline_tokens"] > measurement["optimized_tokens"]


def test_observe_mcp_call_records_fff_without_proxy(tmp_path):
    prices = write_prices(tmp_path / "prices.json")
    data_dir = tmp_path / "state"
    work = tmp_path / "repo"
    work.mkdir()
    (work / "a.txt").write_text("needle\n" * 25, encoding="utf-8")
    payload = {
        "tool_name": "mcp_fff_grep",
        "arguments": {"query": "needle", "maxResults": 20},
        "result_text": "a.txt:1:needle\n",
    }

    result = run_cli(
        [
            "--data-dir", str(data_dir),
            "--prices", str(prices),
            "observe", "mcp-call",
            "--agent", "hermes",
            "--server-tool", "fff",
            "--base-path", str(work),
            "--date", "2026-06-21",
            "--model", "gpt-test",
            "--session-id", "hermes-session",
        ],
        input_text=json.dumps(payload),
    )

    assert result.returncode == 0, result.stderr + result.stdout
    event = read_jsonl(data_dir / "events.jsonl")[0]
    assert event["tool"] == "fff"
    assert event["session_id"] == "hermes-session"
    measurement = event["raw"]["measurement"]
    assert measurement["capture_mode"] == "hermes_hook"
    assert measurement["agent"] == "hermes"
    assert measurement["baseline_tokens"] > measurement["optimized_tokens"]
