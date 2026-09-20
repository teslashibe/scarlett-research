#!/usr/bin/env python3
"""Benchmark the Python and Go derivative-recipe kernels with parity checks."""

from __future__ import annotations

import argparse
import json
import math
import subprocess
import time
from pathlib import Path

from scarlett_research.composites import return_metrics
from scarlett_research.mass_search import masked_returns


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--bars", type=int, default=175_104)
    parser.add_argument("--recipes", type=int, default=30_000)
    parser.add_argument("--sources", type=int, default=64)
    parser.add_argument("--binary", type=Path, default=Path(".local/derivatives-kernel"))
    args = parser.parse_args()

    opens = [100.0 * math.exp(0.0002 * math.sin(index / 37)) for index in range(args.bars)]
    candles = [{"open": value} for value in opens]
    source_indices = [
        [index for index in range(args.bars) if (index * 17 + source * 31) % (23 + source % 11) < 3]
        for source in range(args.sources)
    ]
    source_masks = []
    for indices in source_indices:
        mask = 0
        for index in indices:
            mask |= 1 << index
        source_masks.append(mask)
    recipes = [
        {
            "family": "all_2" if index % 2 == 0 else "any_2",
            "sources": [index % args.sources, (index * 7 + 3) % args.sources],
            "hold": (3, 6, 12, 24)[index % 4],
            "side": 1 if index % 3 else -1,
        }
        for index in range(args.recipes)
    ]

    started = time.perf_counter()
    python_results = []
    for recipe in recipes:
        left, right = (source_masks[value] for value in recipe["sources"])
        mask = left & right if recipe["family"] == "all_2" else left | right
        python_results.append(
            return_metrics(
                masked_returns(candles, mask, recipe["hold"], 13.0, 0, args.bars, recipe["side"])
            )
        )
    python_seconds = time.perf_counter() - started

    request = json.dumps(
        {
            "opens": opens,
            "sources": source_indices,
            "recipes": recipes,
            "start": 0,
            "end": args.bars,
            "costBps": 13.0,
            "workers": 0,
        },
        separators=(",", ":"),
    ).encode()
    started = time.perf_counter()
    completed = subprocess.run([str(args.binary)], input=request, capture_output=True, check=True)
    go_seconds = time.perf_counter() - started
    go_results = json.loads(completed.stdout)["results"]

    for index, (python, go) in enumerate(zip(python_results, go_results, strict=True)):
        for key in ("trades", "totalReturn", "meanReturn", "winRate", "maxDrawdown"):
            left, right = python[key], go[key]
            if left is None or right is None:
                equal = left is right
            elif key == "trades":
                equal = left == right
            else:
                equal = math.isclose(left, right, rel_tol=1e-12, abs_tol=1e-12)
            if not equal:
                raise AssertionError(f"recipe {index} {key}: Python={left} Go={right}")
    print(
        json.dumps(
            {
                "bars": args.bars,
                "recipes": args.recipes,
                "sources": args.sources,
                "pythonSeconds": python_seconds,
                "goEndToEndSeconds": go_seconds,
                "speedup": python_seconds / go_seconds,
                "parity": True,
            },
            indent=2,
        )
    )


if __name__ == "__main__":
    main()
