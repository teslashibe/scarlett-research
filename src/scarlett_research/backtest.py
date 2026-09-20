from __future__ import annotations

import hashlib
import itertools
import json
import math
from concurrent.futures import ProcessPoolExecutor
from dataclasses import asdict, dataclass
from typing import Any

from .technical_analysis import indicator


@dataclass(frozen=True)
class Rule:
    kind: str
    fast: int
    slow: int
    hold: int
    side: str = "both"


def _signal(closes: list[float], rule: Rule) -> list[int]:
    fast = indicator(closes, "ema" if rule.kind == "ema_cross" else "sma", rule.fast)
    slow = indicator(closes, "sma", rule.slow)
    output = [0] * len(closes)
    if rule.kind in {"sma_cross", "ema_cross", "trend_state"}:
        for index in range(1, len(closes)):
            if None in (fast[index - 1], slow[index - 1], fast[index], slow[index]):
                continue
            if rule.kind == "trend_state":
                output[index] = 1 if fast[index] > slow[index] else -1
            elif fast[index - 1] <= slow[index - 1] and fast[index] > slow[index]:
                output[index] = 1
            elif fast[index - 1] >= slow[index - 1] and fast[index] < slow[index]:
                output[index] = -1
    elif rule.kind == "breakout":
        for index in range(rule.slow, len(closes)):
            previous = closes[index - rule.slow : index]
            output[index] = (
                1 if closes[index] > max(previous) else (-1 if closes[index] < min(previous) else 0)
            )
    elif rule.kind == "rsi_reversion":
        rsi = indicator(closes, "rsi", rule.slow)
        for index, value in enumerate(rsi):
            if value is not None:
                output[index] = 1 if value < rule.fast else (-1 if value > 100 - rule.fast else 0)
    elif rule.kind == "rsi_momentum":
        rsi = indicator(closes, "rsi", rule.slow)
        for index, value in enumerate(rsi):
            if value is not None:
                output[index] = (
                    1 if value > 50 + rule.fast else (-1 if value < 50 - rule.fast else 0)
                )
    if rule.side == "long":
        return [max(value, 0) for value in output]
    if rule.side == "short":
        return [min(value, 0) for value in output]
    return output


def backtest(candles: list[dict[str, Any]], rule: Rule, cost_bps: float = 13) -> dict[str, Any]:
    """Signals use bar close; fills use the next bar open; positions never overlap."""
    closes = [float(row["close"]) for row in candles]
    signals = _signal(closes, rule)
    trades = []
    index = 0
    while index + rule.hold + 1 < len(candles):
        side = signals[index]
        if side == 0:
            index += 1
            continue
        entry_index, exit_index = index + 1, index + 1 + rule.hold
        entry, exit_price = float(candles[entry_index]["open"]), float(candles[exit_index]["open"])
        gross = side * (exit_price / entry - 1)
        trades.append(
            {
                "signalTime": candles[index]["closeTime"],
                "entryTime": candles[entry_index]["time"],
                "exitTime": candles[exit_index]["time"],
                "side": side,
                "grossReturn": gross,
                "netReturn": gross - cost_bps / 10_000,
            }
        )
        index = exit_index
    returns = [row["netReturn"] for row in trades]
    curve = peak = drawdown = 0.0
    for value in returns:
        curve += value
        peak = max(peak, curve)
        drawdown = max(drawdown, peak - curve)
    return {
        "rule": asdict(rule),
        "trades": len(trades),
        "totalReturn": sum(returns),
        "meanReturn": sum(returns) / len(returns) if returns else None,
        "winRate": sum(value > 0 for value in returns) / len(returns) if returns else None,
        "maxDrawdown": drawdown,
    }


def rules(max_rules: int = 500) -> list[Rule]:
    family = []
    for kind in ("sma_cross", "ema_cross"):
        for fast, slow, hold, side in itertools.product(
            (3, 5, 8, 13), (21, 34, 55, 89), (4, 8, 16, 32), ("long", "short", "both")
        ):
            if fast < slow:
                family.append(Rule(kind, fast, slow, hold, side))
    for threshold, period, hold, side in itertools.product(
        (20, 25, 30, 35), (7, 14, 21), (4, 8, 16), ("long", "short", "both")
    ):
        family.append(Rule("rsi_reversion", threshold, period, hold, side))
    for window, hold, side in itertools.product(
        (12, 24, 48, 96), (4, 8, 16, 32), ("long", "short", "both")
    ):
        family.append(Rule("breakout", 2, window, hold, side))
    for fast, slow, hold, side in itertools.product(
        (3, 5, 8, 13), (21, 34, 55, 89), (4, 8, 16, 32), ("long", "short", "both")
    ):
        family.append(Rule("trend_state", fast, slow, hold, side))
    for threshold, period, hold, side in itertools.product(
        (5, 10, 15), (7, 14, 21), (4, 8, 16), ("long", "short", "both")
    ):
        family.append(Rule("rsi_momentum", threshold, period, hold, side))
    return family[:max_rules]


def _evaluate_symbol(args: tuple[str, list[dict[str, Any]], float, int, int]) -> dict[str, Any]:
    symbol, candles, cost_bps, max_rules, min_confirmation_trades = args
    first, second = math.floor(len(candles) * 0.6), math.floor(len(candles) * 0.8)
    development, validation, confirmation = candles[:first], candles[first:second], candles[second:]
    ranked = sorted(
        (backtest(development, rule, cost_bps) for rule in rules(max_rules)),
        key=lambda row: (row["trades"] >= 10, row["meanReturn"] or -99),
        reverse=True,
    )
    checked = []
    for row in ranked[:5]:
        valid = backtest(validation, Rule(**row["rule"]), cost_bps)
        checked.append({"development": row, "validation": valid})
    eligible = [
        row
        for row in checked
        if row["validation"]["trades"] >= 5 and (row["validation"]["meanReturn"] or 0) > 0
    ]
    champion = max(eligible, key=lambda row: row["validation"]["meanReturn"], default=None)
    if champion:
        champion["confirmation"] = backtest(
            confirmation, Rule(**champion["development"]["rule"]), cost_bps
        )
        champion["decision"] = (
            "forward_candidate"
            if champion["confirmation"]["trades"] >= min_confirmation_trades
            and (champion["confirmation"]["meanReturn"] or 0) > 0
            else "confirmation_failed"
        )
    return {"symbol": symbol, "bars": len(candles), "finalists": checked, "champion": champion}


def walk_forward(
    bundle: dict[str, Any],
    cost_bps: float = 13,
    max_rules: int = 500,
    min_confirmation_trades: int = 10,
) -> dict[str, Any]:
    jobs = [
        (symbol, candles, cost_bps, max_rules, min_confirmation_trades)
        for symbol, candles in bundle["series"].items()
        if len(candles) >= 300
    ]
    with ProcessPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        folds = list(pool.map(_evaluate_symbol, jobs))
    selected = [
        {"symbol": fold["symbol"], **fold["champion"]} for fold in folds if fold["champion"]
    ]
    protocol = {
        "cost_bps": cost_bps,
        "max_rules": max_rules,
        "min_confirmation_trades": min_confirmation_trades,
        "split": [0.6, 0.2, 0.2],
        "fill": "next_bar_open",
        "non_overlapping": True,
    }
    return {
        "protocol": protocol,
        "protocol_hash": hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest(),
        "folds": folds,
        "selected": selected,
        "conclusion": "forward_candidates"
        if any(x["decision"] == "forward_candidate" for x in selected)
        else "no_confirmed_public_candle_edge",
    }


def _screen_symbol(args: tuple[str, list[dict[str, Any]], float, int]) -> dict[str, Any]:
    symbol, candles, cost_bps, max_rules = args
    split = math.floor(len(candles) * 0.6)
    development, validation = candles[:split], candles[split:]
    ranked = sorted(
        (backtest(development, rule, cost_bps) for rule in rules(max_rules)),
        key=lambda row: (row["trades"] >= 20, row["meanReturn"] or -99),
        reverse=True,
    )
    candidates = []
    for row in ranked[:10]:
        valid = backtest(validation, Rule(**row["rule"]), cost_bps)
        if (
            row["trades"] >= 20
            and valid["trades"] >= 10
            and (row["meanReturn"] or 0) > 0
            and (valid["meanReturn"] or 0) > 0
        ):
            score = (
                min(row["meanReturn"], valid["meanReturn"])
                * math.sqrt(valid["trades"])
                / (1 + valid["maxDrawdown"])
            )
            candidates.append({"development": row, "validation": valid, "selectionScore": score})
    return {
        "symbol": symbol,
        "bars": len(candles),
        "candidates": candidates,
        "bestScore": max((row["selectionScore"] for row in candidates), default=None),
    }


def screen_universe(
    bundle: dict[str, Any], cost_bps: float = 13, max_rules: int = 500, limit: int = 50
) -> dict[str, Any]:
    """Rank asset-family cells using selection-only history and leave later data untouched."""
    if limit < 1:
        raise ValueError("limit must be positive")
    jobs = [
        (symbol, candles, cost_bps, max_rules)
        for symbol, candles in bundle["series"].items()
        if len(candles) >= 300
    ]
    with ProcessPoolExecutor(max_workers=min(8, len(jobs))) as pool:
        folds = list(pool.map(_screen_symbol, jobs))
    eligible = sorted(
        (fold for fold in folds if fold["bestScore"] is not None),
        key=lambda row: row["bestScore"],
        reverse=True,
    )
    selected = eligible[:limit]
    protocol = {
        "purpose": "asset_family_prioritization_only",
        "selectionDataOnly": True,
        "confirmationRead": False,
        "split": [0.6, 0.4],
        "costBps": cost_bps,
        "maxRulesPerAsset": max_rules,
        "minimumBars": 300,
        "shortlistLimit": limit,
        "fills": "next_bar_open",
        "nonOverlapping": True,
    }
    return {
        "protocol": protocol,
        "inputAssets": len(bundle["series"]),
        "eligibleAssets": len(folds),
        "assetsWithCandidates": len(eligible),
        "count": len(selected),
        "symbols": [row["symbol"] for row in selected],
        "ranking": selected,
        "folds": folds,
        "conclusion": "shortlist_for_independent_research",
    }
