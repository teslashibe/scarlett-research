import json
import math
import shutil
import subprocess
from pathlib import Path

import pytest

from scarlett_research.composites import return_metrics
from scarlett_research.mass_search import masked_returns


@pytest.mark.skipif(shutil.which("go") is None, reason="Go toolchain unavailable")
def test_go_kernel_matches_python_return_semantics():
    root = Path(__file__).resolve().parent.parent
    opens = [100, 101, 102, 99, 103, 104, 98, 105, 106]
    signal = [0, 1, 3, 5]
    request = {
        "opens": opens,
        "sources": [signal],
        "recipes": [{"family": "single_1", "sources": [0], "hold": 2, "side": -1}],
        "start": 0,
        "end": len(opens),
        "costBps": 13,
        "workers": 1,
    }
    completed = subprocess.run(
        ["go", "run", "./cmd/derivatives-kernel"],
        cwd=root / "kernel",
        input=json.dumps(request),
        text=True,
        capture_output=True,
        check=True,
        timeout=60,
    )
    actual = json.loads(completed.stdout)["results"][0]
    candles = [{"open": value} for value in opens]
    mask = sum(1 << index for index in signal)
    expected = return_metrics(masked_returns(candles, mask, 2, 13, 0, len(opens), -1))
    assert actual["trades"] == expected["trades"]
    assert math.isclose(actual["totalReturn"], expected["totalReturn"], abs_tol=1e-15)
    assert math.isclose(actual["meanReturn"], expected["meanReturn"], abs_tol=1e-15)
    assert math.isclose(actual["winRate"], expected["winRate"], abs_tol=1e-15)
    assert math.isclose(actual["maxDrawdown"], expected["maxDrawdown"], abs_tol=1e-15)
