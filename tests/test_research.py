import json
from pathlib import Path

from scarlett_research.demo import write_demo
from scarlett_research.evaluation import Candidate, chronological_split, score
from scarlett_research.search import run_search


def test_demo_search_finds_only_exploratory_candidate(tmp_path: Path):
    snapshot = json.loads(write_demo(tmp_path).read_text())
    result = run_search(snapshot["setups"], min_development=20, min_validation=10)
    assert result["conclusion"] in {"exploratory_candidate", "no_supported_edge"}
    assert len(result["trials"]) > 10
    assert all("development" in trial for trial in result["trials"])


def test_chronological_split_has_no_overlap(tmp_path: Path):
    setups = json.loads(write_demo(tmp_path).read_text())["setups"]
    development, validation, boundary = chronological_split(setups)
    assert development and validation
    assert all(x["publishedAt"] < boundary for x in development)
    assert all(x["publishedAt"] >= boundary for x in validation)


def test_costs_reduce_result(tmp_path: Path):
    setups = json.loads(write_demo(tmp_path).read_text())["setups"]
    candidate = Candidate("all")
    assert score(setups, candidate, 0.10).total_r < score(setups, candidate, 0.0).total_r


def test_empty_candidate_is_insufficient():
    result = score([], Candidate("empty"))
    assert result.status == "insufficient_data"
    assert result.mean_r is None
