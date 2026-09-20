from __future__ import annotations

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any


class ScarlettError(RuntimeError):
    pass


@dataclass
class Response:
    body: Any
    headers: dict[str, str]


class ScarlettClient:
    def __init__(self, token: str, base_url: str = "https://api.scarlett.ai/v1", timeout: int = 30):
        if not token:
            raise ValueError("SCARLETT_API_TOKEN is required")
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self.requests_used = 0

    def get(self, path: str, params: dict[str, Any] | None = None) -> Response:
        query = urllib.parse.urlencode({k: v for k, v in (params or {}).items() if v is not None})
        url = self.base_url + path + (("?" + query) if query else "")
        request = urllib.request.Request(
            url,
            headers={
                "Authorization": f"Bearer {self.token}",
                "Accept": "application/json",
                "User-Agent": "scarlett-research/0.1 (+https://github.com/teslashibe/scarlett-research)",
            },
        )
        for attempt in range(4):
            self.requests_used += 1
            try:
                with urllib.request.urlopen(request, timeout=self.timeout) as response:
                    return Response(json.load(response), dict(response.headers.items()))
            except urllib.error.HTTPError as exc:
                if exc.code == 429 and attempt < 3:
                    time.sleep(min(float(exc.headers.get("Retry-After", "1")), 10))
                    continue
                try:
                    detail = json.loads(exc.read()).get("error", {}).get("message", str(exc))
                except Exception:
                    detail = str(exc)
                raise ScarlettError(f"GET {path} failed ({exc.code}): {detail}") from exc
        raise ScarlettError(f"GET {path} exhausted retries")

    def setup_pages(self, strategy: str, limit: int = 100) -> Iterator[list[dict[str, Any]]]:
        before = before_id = None
        while True:
            response = self.get(
                "/setups",
                {
                    "type": "all",
                    "strategy": strategy,
                    "limit": limit,
                    "before": before,
                    "beforeId": before_id,
                },
            ).body
            items = response.get("items", response if isinstance(response, list) else [])
            if not items:
                return
            yield items
            if len(items) < limit:
                return
            last = items[-1]
            before = last.get("publishedAt") or last.get("published_at")
            before_id = last.get("id")
            if not before or not before_id:
                return
