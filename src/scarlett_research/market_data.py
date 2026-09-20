from __future__ import annotations

import datetime as dt
import json
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol


class CandleConnector(Protocol):
    name: str

    def candles(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]: ...


@dataclass
class HyperliquidConnector:
    """Public, keyless Hyperliquid candle-snapshot connector."""

    base_url: str = "https://api.hyperliquid.xyz/info"
    name: str = "hyperliquid"

    def candles(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        body = json.dumps(
            {
                "type": "candleSnapshot",
                "req": {
                    "coin": symbol,
                    "interval": interval,
                    "startTime": start_ms,
                    "endTime": end_ms,
                },
            }
        ).encode()
        request = urllib.request.Request(
            self.base_url,
            data=body,
            headers={"Content-Type": "application/json", "User-Agent": "scarlett-research/0.2"},
            method="POST",
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
        output = []
        for row in rows:
            output.append(
                {
                    "time": dt.datetime.fromtimestamp(int(row["t"]) / 1000, dt.UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "closeTime": dt.datetime.fromtimestamp(int(row["T"]) / 1000, dt.UTC)
                    .isoformat()
                    .replace("+00:00", "Z"),
                    "open": float(row["o"]),
                    "high": float(row["h"]),
                    "low": float(row["l"]),
                    "close": float(row["c"]),
                    "volume": float(row["v"]),
                    "trades": int(row["n"]),
                    "symbol": row["s"],
                    "interval": row["i"],
                    "source": self.name,
                }
            )
        return output


def fetch_bundle(
    connector: CandleConnector,
    symbols: list[str],
    interval: str,
    start_ms: int,
    end_ms: int,
    output: Path,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    series = {symbol: connector.candles(symbol, interval, start_ms, end_ms) for symbol in symbols}
    bundle = {
        "metadata": {
            "source": connector.name,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "retrievedAt": dt.datetime.now(dt.UTC).isoformat(),
            "limits": "Provider returns at most the most recent 5000 candles per request",
        },
        "series": series,
    }
    output.write_text(json.dumps(bundle, indent=2) + "\n")
    return {"output": str(output), "symbols": {key: len(value) for key, value in series.items()}}
