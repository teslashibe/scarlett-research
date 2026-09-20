# %% [markdown]
# # Scarlett research loop
#
# This paired notebook source keeps the workflow reviewable in Git

# %%
import json
from pathlib import Path

from scarlett_research.demo import write_demo
from scarlett_research.search import run_search

# %%
snapshot_path = write_demo(Path("../runs/notebook-demo"))
snapshot = json.loads(snapshot_path.read_text())
result = run_search(snapshot["setups"])
result["conclusion"], result["winner"]
