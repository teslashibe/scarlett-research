from __future__ import annotations

import datetime as dt
import json
import random
from pathlib import Path


def write_demo(output: Path, seed: int = 20260920) -> Path:
    output.mkdir(parents=True, exist_ok=True)
    rng = random.Random(seed)
    start = dt.datetime(2026, 1, 1, tzinfo=dt.UTC)
    setups = []
    symbols = ["BTC", "ETH", "SOL", "ZEC"]
    for index in range(240):
        slug = "signal-v1" if index % 2 == 0 else "noise-v1"
        direction = "long" if rng.random() > 0.25 else "short"
        strength = rng.uniform(0.45, 0.95)
        expected = 0.14 if slug == "signal-v1" and direction == "long" and strength >= 0.65 else 0.0
        result = expected + rng.gauss(0, 0.55)
        setups.append(
            {
                "id": f"demo-{index:04d}",
                "slug": slug,
                "symbol": symbols[index % len(symbols)],
                "publishedAt": (start + dt.timedelta(hours=index * 6))
                .isoformat()
                .replace("+00:00", "Z"),
                "decision": direction,
                "setupType": ["scalp", "intraday", "swing"][index % 3],
                "strength": round(strength, 6),
                "outcome": {"status": "completed", "detail": {"grossResultR": round(result, 6)}},
            }
        )
    path = output / "snapshot.json"
    path.write_text(
        json.dumps(
            {"metadata": {"synthetic": True, "seed": seed}, "strategies": [], "setups": setups},
            indent=2,
        )
        + "\n"
    )
    return path
