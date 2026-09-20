from __future__ import annotations

import datetime as dt
import itertools
from collections import defaultdict
from typing import Any

SECONDS = {"5m": 300, "15m": 900, "1h": 3600, "4h": 14400, "1d": 86400}


def _time(candle: dict[str, Any]) -> float:
    value = candle.get("time") or candle.get("timestamp") or candle.get("sourceClose")
    if isinstance(value, (int, float)):
        return float(value)
    return dt.datetime.fromisoformat(str(value)).timestamp()


def resample(candles: list[dict[str, Any]], source: str, target: str) -> list[dict[str, Any]]:
    """Aggregate only complete UTC-aligned target buckets from closed source candles."""
    if source not in SECONDS or target not in SECONDS or SECONDS[target] < SECONDS[source]:
        raise ValueError("target interval must be supported and no finer than source")
    source_seconds, target_seconds = SECONDS[source], SECONDS[target]
    buckets: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for candle in candles:
        buckets[int(_time(candle) // target_seconds * target_seconds)].append(candle)
    expected = target_seconds // source_seconds
    output = []
    for bucket, rows in sorted(buckets.items()):
        rows.sort(key=_time)
        times = [_time(row) for row in rows]
        contiguous = len(rows) == expected and all(
            abs((right - left) - source_seconds) < 1e-6 for left, right in itertools.pairwise(times)
        )
        if not contiguous:
            continue
        output.append(
            {
                "time": dt.datetime.fromtimestamp(bucket, dt.UTC)
                .isoformat()
                .replace("+00:00", "Z"),
                "open": float(rows[0]["open"]),
                "high": max(float(x["high"]) for x in rows),
                "low": min(float(x["low"]) for x in rows),
                "close": float(rows[-1]["close"]),
                "volume": sum(float(x.get("volume", 0) or 0) for x in rows),
            }
        )
    return output


def indicator(values: list[float], name: str, period: int = 14) -> list[float | None]:
    if period < 2:
        raise ValueError("period must be at least 2")
    result: list[float | None] = [None] * len(values)
    if name == "sma":
        for index in range(period - 1, len(values)):
            result[index] = sum(values[index - period + 1 : index + 1]) / period
    elif name == "ema":
        if len(values) >= period:
            current = sum(values[:period]) / period
            result[period - 1] = current
            alpha = 2 / (period + 1)
            for index in range(period, len(values)):
                current = alpha * values[index] + (1 - alpha) * current
                result[index] = current
    elif name == "rsi":
        if len(values) > period:
            changes = [values[i] - values[i - 1] for i in range(1, len(values))]
            gains = sum(max(x, 0) for x in changes[:period]) / period
            losses = sum(max(-x, 0) for x in changes[:period]) / period
            result[period] = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
            for index in range(period + 1, len(values)):
                change = changes[index - 1]
                gains = (gains * (period - 1) + max(change, 0)) / period
                losses = (losses * (period - 1) + max(-change, 0)) / period
                result[index] = 100 if losses == 0 else 100 - 100 / (1 + gains / losses)
    else:
        raise ValueError(f"unsupported local indicator: {name}")
    return result


def analyze(candles: list[dict[str, Any]], indicator_name: str, period: int) -> dict[str, Any]:
    values = [float(x["close"]) for x in candles]
    series = indicator(values, indicator_name, period)
    return {
        "provenance": "local_research_calculation",
        "indicator": indicator_name,
        "period": period,
        "observations": len(candles),
        "warm": len(values) >= period + (1 if indicator_name == "rsi" else 0),
        "latest": next((value for value in reversed(series) if value is not None), None),
        "series": series,
    }
