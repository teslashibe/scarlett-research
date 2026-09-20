from __future__ import annotations

import hashlib
import itertools
import json
import math
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .catalogue import SignalRule, evaluate, signals
from .composites import return_metrics
from .steering import conservative_score


def _source_key(candidate: dict[str, Any]) -> str:
    return json.dumps(
        {
            "calculation": candidate["calculation"],
            "output": candidate["output"],
            "rule": candidate["rule"],
        },
        sort_keys=True,
        separators=(",", ":"),
    )


def _recipe_id(symbol: str, family: str, sources: Iterable[dict[str, Any]]) -> str:
    identity = {
        "symbol": symbol,
        "family": family,
        "sources": [_source_key(source) for source in sources],
    }
    return hashlib.sha256(
        json.dumps(identity, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _source_id(candidate: dict[str, Any]) -> str:
    return hashlib.sha256(_source_key(candidate).encode()).hexdigest()[:24]


def _source_spec(candidate: dict[str, Any]) -> dict[str, Any]:
    return {
        "function": candidate["function"],
        "category": candidate["category"],
        "calculation": candidate["calculation"],
        "output": candidate["output"],
        "rule": candidate["rule"],
    }


def _signal_mask(values: list[float | int | None], rule: SignalRule) -> int:
    mask = 0
    side = 1 if rule.side == "long" else -1
    for index, value in enumerate(signals(values, rule)):
        if value == side:
            mask |= 1 << index
    return mask


def masked_returns(
    candles: list[dict[str, Any]],
    mask: int,
    hold: int,
    cost_bps: float,
    start: int,
    end: int,
    side: int = 1,
) -> list[float]:
    """Evaluate next-open, non-overlapping trades from a compact causal signal mask."""
    if start < 0 or end > len(candles) or start >= end:
        raise ValueError("invalid backtest partition")
    returns = []
    cursor = start
    partition = mask >> start
    while partition:
        offset = (partition & -partition).bit_length() - 1
        index = start + offset
        if index < cursor:
            partition &= partition - 1
            continue
        entry_index, exit_index = index + 1, index + 1 + hold
        if exit_index >= end:
            break
        entry = float(candles[entry_index]["open"])
        exit_price = float(candles[exit_index]["open"])
        returns.append(side * (exit_price / entry - 1) - cost_bps / 10_000)
        cursor = exit_index
        consumed = cursor - start + 1
        partition &= ~((1 << consumed) - 1)
    return returns


def _calculated_sources(
    binary: Path, candles: list[dict[str, Any]], sources: list[dict[str, Any]]
) -> list[dict[str, Any]]:
    calculations: dict[str, dict[str, Any]] = {}
    for source in sources:
        key = json.dumps(source["calculation"], sort_keys=True)
        if key not in calculations:
            calculation = dict(source["calculation"])
            calculation["id"] = f"m{len(calculations):04d}"
            calculations[key] = calculation
    outputs: dict[str, dict[str, Any]] = {}
    items = list(calculations.items())
    for start in range(0, len(items), 16):
        chunk = items[start : start + 16]
        result = evaluate(binary, candles, [item[1] for item in chunk])
        for (key, _), calculated in zip(chunk, result["calculations"], strict=True):
            outputs[key] = calculated["outputs"]
    enriched = []
    for source in sources:
        key = json.dumps(source["calculation"], sort_keys=True)
        output = outputs[key][source["output"]]
        values = output.get("integer") if output["type"] == "integer_series" else output["real"]
        rule = SignalRule(**source["rule"])
        enriched.append({"candidate": source, "mask": _signal_mask(values, rule), "rule": rule})
    return enriched


def _recipes(sources: list[dict[str, Any]]) -> Iterable[tuple[str, tuple[dict[str, Any], ...]]]:
    by_side: dict[str, list[dict[str, Any]]] = {"long": [], "short": []}
    for source in sources:
        by_side[source["rule"].side].append(source)
    for same_side in by_side.values():
        for pair in itertools.combinations(same_side, 2):
            yield "all_2", pair
            yield "any_2", pair
        for triple in itertools.combinations(same_side, 3):
            yield "all_3", triple


def _combine(family: str, masks: list[int]) -> int:
    if family.startswith("all_"):
        value = masks[0]
        for mask in masks[1:]:
            value &= mask
        return value
    value = 0
    for mask in masks:
        value |= mask
    return value


def run_mass_campaign(
    binary: Path,
    bundle: dict[str, Any],
    catalogue_campaign: dict[str, Any],
    *,
    cost_bps: float = 25,
    source_limit_per_symbol: int = 96,
    max_recipes: int = 250_000,
    shards: int = 1,
    shard: int = 0,
) -> dict[str, Any]:
    if shards < 1 or shard < 0 or shard >= shards:
        raise ValueError("invalid shard")
    if max_recipes < 1:
        raise ValueError("max_recipes must be positive")
    by_symbol: dict[str, list[dict[str, Any]]] = {}
    for candidate in catalogue_campaign["candidates"]:
        by_symbol.setdefault(candidate["symbol"], []).append(candidate)
    trials: list[dict[str, Any]] = []
    survivors: list[dict[str, Any]] = []
    source_catalog: dict[str, dict[str, Any]] = {}
    enumerated = 0
    for symbol in sorted(by_symbol):
        candles = bundle["series"].get(symbol, [])
        if not candles:
            continue
        ranked = sorted(by_symbol[symbol], key=conservative_score, reverse=True)
        unique: dict[str, dict[str, Any]] = {}
        for candidate in ranked:
            unique.setdefault(_source_key(candidate), candidate)
        source_candidates = list(unique.values())[:source_limit_per_symbol]
        calculated = _calculated_sources(binary, candles, source_candidates)
        by_key = {_source_key(row["candidate"]): row for row in calculated}
        first, second = int(len(candles) * 0.6), int(len(candles) * 0.8)
        for family, source_tuple in _recipes(calculated):
            if enumerated >= max_recipes:
                break
            enumerated += 1
            candidates = [row["candidate"] for row in source_tuple]
            source_ids = [_source_id(candidate) for candidate in candidates]
            for source_id, candidate in zip(source_ids, candidates, strict=True):
                source_catalog.setdefault(source_id, _source_spec(candidate))
            recipe_id = _recipe_id(symbol, family, candidates)
            if int(recipe_id[:16], 16) % shards != shard:
                continue
            hold = max(row["rule"].hold for row in source_tuple)
            side = 1 if source_tuple[0]["rule"].side == "long" else -1
            mask = _combine(family, [by_key[_source_key(row)]["mask"] for row in candidates])
            development_returns = masked_returns(candles, mask, hold, cost_bps, 0, first, side)
            validation_returns = masked_returns(
                candles, mask, hold, cost_bps, first, second, side
            )
            development = return_metrics(development_returns)
            validation = return_metrics(validation_returns)
            passed = bool(
                development["trades"] >= 20
                and validation["trades"] >= 10
                and (development["meanReturn"] or 0) > 0
                and (validation["meanReturn"] or 0) > 0
            )
            trial = {
                "recipeId": recipe_id,
                "symbol": symbol,
                "marketSymbol": candidates[0].get("marketSymbol", symbol),
                "side": source_tuple[0]["rule"].side,
                "family": family,
                "hold": hold,
                "sourceIds": source_ids,
                "development": development,
                "validation": validation,
                "status": "candidate" if passed else "rejected",
                "confirmationRead": False,
            }
            trials.append(trial)
            if passed:
                effect = min(development["meanReturn"], validation["meanReturn"])
                trial["selectionScore"] = effect * math.sqrt(validation["trades"]) / (
                    1 + validation["maxDrawdown"]
                )
                survivors.append(trial)
        if enumerated >= max_recipes:
            break
    survivors.sort(key=lambda row: row["selectionScore"], reverse=True)
    selected = []
    for survivor in survivors[:24]:
        expanded = dict(survivor)
        expanded["sources"] = [source_catalog[item] for item in survivor["sourceIds"]]
        selected.append(expanded)
    return {
        "protocol": {
            "family": "mass_boolean_ta_composites",
            "expressions": ["all_2", "any_2", "all_3"],
            "costBps": cost_bps,
            "split": [0.6, 0.2, 0.2],
            "selectionUses": ["development", "validation"],
            "confirmationRead": False,
            "fills": "next_bar_open",
            "nonOverlapping": True,
            "sourceLimitPerSymbol": source_limit_per_symbol,
            "maxRecipes": max_recipes,
            "shards": shards,
            "shard": shard,
        },
        "enumerated": enumerated,
        "evaluated": len(trials),
        "survivors": len(survivors),
        "sourceCatalog": source_catalog,
        "trials": trials,
        "selected": selected,
        "conclusion": "forward_candidates" if survivors else "no_candidate",
    }
