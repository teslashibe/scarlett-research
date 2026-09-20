from __future__ import annotations

import hashlib
import json
import math
import random
from typing import Any


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def max_drawdown(returns: list[float]) -> float:
    wealth = peak = 1.0
    worst = 0.0
    for value in returns:
        wealth *= max(0.0, 1 + value)
        peak = max(peak, wealth)
        worst = max(worst, 1 - wealth / peak)
    return worst


def circular_block_sample(
    values: list[float], size: int, block_length: int, generator: random.Random
) -> list[float]:
    sampled = []
    while len(sampled) < size:
        start = generator.randrange(len(values))
        sampled.extend(values[(start + offset) % len(values)] for offset in range(block_length))
    return sampled[:size]


def simulate_candidate(
    returns: list[float],
    *,
    simulations: int = 10_000,
    block_length: int | None = None,
    cost_shock_bps: float = 25,
    allocation_fraction: float = 0.10,
    ruin_drawdown: float = 0.20,
    seed: int = 20260920,
) -> dict[str, Any]:
    if simulations < 1:
        raise ValueError("simulations must be positive")
    if not returns:
        return {"status": "insufficient_data", "trades": 0}
    if not 0 < allocation_fraction <= 1:
        raise ValueError("allocation fraction must be in (0, 1]")
    length = block_length or max(1, round(math.sqrt(len(returns))))
    if length > len(returns):
        raise ValueError("block length cannot exceed the trade count")
    generator = random.Random(seed)
    totals = []
    means = []
    drawdowns = []
    terminal_returns = []
    for _ in range(simulations):
        path = circular_block_sample(returns, len(returns), length, generator)
        stressed = [
            allocation_fraction
            * (value - generator.triangular(0, cost_shock_bps / 10_000, 0))
            for value in path
        ]
        totals.append(sum(stressed))
        means.append(sum(stressed) / len(stressed))
        drawdowns.append(max_drawdown(stressed))
        wealth = math.prod(max(0.0, 1 + value) for value in stressed)
        terminal_returns.append(wealth - 1)
    probability_positive = sum(value > 0 for value in totals) / simulations
    probability_ruin = sum(value >= ruin_drawdown for value in drawdowns) / simulations
    return {
        "status": "complete" if len(returns) >= 20 else "insufficient_data",
        "trades": len(returns),
        "blockLength": length,
        "allocationFraction": allocation_fraction,
        "meanReturnInterval": {
            "p05": percentile(means, 0.05),
            "p50": percentile(means, 0.50),
            "p95": percentile(means, 0.95),
        },
        "terminalReturnInterval": {
            "p05": percentile(terminal_returns, 0.05),
            "p50": percentile(terminal_returns, 0.50),
            "p95": percentile(terminal_returns, 0.95),
        },
        "maxDrawdown": {
            "p50": percentile(drawdowns, 0.50),
            "p95": percentile(drawdowns, 0.95),
        },
        "probabilityPositive": probability_positive,
        "probabilityLoss": 1 - probability_positive,
        "probabilityRuin": probability_ruin,
        "passes": bool(
            len(returns) >= 20
            and percentile(means, 0.05) > 0
            and probability_positive >= 0.95
            and probability_ruin <= 0.05
        ),
    }


def run_monte_carlo(
    confirmation: dict[str, Any],
    *,
    simulations: int = 10_000,
    block_length: int | None = None,
    cost_shock_bps: float = 25,
    allocation_fraction: float = 0.10,
    ruin_drawdown: float = 0.20,
    seed: int = 20260920,
) -> dict[str, Any]:
    results = []
    for index, candidate in enumerate(confirmation["results"]):
        returns = [row["return"] for row in candidate.get("outcomes", [])]
        identity = json.dumps(
            {
                key: candidate.get(key)
                for key in (
                    "recipeId",
                    "symbol",
                    "function",
                    "output",
                    "rule",
                    "family",
                    "sourceIds",
                )
            },
            sort_keys=True,
        ).encode()
        candidate_seed = seed ^ int.from_bytes(hashlib.sha256(identity).digest()[:8], "big")
        simulation = simulate_candidate(
            returns,
            simulations=simulations,
            block_length=block_length,
            cost_shock_bps=cost_shock_bps,
            allocation_fraction=allocation_fraction,
            ruin_drawdown=ruin_drawdown,
            seed=candidate_seed,
        )
        results.append(
            {
                "symbol": candidate.get("symbol"),
                "function": candidate.get("function"),
                "output": candidate.get("output"),
                "rule": candidate.get("rule"),
                "simulation": simulation,
            }
        )
    return {
        "protocol": {
            "method": "circular_moving_block_bootstrap",
            "simulations": simulations,
            "blockLength": block_length or "round_sqrt_trades",
            "costShockBps": cost_shock_bps,
            "costShockDistribution": "triangular(0,max,mode=0)",
            "ruinDrawdown": ruin_drawdown,
            "allocationFraction": allocation_fraction,
            "minimumTrades": 20,
            "seed": seed,
            "promotionGate": {
                "meanReturnP05AboveZero": True,
                "probabilityPositiveAtLeast": 0.95,
                "probabilityRuinAtMost": 0.05,
            },
            "interpretation": "conditional robustness analysis, not causal proof or a p-value",
        },
        "results": results,
        "passing": sum(row["simulation"].get("passes", False) for row in results),
    }
