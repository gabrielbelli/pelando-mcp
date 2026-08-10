"""HTTP transport for pelando.com.br.

Three things here are load-bearing and were established by measurement, not preference:

1. **The User-Agent is set in the constructor, never per call.** Cloudflare in front of Pelando
   keeps a literal bot-UA string blocklist: `curl/8.7.1` and `python-httpx/0.27.0` both hard-403
   with the "Sorry, you have been blocked" interstitial. Our own library is on that list. An
   honest, self-identifying UA returns 200 — verified against both hosts.
2. **1 req/s, concurrency 1.** Serial headroom is much larger, but ~20 concurrent calls trip a 429
   plus a `Just a moment` challenge. Multi-term work is serialised, never fanned out.
3. **`Just a moment` in a body is a block, not content.** Parsing it yields zero results, which
   looks exactly like an honest "no deals found".

robots.txt is fetched once per host and paths are actually evaluated against it. It costs nothing
and it is the difference between respecting robots and assuming.
"""

from __future__ import annotations

import asyncio
import os
import random
import time
from typing import Any
from urllib.parse import urlencode, urljoin, urlparse
from urllib.robotparser import RobotFileParser

import httpx
import structlog

from .cache import Cache
from .models import API_BASE, WEB_BASE

log = structlog.get_logger("pelando_mcp.client")

DEFAULT_USER_AGENT = "pelando-mcp/0.1 (+https://github.com/gabrielbelli/pelando-mcp)"

TTL = {
    "search": 15 * 60,
    "feed": 15 * 60,
    "deal": 60 * 60,
    "comments": 30 * 60,
    "stores": 24 * 60 * 60,
    "coupons": 24 * 60 * 60,
    "communities": 7 * 24 * 60 * 60,
    "robots": 24 * 60 * 60,
    "ssr_search": 15 * 60,
    "ssr_deal": 30 * 60,  # matches the edge's own s-maxage=1800
}

_CHALLENGE_MARKERS = ("just a moment", "sorry, you have been blocked", "cf-browser-verification")


class PelandoError(RuntimeError):
    """An error reported by the API itself."""

    def __init__(self, message: str, status_code: int | None = None) -> None:
        super().__init__(message)
        self.status_code = status_code


class PelandoBlocked(PelandoError):
    """We were challenged or blocked. Never retried tightly, never parsed as content."""


class PelandoNotFound(PelandoError):
    """404 from the API. Two distinct bodies exist; both land here."""


class RobotsDisallowed(PelandoError):
    """The path is disallowed by robots.txt. Not retried, not worked around."""


class TokenBucket:
    def __init__(self, rps: float, burst: int) -> None:
        self.capacity = max(1, int(burst))
        self.tokens = float(self.capacity)
        self.rate = max(0.1, float(rps))
        self.updated_at = time.monotonic()
        self._lock = asyncio.Lock()

    async def acquire(self) -> None:
        async with self._lock:
            while True:
                now = time.monotonic()
                self.tokens = min(self.capacity, self.tokens + (now - self.updated_at) * self.rate)
                self.updated_at = now
                if self.tokens >= 1:
                    self.tokens -= 1
                    return
                await asyncio.sleep((1 - self.tokens) / self.rate)


class PelandoClient:
    """Polite async client for both the JSON API and the SSR site."""

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        rate_limit_rps: float | None = None,
        rate_limit_burst: int | None = None,
        cache: Cache | None = None,
        timeout: float = 20.0,
        max_retries: int = 3,
        respect_robots: bool = True,
    ) -> None:
        self.user_agent = user_agent or os.getenv("PELANDO_USER_AGENT") or DEFAULT_USER_AGENT
        rps = (
            rate_limit_rps
            if rate_limit_rps is not None
            else float(os.getenv("PELANDO_RATE_LIMIT_RPS", "1.0"))
        )
        burst = (
            rate_limit_burst
            if rate_limit_burst is not None
            else int(os.getenv("PELANDO_RATE_LIMIT_BURST", "3"))
        )
        self.bucket = TokenBucket(rps=rps, burst=burst)
        self.cache = cache
        self.max_retries = max_retries
        self.respect_robots = respect_robots
        self._robots: dict[str, RobotFileParser | None] = {}
        self._robots_lock = asyncio.Lock()
        self._client = httpx.AsyncClient(
            timeout=timeout,
            follow_redirects=True,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json, text/html;q=0.9, */*;q=0.8",
                "Accept-Language": "pt-BR,pt;q=0.9,en;q=0.8",
            },
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def __aenter__(self) -> PelandoClient:
        return self

    async def __aexit__(self, *exc: Any) -> None:
        await self.aclose()

    # ----------------------------------------------------------------- robots

    async def _robots_for(self, url: str) -> RobotFileParser | None:
        parts = urlparse(url)
        origin = f"{parts.scheme}://{parts.netloc}"
        async with self._robots_lock:
            if origin in self._robots:
                return self._robots[origin]
            parser: RobotFileParser | None = None
            try:
                await self.bucket.acquire()
                resp = await self._client.get(f"{origin}/robots.txt")
                if resp.status_code == 200:
                    parser = RobotFileParser()
                    parser.parse(resp.text.splitlines())
                else:
                    # No robots.txt (the API host serves none) — nothing to honour.
                    log.debug("robots_absent", origin=origin, status=resp.status_code)
            except httpx.HTTPError as exc:
                log.warning("robots_fetch_failed", origin=origin, error=str(exc))
            self._robots[origin] = parser
            return parser

    async def _assert_allowed(self, url: str) -> None:
        if not self.respect_robots:
            return
        parser = await self._robots_for(url)
        if parser is None:
            return
        if not parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(f"robots.txt disallows {url} for {self.user_agent}")

    # ------------------------------------------------------------------ fetch

    async def _fetch_text(self, url: str, ttl_seconds: float) -> str:
        if self.cache is not None:
            cached = self.cache.get(url)
            if cached is not None:
                log.debug("cache_hit", url=url)
                return cached

        await self._assert_allowed(url)

        last_exc: Exception | None = None
        for attempt in range(self.max_retries + 1):
            await self.bucket.acquire()
            try:
                resp = await self._client.get(url)
            except (httpx.TransportError, httpx.TimeoutException) as exc:
                last_exc = exc
                await asyncio.sleep(_backoff(attempt))
                continue

            body = resp.text
            if _is_challenge(body) or resp.status_code == 403:
                # Back off hard and slowly. Being challenged means we are already unwelcome;
                # hammering is exactly the wrong response.
                last_exc = PelandoBlocked(
                    f"blocked or challenged at {url} (HTTP {resp.status_code})", resp.status_code
                )
                if attempt >= self.max_retries:
                    break
                await asyncio.sleep(5.0 * (2**attempt))
                continue

            if resp.status_code == 404:
                raise PelandoNotFound(_error_message(resp) or f"not found: {url}", 404)

            if resp.status_code in (429, 500, 502, 503, 504):
                retry_after = resp.headers.get("Retry-After")
                delay = (
                    float(retry_after)
                    if retry_after and retry_after.isdigit()
                    else _backoff(attempt)
                )
                last_exc = PelandoError(f"retryable HTTP {resp.status_code}", resp.status_code)
                await asyncio.sleep(delay)
                continue

            if resp.status_code >= 400:
                raise PelandoError(
                    _error_message(resp) or f"HTTP {resp.status_code} for {url}", resp.status_code
                )

            if self.cache is not None:
                self.cache.put(url, body, ttl_seconds)
            return body

        assert last_exc is not None
        raise last_exc

    async def get_json(self, path: str, params: dict[str, Any] | None = None, *, ttl: float) -> Any:
        """GET a JSON endpoint on the API host.

        Params are built in exactly one place: passing `term` twice yields a 400
        ("term must be a string"), which is what happens if a caller merges params into a path
        that already carries a query string.
        """
        if "?" in path:
            raise ValueError("build query params via `params`, never inline — duplicates 400")
        url = urljoin(API_BASE + "/", path.lstrip("/"))
        if params:
            clean = {k: _param(v) for k, v in params.items() if v is not None}
            if clean:
                url = f"{url}?{urlencode(clean)}"
        body = await self._fetch_text(url, ttl)
        try:
            import json

            return json.loads(body)
        except ValueError as exc:
            raise PelandoError(f"malformed JSON from {url}: {exc}") from exc

    async def get_html(self, path: str, *, ttl: float) -> str:
        """GET a page on the SSR host."""
        url = urljoin(WEB_BASE + "/", path.lstrip("/"))
        return await self._fetch_text(url, ttl)


def unwrap(payload: Any) -> Any:
    """Return the useful part of an API response.

    Success is `{"data": ..., "timestamp": ...}`. Errors use one of two entirely different
    envelopes — `{"statusCode", "errorMessage"}` for application errors and NestJS's
    `{"message", "error", "statusCode"}` for unknown routes. Only `statusCode` is common to both.
    """
    if isinstance(payload, dict):
        if "data" in payload:
            return payload["data"]
        status = payload.get("statusCode")
        if isinstance(status, int) and status >= 400:
            message = payload.get("errorMessage") or payload.get("message") or "API error"
            exc = PelandoNotFound if status == 404 else PelandoError
            raise exc(str(message), status)
    return payload


def _param(value: Any) -> str:
    if isinstance(value, bool):
        return "true" if value else "false"
    return str(value)


def _is_challenge(body: str) -> bool:
    head = body[:4096].lower()
    return any(marker in head for marker in _CHALLENGE_MARKERS)


def _error_message(resp: httpx.Response) -> str | None:
    try:
        payload = resp.json()
    except ValueError:
        return None
    if isinstance(payload, dict):
        msg = payload.get("errorMessage") or payload.get("message")
        if msg:
            return str(msg)
    return None


def _backoff(attempt: int) -> float:
    base = 0.5 * (2**attempt)
    return base + random.uniform(0, base * 0.2)
