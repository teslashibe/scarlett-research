from __future__ import annotations

import json
import math
from collections import Counter
from pathlib import Path
from typing import Any

from .catalogue import SignalRule, evaluate, signals, trade_returns
from .steering import conservative_score


def return_metrics(returns: list[float]) -> dict[str, Any]:
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


def compound_signals(
    trigger_values: list[float | int | None],
    trigger_rule: SignalRule,
    regime_values: list[float | int | None],
    regime_rule: SignalRule,
) -> list[int]:
    trigger = signals(trigger_values, trigger_rule)
    regime = signals(regime_values, regime_rule)
    side = 1 if trigger_rule.side == "long" else -1
    return [side if left == side and right == side else 0 for left, right in zip(trigger, regime)]


def _series(result: dict[str, Any], output_name: str) -> list[float | int | None]:
    output = result["outputs"][output_name]
    return output.get("integer") if output["type"] == "integer_series" else output["real"]


def _source_key(candidate: dict[str, Any]) -> str:
    return json.dumps(
        {
            "calculation": candidate["calculation"],
            "output": candidate["output"],
            "rule": candidate["rule"],
        },
        sort_keys=True,
    )


def _rank_score(development: dict[str, Any], validation: dict[str, Any]) -> float:
    effect = min(development["meanReturn"], validation["meanReturn"])
    return effect * math.sqrt(validation["trades"]) / (1 + validation["maxDrawdown"])


def run_composite_campaign(
    binary: Path,
    bundle: dict[str, Any],
    catalogue_campaign: dict[str, Any],
    cost_bps: float = 25,
    source_limit_per_symbol: int = 20,
    selection_limit: int = 24,
) -> dict[str, Any]:
    """Screen event-plus-regime pairs without reading the final partition."""
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for candidate in catalogue_campaign["candidates"]:
        by_symbol.setdefault(candidate["symbol"], []).append(candidate)

    trials = []
    survivors = []
    for symbol, all_candidates in by_symbol.items():
        candles = bundle["series"].get(symbol, [])
        if not candles:
            continue
        ranked = sorted(all_candidates, key=conservative_score, reverse=True)
        unique = {}
        for candidate in ranked:
            unique.setdefault(_source_key(candidate), candidate)
        source = list(unique.values())[:source_limit_per_symbol]
        events = [row for row in source if row["rule"]["operator"].startswith("crosses_")]
        regimes = [row for row in source if row["rule"]["operator"] in {"gt", "lt"}]

        calculated: dict[str, list[float | int | None]] = {}
        for candidate in source:
            key = _source_key(candidate)
            result = evaluate(binary, candles, [candidate["calculation"]])["calculations"][0]
            calculated[key] = _series(result, candidate["output"])

        first, second = int(len(candles) * 0.6), int(len(candles) * 0.8)
        for trigger in events:
            for regime in regimes:
                if trigger["rule"]["side"] != regime["rule"]["side"]:
                    continue
                if trigger["function"] == regime["function"]:
                    continue
                trigger_rule = SignalRule(**trigger["rule"])
                regime_rule = SignalRule(**regime["rule"])
                combined = compound_signals(
                    calculated[_source_key(trigger)],
                    trigger_rule,
                    calculated[_source_key(regime)],
                    regime_rule,
                )
                development = return_metrics(
                    trade_returns(candles[:first], combined[:first], trigger_rule.hold, cost_bps)
                )
                validation = return_metrics(
                    trade_returns(
                        candles[first:second],
                        combined[first:second],
                        trigger_rule.hold,
                        cost_bps,
                    )
                )
                trial = {
                    "symbol": symbol,
                    "side": trigger_rule.side,
                    "trigger": trigger,
                    "regime": regime,
                    "development": development,
                    "validation": validation,
                    "confirmationRead": False,
                }
                passed = bool(
                    development["trades"] >= 10
                    and validation["trades"] >= 5
                    and (development["meanReturn"] or 0) > 0
                    and (validation["meanReturn"] or 0) > 0
                )
                trial["status"] = "candidate" if passed else "rejected"
                trials.append(trial)
                if passed:
                    trial["selectionScore"] = _rank_score(development, validation)
                    survivors.append(trial)

    selected = []
    assets: Counter[str] = Counter()
    functions: Counter[str] = Counter()
    for candidate in sorted(survivors, key=lambda row: row["selectionScore"], reverse=True):
        pair = "+".join(sorted((candidate["trigger"]["function"], candidate["regime"]["function"])))
        if assets[candidate["symbol"]] >= 3 or functions[pair] >= 2:
            continue
        selected.append(candidate)
        assets[candidate["symbol"]] += 1
        functions[pair] += 1
        if len(selected) == selection_limit:
            break

    return {
        "protocol": {
            "family": "event_trigger_and_regime_filter",
            "costBps": cost_bps,
            "split": [0.6, 0.2, 0.2],
            "selectionUses": ["development", "validation"],
            "confirmationRead": False,
            "fills": "next_bar_open",
            "nonOverlapping": True,
            "sourceLimitPerSymbol": source_limit_per_symbol,
            "selectionLimit": selection_limit,
        },
        "evaluated": len(trials),
        "survivors": len(survivors),
        "trials": trials,
        "selected": selected,
        "coverage": {"assets": dict(assets), "functionPairs": dict(functions)},
        "conclusion": "forward_candidates" if selected else "no_candidate",
    }
