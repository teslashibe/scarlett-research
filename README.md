# Scarlett Research

Scarlett Research is an open-source Python workbench for auditing saved Scarlett trade
setups, searching a bounded family of strategy rules, and deciding whether any candidate
has enough evidence to justify a forward paper test.

It does not place trades and it does not promise to find an edge. A valid run can conclude
that every candidate has insufficient evidence.

## Quick start

```bash
git clone https://github.com/teslashibe/scarlett-research.git
cd scarlett-research
python -m venv .venv
source .venv/bin/activate
pip install -e '.[dev]'
scarlett-research demo --output runs/demo
scarlett-research loop --snapshot runs/demo/snapshot.json --output runs/search
```

For live public and account-scoped data:

```bash
export SCARLETT_API_TOKEN='sc_...'
scarlett-research sync --output data/live
scarlett-research loop --snapshot data/live/snapshot.json --output runs/live-search
```

The loop freezes its protocol before scoring, uses an early chronological development
partition and a later validation partition, logs every candidate, and applies minimum
sample, economic effect, concentration, and stability gates. It never tunes on a hidden
confirmation window or launches a live strategy.

## Commands

- `discover` checks the deployed API capabilities
- `sync` collects strategies and complete setup-history pages available to the key
- `demo` writes a deterministic synthetic snapshot with both signal and noise
- `evaluate` evaluates one candidate definition
- `loop` runs a bounded, reproducible candidate-search loop
- `ta` runs local closed-bar SMA, EMA, or RSI analysis with supported resampling

See [methodology](docs/methodology.md) for evidence limits and
[agent workflow](docs/agent-workflow.md) for using the included skill.

## Snapshot format

The input is JSON with `strategies` and `setups`. Setup records retain the API shape and
must include strategy identity, publication time, direction, setup type, strength, symbol,
and an outcome containing `detail.resultR` or `detail.grossResultR`. Public sync results are
cacheable; account-private datasets and run artifacts are ignored by Git.

## Safety and interpretation

Results are hypothetical research outputs, not investment advice. Saved paper outcomes,
local counterfactuals, and exchange fills are different evidence classes. Costs are modeled,
not observed, unless the source explicitly says otherwise. Any selected candidate needs a
new, immutable forward paper evaluation before it can be described as supported.
