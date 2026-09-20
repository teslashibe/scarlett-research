from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import asdict
from pathlib import Path

from .backtest import walk_forward
from .client import ScarlettClient
from .demo import write_demo
from .evaluation import Candidate, dataset_summary, metrics_dict, score
from .market_data import HyperliquidConnector, fetch_bundle
from .search import run_search
from .sync import discover, sync
from .technical_analysis import analyze, resample


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def write_report(result: dict, output: Path) -> None:
    output.mkdir(parents=True, exist_ok=True)
    (output / "results.json").write_text(json.dumps(result, indent=2) + "\n")
    winner = result.get("winner")
    lines = [
        "# Scarlett research run",
        "",
        f"Conclusion: **{result['conclusion']}**",
        "",
        f"Protocol: `{result['protocol_hash']}`",
        "",
    ]
    if winner:
        lines += [
            f"Selected exploratory candidate: **{winner['candidate']['name']}**",
            "",
            f"Validation mean after modeled costs: **{winner['validation']['mean_r']:.3f}R** across **{winner['validation']['n']}** observations",
            "",
            "This is a retrospective candidate, not a confirmed or tradeable edge. Freeze it before collecting new forward paper evidence.",
        ]
    else:
        lines += [
            "No candidate passed the predeclared sample, effect, stability, and concentration gates."
        ]
    (output / "report.md").write_text("\n".join(lines) + "\n")


def parser() -> argparse.ArgumentParser:
    root = argparse.ArgumentParser(prog="scarlett-research")
    commands = root.add_subparsers(dest="command", required=True)
    commands.add_parser("discover")
    sync_p = commands.add_parser("sync")
    sync_p.add_argument("--output", type=Path, required=True)
    sync_p.add_argument("--max-requests", type=int, default=500)
    demo_p = commands.add_parser("demo")
    demo_p.add_argument("--output", type=Path, required=True)
    demo_p.add_argument("--seed", type=int, default=20260920)
    eval_p = commands.add_parser("evaluate")
    eval_p.add_argument("--snapshot", type=Path, required=True)
    eval_p.add_argument("--strategy", action="append", default=[])
    eval_p.add_argument("--direction")
    eval_p.add_argument("--min-strength", type=float, default=0)
    eval_p.add_argument("--cost-r", type=float, default=0.05)
    loop_p = commands.add_parser("loop")
    loop_p.add_argument("--snapshot", type=Path, required=True)
    loop_p.add_argument("--output", type=Path, required=True)
    loop_p.add_argument("--cost-r", type=float, default=0.05)
    loop_p.add_argument("--max-candidates", type=int, default=200)
    loop_p.add_argument("--min-development", type=int, default=20)
    loop_p.add_argument("--min-validation", type=int, default=10)
    loop_p.add_argument("--min-mean-r", type=float, default=0.05)
    ta_p = commands.add_parser("ta")
    ta_p.add_argument("--candles", type=Path, required=True)
    ta_p.add_argument("--source-interval", required=True)
    ta_p.add_argument("--target-interval", required=True)
    ta_p.add_argument("--indicator", choices=("sma", "ema", "rsi"), required=True)
    ta_p.add_argument("--period", type=int, default=14)
    candles_p = commands.add_parser("candles-fetch")
    candles_p.add_argument("--symbol", action="append", required=True)
    candles_p.add_argument("--interval", default="15m")
    candles_p.add_argument("--days", type=int, default=52)
    candles_p.add_argument("--output", type=Path, required=True)
    backtest_p = commands.add_parser("backtest-loop")
    backtest_p.add_argument("--candles", type=Path, required=True)
    backtest_p.add_argument("--output", type=Path, required=True)
    backtest_p.add_argument("--cost-bps", type=float, default=13)
    backtest_p.add_argument("--max-rules", type=int, default=500)
    return root


def main() -> None:
    args = parser().parse_args()
    if args.command in {"discover", "sync"}:
        client = ScarlettClient(
            os.environ.get("SCARLETT_API_TOKEN", ""),
            os.environ.get("SCARLETT_API_URL", "https://api.scarlett.ai/v1"),
        )
        result = (
            discover(client)
            if args.command == "discover"
            else sync(client, args.output, args.max_requests)
        )
    elif args.command == "demo":
        result = {"snapshot": str(write_demo(args.output, args.seed))}
    elif args.command == "candles-fetch":
        end = dt.datetime.now(dt.UTC)
        start = end - dt.timedelta(days=args.days)
        result = fetch_bundle(
            HyperliquidConnector(),
            args.symbol,
            args.interval,
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
            args.output,
        )
    elif args.command == "backtest-loop":
        result = walk_forward(load(args.candles), args.cost_bps, args.max_rules)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    elif args.command == "ta":
        source = load(args.candles)
        candles = source.get("candles", source) if isinstance(source, dict) else source
        derived = resample(candles, args.source_interval, args.target_interval)
        result = analyze(derived, args.indicator, args.period)
        result.update(
            {"source_interval": args.source_interval, "target_interval": args.target_interval}
        )
    elif args.command == "evaluate":
        data = load(args.snapshot)
        candidate = Candidate(
            "custom", tuple(args.strategy), args.direction, min_strength=args.min_strength
        )
        result = {
            "dataset": dataset_summary(data["setups"]),
            "candidate": asdict(candidate),
            "metrics": metrics_dict(score(data["setups"], candidate, args.cost_r)),
        }
    else:
        data = load(args.snapshot)
        result = run_search(
            data["setups"],
            cost_r=args.cost_r,
            max_candidates=args.max_candidates,
            min_development=args.min_development,
            min_validation=args.min_validation,
            min_mean_r=args.min_mean_r,
        )
        result["dataset"] = dataset_summary(data["setups"])
        write_report(result, args.output)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()
