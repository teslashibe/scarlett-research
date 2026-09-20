from __future__ import annotations

import math
from collections import Counter
from typing import Any


def conservative_score(candidate: dict[str, Any]) -> float:
    """Rank evidence without rewarding a large development/validation disagreement."""
    development = candidate["development"]
    validation = candidate["validation"]
    effect = min(development["meanReturn"], validation["meanReturn"])
    return effect * math.sqrt(validation["trades"]) / (1 + validation["maxDrawdown"])


def candidate_signature(candidate: dict[str, Any]) -> tuple[Any, ...]:
    rule = candidate["rule"]
    calculation = candidate["calculation"]
    return (
        candidate["symbol"],
        calculation["function"],
        candidate["output"],
        rule["operator"],
        round(float(rule["value"]), 12),
        rule["side"],
        tuple(sorted(calculation.get("parameters", {}).items())),
    )


def select_diverse(
    candidates: list[dict[str, Any]],
    limit: int,
    *,
    max_per_asset: int = 3,
    max_per_function: int = 2,
    max_per_category: int = 6,
    minimum_validation_trades: int = 5,
) -> dict[str, Any]:
    """Select a bounded, diversified forward-paper portfolio from screened hypotheses."""
    ranked = sorted(candidates, key=conservative_score, reverse=True)
    selected: list[dict[str, Any]] = []
    seen: set[tuple[Any, ...]] = set()
    assets: Counter[str] = Counter()
    functions: Counter[str] = Counter()
    categories: Counter[str] = Counter()
    exclusions: Counter[str] = Counter()
    for candidate in ranked:
        signature = candidate_signature(candidate)
        if signature in seen:
            exclusions["duplicate"] += 1
            continue
        if candidate["validation"]["trades"] < minimum_validation_trades:
            exclusions["validation_sample"] += 1
            continue
        asset, function, category = (
            candidate["symbol"], candidate["function"], candidate["category"]
        )
        if assets[asset] >= max_per_asset:
            exclusions["asset_cap"] += 1
            continue
        if functions[function] >= max_per_function:
            exclusions["function_cap"] += 1
            continue
        if categories[category] >= max_per_category:
            exclusions["category_cap"] += 1
            continue
        chosen = dict(candidate)
        chosen["selectionScore"] = conservative_score(candidate)
        selected.append(chosen)
        seen.add(signature)
        assets[asset] += 1
        functions[function] += 1
        categories[category] += 1
        if len(selected) == limit:
            break
    return {
        "protocol": {
            "confirmationRead": False,
            "ranking": "min(development_mean,validation_mean)*sqrt(validation_trades)/(1+validation_drawdown)",
            "limits": {
                "candidates": limit,
                "perAsset": max_per_asset,
                "perFunction": max_per_function,
                "perCategory": max_per_category,
                "minimumValidationTrades": minimum_validation_trades,
            },
        },
        "selected": selected,
        "excluded": dict(exclusions),
        "coverage": {
            "assets": dict(assets),
            "functions": dict(functions),
            "categories": dict(categories),
        },
    }
