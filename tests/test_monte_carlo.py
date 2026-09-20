import math
import random

import pytest

from scarlett_research.monte_carlo import (
    circular_block_sample,
    max_drawdown,
    simulate_candidate,
)


def test_circular_blocks_preserve_adjacent_values():
    sample = circular_block_sample([0, 1, 2, 3], 4, 2, random.Random(2))
    assert len(sample) == 4
    assert all((right - left) % 4 == 1 for left, right in zip(sample[::2], sample[1::2]))


def test_max_drawdown_uses_compounded_wealth():
    assert math.isclose(max_drawdown([0.1, -0.2, 0.1]), 0.2)


def test_simulation_is_deterministic_and_rejects_small_samples():
    first = simulate_candidate([0.01] * 10, simulations=100, seed=7)
    second = simulate_candidate([0.01] * 10, simulations=100, seed=7)
    assert first == second
    assert first["status"] == "insufficient_data"
    assert not first["passes"]


def test_invalid_block_length_is_rejected():
    with pytest.raises(ValueError):
        simulate_candidate([0.1, 0.2], simulations=10, block_length=3)


def test_allocation_fraction_scales_account_drawdown():
    assert math.isclose(max_drawdown([-0.2]), 0.2)
    result = simulate_candidate(
        [-0.2] * 20,
        simulations=10,
        cost_shock_bps=0,
        allocation_fraction=0.1,
        seed=1,
    )
    assert result["maxDrawdown"]["p50"] < 0.34
