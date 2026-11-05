"""SSI FastConnect Data stream: ASP.NET SignalR 1.5, hub ``FcMarketDataV2Hub``.

Stream FastConnect Data của SSI: ASP.NET SignalR 1.5, hub ``FcMarketDataV2Hub``.

Steps, as in the official SDK ``ssi-fc-data``:

1. A Bearer token from the AccessToken endpoint, with the account's ``consumerID`` and
   ``consumerSecret`` (the same SSI key as REST: ``SSI_API_KEY``, ``SSI_API_SECRET``).
2. ``negotiate`` on the hub with that token; it answers a connection token and the keep-alive timing.
3. The WebSocket upgrade with the same token, then one ``SwitchChannels("<code>:<SYM1-SYM2>")`` per
   channel. Each call adds to what the connection receives and the hub sends the latest record of
   each symbol right away.
4. Data comes through the hub method ``Broadcast``; each argument becomes one frame v1 whose payload
   is the argument verbatim (``{"DataType": "X-TRADE", "Content": "{...}"}``). ``{}`` is keep-alive.

The token and the connection token never enter a frame, a log or an error.

    client = SsiStreamClient().subscribe("trade", ["FPT", "HPG"]).subscribe("index", ["VN30"])
    async for frame in client.frames():
        ...
"""

from __future__ import annotations

import asyncio
import functools
import json
import logging
import sys
import time
from collections.abc import AsyncIterator, Callable, Sequence
from contextlib import AbstractAsyncContextManager
from dataclasses import dataclass
from importlib import resources
from typing import Any
from urllib.parse import urlencode, urlsplit

from vnstock_oapi.core.transport import USER_AGENT
from vnstock_oapi.errors import AuthError, InputError, NetworkError, PolicyError, SourceError
from vnstock_oapi.stream._blocking import Stopped, call_blocking
from vnstock_oapi.stream.frame import Frame, Kind
from vnstock_oapi.stream.spec import StreamSpec, load_stream_spec

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = ["SsiStreamClient"]

logger = logging.getLogger("vnstock_oapi.stream")

_POLL_S = 1.0
_MAX_BACKOFF_S = 60.0
_REPLY_TIMEOUT_S = 15.0
_SIGNALR_PATH = "/v2.0/signalr"
# Canonical index codes SSI knows by another id.
_SYMBOL_ALIASES = {"UPCOMINDEX": "HNXUPCOMINDEX"}

Connect = Callable[[str, dict[str, str]], AbstractAsyncContextManager[Any]]


@dataclass(frozen=True)
class _Hub:
    token_url: str
    negotiate_url: str
    hub: str
    method: str
    error_method: str
    client_protocol: str


def _load_extra(source: str) -> tuple[_Hub, dict[str, str]]:
    path = resources.files("vnstock_oapi") / "spec" / "sources" / source / "streams.toml"
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    hub = _Hub(**data["signalr"])
    codes = {row["name"]: row["code"] for row in data.get("channels", [])}
    return hub, codes


def _default_connect(url: str, headers: dict[str, str]) -> AbstractAsyncContextManager[Any]:
    try:
        from websockets.asyncio.client import connect
    except ImportError:  # pragma: no cover
        raise InputError("Cần gói websockets: pip install 'vnstock-oapi[stream]'") from None
    # TLS uses the system defaults. The hub keeps the connection alive with "{}" messages.
    return connect(
        url,
        additional_headers=headers,
        open_timeout=15,
        ping_interval=None,
        max_size=2**22,
        user_agent_header=USER_AGENT,
    )


def _closed_errors() -> tuple[type[BaseException], ...]:
    errors: list[type[BaseException]] = [OSError, EOFError, asyncio.TimeoutError, NetworkError]
    try:
        from websockets.exceptions import ConnectionClosed, InvalidMessage

        errors += [ConnectionClosed, InvalidMessage]
    except ImportError:  # pragma: no cover
        pass
    return tuple(errors)


def _refused_status(exc: BaseException) -> int | None:
    """HTTP status of a refused WebSocket upgrade (``websockets.InvalidStatus``), else ``None``."""
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    return status if isinstance(status, int) else None


class SsiStreamClient:
    """One SignalR connection to the FastConnect Data hub, market-data channels only.

    Một kết nối SignalR tới hub FastConnect Data, chỉ kênh dữ liệu thị trường.
    """

    def __init__(
        self,
        source: str = "ssi",
        *,
        profile: str = "default",
        connect: Connect | None = None,
        token: Callable[[], str] | None = None,
        negotiate: Callable[[str], dict[str, Any]] | None = None,
        max_reconnects: int = 10,
        silence_s: float | None = None,
        clock_ns: Callable[[], int] = time.time_ns,
        sleep: Callable[[float], Any] = asyncio.sleep,
    ) -> None:
        self.spec: StreamSpec = load_stream_spec(source)
        self._hub, self._codes = _load_extra(source)
        self._check_http(self._hub.token_url)
        self._check_http(self._hub.negotiate_url)
        self.spec.check_url(self.spec.url)
        self.profile = profile
        self._connect = connect or _default_connect
        self._token = token or self._fc_token
        self._negotiate = negotiate or self._http_negotiate
        self._max_reconnects = max_reconnects
        self._silence_s = silence_s
        self._clock_ns = clock_ns
        self._sleep = sleep
        self._families = {t.upper(): rule.name for rule in self.spec.channels for t in rule.message_types}
        self._subscriptions: dict[str, list[str]] = {}
        self._transport: Any = None
        self._auth: Any = None
        self._seq = 0
        self._stopping = False

    @property
    def source(self) -> str:
        return self.spec.source

    @property
    def subscriptions(self) -> dict[str, list[str]]:
        return {name: list(symbols) for name, symbols in self._subscriptions.items()}

    def subscribe(self, channel: str, symbols: Sequence[str] = ()) -> SsiStreamClient:
        """Add a declared channel for some symbols, or ``["ALL"]``. Others raise right here.

        Thêm một kênh đã khai cho một số mã, hoặc ``["ALL"]``. Kênh khác bị từ chối ngay tại đây.
        """
        rule = self.spec.resolve(channel)
        wanted = [s.strip().upper() for s in symbols if s.strip()]
        wanted = [_SYMBOL_ALIASES.get(s, s) for s in wanted]
        if not wanted:
            raise InputError(f"Kênh {channel!r} cần danh sách mã (hoặc ALL)", source=self.source)
        bad = [s for s in wanted if not s.replace("_", "").isalnum()]
        if bad:
            raise InputError(f"Mã không hợp lệ cho {channel!r}: {bad[:5]}", source=self.source)
        merged = list(dict.fromkeys([*self._subscriptions.get(rule.name, ()), *wanted]))
        self._subscriptions[rule.name] = ["ALL"] if "ALL" in merged else merged
        return self

    def switch_channels(self) -> list[str]:
        """The ``SwitchChannels`` arguments this client sends, e.g. ``["X-TRADE:FPT-HPG"]``.

        Các đối số ``SwitchChannels`` client sẽ gửi.
        """
        return [f"{self._codes[name]}:{'-'.join(symbols)}" for name, symbols in self._subscriptions.items()]

    def stop(self) -> None:
        self._stopping = True

    async def _blocking(self, call: Callable[[], Any], what: str) -> Any:
        return await call_blocking(
            call, what=what, source=self.source, stopping=lambda: self._stopping, logger=logger
        )

    async def frames(self) -> AsyncIterator[Frame]:
        """Data and control frames until ``stop()`` or reconnects run out.

        Frame dữ liệu và frame điều khiển cho tới khi ``stop()`` hoặc hết lượt nối lại.
        """
        if not self._subscriptions:
            raise InputError("Chưa đăng ký kênh nào", source=self.source)
        closed = _closed_errors()
        conn_id, failures, lost_at = 0, 0, None
        while not self._stopping:
            conn_id += 1
            try:
                try:
                    token = await self._blocking(self._token, "token")
                    negotiated = await self._blocking(functools.partial(self._negotiate, token), "negotiate")
                except Stopped as stopped:
                    yield self._control("closed", conn_id, f"{stopped}: stopped before connecting")
                    return
                url = self._socket_url(negotiated)
                silence = self._silence_s or 2 * float(negotiated.get("DisconnectTimeout") or 30)
                headers = {"Authorization": f"Bearer {token}"}
                try:
                    async with self._connect(url, headers) as socket:
                        await self._handshake(socket)
                        if lost_at is None:
                            yield self._control("connected", conn_id)
                        else:
                            gap = {"from_ns": lost_at, "to_ns": self._clock_ns()}
                            yield self._control("gap", conn_id, json.dumps(gap))
                            yield self._control("reconnected", conn_id)
                        lost_at = None
                        async for frame in self._pump(socket, conn_id, silence):
                            failures = 0  # only a connection that delivered data resets the count
                            yield frame
                except Exception as exc:
                    status = _refused_status(exc)
                    if status in (401, 403):
                        raise AuthError(
                            f"ssi từ chối nối stream (HTTP {status})", source=self.source, http_status=status
                        ) from None
                    if status is not None and status >= 500:
                        raise NetworkError(
                            f"ssi lỗi máy chủ khi nối stream (HTTP {status})",
                            source=self.source,
                            http_status=status,
                        ) from None
                    if status is not None:
                        raise SourceError(
                            f"ssi từ chối nối stream (HTTP {status})", source=self.source, http_status=status
                        ) from None
                    raise
            except closed as exc:
                lost_at = lost_at or self._clock_ns()
                yield self._control("closed", conn_id, type(exc).__name__)
                if self._stopping:
                    return
                failures += 1
                if failures > self._max_reconnects:
                    raise NetworkError(
                        f"Mất kết nối stream ssi sau {self._max_reconnects} lần nối lại", source=self.source
                    ) from None
                delay = min(2.0 ** (failures - 1), _MAX_BACKOFF_S)
                logger.warning("Stream ssi mất kết nối (%s); nối lại sau %.0f s", type(exc).__name__, delay)
                await self._sleep(delay)
        yield self._control("closed", conn_id, "stopped")

    # -- HTTP: token and negotiate ------------------------------------------------------

    def _check_http(self, url: str) -> None:
        parts = urlsplit(url)
        token = urlsplit(self._hub.token_url)
        allowed = {
            (token.hostname or "", token.path),
            (self.spec.host, f"{_SIGNALR_PATH}/negotiate"),
        }
        if parts.scheme != "https" or (parts.hostname or "", parts.path) not in allowed:
            raise PolicyError(f"ssi chỉ gọi cổng token và negotiate đã khai, không gọi {url}", url=url)

    def _http(self) -> Any:
        if self._transport is None:
            from vnstock_oapi.core.transport import Transport

            hosts = frozenset({urlsplit(self._hub.token_url).hostname or "", self.spec.host})
            self._transport = Transport(self.source, hosts, rate_per_second=2)
        return self._transport

    def _fc_token(self) -> str:
        if self._auth is None:
            from vnstock_oapi.core import credentials as creds_mod
            from vnstock_oapi.sources import SPECS
            from vnstock_oapi.stream.auth import SsiFcToken

            credentials = creds_mod.load(self.source, self.profile, SPECS[self.source].credentials)

            def post(body: dict[str, Any]) -> tuple[int, Any]:
                response = self._http().request("POST", self._hub.token_url, json=body, allow_post=True)
                try:
                    return response.status, response.json()
                except ValueError:
                    return response.status, None

            self._auth = SsiFcToken(credentials, post)
        return self._auth.token()  # type: ignore[no-any-return]

    def _http_negotiate(self, token: str) -> dict[str, Any]:
        response = self._http().request(
            "GET",
            self._hub.negotiate_url,
            params={"clientProtocol": self._hub.client_protocol, "connectionData": self._connection_data},
            headers={"Authorization": f"Bearer {token}"},
        )
        if response.status in (401, 403):
            if self._auth is not None:
                self._auth.forget()
            raise AuthError(
                f"ssi từ chối negotiate (HTTP {response.status})",
                source=self.source,
                http_status=response.status,
            )
        if response.status != 200:
            raise NetworkError(
                f"ssi negotiate trả HTTP {response.status}", source=self.source, http_status=response.status
            )
        try:
            body = response.json()
        except ValueError:
            raise SourceError("ssi negotiate trả nội dung không phải JSON", source=self.source) from None
        if not isinstance(body, dict) or not body.get("ConnectionToken"):
            raise SourceError("ssi negotiate thiếu ConnectionToken", source=self.source)
        return body

    @property
    def _connection_data(self) -> str:
        return json.dumps([{"name": self._hub.hub}])

    def _socket_url(self, negotiated: dict[str, Any]) -> str:
        query = urlencode(
            {
                "transport": "webSockets",
                "connectionToken": negotiated["ConnectionToken"],
                "connectionData": self._connection_data,
                "clientProtocol": negotiated.get("ProtocolVersion") or self._hub.client_protocol,
            }
        )
        url = f"{self.spec.url}?{query}"
        self.spec.check_url(url)
        return url

    # -- SignalR --------------------------------------------------------------------

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

    async def _handshake(self, socket: Any) -> None:
        deadline = time.monotonic() + _REPLY_TIMEOUT_S
        while True:
            raw = await asyncio.wait_for(socket.recv(), max(0.1, deadline - time.monotonic()))
            message = _json(raw)
            if isinstance(message, dict) and message.get("S") == 1:
                break
        for index, (name, symbols) in enumerate(self._subscriptions.items()):
            self.spec.resolve(name)  # checked once more right before it goes out
            argument = f"{self._codes[name]}:{'-'.join(symbols)}"
            await socket.send(
                json.dumps({"H": self._hub.hub, "M": "SwitchChannels", "A": [argument], "I": index})
            )

    async def _pump(self, socket: Any, conn_id: int, silence_s: float) -> AsyncIterator[Frame]:
        loop = asyncio.get_running_loop()
        heard = loop.time()
        hub = self._hub.hub.lower()
        while not self._stopping:
            if loop.time() - heard > silence_s:
                raise asyncio.TimeoutError("hub silent")
            try:
                raw = await asyncio.wait_for(socket.recv(), _POLL_S)
            except asyncio.TimeoutError:
                continue
            heard = loop.time()
            recv_at = self._clock_ns()
            message = _json(raw)
            if not isinstance(message, dict) or not message:
                continue  # "{}" keep-alive
            if "E" in message:
                raise SourceError(f"ssi từ chối lệnh hub: {str(message['E'])[:200]}", source=self.source)
            for item in message.get("M") or ():
                if not isinstance(item, dict) or str(item.get("H", "")).lower() != hub:
                    continue
                method = item.get("M")
                if method == self._hub.error_method:
                    logger.warning("Stream ssi báo lỗi: %s", str(item.get("A"))[:200])
                    continue
                if method != self._hub.method:
                    continue
                for argument in item.get("A") or ():
                    text = (
                        argument
                        if isinstance(argument, str)
                        else json.dumps(argument, ensure_ascii=False, separators=(",", ":"))
                    )
                    self._seq += 1
                    yield Frame(
                        source=self.source,
                        channel=self._family(text),
                        conn_id=conn_id,
                        seq=self._seq,
                        recv_at_ns=recv_at,
                        payload=text.encode("utf-8"),
                        encoding="text",
                    )

    def _family(self, text: str) -> str:
        body = _json(text)
        kind = body.get(self.spec.type_field) if isinstance(body, dict) else None
        return self._families.get(str(kind).upper(), "unknown")


def _json(raw: str | bytes) -> Any:
    try:
        return json.loads(raw)
    except ValueError:
        return None
