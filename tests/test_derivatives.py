from scarlett_research.derivatives import (
    _change,
    _combine,
    _rolling_mean,
    feature_series,
    merge_derivatives_campaigns,
)


def test_single_derivatives_recipe_preserves_its_mask():
    assert _combine("single_1", [0b101]) == 0b101


def test_changes_and_rolling_means_do_not_read_future_values():
    values = [1.0, 2.0, 4.0, 8.0]
    assert _change(values, 2) == [None, None, 3.0, 3.0]
    assert _rolling_mean(values, 2) == [None, 1.5, 3.0, 6.0]


def test_feature_alignment_does_not_forward_fill_missing_metrics():
    candles = [{"time": "2025-01-01T00:00:00Z"}, {"time": "2025-01-01T00:05:00Z"}]
    metrics = [
        {
            "time": "2025-01-01T00:00:00Z",
            "openInterest": 1.0,
            "topTraderAccountLongShortRatio": 1.0,
            "topTraderPositionLongShortRatio": 1.0,
            "globalLongShortRatio": 1.0,
            "takerLongShortVolumeRatio": 1.0,
        }
    ]
    features = feature_series(candles, metrics)
    assert features["global_ratio"] == [1.0, None]


def test_derivatives_merge_uses_all_shard_trials():
    trial = {
        "recipeId": "a",
        "symbol": "BTCUSDT",
        "family": "all_2",
        "sources": [{"feature": "x"}, {"feature": "y"}],
        "status": "candidate",
        "selectionScore": 1.0,
    }
    campaign = {
        "protocol": {"family": "derivatives", "confirmationRead": False},
        "evaluated": 1,
        "trials": [trial],
        "selected": [],
    }
    merged = merge_derivatives_campaigns([campaign])
    assert merged["selected"] == [trial]
