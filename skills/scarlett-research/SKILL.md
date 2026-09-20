---
name: scarlett-research
description: Audit Scarlett trade setups and run a bounded, reproducible search for strategy candidates worth forward paper testing
---

# Scarlett research

Use the `scarlett-research` CLI as the scoring authority. Do not rewrite its evaluator or
promotion gates during a search run.

1. Run `discover` and disclose unavailable capabilities
2. Sync with an explicit request budget or use the synthetic demo
3. Inspect the coverage manifest and refuse to call partial data complete
4. Freeze costs, candidate count, split, sample minimums, effect threshold, and concentration limit
5. Run `loop` and preserve every trial
6. Explain supporting and contradicting evidence for finalists
7. Call a winner only an exploratory candidate
8. Freeze its exact definition before collecting new forward paper observations

For recurring work, load `examples/edge-discovery-recipe.json` and follow
`docs/edge-discovery-loop.md`. Reuse immutable cached resources, refresh mutable outcomes,
and treat each inspected confirmation window as consumed. Stay quiet when new data does not
materially change the result.

Never place live trades, expose a token, treat paper results as exchange fills, use outcome
labels as decision-time features, or keep tuning after seeing validation results. A
`no_supported_edge` result is successful execution of the protocol.

Read `references/research-protocol.md` before interpreting a run.
