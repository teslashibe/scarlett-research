from __future__ import annotations

import bisect
import datetime as dt
import hashlib
import itertools
import json
import math
from typing import Any

from .catalogue import holm_adjust, sign_flip_p_value
from .composites import return_metrics


def merge_series_bundles(bundles: list[dict[str, Any]]) -> dict[str, list[dict[str, Any]]]:
    merged: dict[str, list[dict[str, Any]]] = {}
    for bundle in bundles:
        for symbol, rows in bundle["series"].items():
            if symbol in merged:
                raise ValueError(f"duplicate symbol across bundles: {symbol}")
            merged[symbol] = rows
    return merged


def carry_recipes(orientation: str = "carry") -> list[dict[str, Any]]:
    if orientation not in {"carry", "momentum"}:
        raise ValueError("orientation must be carry or momentum")
    return [
        {
            "orientation": orientation,
            "assetsPerSide": assets,
            "holdHours": hold,
            "minimumSpreadBps": spread,
        }
        for assets, hold, spread in itertools.product(
            (1, 2, 3), (8, 24, 48), (0.0, 1.0, 2.0, 5.0, 10.0)
        )
    ]


def _recipe_id(recipe: dict[str, Any]) -> str:
    return hashlib.sha256(
        json.dumps(recipe, sort_keys=True, separators=(",", ":")).encode()
    ).hexdigest()


def _anchor_events(funding_by_symbol: dict[str, list[dict[str, Any]]]) -> list[str]:
    events = {row["time"] for rows in funding_by_symbol.values() for row in rows}
    return sorted(
        event
        for event in events
        if (timestamp := dt.datetime.fromisoformat(event)).hour % 8 == 0 and timestamp.minute == 0
    )


def carry_outcomes(
    candles_by_symbol: dict[str, list[dict[str, Any]]],
    funding_by_symbol: dict[str, list[dict[str, Any]]],
    recipe: dict[str, Any],
    cost_bps: float,
    start_event: int = 0,
    end_event: int | None = None,
) -> list[dict[str, Any]]:
    """Evaluate causal, non-overlapping funding-ranked market-neutral baskets."""
    funding_maps = {
        symbol: {row["time"]: float(row["fundingRate"]) for row in rows}
        for symbol, rows in funding_by_symbol.items()
        if symbol in candles_by_symbol
    }
    events = _anchor_events(funding_by_symbol)
    end_event = len(events) if end_event is None else min(end_event, len(events))
    ordered_candles = {
        symbol: sorted(candles, key=lambda row: row["time"])
        for symbol, candles in candles_by_symbol.items()
    }
    times = {
        symbol: [row["time"] for row in candles] for symbol, candles in ordered_candles.items()
    }
    opens = {
        symbol: [float(row["open"]) for row in candles]
        for symbol, candles in ordered_candles.items()
    }
    assets_per_side = int(recipe["assetsPerSide"])
    hold_hours = int(recipe["holdHours"])
    hold_anchors = hold_hours // 8
    minimum_spread = float(recipe["minimumSpreadBps"]) / 10_000
    outcomes = []
    event_index = start_event
    while event_index + hold_anchors < end_event:
        signal_time = events[event_index]
        exit_timestamp = dt.datetime.fromisoformat(signal_time)
        exit_time = (
            (exit_timestamp + dt.timedelta(hours=hold_hours)).isoformat().replace("+00:00", "Z")
        )
        available = [
            (rate_map[signal_time], symbol)
            for symbol, rate_map in funding_maps.items()
            if signal_time in rate_map
        ]
        available.sort()
        if (
            len(available) < assets_per_side * 2
            or available[-1][0] - available[0][0] < minimum_spread
        ):
            event_index += 1
            continue
        if recipe.get("orientation", "carry") == "momentum":
            longs = [symbol for _, symbol in available[-assets_per_side:]]
            shorts = [symbol for _, symbol in available[:assets_per_side]]
        else:
            longs = [symbol for _, symbol in available[:assets_per_side]]
            shorts = [symbol for _, symbol in available[-assets_per_side:]]
        legs = [(symbol, 1) for symbol in longs] + [(symbol, -1) for symbol in shorts]
        leg_returns = []
        funding_return = 0.0
        valid = True
        for symbol, side in legs:
            entry_index = bisect.bisect_right(times[symbol], signal_time)
            exit_index = bisect.bisect_left(times[symbol], exit_time)
            if entry_index >= len(opens[symbol]) or exit_index >= len(opens[symbol]):
                valid = False
                break
            future_funding = sum(
                rate
                for event, rate in funding_maps[symbol].items()
                if signal_time < event <= exit_time
            )
            price_return = side * (opens[symbol][exit_index] / opens[symbol][entry_index] - 1)
            funding_leg = -side * future_funding
            leg_returns.append(price_return + funding_leg - cost_bps / 10_000)
            funding_return += funding_leg / len(legs)
        if valid:
            outcomes.append(
                {
                    "signalTime": signal_time,
                    "entryAfter": signal_time,
                    "exitTime": exit_time,
                    "longs": longs,
                    "shorts": shorts,
                    "return": sum(leg_returns) / len(leg_returns),
                    "fundingReturn": funding_return,
                }
            )
            event_index += hold_anchors
        else:
            event_index += 1
    return outcomes


def run_carry_campaign(
    candle_bundles: list[dict[str, Any]],
    funding_bundles: list[dict[str, Any]],
    cost_bps: float = 13,
    selection_limit: int = 12,
    orientation: str = "carry",
    stress_cost_bps: float = 25,
) -> dict[str, Any]:
    candles = merge_series_bundles(candle_bundles)
    funding = merge_series_bundles(funding_bundles)
    events = _anchor_events(funding)
    first, second = int(len(events) * 0.6), int(len(events) * 0.8)
    trials, survivors = [], []
    for recipe in carry_recipes(orientation):
        development_outcomes = carry_outcomes(candles, funding, recipe, cost_bps, 0, first)
        validation_outcomes = carry_outcomes(candles, funding, recipe, cost_bps, first, second)
        development = return_metrics([row["return"] for row in development_outcomes])
        validation = return_metrics([row["return"] for row in validation_outcomes])
        stress_increment = (stress_cost_bps - cost_bps) / 10_000
        development_stress_mean = (
            development["meanReturn"] - stress_increment
            if development["meanReturn"] is not None
            else None
        )
        validation_stress_mean = (
            validation["meanReturn"] - stress_increment
            if validation["meanReturn"] is not None
            else None
        )
        passed = bool(
            development["trades"] >= 30
            and validation["trades"] >= 10
            and (development["meanReturn"] or 0) > 0
            and (validation["meanReturn"] or 0) > 0
            and (development_stress_mean or 0) > 0
            and (validation_stress_mean or 0) > 0
        )
        trial = {
            "recipeId": _recipe_id(recipe),
            "recipe": recipe,
            "development": development,
            "validation": validation,
            "developmentStressMeanReturn": development_stress_mean,
            "validationStressMeanReturn": validation_stress_mean,
            "status": "candidate" if passed else "rejected",
            "confirmationRead": False,
        }
        if passed:
            trial["selectionScore"] = (
                min(development["meanReturn"], validation["meanReturn"])
                * math.sqrt(validation["trades"])
                / (1 + validation["maxDrawdown"])
            )
            survivors.append(trial)
        trials.append(trial)
    survivors.sort(key=lambda row: row["selectionScore"], reverse=True)
    selected = survivors[:selection_limit]
    return {
        "protocol": {
            "family": f"cross_sectional_funding_{orientation}",
            "split": [0.6, 0.2, 0.2],
            "selectionUses": ["development", "validation"],
            "confirmationRead": False,
            "rankingInput": "funding_rate_already_published_at_signal_time",
            "entry": "first_candle_open_strictly_after_signal_funding_event",
            "fundingCashflows": "future_events_during_holding_period_only",
            "marketNeutral": True,
            "costBpsPerLegRoundTrip": cost_bps,
            "selectionStressCostBpsPerLegRoundTrip": stress_cost_bps,
            "nonOverlapping": True,
            "recipeCount": len(trials),
        },
        "assets": sorted(set(candles) & set(funding)),
        "events": len(events),
        "trials": trials,
        "survivors": len(survivors),
        "selected": selected,
        "conclusion": "confirmation_candidates" if selected else "no_candidate",
    }


def confirm_carry_selection(
    candle_bundles: list[dict[str, Any]],
    funding_bundles: list[dict[str, Any]],
    selection: dict[str, Any],
    cost_bps: float = 13,
    stress_cost_bps: float = 25,
    full_history: bool = False,
) -> dict[str, Any]:
    candles = merge_series_bundles(candle_bundles)
    funding = merge_series_bundles(funding_bundles)
    events = _anchor_events(funding)
    start = 0 if full_history else int(len(events) * 0.8)
    results = []
    for candidate in selection["selected"]:
        recipe = candidate["recipe"]
        outcomes = carry_outcomes(candles, funding, recipe, cost_bps, start, len(events))
        stress = carry_outcomes(candles, funding, recipe, stress_cost_bps, start, len(events))
        returns = [row["return"] for row in outcomes]
        stress_returns = [row["return"] for row in stress]
        metrics = return_metrics(returns)
        results.append(
            {
                "recipeId": candidate["recipeId"],
                "recipe": recipe,
                **metrics,
                "stressMeanReturn": (
                    sum(stress_returns) / len(stress_returns) if stress_returns else None
                ),
                "rawPValue": sign_flip_p_value(
                    returns, int(candidate["recipeId"][:16], 16), samples=100_000
                ),
                "outcomes": outcomes,
            }
        )
    adjusted = holm_adjust([row["rawPValue"] for row in results])
    for row, adjusted_p in zip(results, adjusted, strict=True):
        row["holmAdjustedPValue"] = adjusted_p
        row["supportedUnderTestConditions"] = bool(
            row["trades"] >= 20
            and (row["meanReturn"] or 0) > 0
            and (row["stressMeanReturn"] or 0) > 0
            and adjusted_p <= 0.05
        )
    supported = sum(row["supportedUnderTestConditions"] for row in results)
    return {
        "protocol": {
            "family": selection["protocol"]["family"],
            "partition": (
                "full_history_independent_asset_transfer"
                if full_history
                else "final_20_percent_chronological"
            ),
            "opened": True,
            "familySize": len(results),
            "costBpsPerLegRoundTrip": cost_bps,
            "stressCostBpsPerLegRoundTrip": stress_cost_bps,
            "multipleComparisonCorrection": "Holm family-wise alpha 0.05",
        },
        "results": results,
        "supported": supported,
        "conclusion": ("supported_under_test_conditions" if supported else "confirmation_failed"),
    }
