from scarlett_research.market_data import _metrics_record


def test_metrics_record_normalizes_public_archive_fields():
    result = _metrics_record(
        {
            "create_time": "2025-01-02 00:05:00",
            "symbol": "BTCUSDT",
            "sum_open_interest": "92246.706",
            "sum_open_interest_value": "8721833805.594",
            "count_toptrader_long_short_ratio": "1.66429",
            "sum_toptrader_long_short_ratio": "2.112037",
            "count_long_short_ratio": "1.673763",
            "sum_taker_long_short_vol_ratio": "0.708369",
        }
    )
    assert result == {
        "time": "2025-01-02T00:05:00Z",
        "symbol": "BTCUSDT",
        "openInterest": 92246.706,
        "openInterestValue": 8721833805.594,
        "topTraderAccountLongShortRatio": 1.66429,
        "topTraderPositionLongShortRatio": 2.112037,
        "globalLongShortRatio": 1.673763,
        "takerLongShortVolumeRatio": 0.708369,
    }


def test_metrics_record_preserves_missing_values_as_null():
    row = {
        "create_time": "2025-01-02 00:05:00",
        "symbol": "BTCUSDT",
        "sum_open_interest": "1",
        "sum_open_interest_value": "2",
        "count_toptrader_long_short_ratio": "",
        "sum_toptrader_long_short_ratio": "3",
        "count_long_short_ratio": "4",
        "sum_taker_long_short_vol_ratio": "5",
    }
    assert _metrics_record(row)["topTraderAccountLongShortRatio"] is None
