import datetime as dt

from scarlett_research.backtest import (
    Rule,
    backtest,
    freeze_universe_selection,
    screen_universe,
    walk_forward,
)
from scarlett_research.market_data import (
    BinanceArchiveConnector,
    _is_archive_data_row,
    _next_month,
    fetch_bundle,
    normalize_market_symbol,
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


def test_universe_screen_never_reads_or_emits_confirmation():
    result = screen_universe(
        {"series": {"BTCUSDT": candles(600), "ETHUSDT": candles(600)}},
        max_rules=20,
        limit=1,
    )
    assert result["protocol"]["selectionDataOnly"] is True
    assert result["protocol"]["confirmationRead"] is False
    assert all(
        "confirmation" not in candidate
        for fold in result["folds"]
        for candidate in fold["candidates"]
    )


def test_freeze_universe_excludes_consumed_assets_and_selects_one_rule_each():
    candidate = {
        "development": {"rule": {"kind": "breakout", "fast": 2, "slow": 12, "hold": 4}},
        "validation": {"trades": 20},
        "selectionScore": 1.0,
    }
    screen = {
        "ranking": [
            {"symbol": "USEDUSDT", "candidates": [candidate]},
            {"symbol": "FRESHUSDT", "candidates": [candidate]},
        ]
    }
    frozen = freeze_universe_selection(screen, {"USEDUSDT"}, limit=2)
    assert frozen["symbols"] == ["FRESHUSDT"]
    assert frozen["protocol"]["confirmationRead"] is False


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


def test_binance_instrument_is_mapped_to_forecast_market():
    assert normalize_market_symbol("BTCUSDT", "binance_spot_archive") == "BTC"
    assert normalize_market_symbol("BTC", "hyperliquid") == "BTC"


def test_broad_fetch_preserves_successes_and_records_symbol_failures(tmp_path):
    class Connector:
        name = "test"

        def candles(self, symbol, interval, start_ms, end_ms):
            if symbol == "BADUSDT":
                raise TimeoutError("unavailable")
            return [{"symbol": symbol}]

    output = tmp_path / "bundle.json"
    result = fetch_bundle(Connector(), ["BTCUSDT", "BADUSDT"], "4h", 0, 1, output)
    assert result["symbols"] == {"BTCUSDT": 1, "BADUSDT": 0}
    assert result["failures"]["BADUSDT"]["type"] == "TimeoutError"
