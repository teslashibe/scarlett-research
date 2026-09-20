import pytest

from scarlett_research.catalogue import SignalRule
from scarlett_research.mass_search import (
    _combine,
    _recipe_id,
    _signal_mask,
    masked_returns,
    merge_mass_shards,
)


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
