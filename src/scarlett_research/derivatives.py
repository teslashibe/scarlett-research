from __future__ import annotations

import hashlib
import itertools
import json
import math
import subprocess
from pathlib import Path
from typing import Any

from .catalogue import holm_adjust, percentile, sign_flip_p_value
from .composites import return_metrics
from .mass_search import _balanced_budgets, masked_outcomes, masked_returns


def _change(values: list[float | None], lag: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    for index in range(lag, len(values)):
        current, previous = values[index], values[index - lag]
        if current is not None and previous not in {None, 0}:
            output[index] = current / previous - 1
    return output


def _rolling_mean(values: list[float | None], window: int) -> list[float | None]:
    output: list[float | None] = [None] * len(values)
    total = 0.0
    count = 0
    for index, value in enumerate(values):
        if value is not None:
            total += value
            count += 1
        if index >= window:
            expired = values[index - window]
            if expired is not None:
                total -= expired
                count -= 1
        if index + 1 >= window and count == window:
            output[index] = total / window
    return output


def _rolling_std(values: list[float | None], window: int) -> list[float | None]:
    """Population rolling standard deviation using only values known at each bar."""
    output: list[float | None] = [None] * len(values)
    total = 0.0
    total_squared = 0.0
    count = 0
    for index, value in enumerate(values):
        if value is not None:
            total += value
            total_squared += value * value
            count += 1
        if index >= window:
            expired = values[index - window]
            if expired is not None:
                total -= expired
                total_squared -= expired * expired
                count -= 1
        if index + 1 >= window and count == window:
            variance = max(0.0, total_squared / window - (total / window) ** 2)
            output[index] = math.sqrt(variance)
    return output


def feature_series(
    candles: list[dict[str, Any]],
    metrics: list[dict[str, Any]],
    funding: list[dict[str, Any]] | None = None,
) -> dict[str, list[float | None]]:
    """Align public derivatives metrics to candle-open timestamps without forward filling."""
    by_time = {row["time"]: row for row in metrics}
    fields = {
        "open_interest": "openInterest",
        "top_account_ratio": "topTraderAccountLongShortRatio",
        "top_position_ratio": "topTraderPositionLongShortRatio",
        "global_ratio": "globalLongShortRatio",
        "taker_ratio": "takerLongShortVolumeRatio",
    }
    raw = {
        name: [by_time.get(candle["time"], {}).get(field) for candle in candles]
        for name, field in fields.items()
    }
    closes = [float(candle["close"]) for candle in candles]
    volumes = [float(candle["volume"]) for candle in candles]
    trades = [float(candle["trades"]) for candle in candles]
    bar_returns = _change(closes, 1)
    features: dict[str, list[float | None]] = {
        "oi_change_1h": _change(raw["open_interest"], 12),
        "oi_change_4h": _change(raw["open_interest"], 48),
        "oi_change_24h": _change(raw["open_interest"], 288),
        "price_return_1h": _change(closes, 12),
        "price_return_4h": _change(closes, 48),
        "price_return_24h": _change(closes, 288),
        "realized_volatility_1h": _rolling_std(bar_returns, 12),
        "realized_volatility_4h": _rolling_std(bar_returns, 48),
        "bar_range": [
            (float(candle["high"]) - float(candle["low"])) / float(candle["open"])
            if float(candle["open"]) != 0
            else None
            for candle in candles
        ],
        "volume_change_1h": _change(volumes, 12),
        "trade_count_change_1h": _change(trades, 12),
    }
    for name in ("top_account_ratio", "top_position_ratio", "global_ratio", "taker_ratio"):
        features[name] = raw[name]
        features[f"{name}_change_1h"] = _change(raw[name], 12)
    features["taker_ratio_mean_1h"] = _rolling_mean(raw["taker_ratio"], 12)
    features["taker_ratio_mean_4h"] = _rolling_mean(raw["taker_ratio"], 48)
    if funding:
        ordered = sorted(funding, key=lambda row: row["time"])
        funding_values: list[float | None] = []
        cursor = 0
        latest: float | None = None
        for candle in candles:
            while cursor < len(ordered) and ordered[cursor]["time"] <= candle["time"]:
                latest = float(ordered[cursor]["fundingRate"])
                cursor += 1
            funding_values.append(latest)
        features["funding_rate"] = funding_values
        features["funding_rate_change_8h"] = _change(funding_values, 96)
        features["funding_rate_mean_24h"] = _rolling_mean(funding_values, 288)
    return features


def _rule_sources(features: dict[str, list[float | None]], development_end: int) -> list[dict]:
    sources = []
    for feature, values in features.items():
        sample = [
            float(value)
            for value in values[:development_end]
            if value is not None and math.isfinite(value)
        ]
        if len(sample) < 100:
            continue
        low, high = percentile(sample, 0.2), percentile(sample, 0.8)
        for operator, threshold in (("lt", low), ("gt", high)):
            for side in ("long", "short"):
                for hold in (3, 6, 12, 24):
                    sources.append(
                        {
                            "feature": feature,
                            "operator": operator,
                            "threshold": threshold,
                            "side": side,
                            "hold": hold,
                        }
                    )
    return sources


def _mask(values: list[float | None], operator: str, threshold: float) -> int:
    mask = 0
    for index, value in enumerate(values):
        if value is not None and (
            (operator == "gt" and value > threshold) or (operator == "lt" and value < threshold)
        ):
            mask |= 1 << index
    return mask


def _indices(values: list[float | None], operator: str, threshold: float) -> list[int]:
    return [
        index
        for index, value in enumerate(values)
        if value is not None
        and ((operator == "gt" and value > threshold) or (operator == "lt" and value < threshold))
    ]


def _go_metrics(
    binary: Path,
    candles: list[dict[str, Any]],
    recipes: list[tuple[str, tuple[dict, ...]]],
    source_ids: dict[str, int],
    source_indices: list[list[int]],
    start: int,
    end: int,
    cost_bps: float,
) -> list[dict[str, Any]]:
    payload = {
        "opens": [float(candle["open"]) for candle in candles],
        "sources": source_indices,
        "recipes": [
            {
                "family": family,
                "sources": [source_ids[json.dumps(source, sort_keys=True)] for source in sources],
                "hold": max(source["hold"] for source in sources),
                "side": 1 if sources[0]["side"] == "long" else -1,
            }
            for family, sources in recipes
        ],
        "start": start,
        "end": end,
        "costBps": cost_bps,
        "workers": 0,
    }
    completed = subprocess.run(
        [str(binary)],
        input=json.dumps(payload, separators=(",", ":")).encode(),
        capture_output=True,
        check=True,
    )
    return json.loads(completed.stdout)["results"]


def _recipes(sources: list[dict]) -> list[tuple[str, tuple[dict, ...]]]:
    output = []
    for side in ("long", "short"):
        same_side = [source for source in sources if source["side"] == side]
        output.extend(("single_1", (source,)) for source in same_side)
        for pair in itertools.combinations(same_side, 2):
            if pair[0]["feature"] == pair[1]["feature"]:
                continue
            output.append(("all_2", pair))
            output.append(("any_2", pair))
    return output


def _recipe_id(symbol: str, family: str, sources: tuple[dict, ...]) -> str:
    return hashlib.sha256(
        json.dumps(
            {"symbol": symbol, "family": family, "sources": sources},
            sort_keys=True,
            separators=(",", ":"),
        ).encode()
    ).hexdigest()


def _combine(family: str, masks: list[int]) -> int:
    if family == "single_1":
        return masks[0]
    return masks[0] & masks[1] if family == "all_2" else masks[0] | masks[1]


def run_derivatives_campaign(
    candles_bundle: dict[str, Any],
    metrics_bundle: dict[str, Any],
    funding_bundle: dict[str, Any] | None = None,
    cost_bps: float = 25,
    max_recipes: int = 250_000,
    kernel: Path | None = None,
) -> dict[str, Any]:
    prepared = {}
    for symbol, candles in candles_bundle["series"].items():
        metrics = metrics_bundle["series"].get(symbol, [])
        funding = funding_bundle["series"].get(symbol, []) if funding_bundle else None
        features = feature_series(candles, metrics, funding)
        first = int(len(candles) * 0.6)
        sources = _rule_sources(features, first)
        recipes = _recipes(sources)
        masks = (
            {}
            if kernel is not None
            else {
                json.dumps(source, sort_keys=True): _mask(
                    features[source["feature"]], source["operator"], source["threshold"]
                )
                for source in sources
            }
        )
        source_ids = {
            json.dumps(source, sort_keys=True): index for index, source in enumerate(sources)
        }
        source_indices = (
            [
                _indices(features[source["feature"]], source["operator"], source["threshold"])
                for source in sources
            ]
            if kernel is not None
            else []
        )
        prepared[symbol] = (candles, recipes, masks, source_ids, source_indices)
    budgets = _balanced_budgets(
        {symbol: len(recipes) for symbol, (_, recipes, _, _, _) in prepared.items()}, max_recipes
    )
    trials, survivors = [], []
    for symbol in sorted(prepared):
        candles, recipes, masks, source_ids, source_indices = prepared[symbol]
        first, second = int(len(candles) * 0.6), int(len(candles) * 0.8)
        selected_recipes = recipes[: budgets[symbol]]
        if kernel is not None:
            development_rows = _go_metrics(
                kernel, candles, selected_recipes, source_ids, source_indices, 0, first, cost_bps
            )
            validation_rows = _go_metrics(
                kernel,
                candles,
                selected_recipes,
                source_ids,
                source_indices,
                first,
                second,
                cost_bps,
            )
        else:
            development_rows = validation_rows = None
        for recipe_index, (family, sources) in enumerate(selected_recipes):
            hold = max(source["hold"] for source in sources)
            side = 1 if sources[0]["side"] == "long" else -1
            if kernel is not None:
                development = development_rows[recipe_index]
                validation = validation_rows[recipe_index]
            else:
                mask = _combine(
                    family, [masks[json.dumps(source, sort_keys=True)] for source in sources]
                )
                development = return_metrics(
                    masked_returns(candles, mask, hold, cost_bps, 0, first, side)
                )
                validation = return_metrics(
                    masked_returns(candles, mask, hold, cost_bps, first, second, side)
                )
            passed = bool(
                development["trades"] >= 30
                and validation["trades"] >= 15
                and (development["meanReturn"] or 0) > 0
                and (validation["meanReturn"] or 0) > 0
            )
            trial = {
                "recipeId": _recipe_id(symbol, family, sources),
                "symbol": symbol,
                "marketSymbol": symbol.removesuffix("USDT"),
                "family": family,
                "side": sources[0]["side"],
                "hold": hold,
                "sources": list(sources),
                "development": development,
                "validation": validation,
                "status": "candidate" if passed else "rejected",
                "confirmationRead": False,
            }
            if passed:
                effect = min(development["meanReturn"], validation["meanReturn"])
                trial["selectionScore"] = (
                    effect * math.sqrt(validation["trades"]) / (1 + validation["maxDrawdown"])
                )
                survivors.append(trial)
            trials.append(trial)
    survivors.sort(key=lambda row: row["selectionScore"], reverse=True)
    selected, assets, structures = [], {}, {}
    for candidate in survivors:
        structure = (
            candidate["family"]
            + ":"
            + "+".join(sorted(source["feature"] for source in candidate["sources"]))
        )
        symbol = candidate["symbol"]
        if assets.get(symbol, 0) >= 4 or structures.get(structure, 0) >= 2:
            continue
        selected.append(candidate)
        assets[symbol] = assets.get(symbol, 0) + 1
        structures[structure] = structures.get(structure, 0) + 1
        if len(selected) == 24:
            break
    return {
        "protocol": {
            "family": "public_futures_positioning_composites",
            "costBps": cost_bps,
            "split": [0.6, 0.2, 0.2],
            "selectionUses": ["development", "validation"],
            "confirmationRead": False,
            "fills": "next_bar_open",
            "nonOverlapping": True,
            "thresholdPolicy": "development_quantiles_only",
            "allocation": "balanced_water_fill_by_symbol",
            "recipeBudgetBySymbol": budgets,
            "maxRecipes": max_recipes,
            "engine": "go_batch_v1" if kernel is not None else "python_bitmask_v1",
        },
        "evaluated": len(trials),
        "survivors": len(survivors),
        "trials": trials,
        "selected": selected,
        "conclusion": "forward_candidates" if selected else "no_candidate",
    }


def merge_derivatives_campaigns(campaigns: list[dict[str, Any]], limit: int = 24) -> dict[str, Any]:
    if not campaigns or limit < 1:
        raise ValueError("derivatives campaigns and a positive limit are required")
    candidates = [
        trial
        for campaign in campaigns
        for trial in campaign["trials"]
        if trial["status"] == "candidate"
    ]
    candidates.sort(key=lambda row: row["selectionScore"], reverse=True)
    selected, assets, structures, seen = [], {}, {}, set()
    for candidate in candidates:
        if candidate["recipeId"] in seen:
            continue
        structure = (
            candidate["family"]
            + ":"
            + "+".join(sorted(source["feature"] for source in candidate["sources"]))
        )
        symbol = candidate["symbol"]
        if assets.get(symbol, 0) >= 4 or structures.get(structure, 0) >= 2:
            continue
        selected.append(candidate)
        seen.add(candidate["recipeId"])
        assets[symbol] = assets.get(symbol, 0) + 1
        structures[structure] = structures.get(structure, 0) + 1
        if len(selected) == limit:
            break
    protocol = dict(campaigns[0]["protocol"])
    protocol.update(
        {
            "allocation": "per_asset_shards_merged",
            "confirmationRead": False,
            "frozenConfirmationFamilySize": len(selected),
        }
    )
    protocol.pop("recipeBudgetBySymbol", None)
    return {
        "protocol": protocol,
        "evaluated": sum(campaign["evaluated"] for campaign in campaigns),
        "survivors": len(candidates),
        "trials": [trial for campaign in campaigns for trial in campaign["trials"]],
        "selected": selected,
        "coverage": {"assets": assets, "structures": structures},
        "conclusion": "forward_candidates" if selected else "no_candidate",
    }


def confirm_derivatives_selection(
    candles_bundle: dict[str, Any],
    metrics_bundle: dict[str, Any],
    funding_bundle: dict[str, Any] | None,
    selection: dict[str, Any],
    cost_bps: float = 25,
    stress_cost_bps: float = 50,
) -> dict[str, Any]:
    results = []
    cache = {}
    for candidate in selection["selected"]:
        symbol = candidate["symbol"]
        candles = candles_bundle["series"][symbol]
        features = cache.setdefault(
            symbol,
            feature_series(
                candles,
                metrics_bundle["series"].get(symbol, []),
                funding_bundle["series"].get(symbol, []) if funding_bundle else None,
            ),
        )
        masks = [
            _mask(features[source["feature"]], source["operator"], source["threshold"])
            for source in candidate["sources"]
        ]
        mask = _combine(candidate["family"], masks)
        start, end = int(len(candles) * 0.8), len(candles)
        side = 1 if candidate["side"] == "long" else -1
        outcomes = masked_outcomes(candles, mask, candidate["hold"], cost_bps, start, end, side)
        returns = [row["return"] for row in outcomes]
        stress = masked_returns(candles, mask, candidate["hold"], stress_cost_bps, start, end, side)
        raw_p = sign_flip_p_value(returns, int(candidate["recipeId"][:16], 16), samples=100_000)
        results.append(
            {
                **{
                    key: candidate[key]
                    for key in (
                        "recipeId",
                        "symbol",
                        "marketSymbol",
                        "family",
                        "side",
                        "hold",
                        "sources",
                    )
                },
                "trades": len(returns),
                "totalReturn": sum(returns),
                "meanReturn": sum(returns) / len(returns) if returns else None,
                "stressMeanReturn": sum(stress) / len(stress) if stress else None,
                "winRate": sum(value > 0 for value in returns) / len(returns) if returns else None,
                "rawPValue": raw_p,
                "outcomes": outcomes,
            }
        )
    adjusted = holm_adjust([row["rawPValue"] for row in results])
    for row, value in zip(results, adjusted, strict=True):
        row["holmAdjustedPValue"] = value
        row["supportedUnderTestConditions"] = bool(
            row["trades"] >= 20
            and (row["meanReturn"] or 0) > 0
            and (row["stressMeanReturn"] or 0) > 0
            and value <= 0.05
        )
    supported = sum(row["supportedUnderTestConditions"] for row in results)
    return {
        "protocol": {
            "partition": "final_20_percent_chronological",
            "opened": True,
            "familySize": len(results),
            "costBps": cost_bps,
            "stressCostBps": stress_cost_bps,
            "minimumTrades": 20,
            "multipleComparisonCorrection": "Holm family-wise alpha 0.05",
            "fills": "next_bar_open",
            "nonOverlapping": True,
        },
        "results": results,
        "supported": supported,
        "conclusion": "supported_under_test_conditions" if supported else "confirmation_failed",
    }


def write_json(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2) + "\n")
