import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SRC = ROOT / "src"
sys.path.insert(0, str(SRC))


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


def test_litellm_and_models_dev_prices_are_normalized_like_ccusage():
    from tokgain.pricing import normalize_price_sources

    litellm = {
        "gpt-test": {
            "input_cost_per_token": 1.0e-6,
            "output_cost_per_token": 2.0e-6,
            "cache_creation_input_token_cost": 1.25e-6,
            "cache_read_input_token_cost": 0.1e-6,
            "max_input_tokens": 123456,
        },
        "compact-test": {"i": 3.0e-6, "o": 4.0e-6, "cc": 3.75e-6, "cr": 0.3e-6, "ctx": 99},
    }
    models_dev = {
        "anthropic": {
            "models": {
                "claude-test": {
                    "cost": {"input": 5.0, "output": 25.0, "cache_read": 0.5, "cache_write": 6.25},
                    "limit": {"context": 200000},
                },
                # Existing LiteLLM key must not be overwritten by models.dev fallback.
                "gpt-test": {"cost": {"input": 999.0, "output": 999.0}},
            }
        }
    }

    table = normalize_price_sources(litellm_json=json.dumps(litellm), models_dev_json=json.dumps(models_dev), version="unit")

    assert table["version"] == "unit"
    assert table["source"] == "litellm+models.dev"
    assert table["models"]["gpt-test"]["input_per_1m"] == 1.0
    assert table["models"]["gpt-test"]["output_per_1m"] == 2.0
    assert table["models"]["gpt-test"]["cache_create_per_1m"] == 1.25
    assert table["models"]["gpt-test"]["cache_read_per_1m"] == 0.1
    assert table["models"]["gpt-test"]["context_window"] == 123456
    assert table["models"]["compact-test"]["input_per_1m"] == 3.0
    assert table["models"]["claude-test"]["input_per_1m"] == 5.0
    assert table["models"]["claude-test"]["output_per_1m"] == 25.0


def test_price_estimate_uses_cache_token_rates_when_present():
    from tokgain.pricing import estimate_usd

    prices = {
        "version": "unit",
        "currency": "USD",
        "models": {
            "gpt-test": {
                "input_per_1m": 1.0,
                "output_per_1m": 2.0,
                "cache_create_per_1m": 1.25,
                "cache_read_per_1m": 0.1,
            }
        },
    }
    event = {
        "model": "gpt-test",
        "saved_input_tokens": 1_000_000,
        "saved_output_tokens": 500_000,
        "saved_cache_creation_tokens": 100_000,
        "saved_cache_read_tokens": 200_000,
    }

    usd, version, mode, missing = estimate_usd(event, prices)

    assert version == "unit"
    assert mode == "input_output_cache"
    assert missing is False
    assert usd == 2.145


def test_price_estimate_resolves_codex_aliases_like_ccusage():
    from tokgain.pricing import estimate_usd

    prices = {
        "version": "unit",
        "currency": "USD",
        "models": {
            "gpt-5.3-codex-spark": {"input_per_1m": 10.0, "output_per_1m": 20.0},
        },
    }

    usd, _, _, missing = estimate_usd(
        {"model": "gpt-5.3-spark", "saved_input_tokens": 100_000, "saved_output_tokens": 0},
        prices,
    )

    assert missing is False
    assert usd == 1.0


def test_prices_show_reads_offline_cache(tmp_path):
    cache = tmp_path / "prices-cache.json"
    cache.write_text(
        json.dumps(
            {
                "version": "cached-unit",
                "currency": "USD",
                "source": "litellm+models.dev",
                "models": {"gpt-test": {"input_per_1m": 1.0, "output_per_1m": 2.0}},
            }
        ),
        encoding="utf-8",
    )

    result = run_cli(["--offline-prices", "--price-cache", str(cache), "prices", "show"])

    assert result.returncode == 0, result.stderr + result.stdout
    payload = json.loads(result.stdout)
    assert payload["version"] == "cached-unit"
    assert payload["models"]["gpt-test"]["input_per_1m"] == 1.0
