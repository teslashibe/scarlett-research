from scarlett_research.catalogue import SignalRule
from scarlett_research.composites import compound_signals, return_metrics


def test_compound_signal_requires_trigger_and_regime_on_same_bar():
    trigger = SignalRule("crosses_above", 0, "long", 4)
    regime = SignalRule("gt", 10, "long", 8)
    assert compound_signals(
        [-1, 1, 2, -1, 1], trigger, [11, 11, 9, 11, 11], regime
    ) == [0, 1, 0, 0, 1]


def test_return_metrics_tracks_drawdown():
    result = return_metrics([0.1, -0.2, 0.05])
    assert result["trades"] == 3
    assert result["maxDrawdown"] == 0.2
