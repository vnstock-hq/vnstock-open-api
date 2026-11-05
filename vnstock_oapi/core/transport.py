"""The only network layer of vnstock-oapi.

Lớp mạng duy nhất của vnstock-oapi.

Every request to a source goes through :class:`Transport`. It enforces, for every source and every
fork that keeps this file:

- TLS fully verified; there is no ``verify`` knob.
- Only hosts on the source's allowlist; redirects are refused, never followed.
- GET only, unless the caller is a declared token endpoint (``allow_post=True``).
- Connect timeout 10 s, read timeout 30 s.
- One neutral User-Agent for every request, ``Python/<major>.<minor>``: true of every client this package
  opens (httpx, websockets), never a browser's, and it names no library, project or person. These are
  official APIs called with the user's own key, so nothing is disguised; no request carries the word
  ``vnstock`` anywhere.
- 429: wait exactly ``Retry-After`` (or ``X-RateLimit-Reset``) once, at most 30 s, then give up. No
  other retries.
"""

from __future__ import annotations

import logging
import sys
import threading
import time
from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import urlsplit

import httpx

from vnstock_oapi.core.redact import RedactFilter, safe_url
from vnstock_oapi.errors import NetworkError, PolicyError, RateLimitError

__all__ = ["USER_AGENT", "Response", "Transport"]

logger = logging.getLogger("vnstock_oapi.transport")
logger.addFilter(RedactFilter())

USER_AGENT = f"Python/{sys.version_info[0]}.{sys.version_info[1]}"
# Text no outbound request may carry, in the URL, a header or the body. Matched case-insensitively.
UNDISCLOSED = ("vnstock",)
CONNECT_TIMEOUT_S = 10.0
READ_TIMEOUT_S = 30.0
RETRY_AFTER_CAP_S = 30.0
_UNSET: Any = object()
_INVALID_JSON: Any = object()


@dataclass
class Response:
    status: int
    headers: Mapping[str, str]
    content: bytes
    url: str
    method: str
    latency_ms: int
    request_count: int = 1
    extra: dict[str, Any] = field(default_factory=dict)
    _parsed: Any = field(default=_UNSET, init=False, repr=False, compare=False)

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    def json(self) -> Any:
        import json

        if self._parsed is _UNSET:
            try:
                self._parsed = json.loads(self.content)
            except ValueError:
                self._parsed = _INVALID_JSON
        if self._parsed is _INVALID_JSON:
            raise ValueError("Response content is not valid JSON")
        return self._parsed


class _RateLimiter:
    """Keep a minimum gap between two requests to the same source within a process.

    Giữ khoảng cách tối thiểu giữa hai request tới cùng một nguồn trong tiến trình.
    """

    def __init__(self, per_second: float | None, *, sleep: Any = time.sleep) -> None:
        self._interval = 1.0 / per_second if per_second else 0.0
        self._next = 0.0
        self._lock = threading.Lock()
        self._sleep = sleep or time.sleep

    def wait(self) -> None:
        if not self._interval:
            return
        with self._lock:
            now = time.monotonic()
            delay = self._next - now
            self._next = max(now, self._next) + self._interval
        if delay > 0:
            self._sleep(delay)


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(float(value), 0.0)
    except ValueError:
        return None


def _wait_seconds(headers: Mapping[str, str], now: float | None = None) -> float | None:
    """Seconds to wait after a 429: ``Retry-After``, else ``X-RateLimit-Reset``.

    Số giây phải chờ sau 429: ``Retry-After``; không có thì ``X-RateLimit-Reset``.

    ``X-RateLimit-Reset`` is an epoch in seconds at FHSC; a small value is read as a delay instead.
    """
    wait = _retry_after_seconds(headers.get("Retry-After"))
    if wait is not None:
        return wait
    reset = _retry_after_seconds(headers.get("X-RateLimit-Reset"))
    if reset is None:
        return None
    if reset > 1e9:
        return max(reset - (time.time() if now is None else now), 0.0)
    return reset


class Transport:
    """HTTP client bound to one source's host allowlist.

    HTTP cho một nguồn, chỉ tới các host trong danh sách được phép.
    """

    def __init__(
        self,
        source: str,
        allowed_hosts: frozenset[str],
        *,
        rate_per_second: float | None = None,
        client: httpx.Client | None = None,
        sleep: Any = time.sleep,
    ) -> None:
        self.source = source
        self.allowed_hosts = allowed_hosts
        self._sleep = sleep or time.sleep
        self._limiter = _RateLimiter(rate_per_second, sleep=self._sleep)
        self._client = client or httpx.Client(
            timeout=httpx.Timeout(READ_TIMEOUT_S, connect=CONNECT_TIMEOUT_S),
            follow_redirects=False,
            headers={"User-Agent": USER_AGENT, "Accept": "application/json"},
        )
        self._client.follow_redirects = False
        self._client.headers["User-Agent"] = USER_AGENT
        hooks = self._client.event_hooks.get("request", [])
        self._client.event_hooks["request"] = [*hooks, self._undisclosed]

    def _undisclosed(self, request: httpx.Request) -> None:
        """Refuse a request that names this library, its project or its author.

        Từ chối request có nhắc tên thư viện, dự án hay tác giả.
        """
        parts = [str(request.url), *(f"{k}: {v}" for k, v in request.headers.items())]
        text = "\n".join(parts).lower() + request.content.decode("utf-8", "replace").lower()
        if any(word in text for word in UNDISCLOSED):
            raise PolicyError(
                "Request không được nhắc tên thư viện hay dự án",
                source=self.source,
                url=safe_url(str(request.url)),
            )

    def _check(self, method: str, url: str, allow_post: bool) -> None:
        parts = urlsplit(url)
        if parts.scheme != "https":
            raise PolicyError("Chỉ cho phép https", source=self.source, url=safe_url(url))
        if parts.hostname not in self.allowed_hosts:
            raise PolicyError(
                f"Host {parts.hostname} không nằm trong danh sách được phép của {self.source}",
                source=self.source,
                url=safe_url(url),
            )
        if method != "GET" and not (method == "POST" and allow_post):
            raise PolicyError(
                f"Method {method} không được phép; vnstock-oapi chỉ đọc dữ liệu",
                source=self.source,
                url=safe_url(url),
            )

    def _send(
        self,
        method: str,
        url: str,
        params: Mapping[str, Any] | None,
        json: Any,
        headers: Mapping[str, str] | None,
    ) -> httpx.Response:
        self._limiter.wait()
        try:
            return self._client.request(method, url, params=params, json=json, headers=headers)
        except httpx.TimeoutException as exc:
            raise NetworkError(
                f"Hết thời gian chờ: {type(exc).__name__}", source=self.source, url=safe_url(url)
            ) from None
        except httpx.TransportError as exc:
            raise NetworkError(
                f"Lỗi kết nối: {type(exc).__name__}", source=self.source, url=safe_url(url)
            ) from None

    def request(
        self,
        method: str,
        url: str,
        *,
        params: Mapping[str, Any] | None = None,
        json: Any = None,
        headers: Mapping[str, str] | None = None,
        allow_post: bool = False,
    ) -> Response:
        method = method.upper()
        self._check(method, url, allow_post)
        started = time.monotonic()
        count = 1
        resp = self._send(method, url, params, json, headers)
        if resp.status_code == 429:
            wait = _wait_seconds(resp.headers)
            if wait is None or wait > RETRY_AFTER_CAP_S:
                raise RateLimitError(
                    "Nguồn báo vượt giới hạn tần suất (429)",
                    retry_after=wait,
                    source=self.source,
                    http_status=429,
                    url=safe_url(url),
                )
            logger.info("429 từ %s, chờ %.1f giây theo nguồn rồi gửi lại một lần", self.source, wait)
            self._sleep(wait)
            count += 1
            resp = self._send(method, url, params, json, headers)
            if resp.status_code == 429:
                raise RateLimitError(
                    "Nguồn vẫn báo vượt giới hạn tần suất sau khi đã chờ",
                    retry_after=_wait_seconds(resp.headers),
                    source=self.source,
                    http_status=429,
                    url=safe_url(url),
                )
        return Response(
            status=resp.status_code,
            headers=dict(resp.headers),
            content=resp.content,
            url=str(resp.request.url),
            method=method,
            latency_ms=int((time.monotonic() - started) * 1000),
            request_count=count,
        )

    def close(self) -> None:
        self._client.close()
