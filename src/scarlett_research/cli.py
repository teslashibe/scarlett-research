from __future__ import annotations

import argparse
import datetime as dt
import json
import os
from dataclasses import asdict
from pathlib import Path

from .backtest import screen_universe, walk_forward
from .carry import confirm_carry_selection, run_carry_campaign
from .catalogue import confirm_selection, run_catalogue_campaign
from .client import ScarlettClient
from .composites import run_composite_campaign
from .demo import write_demo
from .derivatives import (
    confirm_derivatives_selection,
    merge_derivatives_campaigns,
    run_derivatives_campaign,
    write_json,
)
from .evaluation import Candidate, dataset_summary, metrics_dict, score
from .market_data import (
    BinanceArchiveConnector,
    BinanceFundingArchiveConnector,
    BinanceFuturesMetricsConnector,
    HyperliquidConnector,
    binance_futures_universe,
    fetch_bundle,
    fetch_funding_bundle,
    fetch_metrics_bundle,
    ranked_crypto_futures_universe,
)
from .mass_search import confirm_mass_selection, merge_mass_shards, run_mass_campaign
from .monte_carlo import run_monte_carlo
from .search import run_search
from .steering import select_diverse
from .sync import discover, sync
from .technical_analysis import analyze, resample


def load(path: Path) -> dict:
    return json.loads(path.read_text())


def _requested_symbols(symbols: list[str], universe: Path | None) -> list[str]:
    requested = list(symbols)
    if universe is not None:
        requested.extend(load(universe).get("symbols", []))
    deduplicated = list(dict.fromkeys(requested))
    if not deduplicated:
        raise ValueError("at least one --symbol or --universe is required")
    return deduplicated


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
    archive_p = commands.add_parser("candles-archive")
    archive_p.add_argument("--symbol", action="append", default=[])
    archive_p.add_argument("--universe", type=Path)
    archive_p.add_argument("--interval", default="1h")
    archive_p.add_argument("--market", choices=("spot", "um_futures"), default="spot")
    archive_p.add_argument("--start", required=True, help="UTC date, for example 2020-01-01")
    archive_p.add_argument("--end", help="UTC date, defaults to now")
    archive_p.add_argument("--output", type=Path, required=True)
    metrics_p = commands.add_parser("futures-metrics-archive")
    metrics_p.add_argument("--symbol", action="append", default=[])
    metrics_p.add_argument("--universe", type=Path)
    metrics_p.add_argument("--start", required=True, help="UTC date, for example 2024-01-01")
    metrics_p.add_argument("--end", help="UTC date, defaults to now")
    metrics_p.add_argument("--output", type=Path, required=True)
    funding_p = commands.add_parser("funding-archive")
    funding_p.add_argument("--symbol", action="append", default=[])
    funding_p.add_argument("--universe", type=Path)
    funding_p.add_argument("--start", required=True, help="UTC date, for example 2024-01-01")
    funding_p.add_argument("--end", help="UTC date, defaults to now")
    funding_p.add_argument("--output", type=Path, required=True)
    universe_p = commands.add_parser("futures-universe")
    universe_p.add_argument("--quote", default="USDT")
    universe_p.add_argument("--limit", type=int, default=200)
    universe_p.add_argument("--ranking-pages", type=int, default=2)
    universe_p.add_argument("--output", type=Path, required=True)
    backtest_p = commands.add_parser("backtest-loop")
    backtest_p.add_argument("--candles", type=Path, required=True)
    backtest_p.add_argument("--output", type=Path, required=True)
    backtest_p.add_argument("--cost-bps", type=float, default=13)
    backtest_p.add_argument("--max-rules", type=int, default=500)
    screen_p = commands.add_parser("universe-screen")
    screen_p.add_argument("--candles", type=Path, required=True)
    screen_p.add_argument("--output", type=Path, required=True)
    screen_p.add_argument("--cost-bps", type=float, default=13)
    screen_p.add_argument("--max-rules", type=int, default=500)
    screen_p.add_argument("--limit", type=int, default=50)
    catalogue_p = commands.add_parser("catalogue-loop")
    catalogue_p.add_argument("--binary", type=Path, required=True)
    catalogue_p.add_argument("--catalogue", type=Path, required=True)
    catalogue_p.add_argument("--candles", type=Path, required=True)
    catalogue_p.add_argument("--output", type=Path, required=True)
    catalogue_p.add_argument("--cost-bps", type=float, default=25)
    select_p = commands.add_parser("select-forward")
    select_p.add_argument("--campaign", type=Path, required=True)
    select_p.add_argument("--output", type=Path, required=True)
    select_p.add_argument("--limit", type=int, default=20)
    confirm_p = commands.add_parser("confirm-selection")
    confirm_p.add_argument("--binary", type=Path, required=True)
    confirm_p.add_argument("--candles", type=Path, required=True)
    confirm_p.add_argument("--selection", type=Path, required=True)
    confirm_p.add_argument("--output", type=Path, required=True)
    confirm_p.add_argument("--cost-bps", type=float, default=25)
    confirm_p.add_argument("--stress-cost-bps", type=float, default=50)
    composite_p = commands.add_parser("composite-loop")
    composite_p.add_argument("--binary", type=Path, required=True)
    composite_p.add_argument("--candles", type=Path, required=True)
    composite_p.add_argument("--campaign", type=Path, required=True)
    composite_p.add_argument("--output", type=Path, required=True)
    composite_p.add_argument("--cost-bps", type=float, default=25)
    composite_p.add_argument("--source-limit-per-symbol", type=int, default=20)
    composite_p.add_argument("--selection-limit", type=int, default=24)
    monte_p = commands.add_parser("monte-carlo")
    monte_p.add_argument("--confirmation", type=Path, required=True)
    monte_p.add_argument("--output", type=Path, required=True)
    monte_p.add_argument("--simulations", type=int, default=10_000)
    monte_p.add_argument("--block-length", type=int)
    monte_p.add_argument("--cost-shock-bps", type=float, default=25)
    monte_p.add_argument("--allocation-fraction", type=float, default=0.10)
    monte_p.add_argument("--ruin-drawdown", type=float, default=0.20)
    monte_p.add_argument("--seed", type=int, default=20260920)
    mass_p = commands.add_parser("mass-loop")
    mass_p.add_argument("--binary", type=Path, required=True)
    mass_p.add_argument("--candles", type=Path, required=True)
    mass_p.add_argument("--campaign", type=Path, required=True)
    mass_p.add_argument("--output", type=Path, required=True)
    mass_p.add_argument("--cost-bps", type=float, default=25)
    mass_p.add_argument("--source-limit-per-symbol", type=int, default=96)
    mass_p.add_argument("--max-recipes", type=int, default=250_000)
    mass_p.add_argument("--shards", type=int, default=1)
    mass_p.add_argument("--shard", type=int, default=0)
    merge_mass_p = commands.add_parser("merge-mass")
    merge_mass_p.add_argument("--input", type=Path, action="append", required=True)
    merge_mass_p.add_argument("--output", type=Path, required=True)
    merge_mass_p.add_argument("--limit", type=int, default=24)
    confirm_mass_p = commands.add_parser("confirm-mass")
    confirm_mass_p.add_argument("--binary", type=Path, required=True)
    confirm_mass_p.add_argument("--candles", type=Path, required=True)
    confirm_mass_p.add_argument("--selection", type=Path, required=True)
    confirm_mass_p.add_argument("--output", type=Path, required=True)
    confirm_mass_p.add_argument("--cost-bps", type=float, default=25)
    confirm_mass_p.add_argument("--stress-cost-bps", type=float, default=50)
    derivatives_p = commands.add_parser("derivatives-loop")
    derivatives_p.add_argument("--candles", type=Path, required=True)
    derivatives_p.add_argument("--metrics", type=Path, required=True)
    derivatives_p.add_argument("--funding", type=Path)
    derivatives_p.add_argument("--output", type=Path, required=True)
    derivatives_p.add_argument("--cost-bps", type=float, default=25)
    derivatives_p.add_argument("--max-recipes", type=int, default=250_000)
    derivatives_p.add_argument("--symbol", action="append", default=[])
    derivatives_p.add_argument("--kernel", type=Path)
    merge_derivatives_p = commands.add_parser("merge-derivatives")
    merge_derivatives_p.add_argument("--input", type=Path, action="append", required=True)
    merge_derivatives_p.add_argument("--output", type=Path, required=True)
    merge_derivatives_p.add_argument("--limit", type=int, default=24)
    confirm_derivatives_p = commands.add_parser("confirm-derivatives")
    confirm_derivatives_p.add_argument("--candles", type=Path, required=True)
    confirm_derivatives_p.add_argument("--metrics", type=Path, required=True)
    confirm_derivatives_p.add_argument("--funding", type=Path)
    confirm_derivatives_p.add_argument("--selection", type=Path, required=True)
    confirm_derivatives_p.add_argument("--output", type=Path, required=True)
    confirm_derivatives_p.add_argument("--cost-bps", type=float, default=25)
    confirm_derivatives_p.add_argument("--stress-cost-bps", type=float, default=50)
    carry_p = commands.add_parser("carry-loop")
    carry_p.add_argument("--candles", type=Path, action="append", required=True)
    carry_p.add_argument("--funding", type=Path, action="append", required=True)
    carry_p.add_argument("--output", type=Path, required=True)
    carry_p.add_argument("--cost-bps", type=float, default=13)
    carry_p.add_argument("--selection-limit", type=int, default=12)
    carry_p.add_argument("--orientation", choices=("carry", "momentum"), default="carry")
    carry_p.add_argument("--stress-cost-bps", type=float, default=25)
    confirm_carry_p = commands.add_parser("confirm-carry")
    confirm_carry_p.add_argument("--candles", type=Path, action="append", required=True)
    confirm_carry_p.add_argument("--funding", type=Path, action="append", required=True)
    confirm_carry_p.add_argument("--selection", type=Path, required=True)
    confirm_carry_p.add_argument("--output", type=Path, required=True)
    confirm_carry_p.add_argument("--cost-bps", type=float, default=13)
    confirm_carry_p.add_argument("--stress-cost-bps", type=float, default=25)
    confirm_carry_p.add_argument("--full-history", action="store_true")
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
    elif args.command == "candles-archive":
        start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.UTC)
        end = (
            dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.UTC)
            if args.end
            else dt.datetime.now(dt.UTC)
        )
        result = fetch_bundle(
            BinanceArchiveConnector.for_market(args.market),
            _requested_symbols(args.symbol, args.universe),
            args.interval,
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
            args.output,
        )
    elif args.command == "futures-universe":
        archived = binance_futures_universe(args.quote)
        ranked = ranked_crypto_futures_universe(archived, args.limit, args.ranking_pages)
        result = {
            "source": "coingecko_market_cap_intersect_binance_public_archive",
            "quote": args.quote,
            "retrievedAt": dt.datetime.now(dt.UTC).isoformat(),
            "archiveSymbolCount": len(archived),
            "count": len(ranked),
            "symbols": [row["symbol"] for row in ranked],
            "ranking": ranked,
        }
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "archiveSymbolCount": result["archiveSymbolCount"],
            "count": result["count"],
        }
    elif args.command == "futures-metrics-archive":
        start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.UTC)
        end = (
            dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.UTC)
            if args.end
            else dt.datetime.now(dt.UTC)
        )
        result = fetch_metrics_bundle(
            BinanceFuturesMetricsConnector(),
            _requested_symbols(args.symbol, args.universe),
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
            args.output,
        )
    elif args.command == "funding-archive":
        start = dt.datetime.fromisoformat(args.start).replace(tzinfo=dt.UTC)
        end = (
            dt.datetime.fromisoformat(args.end).replace(tzinfo=dt.UTC)
            if args.end
            else dt.datetime.now(dt.UTC)
        )
        result = fetch_funding_bundle(
            BinanceFundingArchiveConnector(),
            _requested_symbols(args.symbol, args.universe),
            int(start.timestamp() * 1000),
            int(end.timestamp() * 1000),
            args.output,
        )
    elif args.command == "backtest-loop":
        result = walk_forward(load(args.candles), args.cost_bps, args.max_rules)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    elif args.command == "universe-screen":
        result = screen_universe(load(args.candles), args.cost_bps, args.max_rules, args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
    elif args.command == "derivatives-loop":
        candles, metrics = load(args.candles), load(args.metrics)
        if args.symbol:
            candles["series"] = {
                symbol: candles["series"][symbol]
                for symbol in args.symbol
                if symbol in candles["series"]
            }
            metrics["series"] = {
                symbol: metrics["series"].get(symbol, []) for symbol in candles["series"]
            }
        funding = load(args.funding) if args.funding else None
        if funding is not None and args.symbol:
            funding["series"] = {
                symbol: funding["series"].get(symbol, []) for symbol in candles["series"]
            }
        result = run_derivatives_campaign(
            candles, metrics, funding, args.cost_bps, args.max_recipes, args.kernel
        )
        write_json(args.output, result)
        result = {
            "output": str(args.output),
            "evaluated": result["evaluated"],
            "survivors": result["survivors"],
            "selected": len(result["selected"]),
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "merge-derivatives":
        result = merge_derivatives_campaigns([load(path) for path in args.input], args.limit)
        write_json(args.output, result)
        result = {
            "output": str(args.output),
            "evaluated": result["evaluated"],
            "survivors": result["survivors"],
            "selected": len(result["selected"]),
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "confirm-derivatives":
        result = confirm_derivatives_selection(
            load(args.candles),
            load(args.metrics),
            load(args.funding) if args.funding else None,
            load(args.selection),
            args.cost_bps,
            args.stress_cost_bps,
            args.full_history,
        )
        write_json(args.output, result)
        result = {
            "output": str(args.output),
            "tested": len(result["results"]),
            "supported": result["supported"],
            "conclusion": result["conclusion"],
        }
    elif args.command == "carry-loop":
        result = run_carry_campaign(
            [load(path) for path in args.candles],
            [load(path) for path in args.funding],
            args.cost_bps,
            args.selection_limit,
            args.orientation,
            args.stress_cost_bps,
        )
        write_json(args.output, result)
        result = {
            "output": str(args.output),
            "assets": len(result["assets"]),
            "events": result["events"],
            "evaluated": len(result["trials"]),
            "survivors": result["survivors"],
            "selected": len(result["selected"]),
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "confirm-carry":
        result = confirm_carry_selection(
            [load(path) for path in args.candles],
            [load(path) for path in args.funding],
            load(args.selection),
            args.cost_bps,
            args.stress_cost_bps,
        )
        write_json(args.output, result)
        result = {
            "output": str(args.output),
            "tested": len(result["results"]),
            "supported": result["supported"],
            "conclusion": result["conclusion"],
        }
    elif args.command == "catalogue-loop":
        result = run_catalogue_campaign(
            args.binary,
            load(args.catalogue),
            load(args.candles),
            args.cost_bps,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        audit = result["catalogueAudit"]
        result = {
            "output": str(args.output),
            "tested": audit["tested"],
            "compatible": audit["compatible"],
            "incompatible": audit["incompatible"],
            "candidates": len(result["candidates"]),
            "confirmationRead": result["protocol"]["confirmationRead"],
            "conclusion": result["conclusion"],
        }
    elif args.command == "select-forward":
        campaign = load(args.campaign)
        result = select_diverse(campaign["candidates"], args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "selected": len(result["selected"]),
            "coverage": result["coverage"],
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "confirm-selection":
        result = confirm_selection(
            args.binary,
            load(args.candles),
            load(args.selection),
            args.cost_bps,
            args.stress_cost_bps,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "tested": len(result["results"]),
            "supported": result["supported"],
            "conclusion": result["conclusion"],
        }
    elif args.command == "composite-loop":
        result = run_composite_campaign(
            args.binary,
            load(args.candles),
            load(args.campaign),
            args.cost_bps,
            args.source_limit_per_symbol,
            args.selection_limit,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "evaluated": result["evaluated"],
            "survivors": result["survivors"],
            "selected": len(result["selected"]),
            "coverage": result["coverage"],
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "monte-carlo":
        result = run_monte_carlo(
            load(args.confirmation),
            simulations=args.simulations,
            block_length=args.block_length,
            cost_shock_bps=args.cost_shock_bps,
            allocation_fraction=args.allocation_fraction,
            ruin_drawdown=args.ruin_drawdown,
            seed=args.seed,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "tested": len(result["results"]),
            "passing": result["passing"],
            "simulationsPerCandidate": result["protocol"]["simulations"],
        }
    elif args.command == "mass-loop":
        result = run_mass_campaign(
            args.binary,
            load(args.candles),
            load(args.campaign),
            cost_bps=args.cost_bps,
            source_limit_per_symbol=args.source_limit_per_symbol,
            max_recipes=args.max_recipes,
            shards=args.shards,
            shard=args.shard,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "enumerated": result["enumerated"],
            "evaluated": result["evaluated"],
            "survivors": result["survivors"],
            "selected": len(result["selected"]),
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "merge-mass":
        result = merge_mass_shards([load(path) for path in args.input], args.limit)
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "evaluated": result["protocol"]["evaluated"],
            "selected": len(result["selected"]),
            "coverage": result["coverage"],
            "confirmationRead": result["protocol"]["confirmationRead"],
        }
    elif args.command == "confirm-mass":
        result = confirm_mass_selection(
            args.binary,
            load(args.candles),
            load(args.selection),
            args.cost_bps,
            args.stress_cost_bps,
        )
        args.output.parent.mkdir(parents=True, exist_ok=True)
        args.output.write_text(json.dumps(result, indent=2) + "\n")
        result = {
            "output": str(args.output),
            "tested": len(result["results"]),
            "supported": result["supported"],
            "conclusion": result["conclusion"],
        }
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
