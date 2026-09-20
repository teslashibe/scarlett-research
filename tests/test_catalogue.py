import math

from scarlett_research.catalogue import (
    SignalRule,
    calculation,
    holm_adjust,
    percentile,
    sign_flip_p_value,
    signal_backtest,
    signals,
)


def test_calculation_maps_named_ohlcv_and_generic_real_inputs():
    definition = {
        "name": "TEST",
        "inputs": [
            {"name": "high", "type": "real_series"},
            {"name": "real", "type": "real_series"},
        ],
        "parameters": [{"name": "time_period", "default": 14}],
    }
    output = calculation(definition, "test")
    assert output["inputs"] == {"high": {"series": "high"}, "real": {"series": "close"}}
    assert output["parameters"] == {"time_period": 14}


def test_percentile_and_cross_signals_are_causal():
    assert percentile([1, 2, 3, 4, 5], 0.5) == 3
    rule = SignalRule("crosses_above", 2, "long", 1)
    assert signals([1, 2, 3, 2, 3], rule) == [0, 0, 1, 0, 1]


def test_signal_backtest_enters_on_next_open_and_charges_cost():
    candles = [
        {"open": 100},
        {"open": 100},
        {"open": 100},
        {"open": 110},
    ]
    values = [0, 1, 1, 1]
    result = signal_backtest(candles, values, SignalRule("gt", 0, "long", 1), 25)
    assert result["trades"] == 1
    assert math.isclose(result["totalReturn"], 0.0975)


def test_sign_flip_and_holm_correction_are_deterministic():
    assert sign_flip_p_value([1.0, 1.0, 1.0], seed=1) == 0.125
    assert holm_adjust([0.01, 0.04, 0.03]) == [0.03, 0.06, 0.06]
