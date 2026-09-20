import random

import pytest

from scarlett_research.catalogue import SignalRule
from scarlett_research.mass_search import (
    _balanced_budgets,
    _combine,
    _recipe_count,
    _recipe_id,
    _signal_mask,
    masked_outcomes,
    masked_returns,
    merge_mass_shards,
)


def test_recipe_budget_is_balanced_and_redistributes_unused_capacity():
    assert _balanced_budgets({"ADA": 100, "BTC": 100, "ETH": 2}, 12) == {
        "ADA": 5,
        "BTC": 5,
        "ETH": 2,
    }


def test_recipe_count_matches_enumeration_families():
    sources = [
        {"rule": SignalRule("gt", 0, side, 1)}
        for side in ("long", "long", "long", "short", "short")
    ]
    assert _recipe_count(sources) == 2 * (3 + 1) + 1


def test_masks_combine_as_deployable_all_and_any_expressions():
    left = _signal_mask([0, 2, 0, 2], SignalRule("gt", 1, "long", 1))
    right = _signal_mask([0, 0, 3, 3], SignalRule("gt", 1, "long", 1))
    assert _combine("all_2", [left, right]) == 1 << 3
    assert _combine("any_2", [left, right]) == (1 << 1) | (1 << 2) | (1 << 3)


def test_masked_returns_use_next_open_and_do_not_overlap():
    candles = [{"open": value} for value in (100, 101, 102, 103, 104, 105, 106)]
    mask = (1 << 0) | (1 << 1) | (1 << 3)
    returns = masked_returns(candles, mask, hold=1, cost_bps=0, start=0, end=len(candles))
    assert returns == [102 / 101 - 1, 105 / 104 - 1]


def test_return_only_kernel_matches_timestamped_outcomes():
    candles = [{"open": value, "time": str(index)} for index, value in enumerate(range(100, 110))]
    mask = sum(1 << index for index in (0, 1, 3, 7))
    direct = masked_returns(candles, mask, hold=2, cost_bps=13, start=0, end=len(candles))
    from_outcomes = [
        row["return"] for row in masked_outcomes(candles, mask, 2, 13, 0, len(candles))
    ]
    assert direct == from_outcomes


def test_optimized_kernel_matches_straightforward_scan():
    generator = random.Random(7)
    candles = [{"open": 100 + generator.random()} for _ in range(200)]
    for hold in (1, 4, 16):
        for _ in range(20):
            mask = sum(1 << index for index in range(200) if generator.random() < 0.2)
            expected = []
            index = 0
            while index + hold + 1 < len(candles):
                if not mask & (1 << index):
                    index += 1
                    continue
                entry, exit_index = index + 1, index + 1 + hold
                expected.append(candles[exit_index]["open"] / candles[entry]["open"] - 1)
                index = exit_index + 1
            assert masked_returns(candles, mask, hold, 0, 0, len(candles)) == expected


def test_recipe_identity_is_order_independent_after_canonical_source_ordering():
    source = {
        "calculation": {"id": "feature", "function": "RSI"},
        "output": "real",
        "rule": {"operator": "gt", "value": 50, "side": "long", "hold": 4},
    }
    assert _recipe_id("BTC", "all_2", [source, source]) == _recipe_id(
        "BTC", "all_2", [source, source]
    )


def test_merge_rejects_mismatched_protocols():
    base = {
        "protocol": {"family": "mass", "shards": 2, "shard": 0},
        "evaluated": 1,
        "selected": [],
    }
    other = {
        "protocol": {"family": "different", "shards": 2, "shard": 1},
        "evaluated": 1,
        "selected": [],
    }
    with pytest.raises(ValueError, match="protocols do not match"):
        merge_mass_shards([base, other])


def test_merge_uses_all_survivors_not_only_per_shard_shortlist():
    source = {
        "function": "RSI",
        "category": "Momentum Indicators",
        "calculation": {"id": "feature", "function": "RSI"},
        "output": "real",
        "rule": {"operator": "gt", "value": 50, "side": "long", "hold": 4},
    }
    trial = {
        "recipeId": "a",
        "symbol": "BTCUSDT",
        "marketSymbol": "BTC",
        "side": "long",
        "family": "all_2",
        "hold": 4,
        "sourceIds": ["s", "s"],
        "development": {},
        "validation": {},
        "status": "candidate",
        "confirmationRead": False,
        "selectionScore": 2.0,
    }
    shard = {
        "protocol": {"family": "mass", "shards": 1, "shard": 0},
        "evaluated": 1,
        "sourceCatalog": {"s": source},
        "trials": [trial],
        "selected": [],
    }
    merged = merge_mass_shards([shard])
    assert merged["selected"][0]["recipeId"] == "a"
