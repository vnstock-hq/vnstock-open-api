"""Binance Spot public market streams on ``data-stream.binance.vision``. No key.

Stream dữ liệu thị trường công khai của Binance Spot qua ``data-stream.binance.vision``.
Không dùng khoá.

Streams go in the URL of a combined connection (``/stream?streams=btcusdt@trade/ethusdt@kline_1m``),
so the client sends nothing upstream and stays far from the limit of 5 incoming messages per second.
More than ``streams_per_connection`` streams (1024 by the docs) open more connections. A connection
lives 24 hours: the client reconnects on its own after 23 hours, and at once on ``serverShutdown``.
The server pings every 20 s; the WebSocket library answers. A refusal at connect (HTTP 403, 418, 429,
451) stops the stream: no retry, no other identity.

Each message becomes one frame v1 whose payload is the message verbatim
(``{"stream": "btcusdt@trade", "data": {...}}``) and whose channel is the family of that stream.

    client = BinanceStreamClient().subscribe("trade", ["BTCUSDT"]).subscribe("kline.1m", ["BTCUSDT"])
    async for frame in client.frames():
        ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from importlib import resources
from typing import Any

from vnstock_oapi.core.transport import USER_AGENT
from vnstock_oapi.errors import InputError, NetworkError, RateLimitError, SourceError
from vnstock_oapi.stream.frame import Frame, Kind
from vnstock_oapi.stream.spec import StreamSpec, load_stream_spec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = ["BinanceStreamClient"]

logger = logging.getLogger("vnstock_oapi.stream")

_POLL_S = 1.0
_MAX_BACKOFF_S = 60.0
# Keep each connection URL short enough for any proxy on the way.
_MAX_URL_CHARS = 7000
_REFUSED = (403, 418, 429, 451)
SHUTDOWN = "server_shutdown"

Connect = Callable[[str], AbstractAsyncContextManager[Any]]


def _load_extra(source: str) -> tuple[dict[str, str], dict[str, Any]]:
    path = resources.files("vnstock_oapi") / "spec" / "sources" / source / "streams.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    wires = {row["pattern"]: row["wire"] for row in data.get("channels", [])}
    return wires, dict(data.get("limits", {}))


def _default_connect(url: str) -> AbstractAsyncContextManager[Any]:
    try:
        from websockets.asyncio.client import connect
    except ImportError:  # pragma: no cover
        raise InputError("Cần gói websockets: pip install 'vnstock-oapi[stream]'") from None
    # The server pings every 20 s and the library pongs. One client ping a minute only checks the link.
    return connect(
        url, open_timeout=15, ping_interval=60, ping_timeout=30, max_size=2**24, user_agent_header=USER_AGENT
    )


def _closed_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError, EOFError, asyncio.TimeoutError]
    try:
        from websockets.exceptions import ConnectionClosed, InvalidMessage

        errors += [ConnectionClosed, InvalidMessage]
    except ImportError:  # pragma: no cover
        pass
    return tuple(errors)


def _refused_status(exc: BaseException) -> int | None:
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class _Rotate(Exception):
    """The connection reached its planned end (23 hours, or ``serverShutdown``): reconnect now."""


class BinanceStreamClient:
    """Combined-stream connections to Binance's market-data endpoint, merged into one stream of frames.

    Các kết nối stream gộp tới cổng dữ liệu thị trường của Binance, gộp thành một dòng frame.
    """

    def __init__(
        self,
        source: str = "binance",
        *,
        profile: str = "default",
        connect: Connect | None = None,
        max_reconnects: int = 10,
        streams_per_connection: int | None = None,
        lifetime_s: float | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.spec: StreamSpec = load_stream_spec(source)
        self._wire_templates, limits = _load_extra(source)
        self.profile = profile  # no key: kept only for the common client signature
        self._connect = connect or _default_connect
        self._max_reconnects = max_reconnects
        self._per_connection = int(streams_per_connection or limits.get("streams_per_connection", 1024))
        self._lifetime_s = float(lifetime_s or float(limits.get("reconnect_after_hours", 23)) * 3600)
        self._clock_ns = clock_ns
        self._sleep = sleep
        self._wires: dict[str, str] = {}  # stream name -> channel family
        self._seq = 0
        self._conn_ids = 0
        self._stopping = False

    @property
    def source(self) -> str:
        return self.spec.source

    @property
    def streams(self) -> list[str]:
        """Binance stream names this client opens, e.g. ``["btcusdt@trade"]``."""
        return list(self._wires)

    def subscribe(self, channel: str, symbols: Sequence[str] = ()) -> BinanceStreamClient:
        """Add a declared channel; user-data and undeclared streams raise ``PolicyError`` here.

        Thêm một kênh đã khai; stream dữ liệu người dùng và kênh chưa khai bị từ chối ngay tại đây.
        """
        rule = self.spec.resolve(channel)
        wanted = [s.strip().lower() for s in symbols if s.strip()]
        if rule.symbols and not wanted:
            raise InputError(f"Kênh {channel!r} cần danh sách cặp (BTCUSDT...)", source=self.source)
        if not rule.symbols and wanted:
            raise InputError(f"Kênh {channel!r} không nhận danh sách cặp", source=self.source)
        bad = [s for s in wanted if not s.isalnum()]
        if bad:
            raise InputError(f"Cặp không hợp lệ: {bad[:5]}", source=self.source)
        params = _params(rule.pattern, channel)
        template = self._wire_templates[rule.pattern]
        for symbol in wanted or [""]:
            self._wires.setdefault(template.format(symbol=symbol, **params), rule.name)
        return self

    def connections(self) -> list[str]:
        """The URL of each connection ``frames()`` opens.

        URL của từng kết nối ``frames()`` sẽ mở.
        """
        urls: list[str] = []
        chunk: list[str] = []

        def url(names: list[str]) -> str:
            return f"{self.spec.url}?streams={'/'.join(names)}"

        for name in self._wires:
            if chunk and (len(chunk) >= self._per_connection or len(url([*chunk, name])) > _MAX_URL_CHARS):
                urls.append(url(chunk))
                chunk = []
            chunk.append(name)
        if chunk:
            urls.append(url(chunk))
        for each in urls:
            self.spec.check_url(each)
        return urls

    def stop(self) -> None:
        self._stopping = True

    async def frames(self) -> AsyncIterator[Frame]:
        """Frames of every connection, in arrival order, until ``stop()``.

        Frame của mọi kết nối, theo thứ tự nhận, cho tới khi ``stop()``.
        """
        if not self._wires:
            raise InputError("Chưa đăng ký kênh nào", source=self.source)
        queue: asyncio.Queue[Frame | BaseException | None] = asyncio.Queue()
        tasks = [asyncio.create_task(self._run(url, queue)) for url in self.connections()]
        running = len(tasks)
        try:
            while running:
                item = await queue.get()
                if item is None:
                    running -= 1
                elif isinstance(item, BaseException):
                    raise item
                else:
                    yield item
        finally:
            self._stopping = True
            for task in tasks:
                task.cancel()

    # -- internals --------------------------------------------------------------

    def _frame(self, kind: Kind, conn_id: int, detail: str = "") -> Frame:
        self._seq += 1
        return Frame(
            source=self.source,
            channel="",
            conn_id=conn_id,
            seq=self._seq,
            recv_at_ns=self._clock_ns(),
            payload=b"",
            kind=kind,
            detail=detail,
        )

    async def _run(self, url: str, queue: asyncio.Queue[Any]) -> None:
        closed = _closed_errors()
        retried: tuple[type[BaseException], ...] = (*closed, NetworkError)
        failures, lost_at = 0, None
        conn_id = 0
        try:
            while not self._stopping:
                self._conn_ids += 1
                conn_id = self._conn_ids
                try:
                    try:
                        async with self._connect(url) as socket:
                            if lost_at is None:
                                await queue.put(self._frame("connected", conn_id))
                            else:
                                gap = {"from_ns": lost_at, "to_ns": self._clock_ns()}
                                await queue.put(self._frame("gap", conn_id, json.dumps(gap)))
                                await queue.put(self._frame("reconnected", conn_id))
                            lost_at = None
                            received = [0]
                            try:
                                await self._pump(socket, conn_id, queue, received)
                            finally:
                                if received[0]:
                                    failures = 0  # only a connection that delivered data resets the count
                    except closed:
                        raise
                    except (_Rotate, InputError, SourceError, RateLimitError, asyncio.CancelledError):
                        raise
                    except Exception as exc:
                        status = _refused_status(exc)
                        if status in _REFUSED:
                            error_type = RateLimitError if status in (418, 429) else SourceError
                            raise error_type(
                                f"binance từ chối kết nối stream (HTTP {status}); dừng, không thử lại",
                                source=self.source,
                                http_status=status,
                            ) from None
                        if status is not None:
                            raise NetworkError(
                                f"binance trả HTTP {status} khi nối stream",
                                source=self.source,
                                http_status=status,
                            ) from None
                        raise
                except _Rotate as planned:
                    lost_at = self._clock_ns()
                    await queue.put(self._frame("closed", conn_id, f"rotate: {planned}"))
                except retried as exc:
                    lost_at = lost_at or self._clock_ns()
                    await queue.put(self._frame("closed", conn_id, type(exc).__name__))
                    if self._stopping:
                        break
                    failures += 1
                    if failures > self._max_reconnects:
                        raise NetworkError(
                            f"Mất kết nối stream binance sau {self._max_reconnects} lần nối lại",
                            source=self.source,
                        ) from None
                    delay = min(2.0 ** (failures - 1), _MAX_BACKOFF_S)
                    logger.warning(
                        "Stream binance mất kết nối (%s); nối lại sau %.0f s", type(exc).__name__, delay
                    )
                    await self._sleep(delay)
            await queue.put(self._frame("closed", conn_id, "stopped"))
            await queue.put(None)
        except BaseException as exc:  # noqa: BLE001 - hand every failure to frames()
            if not isinstance(exc, asyncio.CancelledError):
                await queue.put(exc)
            raise

    async def _pump(self, socket: Any, conn_id: int, queue: asyncio.Queue[Any], received: list[int]) -> None:
        loop = asyncio.get_running_loop()
        ends = loop.time() + self._lifetime_s
        while not self._stopping:
            if loop.time() >= ends:
                raise _Rotate("connection lifetime")
            try:
                raw = await asyncio.wait_for(socket.recv(), _POLL_S)
            except asyncio.TimeoutError:
                continue
            recv_at = self._clock_ns()
            received[0] += 1
            text = raw.decode("utf-8") if isinstance(raw, bytes) else raw
            try:
                message = json.loads(text)
            except ValueError:
                message = None
            if isinstance(message, dict) and "result" in message and "id" in message:
                continue  # reply to a request; none is sent, kept for safety
            stream = message.get("stream") if isinstance(message, dict) else None
            data = message.get("data") if isinstance(message, dict) else None
            shutdown = stream == "!serverShutdown" or (
                isinstance(data, dict) and data.get("e") == "serverShutdown"
            )
            self._seq += 1
            await queue.put(
                Frame(
                    source=self.source,
                    channel=SHUTDOWN if shutdown else self._wires.get(str(stream), "unknown"),
                    conn_id=conn_id,
                    seq=self._seq,
                    recv_at_ns=recv_at,
                    payload=text.encode("utf-8"),
                    encoding="text",
                )
            )
            if shutdown:
                raise _Rotate("serverShutdown")


def _params(pattern: str, channel: str) -> dict[str, str]:
    """``kline.{interval}`` and ``kline.1m`` → ``{"interval": "1m"}`` (after ``resolve``)."""
    out: dict[str, str] = {}
    for part, value in zip(pattern.split("."), channel.split("."), strict=False):
        if part.startswith("{") and part.endswith("}"):
            out[part[1:-1]] = value
    return out
