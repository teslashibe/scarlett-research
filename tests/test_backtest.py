import datetime as dt

from scarlett_research.backtest import Rule, backtest, walk_forward
from scarlett_research.market_data import (
    BinanceArchiveConnector,
    _is_archive_data_row,
    _next_month,
)


def candles(count: int, drift: float = 0.001):
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    price = 100.0
    rows = []
    for index in range(count):
        open_price = price
        price *= 1 + drift + (0.003 if index % 17 == 0 else -0.0002)
        rows.append(
            {
                "time": (start + dt.timedelta(minutes=15 * index)).isoformat(),
                "closeTime": (start + dt.timedelta(minutes=15 * (index + 1))).isoformat(),
                "open": open_price,
                "high": max(open_price, price),
                "low": min(open_price, price),
                "close": price,
                "volume": 1,
            }
        )
    return rows


def test_backtest_enters_after_signal_and_charges_cost():
    data = candles(500)
    rule = Rule("sma_cross", 3, 21, 8, "both")
    free = backtest(data, rule, 0)
    paid = backtest(data, rule, 13)
    assert free["trades"] == paid["trades"]
    assert paid["totalReturn"] <= free["totalReturn"]


def test_walk_forward_is_bounded():
    result = walk_forward({"series": {"BTC": candles(600)}}, max_rules=20)
    assert result["protocol"]["max_rules"] == 20
    assert len(result["folds"][0]["finalists"]) == 5


def test_archive_month_rollover():
    assert _next_month(dt.datetime(2025, 12, 1, tzinfo=dt.UTC)) == dt.datetime(
        2026, 1, 1, tzinfo=dt.UTC
    )


def test_archive_market_selection():
    spot = BinanceArchiveConnector.for_market("spot")
    futures = BinanceArchiveConnector.for_market("um_futures")
    assert spot.name == "binance_spot_archive"
    assert "/spot/" in spot.base_url
    assert futures.name == "binance_um_futures_archive"
    assert "/futures/um/" in futures.base_url


def test_futures_archive_header_is_not_a_timestamp():
    assert not _is_archive_data_row(["open_time", "open", "high"])
    assert _is_archive_data_row(["1577836800000", "1", "2"])
