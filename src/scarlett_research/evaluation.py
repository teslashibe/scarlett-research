from __future__ import annotations

import datetime as dt
import math
from collections import Counter, defaultdict
from dataclasses import asdict, dataclass
from typing import Any


def parse_time(value: str) -> float:
    return dt.datetime.fromisoformat(value).timestamp()


def get(record: dict[str, Any], *names: str, default: Any = None) -> Any:
    for name in names:
        if name in record:
            return record[name]
    return default


def result_r(setup: dict[str, Any]) -> float | None:
    outcome = setup.get("outcome") or {}
    detail = outcome.get("detail") or {}
    value = detail.get("grossResultR", detail.get("resultR"))
    return float(value) if value is not None else None


@dataclass(frozen=True)
class Candidate:
    name: str
    strategies: tuple[str, ...] = ()
    direction: str | None = None
    setup_type: str | None = None
    min_strength: float = 0.0


@dataclass
class Metrics:
    n: int
    total_r: float
    mean_r: float | None
    win_rate: float | None
    profit_factor: float | None
    max_drawdown_r: float
    symbols: int
    top_symbol_share: float | None
    status: str


def matches(setup: dict[str, Any], candidate: Candidate) -> bool:
    slug = get(setup, "slug", "strategySlug", default="")
    return (
        (not candidate.strategies or slug in candidate.strategies)
        and (
            candidate.direction is None
            or get(setup, "decision", "direction") == candidate.direction
        )
        and (
            candidate.setup_type is None
            or get(setup, "setup_type", "setupType") == candidate.setup_type
        )
        and float(get(setup, "strength", default=0) or 0) >= candidate.min_strength
    )


def score(setups: list[dict[str, Any]], candidate: Candidate, cost_r: float = 0.05) -> Metrics:
    rows = []
    by_symbol: dict[str, float] = defaultdict(float)
    for setup in sorted(
        setups, key=lambda x: (get(x, "published_at", "publishedAt", default=""), x.get("id", ""))
    ):
        if not matches(setup, candidate):
            continue
        value = result_r(setup)
        if value is None:
            continue
        net = value - cost_r
        rows.append(net)
        by_symbol[str(get(setup, "symbol", default="unknown"))] += net
    if not rows:
        return Metrics(0, 0.0, None, None, None, 0.0, 0, None, "insufficient_data")
    curve = peak = drawdown = 0.0
    for value in rows:
        curve += value
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    gains = sum(v for v in rows if v > 0)
    losses = -sum(v for v in rows if v < 0)
    total = sum(rows)
    concentration = max((abs(v) for v in by_symbol.values()), default=0) / max(
        sum(abs(v) for v in by_symbol.values()), 1e-12
    )
    return Metrics(
        len(rows),
        total,
        total / len(rows),
        sum(v > 0 for v in rows) / len(rows),
        gains / losses if losses else None,
        drawdown,
        len(by_symbol),
        concentration,
        "measured",
    )


def chronological_split(
    setups: list[dict[str, Any]], fraction: float = 0.7
) -> tuple[list[dict[str, Any]], list[dict[str, Any]], str]:
    ordered = sorted(setups, key=lambda x: get(x, "published_at", "publishedAt", default=""))
    times = [
        get(x, "published_at", "publishedAt")
        for x in ordered
        if get(x, "published_at", "publishedAt")
    ]
    if not times:
        return [], [], ""
    boundary = times[min(len(times) - 1, max(1, math.floor(len(times) * fraction)))]
    return (
        [x for x in ordered if get(x, "published_at", "publishedAt", default="") < boundary],
        [x for x in ordered if get(x, "published_at", "publishedAt", default="") >= boundary],
        boundary,
    )


def metrics_dict(metrics: Metrics) -> dict[str, Any]:
    return asdict(metrics)


def dataset_summary(setups: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "setups": len(setups),
        "scored": sum(result_r(x) is not None for x in setups),
        "strategies": dict(
            Counter(get(x, "slug", "strategySlug", default="unknown") for x in setups)
        ),
        "symbols": len({get(x, "symbol") for x in setups}),
    }
