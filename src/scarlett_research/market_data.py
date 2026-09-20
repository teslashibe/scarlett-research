from __future__ import annotations

import csv
import datetime as dt
import io
import json
import re
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET
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


def _archive_prefixes(payload: bytes) -> tuple[list[str], str | None]:
    root = ET.fromstring(payload)
    namespace = {"s3": "http://s3.amazonaws.com/doc/2006-03-01/"}
    prefixes = [node.text or "" for node in root.findall("s3:CommonPrefixes/s3:Prefix", namespace)]
    token = root.findtext("s3:NextContinuationToken", default=None, namespaces=namespace)
    return prefixes, token


def binance_futures_universe(quote: str = "USDT") -> list[str]:
    """List USD-M kline symbols from the public archive, independent of live API access."""
    endpoint = "https://s3-ap-northeast-1.amazonaws.com/data.binance.vision"
    prefix = "data/futures/um/monthly/klines/"
    token: str | None = None
    symbols: set[str] = set()
    while True:
        query = {"list-type": "2", "delimiter": "/", "prefix": prefix}
        if token:
            query["continuation-token"] = token
        request = urllib.request.Request(
            endpoint + "?" + urllib.parse.urlencode(query),
            headers={"User-Agent": "scarlett-research/0.2"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            prefixes, token = _archive_prefixes(response.read())
        for value in prefixes:
            symbol = value.removeprefix(prefix).removesuffix("/")
            if symbol.endswith(quote):
                symbols.add(symbol)
        if not token:
            break
    return sorted(symbols)


def _valid_contract_base(symbol: str) -> bool:
    return re.fullmatch(r"[A-Z0-9]+", symbol) is not None


def ranked_crypto_futures_universe(
    archive_symbols: list[str], limit: int = 200, pages: int = 2
) -> list[dict[str, Any]]:
    """Intersect an independent CoinGecko ranking with archived Binance contracts."""
    if limit < 1 or pages < 1:
        raise ValueError("limit and pages must be positive")
    available = set(archive_symbols)
    ranked: list[dict[str, Any]] = []
    seen: set[str] = set()
    for page in range(1, pages + 1):
        query = urllib.parse.urlencode(
            {
                "vs_currency": "usd",
                "order": "market_cap_desc",
                "per_page": 250,
                "page": page,
                "sparkline": "false",
            }
        )
        request = urllib.request.Request(
            "https://api.coingecko.com/api/v3/coins/markets?" + query,
            headers={"User-Agent": "scarlett-research/0.2"},
        )
        with urllib.request.urlopen(request, timeout=60) as response:
            rows = json.load(response)
        for row in rows:
            base = str(row["symbol"]).upper()
            if not _valid_contract_base(base):
                continue
            symbol = base + "USDT"
            if symbol not in available or symbol in seen:
                continue
            seen.add(symbol)
            ranked.append(
                {
                    "symbol": symbol,
                    "coinId": row["id"],
                    "marketCapRank": row.get("market_cap_rank"),
                    "marketCapUsd": row.get("market_cap"),
                    "volume24hUsd": row.get("total_volume"),
                }
            )
            if len(ranked) == limit:
                return ranked
    return ranked


def normalize_market_symbol(symbol: str, source: str) -> str:
    """Map provider instruments to Scarlett's base-asset forecast market."""
    if source.startswith("binance_"):
        for quote in ("USDT", "USDC", "USD"):
            if symbol.endswith(quote) and len(symbol) > len(quote):
                return symbol[: -len(quote)]
    return symbol


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
                    if not _is_archive_data_row(row):
                        # USD-M archives include a header row; spot archives do not.
                        continue
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


@dataclass
class BinanceFuturesMetricsConnector:
    """Public Binance Vision daily USD-M positioning and open-interest connector."""

    base_url: str = "https://data.binance.vision/data/futures/um/daily/metrics"
    name: str = "binance_um_futures_metrics_archive"

    def metrics(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        day = dt.datetime.fromtimestamp(start_ms / 1000, dt.UTC).replace(
            hour=0, minute=0, second=0, microsecond=0
        )
        end = dt.datetime.fromtimestamp(end_ms / 1000, dt.UTC)
        output = []
        while day <= end:
            label = day.strftime("%Y-%m-%d")
            filename = f"{symbol}-metrics-{label}.zip"
            url = f"{self.base_url}/{symbol}/{filename}"
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
            if archive is not None:
                with archive.open(archive.namelist()[0]) as source:
                    for row in csv.DictReader(io.TextIOWrapper(source)):
                        record = _metrics_record(row)
                        timestamp = dt.datetime.fromisoformat(record["time"])
                        if start_ms <= timestamp.timestamp() * 1000 <= end_ms:
                            output.append(record)
            day += dt.timedelta(days=1)
        return output


@dataclass
class BinanceFundingArchiveConnector:
    """Public Binance Vision monthly USD-M funding-rate connector."""

    base_url: str = "https://data.binance.vision/data/futures/um/monthly/fundingRate"
    name: str = "binance_um_funding_rate_archive"

    def funding(self, symbol: str, start_ms: int, end_ms: int) -> list[dict[str, Any]]:
        start = dt.datetime.fromtimestamp(start_ms / 1000, dt.UTC)
        end = dt.datetime.fromtimestamp(end_ms / 1000, dt.UTC)
        month = dt.datetime(start.year, start.month, 1, tzinfo=dt.UTC)
        output = []
        while month <= end:
            label = f"{month.year:04d}-{month.month:02d}"
            filename = f"{symbol}-fundingRate-{label}.zip"
            url = f"{self.base_url}/{symbol}/{filename}"
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
            if archive is not None:
                with archive.open(archive.namelist()[0]) as source:
                    for row in csv.DictReader(io.TextIOWrapper(source)):
                        raw_time = int(row["calc_time"])
                        divisor = 1_000_000 if raw_time > 10**14 else 1_000
                        timestamp = dt.datetime.fromtimestamp(raw_time / divisor, dt.UTC)
                        timestamp_ms = timestamp.timestamp() * 1000
                        if start_ms <= timestamp_ms <= end_ms:
                            output.append(
                                {
                                    "time": timestamp.isoformat().replace("+00:00", "Z"),
                                    "symbol": symbol,
                                    "intervalHours": int(row["funding_interval_hours"]),
                                    "fundingRate": float(row["last_funding_rate"]),
                                }
                            )
            month = _next_month(month)
        return sorted(output, key=lambda row: row["time"])


def _metrics_record(row: dict[str, str]) -> dict[str, Any]:
    timestamp = dt.datetime.fromisoformat(row["create_time"]).replace(tzinfo=dt.UTC)

    def optional_float(name: str) -> float | None:
        value = row.get(name, "").strip()
        return float(value) if value else None

    return {
        "time": timestamp.isoformat().replace("+00:00", "Z"),
        "symbol": row["symbol"],
        "openInterest": optional_float("sum_open_interest"),
        "openInterestValue": optional_float("sum_open_interest_value"),
        "topTraderAccountLongShortRatio": optional_float("count_toptrader_long_short_ratio"),
        "topTraderPositionLongShortRatio": optional_float("sum_toptrader_long_short_ratio"),
        "globalLongShortRatio": optional_float("count_long_short_ratio"),
        "takerLongShortVolumeRatio": optional_float("sum_taker_long_short_vol_ratio"),
    }


def _next_month(value: dt.datetime) -> dt.datetime:
    return dt.datetime(value.year + (value.month == 12), value.month % 12 + 1, 1, tzinfo=dt.UTC)


def _is_archive_data_row(row: list[str]) -> bool:
    return bool(row and row[0].isdigit())


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


def fetch_metrics_bundle(
    connector: BinanceFuturesMetricsConnector,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
    output: Path,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=min(8, len(symbols))) as pool:
        fetched = pool.map(
            lambda symbol: (symbol, connector.metrics(symbol, start_ms, end_ms)), symbols
        )
        series = dict(fetched)
    bundle = {
        "metadata": {
            "source": connector.name,
            "interval": "5m",
            "startTime": start_ms,
            "endTime": end_ms,
            "retrievedAt": dt.datetime.now(dt.UTC).isoformat(),
            "limits": "Daily archive files; unavailable symbol-days are skipped",
        },
        "series": series,
    }
    output.write_text(json.dumps(bundle, indent=2) + "\n")
    return {"output": str(output), "symbols": {key: len(value) for key, value in series.items()}}


def fetch_funding_bundle(
    connector: BinanceFundingArchiveConnector,
    symbols: list[str],
    start_ms: int,
    end_ms: int,
    output: Path,
) -> dict[str, Any]:
    output.parent.mkdir(parents=True, exist_ok=True)
    with ThreadPoolExecutor(max_workers=min(6, len(symbols))) as pool:
        fetched = pool.map(
            lambda symbol: (symbol, connector.funding(symbol, start_ms, end_ms)), symbols
        )
        series = dict(fetched)
    bundle = {
        "metadata": {
            "source": connector.name,
            "startTime": start_ms,
            "endTime": end_ms,
            "retrievedAt": dt.datetime.now(dt.UTC).isoformat(),
            "limits": "Monthly archive files; unavailable symbol-months are skipped",
        },
        "series": series,
    }
    output.write_text(json.dumps(bundle, indent=2) + "\n")
    return {"output": str(output), "symbols": {key: len(value) for key, value in series.items()}}
