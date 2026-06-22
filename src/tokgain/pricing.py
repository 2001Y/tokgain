from __future__ import annotations

import json
import urllib.request
from datetime import datetime, timezone
from importlib import resources
from pathlib import Path
from typing import Any

PRICE_TABLE_RESOURCE = "data/prices.json"
LITELLM_PRICING_URL = "https://raw.githubusercontent.com/BerriAI/litellm/main/model_prices_and_context_window.json"
MODELS_DEV_API_URL = "https://models.dev/api.json"
DEFAULT_PRICE_CACHE = Path("~/.cache/tokgain/prices.json").expanduser()
FETCH_TIMEOUT_SECONDS = 10
FETCH_MAX_BYTES = 64 * 1024 * 1024


def load_prices(
    path: str | Path | None = None,
    *,
    offline: bool = False,
    cache_path: str | Path | None = None,
    refresh: bool = True,
) -> dict[str, Any]:
    """Load a normalized price table.

    Mirrors ccusage's source order at a small scale:
    explicit override file > live LiteLLM table + models.dev fallback > cached table > packaged fallback.
    """
    if path:
        return _normalize_user_table(_read_json(Path(path).expanduser()))

    cache = Path(cache_path).expanduser() if cache_path else DEFAULT_PRICE_CACHE
    if not offline and refresh:
        try:
            table = refresh_prices()
            _write_cache(cache, table)
            return table
        except OSError:
            # Collection must not fail just because live pricing is unavailable.
            pass

    if cache.exists():
        return _normalize_user_table(_read_json(cache))
    return _normalize_user_table(_load_packaged_prices())


def refresh_prices(*, fetcher: Any | None = None, version: str | None = None) -> dict[str, Any]:
    fetch = fetcher or fetch_json_url
    litellm_json = fetch(LITELLM_PRICING_URL)
    try:
        models_dev_json = fetch(MODELS_DEV_API_URL)
    except OSError:
        models_dev_json = None
    return normalize_price_sources(
        litellm_json=litellm_json,
        models_dev_json=models_dev_json,
        version=version or f"litellm+models.dev:{_today_utc()}",
    )


def normalize_price_sources(
    *,
    litellm_json: str,
    models_dev_json: str | None = None,
    version: str,
) -> dict[str, Any]:
    models: dict[str, dict[str, Any]] = {}
    models.update(_parse_litellm_prices(json.loads(litellm_json)))
    if models_dev_json:
        for model, rate in _parse_models_dev_prices(json.loads(models_dev_json)).items():
            models.setdefault(model, rate)
    return {
        "version": version,
        "currency": "USD",
        "source": "litellm+models.dev" if models_dev_json else "litellm",
        "source_urls": [LITELLM_PRICING_URL, MODELS_DEV_API_URL] if models_dev_json else [LITELLM_PRICING_URL],
        "models": dict(sorted(models.items())),
    }


def fetch_json_url(url: str) -> str:
    request = urllib.request.Request(url, headers={"User-Agent": "tokgain/0.1"})
    with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT_SECONDS) as response:
        if getattr(response, "status", 200) != 200:
            raise OSError(f"HTTP {response.status}")
        data = response.read(FETCH_MAX_BYTES + 1)
    if len(data) > FETCH_MAX_BYTES:
        raise OSError("pricing response too large")
    return data.decode("utf-8")


def estimate_usd(event: dict[str, Any], prices: dict[str, Any]) -> tuple[float, str, str, bool]:
    """Return (usd, price_table_version, estimate_mode, price_missing)."""
    version = str(prices.get("version", "unknown"))
    model = event.get("model")
    models = prices.get("models") or {}
    rate = _find_model_rate(models, str(model)) if model else {}
    price_missing = not bool(rate)

    input_rate = _float_or_zero(rate.get("input_per_1m"))
    output_rate = _float_or_zero(rate.get("output_per_1m"))
    cache_create_rate = _float_or_zero(rate.get("cache_create_per_1m"))
    cache_read_rate = _float_or_zero(rate.get("cache_read_per_1m"))

    saved_input = _int_or_none(event.get("saved_input_tokens"))
    saved_output = _int_or_none(event.get("saved_output_tokens"))
    saved_cache_create = _int_or_none(event.get("saved_cache_creation_tokens"))
    saved_cache_read = _int_or_none(event.get("saved_cache_read_tokens"))
    saved_total = _int_or_none(event.get("saved_tokens")) or 0

    usd = 0.0
    if saved_input is not None and saved_output is not None:
        estimate_mode = "input_output"
        usd += saved_input / 1_000_000 * input_rate
        usd += saved_output / 1_000_000 * output_rate
    elif saved_input is not None:
        estimate_mode = "prompt_equivalent"
        usd += saved_input / 1_000_000 * input_rate
    else:
        estimate_mode = "prompt_equivalent"
        usd += saved_total / 1_000_000 * input_rate

    if saved_cache_create is not None or saved_cache_read is not None:
        estimate_mode = "input_output_cache" if estimate_mode == "input_output" else "prompt_equivalent_cache"
        usd += (saved_cache_create or 0) / 1_000_000 * cache_create_rate
        usd += (saved_cache_read or 0) / 1_000_000 * cache_read_rate

    return (round(usd, 10), version, estimate_mode, price_missing)


def _parse_litellm_prices(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    models: dict[str, dict[str, Any]] = {}
    for model, entry in raw.items():
        if not isinstance(entry, dict):
            continue
        input_cost = entry.get("input_cost_per_token", entry.get("i"))
        output_cost = entry.get("output_cost_per_token", entry.get("o"))
        if input_cost is None or output_cost is None:
            continue
        rate: dict[str, Any] = {
            "input_per_1m": round(float(input_cost) * 1_000_000, 10),
            "output_per_1m": round(float(output_cost) * 1_000_000, 10),
            "source": "litellm",
        }
        _copy_litellm_optional_rate(entry, rate, "cache_creation_input_token_cost", "cc", "cache_create_per_1m")
        _copy_litellm_optional_rate(entry, rate, "cache_read_input_token_cost", "cr", "cache_read_per_1m")
        _copy_litellm_optional_rate(entry, rate, "input_cost_per_token_above_200k_tokens", "ia", "input_above_200k_per_1m")
        _copy_litellm_optional_rate(entry, rate, "output_cost_per_token_above_200k_tokens", "oa", "output_above_200k_per_1m")
        _copy_litellm_optional_rate(entry, rate, "cache_creation_input_token_cost_above_200k_tokens", "cca", "cache_create_above_200k_per_1m")
        _copy_litellm_optional_rate(entry, rate, "cache_read_input_token_cost_above_200k_tokens", "cra", "cache_read_above_200k_per_1m")
        context = entry.get("max_input_tokens", entry.get("ctx"))
        context_int = _int_or_none(context)
        if context_int is not None:
            rate["context_window"] = context_int
        models[str(model)] = rate
    return models


def _parse_models_dev_prices(raw: Any) -> dict[str, dict[str, Any]]:
    if not isinstance(raw, dict):
        return {}
    if any(isinstance(value, dict) and isinstance(value.get("models"), dict) for value in raw.values()):
        entries: dict[str, Any] = {}
        for provider in raw.values():
            if isinstance(provider, dict) and isinstance(provider.get("models"), dict):
                entries.update(provider["models"])
    else:
        entries = raw

    models: dict[str, dict[str, Any]] = {}
    for model_key, entry in entries.items():
        if not isinstance(entry, dict):
            continue
        cost = entry.get("cost")
        if not isinstance(cost, dict) or cost.get("input") is None or cost.get("output") is None:
            continue
        model = str(entry.get("id") or model_key)
        rate: dict[str, Any] = {
            "input_per_1m": float(cost["input"]),
            "output_per_1m": float(cost["output"]),
            "source": "models.dev",
        }
        if cost.get("cache_write") is not None:
            rate["cache_create_per_1m"] = float(cost["cache_write"])
        if cost.get("cache_read") is not None:
            rate["cache_read_per_1m"] = float(cost["cache_read"])
        limit = entry.get("limit")
        if isinstance(limit, dict) and limit.get("context") is not None:
            rate["context_window"] = int(limit["context"])
        models[model] = rate
    return models


def _normalize_user_table(table: dict[str, Any]) -> dict[str, Any]:
    # Already normalized / legacy tokgain table. Preserve user-controlled versions.
    normalized = dict(table)
    normalized.setdefault("currency", "USD")
    normalized.setdefault("version", "manual")
    normalized.setdefault("models", {})
    fixed_models: dict[str, dict[str, Any]] = {}
    for model, rate in (normalized.get("models") or {}).items():
        if not isinstance(rate, dict):
            continue
        fixed = dict(rate)
        if "input_per_1m" not in fixed and "input_cost_per_token" in fixed:
            fixed["input_per_1m"] = float(fixed["input_cost_per_token"]) * 1_000_000
        if "output_per_1m" not in fixed and "output_cost_per_token" in fixed:
            fixed["output_per_1m"] = float(fixed["output_cost_per_token"]) * 1_000_000
        fixed_models[str(model)] = fixed
    normalized["models"] = fixed_models
    return normalized


def _copy_litellm_optional_rate(entry: dict[str, Any], rate: dict[str, Any], full_key: str, compact_key: str, out_key: str) -> None:
    value = entry.get(full_key, entry.get(compact_key))
    if value is not None:
        rate[out_key] = round(float(value) * 1_000_000, 10)


def _load_packaged_prices() -> dict[str, Any]:
    with resources.files("tokgain").joinpath(PRICE_TABLE_RESOURCE).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _read_json(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as fh:
        return json.load(fh)


def _write_cache(path: Path, table: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(table, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _find_model_rate(models: dict[str, Any], model: str) -> dict[str, Any]:
    for candidate in _pricing_candidates(model):
        rate = models.get(candidate)
        if isinstance(rate, dict):
            return rate
    normalized = _normalized_pricing_key(model)
    matches = [
        (key, rate)
        for key, rate in models.items()
        if isinstance(rate, dict) and _pricing_key_matches(str(key), model, normalized)
    ]
    if not matches:
        return {}
    key, rate = max(matches, key=lambda item: (len(str(item[0])), str(item[0])))
    return rate


def _pricing_candidates(model: str) -> list[str]:
    candidates = [model]
    alias = _pricing_alias(model)
    if alias:
        candidates.append(alias)
    normalized = _normalized_pricing_key(model)
    if normalized != model:
        candidates.append(normalized)
    return candidates


def _pricing_alias(model: str) -> str | None:
    # ccusage maps this Codex log label to the canonical pricing key.
    if model == "gpt-5.3-spark":
        return "gpt-5.3-codex-spark"
    return None


def _pricing_key_matches(candidate: str, model: str, normalized_model: str) -> bool:
    normalized_candidate = _normalized_pricing_key(candidate)
    return (
        _contains_pricing_key(model, candidate)
        or _contains_pricing_key(candidate, model)
        or _contains_pricing_key(normalized_model, normalized_candidate)
        or _contains_pricing_key(normalized_candidate, normalized_model)
    )


def _contains_pricing_key(value: str, key: str) -> bool:
    if not key:
        return False
    start = 0
    while True:
        index = value.find(key, start)
        if index < 0:
            return False
        before = value[index - 1] if index > 0 else ""
        suffix = value[index + len(key) :]
        if (not before or not before.isalnum()) and _suffix_allows_pricing_key_match(key, suffix):
            return True
        start = index + 1


def _suffix_allows_pricing_key_match(key: str, suffix: str) -> bool:
    if not suffix:
        return True
    if suffix[0].isalnum():
        return False
    if key[-1:].isdigit() and suffix[0] in "-.":
        digits = []
        for char in suffix[1:]:
            if not char.isdigit():
                break
            digits.append(char)
        if digits and not (len(digits) == 8 and len(suffix) == len(digits) + 1):
            return False
    return True


def _normalized_pricing_key(value: str) -> str:
    return value.replace(".", "-").replace("@", "-")


def _today_utc() -> str:
    return datetime.now(timezone.utc).date().isoformat()


def _int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        try:
            return int(float(value))
        except (TypeError, ValueError):
            return None


def _float_or_zero(value: Any) -> float:
    if value is None or value == "":
        return 0.0
    try:
        return float(value)
    except (TypeError, ValueError):
        return 0.0
