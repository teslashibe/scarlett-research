from __future__ import annotations

import datetime as dt
import hashlib
import json
from pathlib import Path
from typing import Any

from .client import ScarlettClient


def sync(client: ScarlettClient, output: Path, max_requests: int = 500) -> dict[str, Any]:
    output.mkdir(parents=True, exist_ok=True)
    registries = {}
    for name, endpoint in {
        "markets": "/markets",
        "profiles": "/profiles",
        "primitives": "/strategy-primitives",
        "taFunctions": "/ta/functions?limit=100",
    }.items():
        try:
            registries[name] = client.get(endpoint).body
        except Exception as exc:
            registries[name] = {"unavailable": str(exc)}
    strategies = client.get("/strategies").body.get("items", [])
    setups: list[dict[str, Any]] = []
    coverage = []
    for strategy in strategies:
        slug = strategy["slug"]
        pages = count = 0
        complete = True
        try:
            for page in client.setup_pages(slug):
                if client.requests_used >= max_requests:
                    complete = False
                    break
                setups.extend(page)
                pages += 1
                count += len(page)
        except Exception as exc:
            complete = False
            coverage.append({"strategy": slug, "complete": False, "error": str(exc)})
            continue
        coverage.append({"strategy": slug, "pages": pages, "records": count, "complete": complete})
        if client.requests_used >= max_requests:
            break
    payload: dict[str, Any] = {
        "metadata": {
            "retrievedAt": dt.datetime.now(dt.UTC).isoformat(),
            "requests": client.requests_used,
            "complete": all(x.get("complete") for x in coverage),
        },
        "strategies": strategies,
        "setups": setups,
        "coverage": coverage,
        "registries": registries,
    }
    encoded = json.dumps(payload, indent=2, sort_keys=True).encode()
    (output / "snapshot.json").write_bytes(encoded)
    manifest = {
        "sha256": hashlib.sha256(encoded).hexdigest(),
        "records": len(setups),
        "coverage": coverage,
    }
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")
    return manifest


def discover(client: ScarlettClient) -> dict[str, Any]:
    endpoints = [
        "/markets",
        "/profiles",
        "/strategies",
        "/strategy-primitives",
        "/ta/functions?limit=1",
    ]
    result = {}
    for endpoint in endpoints:
        try:
            response = client.get(endpoint)
            result[endpoint] = {"available": True, "shape": type(response.body).__name__}
        except Exception as exc:
            result[endpoint] = {"available": False, "error": str(exc)}
    result["requests"] = client.requests_used
    return result
