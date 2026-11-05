"""The Python reference stream client: connect, authenticate, subscribe, keep alive, reconnect.

Client stream Python tham chiếu: kết nối, xác thực, đăng ký kênh, giữ nhịp, nối lại.

This client exists so anyone can read exactly what happens with their keys and every frame. It is
the slow path: socket reads, JSON parsing and your code share one GIL and one event loop, so a slow
consumer delays heartbeats and the source may drop the connection.

Client này để ai cũng đọc được chính xác điều gì xảy ra với khoá và từng frame. Đọc socket, đọc
JSON và mã của bạn cùng chạy trên một GIL, một vòng sự kiện: xử lý chậm làm trễ nhịp tim và nguồn
có thể đóng kết nối.

    client = StreamClient("<nguồn>").subscribe("<kênh>", ["FPT", "HPG"])
    async for frame in client.frames():
        ...
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from typing import Any, Protocol

from vnstock_oapi.core import credentials as creds_mod
from vnstock_oapi.core.transport import USER_AGENT
from vnstock_oapi.errors import AuthError, InputError, NetworkError, SourceError
from vnstock_oapi.stream.auth import StreamAuth, stream_auth
from vnstock_oapi.stream.frame import Frame, Kind
from vnstock_oapi.stream.spec import StreamSpec, load_stream_spec

__all__ = ["Socket", "StreamClient", "collect"]

logger = logging.getLogger("vnstock_oapi.stream")

# How often the receive loop wakes up to check stop() and send heartbeats.
_POLL_S = 1.0
_MAX_BACKOFF_S = 60.0


class Socket(Protocol):
    async def send(self, message: str) -> None: ...

    async def recv(self) -> str | bytes: ...


Connect = Callable[[str], AbstractAsyncContextManager[Socket]]


def _default_connect(url: str) -> AbstractAsyncContextManager[Socket]:
    try:
        from websockets.asyncio.client import connect
    except ImportError:  # pragma: no cover
        raise InputError("Cần gói websockets: pip install 'vnstock-oapi[stream]'") from None
    # TLS uses the system defaults: certificate and hostname are always checked.
    return connect(
        url, open_timeout=15, ping_interval=20, ping_timeout=20, max_size=2**22, user_agent_header=USER_AGENT
    )


def _closed_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError, EOFError, asyncio.TimeoutError]
    try:
        from websockets.exceptions import WebSocketException

        errors.append(WebSocketException)
    except ImportError:  # pragma: no cover
        pass
    return tuple(errors)


class StreamClient:
    """One connection to one source's official stream, market-data channels only.

    Một kết nối tới cổng stream chính thức của một nguồn, chỉ kênh dữ liệu thị trường.
    """

    def __init__(
        self,
        source: str,
        *,
        profile: str = "default",
        connect: Connect | None = None,
        auth: StreamAuth | None = None,
        max_reconnects: int = 10,
        heartbeat_s: float | None = None,
        reply_timeout_s: float = 15.0,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.spec: StreamSpec = load_stream_spec(source.lower())
        self.profile = profile
        self._connect = connect or _default_connect
        self._auth = auth
        self._max_reconnects = max_reconnects
        self._heartbeat_s = heartbeat_s if heartbeat_s is not None else self.spec.heartbeat_s
        self._reply_timeout_s = reply_timeout_s
        self._clock_ns = clock_ns
        self._sleep = sleep
        self._subscriptions: dict[str, list[str]] = {}
        self._seq = 0
        self._stopping = False

    @property
    def source(self) -> str:
        return self.spec.source

    @property
    def subscriptions(self) -> dict[str, list[str]]:
        return {name: list(symbols) for name, symbols in self._subscriptions.items()}

    def subscribe(self, channel: str, symbols: Sequence[str] = ()) -> StreamClient:
        """Add a channel. Undeclared, order and account channels raise ``PolicyError`` right here.

        Thêm một kênh. Kênh chưa khai, kênh lệnh, kênh tài khoản bị ``PolicyError`` ngay tại đây.
        """
        rule = self.spec.resolve(channel)
        wanted = [symbol.strip().upper() for symbol in symbols if symbol.strip()]
        if rule.symbols and not wanted:
            raise InputError(f"Kênh {channel!r} cần danh sách mã", source=self.source)
        if not rule.symbols and wanted:
            raise InputError(f"Kênh {channel!r} không nhận danh sách mã", source=self.source)
        wire = self.spec.wire_name(channel)
        merged = list(dict.fromkeys([*self._subscriptions.get(wire, ()), *wanted]))
        self._subscriptions[wire] = merged
        return self

    def stop(self) -> None:
        """Ask ``frames()`` to finish within about a second.

        Yêu cầu ``frames()`` dừng trong khoảng một giây.
        """
        self._stopping = True

    async def frames(self) -> AsyncIterator[Frame]:
        """Yield data and control frames until ``stop()`` or reconnects run out.

        Trả frame dữ liệu và frame điều khiển cho tới khi ``stop()`` hoặc hết lượt nối lại.
        """
        if not self._subscriptions:
            raise InputError("Chưa đăng ký kênh nào", source=self.source)
        self.spec.check_url(self.spec.url)
        auth = self._auth or stream_auth(self.spec.auth, self._credentials())
        closed = _closed_errors()
        conn_id = 0
        failures = 0
        lost_at: int | None = None
        while not self._stopping:
            conn_id += 1
            try:
                async with self._connect(self.spec.url) as socket:
                    await self._handshake(socket, auth)
                    failures = 0
                    if lost_at is None:
                        yield self._control("connected", conn_id)
                    else:
                        gap = {"from_ns": lost_at, "to_ns": self._clock_ns()}
                        yield self._control("gap", conn_id, json.dumps(gap))
                        yield self._control("reconnected", conn_id)
                    lost_at = None
                    async for frame in self._pump(socket, conn_id):
                        yield frame
            except closed as exc:
                lost_at = lost_at or self._clock_ns()
                yield self._control("closed", conn_id, type(exc).__name__)
                if self._stopping:
                    return
                failures += 1
                if failures > self._max_reconnects:
                    raise NetworkError(
                        f"Mất kết nối stream {self.source} sau {self._max_reconnects} lần nối lại",
                        source=self.source,
                    ) from None
                delay = min(2.0 ** (failures - 1), _MAX_BACKOFF_S)
                logger.warning("Stream %s mất kết nối (%s); nối lại sau %.0f s", self.source, exc, delay)
                await self._sleep(delay)
        yield self._control("closed", conn_id, "stopped")

    # -- internals --------------------------------------------------------------

    def _credentials(self) -> creds_mod.Credentials:
        from vnstock_oapi.sources import SPECS

        return creds_mod.load(self.source, self.profile, SPECS[self.source].credentials)

    def _control(self, kind: Kind, conn_id: int, detail: str = "") -> Frame:
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

    async def _reply(self, socket: Socket) -> dict[str, Any]:
        raw = await asyncio.wait_for(socket.recv(), self._reply_timeout_s)
        message = json.loads(raw)
        if not isinstance(message, dict):
            raise SourceError(f"Stream {self.source} trả thông điệp bắt tay lạ", source=self.source)
        return message

    async def _handshake(self, socket: Socket, auth: StreamAuth) -> None:
        field = self.spec.control_field
        welcome = await self._reply(socket)
        if welcome.get(field) != "welcome":
            raise SourceError(f"Stream {self.source} không gửi lời chào", source=self.source)
        await socket.send(json.dumps(auth.message()))
        reply = await self._reply(socket)
        if reply.get(field) != "auth_success":
            reason = str(reply.get("message") or reply.get("msg") or reply.get(field))[:200]
            raise AuthError(f"{self.source} từ chối xác thực stream: {reason}", source=self.source)
        for wire, symbols in self._subscriptions.items():
            # resolve() again: the wire list could have been edited after subscribe().
            self.spec.resolve(wire)
            await socket.send(
                json.dumps({"action": "subscribe", "channels": [{"name": wire, "symbols": symbols}]})
            )

    async def _pump(self, socket: Socket, conn_id: int) -> AsyncIterator[Frame]:
        loop = asyncio.get_running_loop()
        next_ping = loop.time() + self._heartbeat_s
        field, type_field = self.spec.control_field, self.spec.type_field
        while not self._stopping:
            if loop.time() >= next_ping:
                await socket.send(json.dumps({"action": "ping"}))
                next_ping = loop.time() + self._heartbeat_s
            wait = max(0.0, min(_POLL_S, next_ping - loop.time()))
            try:
                raw = await asyncio.wait_for(socket.recv(), wait)
            except asyncio.TimeoutError:
                continue  # the ping is due: sent at the top of the loop
            recv_at = self._clock_ns()
            payload = raw.encode("utf-8") if isinstance(raw, str) else bytes(raw)
            try:
                message = json.loads(payload)
            except ValueError:
                message = None
            if isinstance(message, dict) and field in message:
                await self._on_control(socket, message[field], message)
                continue
            channel = "unknown"
            if isinstance(message, dict):
                channel = self.spec.family_of(str(message.get(type_field))) or "unknown"
            self._seq += 1
            yield Frame(
                source=self.source,
                channel=channel,
                conn_id=conn_id,
                seq=self._seq,
                recv_at_ns=recv_at,
                payload=payload,
                encoding="text" if isinstance(raw, str) else "binary",
            )

    async def _on_control(self, socket: Socket, action: Any, message: dict[str, Any]) -> None:
        if action == "ping":
            await socket.send(json.dumps({"action": "pong"}))
        elif action == "error":
            logger.warning("Stream %s báo lỗi: %s", self.source, str(message.get("message") or "")[:200])
        elif action == "subscribed":
            logger.info("Stream %s đã đăng ký %s", self.source, message.get("channel"))


async def collect(
    source: str,
    subscriptions: dict[str, Sequence[str]],
    seconds: float,
    *,
    profile: str = "default",
    client: StreamClient | None = None,
) -> list[Frame]:
    """Receive for ``seconds`` and return every frame. In a notebook: ``await collect(...)``.

    Nhận trong ``seconds`` giây rồi trả mọi frame. Trong notebook: ``await collect(...)``.
    """
    client = client or StreamClient(source, profile=profile)
    for channel, symbols in subscriptions.items():
        client.subscribe(channel, symbols)
    asyncio.get_running_loop().call_later(seconds, client.stop)
    return [frame async for frame in client.frames()]
