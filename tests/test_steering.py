from scarlett_research.steering import select_diverse


def row(symbol, function, category, score, trades=10):
    return {
        "symbol": symbol,
        "function": function,
        "category": category,
        "output": "real",
        "calculation": {"function": function, "parameters": {}},
        "rule": {"operator": "crosses_above", "value": score, "side": "long"},
        "development": {"meanReturn": score, "trades": trades, "maxDrawdown": 0.1},
        "validation": {"meanReturn": score, "trades": trades, "maxDrawdown": 0.1},
    }


def test_select_diverse_enforces_caps_and_deduplicates():
    candidates = [
        row("BTC", "RSI", "Momentum", 0.5),
        row("BTC", "RSI", "Momentum", 0.5),
        row("BTC", "MACD", "Momentum", 0.4),
        row("ETH", "RSI", "Momentum", 0.3),
        row("SOL", "ATR", "Volatility", 0.2),
    ]
    result = select_diverse(
        candidates, 3, max_per_asset=1, max_per_function=2, max_per_category=2
    )
    assert [(x["symbol"], x["function"]) for x in result["selected"]] == [
        ("BTC", "RSI"),
        ("ETH", "RSI"),
        ("SOL", "ATR"),
    ]
    assert result["excluded"]["duplicate"] == 1


def test_select_diverse_does_not_read_confirmation_fields():
    candidate = row("BTC", "RSI", "Momentum", 0.2)
    candidate["confirmation"] = {"meanReturn": -99}
    assert len(select_diverse([candidate], 1)["selected"]) == 1
