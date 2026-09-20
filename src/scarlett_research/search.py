from __future__ import annotations

import hashlib
import itertools
import json
from dataclasses import asdict
from typing import Any

from .evaluation import Candidate, chronological_split, metrics_dict, score


def candidate_family(setups: list[dict[str, Any]], max_candidates: int = 200) -> list[Candidate]:
    slugs = sorted({x.get("slug") or x.get("strategySlug") for x in setups} - {None})
    candidates = [Candidate("all")]
    for slug in slugs:
        candidates.append(Candidate(slug, (slug,)))
        for direction in ("long", "short"):
            candidates.append(Candidate(f"{slug}:{direction}", (slug,), direction=direction))
        for kind in ("scalp", "intraday", "swing"):
            candidates.append(Candidate(f"{slug}:{kind}", (slug,), setup_type=kind))
        for threshold in (0.55, 0.65, 0.75, 0.85):
            candidates.append(
                Candidate(f"{slug}:strength>={threshold}", (slug,), min_strength=threshold)
            )
    for left, right in itertools.pairwise(slugs):
        candidates.append(Candidate(f"blend:{left}+{right}", (left, right)))
    unique = {json.dumps(asdict(x), sort_keys=True): x for x in candidates}
    return list(unique.values())[:max_candidates]


def run_search(
    setups: list[dict[str, Any]],
    *,
    cost_r: float = 0.05,
    max_candidates: int = 200,
    min_development: int = 20,
    min_validation: int = 10,
    min_mean_r: float = 0.05,
    max_top_symbol_share: float = 0.5,
) -> dict[str, Any]:
    development, validation, boundary = chronological_split(setups)
    trials = []
    for candidate in candidate_family(development, max_candidates):
        dev = score(development, candidate, cost_r)
        trials.append({"candidate": asdict(candidate), "development": metrics_dict(dev)})
    trials.sort(
        key=lambda x: (
            x["development"]["mean_r"] is not None,
            x["development"]["mean_r"] or -999,
            -len(x["candidate"]["strategies"]),
        ),
        reverse=True,
    )
    finalists = [x for x in trials if x["development"]["n"] >= min_development][:5]
    for trial in finalists:
        candidate = Candidate(**trial["candidate"])
        trial["validation"] = metrics_dict(score(validation, candidate, cost_r))
        valid = trial["validation"]
        trial["decision"] = (
            "exploratory_candidate"
            if valid["n"] >= min_validation
            and valid["mean_r"] is not None
            and valid["mean_r"] >= min_mean_r
            and valid["top_symbol_share"] is not None
            and valid["top_symbol_share"] <= max_top_symbol_share
            else "insufficient_data"
        )
    eligible = [x for x in finalists if x.get("decision") == "exploratory_candidate"]
    winner = max(eligible, key=lambda x: x["validation"]["mean_r"], default=None)
    protocol = {
        "cost_r": cost_r,
        "max_candidates": max_candidates,
        "min_development": min_development,
        "min_validation": min_validation,
        "min_mean_r": min_mean_r,
        "max_top_symbol_share": max_top_symbol_share,
        "split": boundary,
    }
    protocol_hash = hashlib.sha256(json.dumps(protocol, sort_keys=True).encode()).hexdigest()
    return {
        "protocol": protocol,
        "protocol_hash": protocol_hash,
        "development_count": len(development),
        "validation_count": len(validation),
        "trials": trials,
        "finalists": finalists,
        "winner": winner,
        "conclusion": "exploratory_candidate" if winner else "no_supported_edge",
    }
