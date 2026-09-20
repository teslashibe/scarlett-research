import json

import pytest

from scarlett_research.cli import _requested_symbols


def test_requested_symbols_combines_manifest_and_cli_without_duplicates(tmp_path):
    manifest = tmp_path / "universe.json"
    manifest.write_text(json.dumps({"symbols": ["ETHUSDT", "SOLUSDT"]}))
    assert _requested_symbols(["BTCUSDT", "ETHUSDT"], manifest) == [
        "BTCUSDT",
        "ETHUSDT",
        "SOLUSDT",
    ]


def test_requested_symbols_requires_a_source():
    with pytest.raises(ValueError, match="at least one"):
        _requested_symbols([], None)
