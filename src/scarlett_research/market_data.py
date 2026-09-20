from __future__ import annotations

import csv
import datetime as dt
import io
import json
import time
import urllib.error
import urllib.request
import zipfile
from concurrent.futures import ThreadPoolExecutor
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


@dataclass
class BinanceArchiveConnector:
    """Public Binance Vision monthly spot or USD-M futures kline connector."""

    base_url: str = "https://data.binance.vision/data/spot/monthly/klines"
    name: str = "binance_spot_archive"

    @classmethod
    def for_market(cls, market: str) -> BinanceArchiveConnector:
        if market == "spot":
            return cls()
        if market == "um_futures":
            return cls(
                base_url="https://data.binance.vision/data/futures/um/monthly/klines",
                name="binance_um_futures_archive",
            )
        raise ValueError(f"unsupported Binance archive market: {market}")

    def candles(
        self, symbol: str, interval: str, start_ms: int, end_ms: int
    ) -> list[dict[str, Any]]:
        start = dt.datetime.fromtimestamp(start_ms / 1000, dt.UTC)
        end = dt.datetime.fromtimestamp(end_ms / 1000, dt.UTC)
        month = dt.datetime(start.year, start.month, 1, tzinfo=dt.UTC)
        output = []
        while month <= end:
            label = f"{month.year:04d}-{month.month:02d}"
            filename = f"{symbol}-{interval}-{label}.zip"
            url = f"{self.base_url}/{symbol}/{interval}/{filename}"
            archive = None
            for attempt in range(4):
                try:
                    with urllib.request.urlopen(url, timeout=60) as response:
                        archive = zipfile.ZipFile(io.BytesIO(response.read()))
                    break
                except urllib.error.HTTPError as exc:
                    if exc.code == 404:
                        break
                    if attempt == 3:
                        raise
                except (urllib.error.URLError, ConnectionError, TimeoutError):
                    if attempt == 3:
                        raise
                time.sleep(0.5 * (2**attempt))
            if archive is None:
                month = _next_month(month)
                continue
            with archive.open(archive.namelist()[0]) as source:
                reader = csv.reader(io.TextIOWrapper(source))
                for row in reader:
                    raw_open = int(row[0])
                    raw_close = int(row[6])
                    divisor = 1_000_000 if raw_open > 10**14 else 1_000
                    opened = raw_open / divisor
                    closed = raw_close / divisor
                    if opened * 1000 < start_ms or opened * 1000 > end_ms:
                        continue
                    output.append(
                        {
                            "time": dt.datetime.fromtimestamp(opened, dt.UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "closeTime": dt.datetime.fromtimestamp(closed, dt.UTC)
                            .isoformat()
                            .replace("+00:00", "Z"),
                            "open": float(row[1]),
                            "high": float(row[2]),
                            "low": float(row[3]),
                            "close": float(row[4]),
                            "volume": float(row[5]),
                            "trades": int(row[8]),
                            "symbol": symbol,
                            "interval": interval,
                            "source": self.name,
                        }
                    )
            month = _next_month(month)
        return sorted(output, key=lambda row: row["time"])


def _next_month(value: dt.datetime) -> dt.datetime:
    return dt.datetime(value.year + (value.month == 12), value.month % 12 + 1, 1, tzinfo=dt.UTC)


def fetch_bundle(
    connector: CandleConnector,
    symbols: list[str],
    interval: str,
    start_ms: int,
    end_ms: int,
    output: Path,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    workers = 4 if connector.name.startswith("binance_") else 8
    with ThreadPoolExecutor(max_workers=min(workers, len(symbols))) as pool:
        fetched = pool.map(
            lambda symbol: (symbol, connector.candles(symbol, interval, start_ms, end_ms)),
            symbols,
        )
        series = dict(fetched)
    bundle = {
        "metadata": {
            "source": connector.name,
            "interval": interval,
            "startTime": start_ms,
            "endTime": end_ms,
            "retrievedAt": dt.datetime.now(dt.UTC).isoformat(),
            "limits": (
                "Monthly archive files; unavailable symbol-months are skipped"
                if connector.name.startswith("binance_")
                else "Provider returns at most the most recent 5000 candles per request"
            ),
        },
        "series": series,
    }
    output.write_text(json.dumps(bundle, indent=2) + "\n")
    return {"output": str(output), "symbols": {key: len(value) for key, value in series.items()}}
