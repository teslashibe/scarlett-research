from scarlett_research.derivatives import (
    _change,
    _combine,
    _rolling_mean,
    _rolling_std,
    feature_series,
    merge_derivatives_campaigns,
)


def test_single_derivatives_recipe_preserves_its_mask():
    assert _combine("single_1", [0b101]) == 0b101


def test_changes_and_rolling_means_do_not_read_future_values():
    values = [1.0, 2.0, 4.0, 8.0]
    assert _change(values, 2) == [None, None, 3.0, 3.0]
    assert _rolling_mean(values, 2) == [None, 1.5, 3.0, 6.0]
    assert _rolling_std(values, 2) == [None, 0.5, 1.0, 2.0]


def test_feature_alignment_does_not_forward_fill_missing_metrics():
    candles = [
        {
            "time": "2025-01-01T00:00:00Z",
            "open": 1,
            "high": 2,
            "low": 1,
            "close": 2,
            "volume": 10,
            "trades": 2,
        },
        {
            "time": "2025-01-01T00:05:00Z",
            "open": 2,
            "high": 3,
            "low": 2,
            "close": 3,
            "volume": 20,
            "trades": 3,
        },
    ]
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


def test_funding_is_only_forward_filled_after_publication():
    candles = [
        {
            "time": "2025-01-01T00:00:00Z",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "trades": 1,
        },
        {
            "time": "2025-01-01T00:05:00Z",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "trades": 1,
        },
        {
            "time": "2025-01-01T00:10:00Z",
            "open": 1,
            "high": 1,
            "low": 1,
            "close": 1,
            "volume": 1,
            "trades": 1,
        },
    ]
    metrics = []
    funding = [{"time": "2025-01-01T00:05:00Z", "fundingRate": 0.0001}]
    assert feature_series(candles, metrics, funding)["funding_rate"] == [None, 0.0001, 0.0001]


def test_price_regime_features_use_closed_bar_data():
    candles = [
        {
            "time": f"2025-01-01T00:{index:02d}:00Z",
            "open": value,
            "high": value + 1,
            "low": value - 1,
            "close": value,
            "volume": value * 10,
            "trades": value * 2,
        }
        for index, value in enumerate(range(1, 14))
    ]
    features = feature_series(candles, [])
    assert features["price_return_1h"][:12] == [None] * 12
    assert features["price_return_1h"][12] == 12.0
    assert features["realized_volatility_1h"][10] is None
    assert features["realized_volatility_1h"][11] is None
    assert features["realized_volatility_1h"][12] is not None
    assert features["bar_range"][0] == 2.0


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
