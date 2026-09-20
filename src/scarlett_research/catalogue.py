from __future__ import annotations

import hashlib
import json
import math
import random
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .market_data import normalize_market_symbol

RESEARCH_CATEGORIES = {
    "Cycle Indicators",
    "Momentum Indicators",
    "Overlap Studies",
    "Pattern Recognition",
    "Price Transform",
    "Statistic Functions",
    "Volatility Indicators",
    "Volume Indicators",
}


@dataclass(frozen=True)
class SignalRule:
    operator: str
    value: float
    side: str
    hold: int


def source_reference(name: str) -> str:
    return name if name in {"open", "high", "low", "close", "volume"} else "close"


def calculation(definition: dict[str, Any], identifier: str) -> dict[str, Any]:
    return {
        "id": identifier,
        "function": definition["name"],
        "timeframe": "source_candle",
        "inputs": {
            item["name"]: {"series": source_reference(item["name"])}
            for item in definition["inputs"]
        },
        "parameters": {item["name"]: item["default"] for item in definition["parameters"]},
    }


def evaluate(
    binary: Path, candles: list[dict[str, Any]], calculations: list[dict[str, Any]]
) -> dict[str, Any]:
    request = {
        "calculations": calculations,
        "candles": [
            {
                "open": row["open"],
                "high": row["high"],
                "low": row["low"],
                "close": row["close"],
                "volume": row["volume"],
            }
            for row in candles
        ],
    }
    completed = subprocess.run(
        [str(binary)],
        input=json.dumps(request),
        text=True,
        capture_output=True,
        timeout=180,
        check=False,
    )
    if completed.returncode:
        raise RuntimeError(completed.stderr.strip() or "TA series evaluation failed")
    return json.loads(completed.stdout)


def audit_catalogue(
    binary: Path, catalogue: dict[str, Any], candles: list[dict[str, Any]]
) -> dict[str, Any]:
    rows = []
    sample = candles[: min(4096, len(candles))]
    for index, definition in enumerate(catalogue["functions"]):
        try:
            result = evaluate(binary, sample, [calculation(definition, f"f{index:03d}")])
            outputs = result["calculations"][0]["outputs"]
            rows.append(
                {
                    "function": definition["name"],
                    "category": definition["category"],
                    "status": "compatible",
                    "outputs": list(outputs),
                }
            )
        except RuntimeError as error:
            rows.append(
                {
                    "function": definition["name"],
                    "category": definition["category"],
                    "status": "incompatible_sample_domain",
                    "reason": str(error),
                }
            )
    return {
        "engine": catalogue["engine"],
        "tested": len(rows),
        "compatible": sum(row["status"] == "compatible" for row in rows),
        "incompatible": sum(row["status"] != "compatible" for row in rows),
        "functions": rows,
    }


def percentile(values: list[float], fraction: float) -> float:
    ordered = sorted(values)
    if not ordered:
        raise ValueError("percentile requires values")
    position = (len(ordered) - 1) * fraction
    low, high = math.floor(position), math.ceil(position)
    if low == high:
        return ordered[low]
    return ordered[low] * (high - position) + ordered[high] * (position - low)


def rules_for_series(values: list[float | int | None], integer: bool) -> list[SignalRule]:
    if integer:
        return [SignalRule("gt", 0, "long", hold) for hold in (4, 8, 16, 32)] + [
            SignalRule("lt", 0, "short", hold) for hold in (4, 8, 16, 32)
        ]
    development = [float(value) for value in values if value is not None and math.isfinite(value)]
    if len(development) < 20:
        return []
    low, middle, high = (percentile(development, fraction) for fraction in (0.2, 0.5, 0.8))
    templates = (
        ("crosses_above", low, "long"),
        ("crosses_below", high, "short"),
        ("crosses_above", high, "long"),
        ("crosses_below", low, "short"),
        ("crosses_above", middle, "long"),
        ("crosses_below", middle, "short"),
    )
    return [
        SignalRule(operator, value, side, hold)
        for operator, value, side in templates
        for hold in (4, 8, 16, 32)
    ]


def signals(values: list[float | int | None], rule: SignalRule) -> list[int]:
    output = [0] * len(values)
    for index, current in enumerate(values):
        if current is None:
            continue
        if rule.operator == "gt" and current > rule.value:
            output[index] = 1
        elif rule.operator == "lt" and current < rule.value:
            output[index] = -1
        elif index > 0 and values[index - 1] is not None:
            previous = values[index - 1]
            if rule.operator == "crosses_above" and previous <= rule.value < current:
                output[index] = 1
            elif rule.operator == "crosses_below" and previous >= rule.value > current:
                output[index] = -1
    return output


def signal_backtest(
    candles: list[dict[str, Any]],
    values: list[float | int | None],
    rule: SignalRule,
    cost_bps: float,
) -> dict[str, Any]:
    signal = signals(values, rule)
    returns = trade_returns(candles, signal, rule.hold, cost_bps)
    curve = peak = drawdown = 0.0
    for value in returns:
        curve += value
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    return {
        "trades": len(returns),
        "totalReturn": sum(returns),
        "meanReturn": sum(returns) / len(returns) if returns else None,
        "winRate": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "maxDrawdown": drawdown,
    }


def trade_returns(
    candles: list[dict[str, Any]], signal: list[int], hold: int, cost_bps: float
) -> list[float]:
    return [row["return"] for row in trade_outcomes(candles, signal, hold, cost_bps)]


def trade_outcomes(
    candles: list[dict[str, Any]], signal: list[int], hold: int, cost_bps: float
) -> list[dict[str, Any]]:
    returns = []
    index = 0
    while index + hold + 1 < len(candles):
        side = signal[index]
        if side == 0:
            index += 1
            continue
        entry_index, exit_index = index + 1, index + 1 + hold
        entry, exit_price = float(candles[entry_index]["open"]), float(candles[exit_index]["open"])
        returns.append(
            {
                "entryIndex": entry_index,
                "exitIndex": exit_index,
                "entryTime": candles[entry_index].get("time"),
                "exitTime": candles[exit_index].get("time"),
                "side": side,
                "return": side * (exit_price / entry - 1) - cost_bps / 10_000,
            }
        )
        index = exit_index
    return returns


def sign_flip_p_value(returns: list[float], seed: int, samples: int = 100_000) -> float:
    """One-sided randomization p-value for positive mean under a symmetric zero null."""
    if not returns:
        return 1.0
    observed = sum(returns)
    if len(returns) <= 20:
        exceed = 0
        total = 1 << len(returns)
        for mask in range(total):
            randomized = sum(value if mask & (1 << i) else -value for i, value in enumerate(returns))
            exceed += randomized >= observed - 1e-15
        return exceed / total
    generator = random.Random(seed)
    exceed = 1
    for _ in range(samples):
        randomized = sum(value if generator.getrandbits(1) else -value for value in returns)
        exceed += randomized >= observed - 1e-15
    return exceed / (samples + 1)


def holm_adjust(p_values: list[float]) -> list[float]:
    adjusted = [1.0] * len(p_values)
    running = 0.0
    for rank, index in enumerate(sorted(range(len(p_values)), key=p_values.__getitem__)):
        running = max(running, (len(p_values) - rank) * p_values[index])
        adjusted[index] = min(1.0, running)
    return adjusted


def confirm_selection(
    binary: Path,
    bundle: dict[str, Any],
    selection: dict[str, Any],
    cost_bps: float = 25,
    stress_cost_bps: float = 50,
) -> dict[str, Any]:
    """Open the final chronological partition once for an already frozen family."""
    results = []
    cache: dict[tuple[str, str], dict[str, Any]] = {}
    for candidate in selection["selected"]:
        symbol = candidate["symbol"]
        candles = bundle["series"][symbol]
        calculation_key = json.dumps(candidate["calculation"], sort_keys=True)
        key = (symbol, calculation_key)
        if key not in cache:
            cache[key] = evaluate(binary, candles, [candidate["calculation"]])["calculations"][0]
        output = cache[key]["outputs"][candidate["output"]]
        values = output.get("integer") if output["type"] == "integer_series" else output.get("real")
        start = int(len(candles) * 0.8)
        confirmation_candles = candles[start:]
        confirmation_values = values[start:]
        rule = SignalRule(**candidate["rule"])
        base_signal = signals(confirmation_values, rule)
        outcomes = trade_outcomes(confirmation_candles, base_signal, rule.hold, cost_bps)
        returns = [row["return"] for row in outcomes]
        stress_returns = trade_returns(
            confirmation_candles, base_signal, rule.hold, stress_cost_bps
        )
        seed_material = json.dumps(
            {
                "symbol": symbol,
                "function": candidate["function"],
                "output": candidate["output"],
                "rule": candidate["rule"],
            },
            sort_keys=True,
        ).encode()
        seed = int.from_bytes(hashlib.sha256(seed_material).digest()[:8], "big")
        mean = sum(returns) / len(returns) if returns else None
        stress_mean = sum(stress_returns) / len(stress_returns) if stress_returns else None
        results.append(
            {
                "symbol": symbol,
                "function": candidate["function"],
                "category": candidate["category"],
                "output": candidate["output"],
                "rule": candidate["rule"],
                "trades": len(returns),
                "totalReturn": sum(returns),
                "meanReturn": mean,
                "winRate": sum(value > 0 for value in returns) / len(returns) if returns else None,
                "stressMeanReturn": stress_mean,
                "rawPValue": sign_flip_p_value(returns, seed),
                "outcomes": outcomes,
            }
        )
    adjusted = holm_adjust([row["rawPValue"] for row in results])
    for row, p_value in zip(results, adjusted, strict=True):
        row["holmAdjustedPValue"] = p_value
        row["supportedUnderTestConditions"] = bool(
            row["trades"] >= 10
            and (row["meanReturn"] or 0) > 0
            and (row["stressMeanReturn"] or 0) > 0
            and p_value <= 0.05
        )
    return {
        "protocol": {
            "partition": "final_20_percent_chronological",
            "opened": True,
            "familySize": len(results),
            "costBps": cost_bps,
            "stressCostBps": stress_cost_bps,
            "minimumTrades": 10,
            "test": "one_sided_sign_flip_mean_greater_than_zero",
            "testAssumption": "trade returns are symmetric under the zero-mean null",
            "multipleComparisonCorrection": "Holm family-wise alpha 0.05",
            "fills": "next_bar_open",
            "nonOverlapping": True,
        },
        "results": results,
        "supported": sum(row["supportedUnderTestConditions"] for row in results),
        "conclusion": (
            "supported_under_test_conditions"
            if any(row["supportedUnderTestConditions"] for row in results)
            else "confirmation_failed"
        ),
    }


def screen_output(
    candles: list[dict[str, Any]], values: list[float | int | None], integer: bool, cost_bps: float
) -> dict[str, Any] | None:
    first, second = int(len(candles) * 0.6), int(len(candles) * 0.8)
    development_candles, validation_candles = candles[:first], candles[first:second]
    development_values, validation_values = values[:first], values[first:second]
    ranked = []
    for rule in rules_for_series(development_values, integer):
        metrics = signal_backtest(development_candles, development_values, rule, cost_bps)
        if metrics["trades"] >= 10 and (metrics["meanReturn"] or 0) > 0:
            ranked.append((metrics["meanReturn"], rule, metrics))
    if not ranked:
        return None
    _, rule, development = max(ranked, key=lambda row: row[0])
    validation = signal_backtest(validation_candles, validation_values, rule, cost_bps)
    if validation["trades"] < 3 or (validation["meanReturn"] or 0) <= 0:
        return None
    return {
        "rule": {
            "operator": rule.operator,
            "value": rule.value,
            "side": rule.side,
            "hold": rule.hold,
        },
        "development": development,
        "validation": validation,
        "confirmationRead": False,
    }


def run_catalogue_campaign(
    binary: Path,
    catalogue: dict[str, Any],
    bundle: dict[str, Any],
    cost_bps: float = 25,
    chunk_size: int = 16,
) -> dict[str, Any]:
    definitions = catalogue["functions"]
    first_symbol = next(iter(bundle["series"]))
    audit = audit_catalogue(binary, catalogue, bundle["series"][first_symbol])
    compatible_names = {
        row["function"] for row in audit["functions"] if row["status"] == "compatible"
    }
    indexed = [
        (index, definition)
        for index, definition in enumerate(definitions)
        if definition["name"] in compatible_names
    ]
    candidates = []
    failures = []
    for symbol, candles in bundle["series"].items():
        for start in range(0, len(indexed), chunk_size):
            group = indexed[start : start + chunk_size]
            calculations = [calculation(definition, f"f{index:03d}") for index, definition in group]
            try:
                evaluated = evaluate(binary, candles, calculations)
                results = evaluated["calculations"]
            except RuntimeError:
                results = []
                for index, definition in group:
                    try:
                        single = evaluate(
                            binary, candles, [calculation(definition, f"f{index:03d}")]
                        )
                        results.extend(single["calculations"])
                    except RuntimeError as error:
                        failures.append(
                            {
                                "symbol": symbol,
                                "function": definition["name"],
                                "reason": str(error),
                            }
                        )
            for result in results:
                definition = definitions[int(result["id"][1:])]
                if definition["category"] not in RESEARCH_CATEGORIES:
                    continue
                for output_name, output in result["outputs"].items():
                    integer = output["type"] == "integer_series"
                    values = output.get("integer") if integer else output.get("real")
                    candidate = screen_output(candles, values, integer, cost_bps)
                    if candidate is None:
                        continue
                    candidate.update(
                        {
                            "symbol": symbol,
                            "marketSymbol": normalize_market_symbol(
                                symbol, bundle.get("metadata", {}).get("source", "")
                            ),
                            "function": definition["name"],
                            "category": definition["category"],
                            "calculation": calculation(definition, "feature"),
                            "output": output_name,
                            "outputType": output["type"],
                        }
                    )
                    candidates.append(candidate)
    candidates.sort(
        key=lambda row: (
            min(row["development"]["meanReturn"], row["validation"]["meanReturn"]),
            row["validation"]["trades"],
        ),
        reverse=True,
    )
    return {
        "protocol": {
            "engine": catalogue["engine"],
            "costBps": cost_bps,
            "split": [0.6, 0.2, 0.2],
            "selectionUses": ["development", "validation"],
            "confirmationRead": False,
            "fills": "next_bar_open",
            "nonOverlapping": True,
            "parameterPolicy": "catalogue_defaults",
            "thresholdPolicy": "development_quantiles_only",
        },
        "catalogueAudit": audit,
        "evaluations": {
            "symbols": len(bundle["series"]),
            "compatibleFunctions": len(indexed),
            "failures": failures,
        },
        "candidates": candidates,
        "conclusion": "forward_candidates" if candidates else "no_candidate",
    }
