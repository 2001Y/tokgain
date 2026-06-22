from __future__ import annotations

import json
from importlib import resources
from pathlib import Path
from typing import Any


PRICE_TABLE_RESOURCE = "data/prices.json"


def load_prices(path: str | Path | None = None) -> dict[str, Any]:
    if path:
        with Path(path).expanduser().open("r", encoding="utf-8") as fh:
            return json.load(fh)
    with resources.files("tokgain").joinpath(PRICE_TABLE_RESOURCE).open("r", encoding="utf-8") as fh:
        return json.load(fh)


def estimate_usd(event: dict[str, Any], prices: dict[str, Any]) -> tuple[float, str, str, bool]:
    """Return (usd, price_table_version, estimate_mode, price_missing)."""
    version = str(prices.get("version", "unknown"))
    model = event.get("model")
    models = prices.get("models") or {}
    rate = models.get(model) or {}
    price_missing = not bool(rate)

    input_rate = _float_or_zero(rate.get("input_per_1m"))
    output_rate = _float_or_zero(rate.get("output_per_1m"))

    saved_input = _int_or_none(event.get("saved_input_tokens"))
    saved_output = _int_or_none(event.get("saved_output_tokens"))
    saved_total = _int_or_none(event.get("saved_tokens")) or 0

    if saved_input is not None and saved_output is not None:
        estimate_mode = "input_output"
        usd = saved_input / 1_000_000 * input_rate + saved_output / 1_000_000 * output_rate
    elif saved_input is not None:
        estimate_mode = "prompt_equivalent"
        usd = saved_input / 1_000_000 * input_rate
    else:
        estimate_mode = "prompt_equivalent"
        usd = saved_total / 1_000_000 * input_rate

    return (round(usd, 10), version, estimate_mode, price_missing)


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
