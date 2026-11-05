"""TCBS market-data stream: the pipe-delimited protocol of the iFlash Open API sockets.

Stream dữ liệu thị trường TCBS: giao thức chữ phân tách bằng ``|`` của các socket iFlash Open API.

One socket per endpoint of ``streams.toml`` (price board, derivative board, price history). Each
socket authenticates with the REST token (``d|a|||<base64 token>``), subscribes, and sends a text
heartbeat every 2 seconds; RFC 6455 pings stay off. Every
``s|<id>|<JSON>`` line (``<CODE>|<SYMBOL>|<JSON>`` on the history endpoint) becomes a frame v1 with
the line verbatim as payload. The token never enters
a frame, a log or an error.

    client = TcbsStreamClient().subscribe("match", ["FPT", "HPG"]).subscribe("history_1m", ["FPT"])
    async for frame in client.frames():
        ...
"""

from __future__ import annotations

import asyncio
import base64
import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from dataclasses import dataclass
from importlib import resources
from typing import Any
from urllib.parse import urlsplit

from vnstock_oapi.core.transport import USER_AGENT
from vnstock_oapi.errors import AuthError, InputError, NetworkError, PolicyError
from vnstock_oapi.stream._blocking import Stopped, call_blocking
from vnstock_oapi.stream.frame import Frame, Kind
from vnstock_oapi.stream.spec import StreamSpec, load_stream_spec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = ["TcbsStreamClient"]

logger = logging.getLogger("vnstock_oapi.stream")

_MAX_BACKOFF_S = 30.0
_REPLY_TIMEOUT_S = 15.0


@dataclass(frozen=True)
class _Endpoint:
    name: str
    url: str
    subscribe: str
    heartbeat: str


@dataclass(frozen=True)
class _Route:
    endpoint: str
    object: str
    prop: str


def _load_extra(source: str) -> tuple[dict[str, _Endpoint], dict[str, _Route], tuple[str, ...]]:
    path = resources.files("vnstock_oapi") / "spec" / "sources" / source / "streams.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    endpoints = {
        name: _Endpoint(name, row["url"], row["subscribe"], row["heartbeat"])
        for name, row in data.get("endpoints", {}).items()
    }
    routes = {
        row["name"]: _Route(row["endpoint"], row.get("object", ""), row["prop"])
        for row in data.get("channels", [])
    }
    return endpoints, routes, tuple(data.get("forbidden_paths", ()))


def _default_connect(url: str) -> Any:
    try:
        from websockets.asyncio.client import connect
    except ImportError:  # pragma: no cover
        raise InputError("Cần gói websockets: pip install 'vnstock-oapi[stream]'") from None
    # TLS uses the system defaults. ping_interval=None: TCBS closes with 1002 on protocol pings.
    return connect(url, open_timeout=15, ping_interval=None, max_size=2**22, user_agent_header=USER_AGENT)


class TcbsStreamClient:
    """Market-data sockets of TCBS, one per endpoint, merged into one stream of frames v1.

    Các socket dữ liệu thị trường của TCBS, mỗi cổng một socket, gộp thành một dòng frame v1.
    """

    def __init__(
        self,
        source: str = "tcbs",
        *,
        profile: str = "default",
        connect: Callable[[str], Any] | None = None,
        token: Callable[[], str] | None = None,
        max_reconnects: int = 10,
        clock_ns: Callable[[], int] = time.time_ns,
    ) -> None:
        self.spec: StreamSpec = load_stream_spec(source)
        self._endpoints, self._routes, self._forbidden_paths = _load_extra(source)
        for endpoint in self._endpoints.values():
            self._check_endpoint(endpoint.url)
        self.profile = profile
        self._connect = connect or _default_connect
        self._token = token or self._rest_token
        self._max_reconnects = max_reconnects
        self._clock_ns = clock_ns
        # endpoint -> (object, prop) -> symbols
        self._subscriptions: dict[str, dict[tuple[str, str], list[str]]] = {}
        self._families = {t: rule.name for rule in self.spec.channels for t in rule.message_types}
        # The history endpoint (/ws/ouranos) sends "<CODE>|<SYMBOL>|{JSON}", not "s|<id>|...": its
        # data lines start with a message type declared for that endpoint.
        self._line_heads: dict[str, frozenset[str]] = {}
        for rule in self.spec.channels:
            route = self._routes[rule.name]
            heads = self._line_heads.get(route.endpoint, frozenset())
            self._line_heads[route.endpoint] = heads | frozenset(rule.message_types)
        self._seq = 0
        self._conn_ids = 0
        self._stopping = False

    @property
    def source(self) -> str:
        return self.spec.source

    def _check_endpoint(self, url: str) -> None:
        parts = urlsplit(url)
        if parts.scheme != "wss" or parts.hostname != self.spec.host:
            raise PolicyError(f"{self.source} chỉ được mở stream tới {self.spec.host}", url=url)
        if any(parts.path.startswith(p) for p in self._forbidden_paths):
            raise PolicyError(f"{parts.path} là cổng lệnh hoặc tài khoản, không được mở", url=url)

    def _rest_token(self) -> str:
        from vnstock_oapi import source as rest_source

        return rest_source(self.source, self.profile).auth.access_token()  # type: ignore[attr-defined,no-any-return]

    def subscribe(self, channel: str, symbols: Sequence[str] = ()) -> TcbsStreamClient:
        """Add a declared channel; order and account channels raise ``PolicyError`` here.

        Thêm một kênh đã khai; kênh lệnh, kênh tài khoản bị ``PolicyError`` ngay tại đây.
        """
        rule = self.spec.resolve(channel)
        route = self._routes[rule.name]
        wanted = [s.strip().upper() for s in symbols if s.strip()]
        if rule.symbols and not wanted:
            raise InputError(f"Kênh {channel!r} cần danh sách mã", source=self.source)
        if rule.name == "match_history" and len(wanted) > 1:
            # Its lines carry no symbol, so with two symbols a trade cannot be told apart.
            raise InputError(
                "Kênh match_history chỉ nhận một mã mỗi kết nối: dữ liệu trả về không ghi mã chứng khoán",
                source=self.source,
            )
        if not rule.symbols:
            if wanted:
                raise InputError(f"Kênh {channel!r} không nhận danh sách mã", source=self.source)
            wanted = [channel.split(".", 1)[1]]  # put_through.1 -> floor 1
        group = self._subscriptions.setdefault(route.endpoint, {}).setdefault((route.object, route.prop), [])
        group.extend(s for s in wanted if s not in group)
        return self

    def stop(self) -> None:
        self._stopping = True

    async def frames(self) -> AsyncIterator[Frame]:
        """Frames of every subscribed endpoint, in arrival order, until ``stop()``.

        Frame của mọi cổng đã đăng ký, theo thứ tự nhận, cho tới khi ``stop()``.
        """
        if not self._subscriptions:
            raise InputError("Chưa đăng ký kênh nào", source=self.source)
        try:
            token = await call_blocking(
                self._token,
                what="token",
                source=self.source,
                stopping=lambda: self._stopping,
                logger=logger,
            )
        except Stopped:
            yield self._frame("closed", 0, detail="token: stopped before connecting")
            return
        queue: asyncio.Queue[Frame | BaseException | None] = asyncio.Queue()
        tasks = [
            asyncio.create_task(self._run_endpoint(self._endpoints[name], groups, token, queue))
            for name, groups in self._subscriptions.items()
        ]
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

    def _frame(
        self, kind: Kind, conn_id: int, channel: str = "", payload: bytes = b"", detail: str = ""
    ) -> Frame:
        self._seq += 1
        return Frame(
            source=self.source,
            channel=channel,
            conn_id=conn_id,
            seq=self._seq,
            recv_at_ns=self._clock_ns(),
            payload=payload,
            kind=kind,
            detail=detail,
        )

    async def _run_endpoint(
        self,
        endpoint: _Endpoint,
        groups: dict[tuple[str, str], list[str]],
        token: str,
        queue: asyncio.Queue[Any],
    ) -> None:
        self._check_endpoint(endpoint.url)
        failures, lost_at = 0, None
        try:
            while not self._stopping:
                self._conn_ids += 1
                conn_id = self._conn_ids
                try:
                    async with self._connect(endpoint.url) as socket:
                        await self._handshake(socket, token)
                        for line in self._subscribe_lines(endpoint, groups):
                            await socket.send(line)
                        failures = 0
                        if lost_at is None:
                            await queue.put(self._frame("connected", conn_id, detail=endpoint.name))
                        else:
                            gap = {"from_ns": lost_at, "to_ns": self._clock_ns(), "endpoint": endpoint.name}
                            await queue.put(self._frame("gap", conn_id, detail=json.dumps(gap)))
                            await queue.put(self._frame("reconnected", conn_id, detail=endpoint.name))
                        lost_at = None
                        await self._pump(socket, endpoint, conn_id, queue)
                except (OSError, EOFError, asyncio.TimeoutError, _closed_error()) as exc:
                    lost_at = lost_at or self._clock_ns()
                    await queue.put(
                        self._frame("closed", conn_id, detail=f"{endpoint.name}: {type(exc).__name__}")
                    )
                    if self._stopping:
                        break
                    failures += 1
                    if failures > self._max_reconnects:
                        raise NetworkError(
                            f"Mất kết nối stream tcbs/{endpoint.name}", source=self.source
                        ) from None
                    delay = min(2.0 ** (failures - 1), _MAX_BACKOFF_S)
                    logger.warning(
                        "Stream tcbs/%s mất kết nối (%s); nối lại sau %.0f s", endpoint.name, exc, delay
                    )
                    await asyncio.sleep(delay)
            await queue.put(self._frame("closed", self._conn_ids, detail=f"{endpoint.name}: stopped"))
            await queue.put(None)
        except BaseException as exc:  # noqa: BLE001 - hand every failure to frames()
            if not isinstance(exc, asyncio.CancelledError):
                await queue.put(exc)
            raise

    async def _handshake(self, socket: Any, token: str) -> None:
        await socket.send("d|a|||" + base64.b64encode(token.encode()).decode("ascii"))
        deadline = time.monotonic() + _REPLY_TIMEOUT_S
        while True:
            raw = await asyncio.wait_for(socket.recv(), max(0.1, deadline - time.monotonic()))
            text = raw.decode() if isinstance(raw, bytes) else raw
            if text.startswith("d|0|"):
                reply = json.loads(text[4:])
                if not reply.get("success"):
                    error = reply.get("error") or {}
                    raise AuthError(
                        f"tcbs từ chối xác thực stream: {str(error.get('message'))[:200]}", source=self.source
                    )
                return

    def _subscribe_lines(self, endpoint: _Endpoint, groups: dict[tuple[str, str], list[str]]) -> list[str]:
        # Channels with the same object and symbols go in one line, props joined by "+".
        merged: dict[tuple[str, tuple[str, ...]], list[str]] = {}
        for (obj, prop), symbols in groups.items():
            merged.setdefault((obj, tuple(symbols)), []).append(prop)
        return [
            endpoint.subscribe.format(object=obj, props="+".join(props), symbols=",".join(symbols))
            for (obj, symbols), props in merged.items()
        ]

    async def _pump(self, socket: Any, endpoint: _Endpoint, conn_id: int, queue: asyncio.Queue[Any]) -> None:
        loop = asyncio.get_running_loop()
        next_beat = loop.time()
        while not self._stopping:
            if loop.time() >= next_beat:
                await socket.send(endpoint.heartbeat)
                next_beat = loop.time() + self.spec.heartbeat_s
            try:
                raw = await asyncio.wait_for(socket.recv(), max(0.05, next_beat - loop.time()))
            except asyncio.TimeoutError:
                continue
            recv_at = self._clock_ns()
            text = raw.decode() if isinstance(raw, bytes) else raw
            head = text.split("|", 1)[0]
            if text.startswith("s|"):
                channel_id = text.split("|", 2)[1]
            elif head in self._line_heads.get(endpoint.name, ()) and text.count("|") >= 2:
                channel_id = head  # history endpoint: C001|TCB|{...}
            else:
                if '"success":false' in text:
                    logger.warning("Stream tcbs/%s báo lỗi: %s", endpoint.name, text[:200])
                continue
            self._seq += 1
            await queue.put(
                Frame(
                    source=self.source,
                    channel=self._families.get(channel_id, "unknown"),
                    conn_id=conn_id,
                    seq=self._seq,
                    recv_at_ns=recv_at,
                    payload=text.encode("utf-8"),
                    encoding="text",
                )
            )


def _closed_error() -> type[BaseException]:
    try:
        from websockets.exceptions import WebSocketException

        return WebSocketException
    except ImportError:  # pragma: no cover
        return OSError
