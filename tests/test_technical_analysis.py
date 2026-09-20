import datetime as dt

from scarlett_research.technical_analysis import analyze, resample


def candles(count: int):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    return [
        {
            "time": (start + dt.timedelta(minutes=5 * index)).isoformat(),
            "open": index + 1,
            "high": index + 2,
            "low": index,
            "close": index + 1.5,
            "volume": 10,
        }
        for index in range(count)
    ]


def test_resample_uses_complete_buckets_only():
    result = resample(candles(7), "5m", "15m")
    assert len(result) == 2
    assert result[0]["open"] == 1
    assert result[0]["close"] == 3.5
    assert result[0]["volume"] == 30


def test_ta_reports_local_provenance_and_warmup():
    result = analyze(candles(20), "rsi", 14)
    assert result["provenance"] == "local_research_calculation"
    assert result["warm"] is True
    assert result["latest"] is not None
