import datetime as dt

import pytest

from scarlett_research.carry import carry_outcomes, merge_series_bundles


def _time(hours: int, minutes: int = 0) -> str:
    value = dt.datetime(2025, 1, 1, tzinfo=dt.UTC) + dt.timedelta(hours=hours, minutes=minutes)
    return value.isoformat().replace("+00:00", "Z")


def test_carry_uses_published_rate_then_enters_on_next_candle():
    symbols = ("AUSDT", "BUSDT", "CUSDT", "DUSDT")
    candles = {
        symbol: [{"time": _time(hour), "open": 100.0} for hour in (0, 8, 16)]
        + [{"time": _time(hour, 5), "open": 100.0} for hour in (0, 8)]
        for symbol in symbols
    }
    rates = {"AUSDT": -0.001, "BUSDT": -0.0001, "CUSDT": 0.0001, "DUSDT": 0.001}
    funding = {
        symbol: [{"time": _time(hour), "fundingRate": rate} for hour in (0, 8, 16)]
        for symbol, rate in rates.items()
    }
    outcomes = carry_outcomes(
        candles,
        funding,
        {"assetsPerSide": 1, "holdFundingEvents": 1, "minimumSpreadBps": 0},
        cost_bps=1,
    )
    assert outcomes[0]["longs"] == ["AUSDT"]
    assert outcomes[0]["shorts"] == ["DUSDT"]
    assert outcomes[0]["entryAfter"] == _time(0)
    assert outcomes[0]["fundingReturn"] == pytest.approx(0.001)
    assert outcomes[0]["return"] == pytest.approx(0.0009)


def test_bundle_merge_rejects_duplicate_symbols():
    bundle = {"series": {"BTCUSDT": []}}
    with pytest.raises(ValueError, match="duplicate symbol"):
        merge_series_bundles([bundle, bundle])
