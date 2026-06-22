import json
import os
import stat
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"


def run_cli(args, *, env=None):
    merged_env = os.environ.copy()
    merged_env["PYTHONPATH"] = str(SRC)
    if env:
        merged_env.update({k: str(v) for k, v in env.items()})
    return subprocess.run(
        [sys.executable, "-m", "tokgain.cli", *args],
        cwd=ROOT,
        env=merged_env,
        text=True,
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
    assert "h5i capture run" in event["error"].lower()
    daily = json.loads((data_dir / "daily" / "2026-06-21.json").read_text(encoding="utf-8"))
    assert daily["errors"][0]["tool"] == "h5i"


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
