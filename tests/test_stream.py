"""Streams: endpoint and channel allow-list, frame v1, DNSE auth, the reference client.

Stream: cổng và danh sách kênh được phép, frame v1, xác thực DNSE, client tham chiếu.

Every test uses a scripted fake socket; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import re
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import pytest
import tomllib

from vnstock_oapi.core.credentials import Credentials
from vnstock_oapi.core.secret import Secret
from vnstock_oapi.errors import AuthError, InputError, NetworkError, PolicyError, UnsupportedError
from vnstock_oapi.stream import (
    Frame,
    StreamClient,
    client_for,
    load_frames,
    load_stream_spec,
    save_frames,
    stream_sources,
)
from vnstock_oapi.stream.auth import HmacHexAuth

SRC = Path(__file__).resolve().parents[1] / "vnstock_oapi"
KEY, SECRET = "test-key-0001", "test-secret-0001"
CREDS = Credentials(values={"api_key": Secret(KEY), "api_secret": Secret(SECRET)}, origins={})

TRADE = {"T": "t", "symbol": "FPT", "boardId": "G1", "matchPrice": 12.3, "matchQtty": 100}
QUOTE = {"T": "q", "symbol": "FPT", "bid": [{"price": 12.2, "qtty": 500}], "offer": []}


# -- the allow-list ------------------------------------------------------------------------


def test_only_declared_sources_have_streams() -> None:
    assert stream_sources() == ["binance", "dnse", "ssi", "tcbs"]
    for name in ("fhsc", "fmp"):
        with pytest.raises(UnsupportedError):
            load_stream_spec(name)


@pytest.mark.parametrize(
    "channel",
    [
        "tick.G1",
        "tick.G1.json",
        "tick.T1.msgpack",
        "top_price.G3",
        "market_index.VNINDEX",
        "ohlc.1",
        "ohlc_closed.1D",
        "market_index_influence.VN30.1",
        "session.STX.G1",
        "foreign.G1",
    ],
)
def test_declared_market_data_channels_pass(channel: str) -> None:
    load_stream_spec("dnse").resolve(channel)


@pytest.mark.parametrize(
    "channel",
    [
        "order.stock.json",
        "orders",
        "positions",
        "position.derivative.json",
        "account",
        "order.broker.stock.123.json",
        "position.broker.derivative.123.json",
        "ORDER.stock",
    ],
)
def test_order_and_account_channels_are_refused(channel: str) -> None:
    with pytest.raises(PolicyError, match="lệnh hoặc tài khoản"):
        load_stream_spec("dnse").resolve(channel)


@pytest.mark.parametrize(
    "channel",
    ["tick.ZZ", "tick", "tick.G1.extra", "ohlc.2", "market_index.vn index", "news.G1", "tick.G1.csv"],
)
def test_undeclared_channels_are_refused(channel: str) -> None:
    with pytest.raises(PolicyError, match="chưa khai"):
        load_stream_spec("dnse").resolve(channel)


@pytest.mark.parametrize(
    "url",
    [
        "ws://ws-openapi.dnse.com.vn/v1/stream",
        "wss://evil.example/v1/stream",
        "wss://ws-openapi.dnse.com.vn.evil.example/v1/stream",
        "wss://ws-openapi.dnse.com.vn/v2/stream",
        "wss://ws-openapi.dnse.com.vn:8443/v1/stream",
        "https://ws-openapi.dnse.com.vn/v1/stream",
    ],
)
def test_unknown_hosts_and_paths_are_refused(url: str) -> None:
    with pytest.raises(PolicyError, match="chỉ được mở stream tới"):
        load_stream_spec("dnse").check_url(url)


def test_declared_url_with_query_passes() -> None:
    load_stream_spec("dnse").check_url("wss://ws-openapi.dnse.com.vn/v1/stream?encoding=json")


def test_subscribe_refuses_before_anything_is_sent() -> None:
    client = StreamClient("dnse", auth=HmacHexAuth(CREDS), connect=_never_connect)
    with pytest.raises(PolicyError):
        client.subscribe("order.stock.json")
    with pytest.raises(InputError, match="cần danh sách mã"):
        client.subscribe("tick.G1")
    with pytest.raises(InputError, match="không nhận"):
        client.subscribe("market_index.VNINDEX", ["FPT"])
    assert client.subscriptions == {}


def _declared_endpoints() -> set[str]:
    out = set()
    for name in stream_sources():
        out.add(load_stream_spec(name).url)
        path = SRC / "spec" / "sources" / name / "streams.toml"
        endpoints = tomllib.loads(path.read_text(encoding="utf-8")).get("endpoints", {})
        out |= {row["url"] for row in endpoints.values()}
    return out


def test_every_wss_literal_is_a_declared_endpoint() -> None:
    declared = _declared_endpoints()
    found = {
        match
        for path in [*SRC.rglob("*.py"), *SRC.rglob("*.toml")]
        for match in re.findall(r"wss?://[A-Za-z0-9][^\s\"'?`]*", path.read_text(encoding="utf-8"))
    }
    assert found <= declared, found - declared


def test_streams_toml_forbids_order_and_account_families() -> None:
    for name in stream_sources():
        spec = load_stream_spec(name)
        assert {"order", "orders", "position", "positions", "account"} <= set(spec.forbidden)
        assert all(rule.pattern.split(".")[0] not in spec.forbidden for rule in spec.channels)


def test_only_the_client_imports_websockets() -> None:
    users = {
        str(path.relative_to(SRC))
        for path in SRC.rglob("*.py")
        if re.search(r"^\s*(import|from) websockets", path.read_text(encoding="utf-8"), re.M)
    }
    assert users == {"stream/client.py", "stream/tcbs.py", "stream/ssi.py", "stream/binance.py"}


def test_tcbs_refuses_order_channels_and_account_sockets() -> None:
    from vnstock_oapi.stream.tcbs import TcbsStreamClient

    client = TcbsStreamClient(connect=_never_connect, token=lambda: "t")
    for channel in ("order", "stock_order", "de_open_position"):
        with pytest.raises(PolicyError):
            client.subscribe(channel, ["FPT"])
    for path in ("/ws/aither", "/ws/nesoi"):
        with pytest.raises(PolicyError):
            client._check_endpoint(f"wss://openapi.tcbs.com.vn{path}")
    with pytest.raises(PolicyError):
        client._check_endpoint("wss://example.com/ws/thesis/v1/stream/normal")


def test_tcbs_subscribe_lines_group_props_per_endpoint() -> None:
    from vnstock_oapi.stream.tcbs import TcbsStreamClient

    client = TcbsStreamClient(connect=_never_connect, token=lambda: "t")
    client.subscribe("match", ["fpt", "HPG"]).subscribe("bid", ["FPT", "HPG"]).subscribe("put_through.1")
    client.subscribe("history_1m", ["FPT"])
    normal = client._subscribe_lines(client._endpoints["normal"], client._subscriptions["normal"])
    assert normal == ["d|s|tk|tm+bi|FPT,HPG", "d|s|pt|ptm+ptb+pts|1"]
    history = client._subscribe_lines(client._endpoints["history"], client._subscriptions["history"])
    assert history == ["d|st|C001|FPT"]


def test_tcbs_handshake_frames_and_never_the_token() -> None:
    import asyncio
    import base64

    from vnstock_oapi.stream.tcbs import TcbsStreamClient

    class Socket:
        def __init__(self) -> None:
            self.sent: list[str] = []
            self.inbox = [
                'd|0|{"success":true,"error":null}',
                "d|34|1",
                's|6|{"symbol":"FPT","matchPrice":"52600"}',
            ]

        async def __aenter__(self):  # noqa: ANN204
            return self

        async def __aexit__(self, *exc: object) -> None:
            return None

        async def send(self, text: str) -> None:
            self.sent.append(text)

        async def recv(self) -> str:
            if self.inbox:
                return self.inbox.pop(0)
            await asyncio.sleep(3600)
            return ""

    socket = Socket()
    client = TcbsStreamClient(connect=lambda url: socket, token=lambda: "secret-jwt")
    client.subscribe("match", ["FPT"])

    async def first_data() -> list:
        out = []
        async for frame in client.frames():
            out.append(frame)
            if frame.kind == "data":
                client.stop()
                break
        return out

    frames = asyncio.run(first_data())
    assert socket.sent[0] == "d|a|||" + base64.b64encode(b"secret-jwt").decode()
    assert socket.sent[1] == "d|s|tk|tm|FPT" and socket.sent[2] == "d|p|||"
    assert [f.kind for f in frames] == ["connected", "data"]
    assert frames[1].channel == "match" and frames[1].payload.startswith(b"s|6|")
    assert all("secret-jwt" not in json.dumps(f.to_dict()) for f in frames)


# -- auth and frames ---------------------------------------------------------------------------


def test_client_for_follows_the_declared_protocol() -> None:
    from vnstock_oapi.stream.tcbs import TcbsStreamClient

    assert load_stream_spec("tcbs").protocol == "tcbs_pipe"
    assert isinstance(client_for("tcbs"), TcbsStreamClient)
    assert load_stream_spec("dnse").protocol == "json"
    assert type(client_for("dnse")) is StreamClient


def test_dnse_auth_message_matches_the_sdk_formula() -> None:
    message = HmacHexAuth(CREDS, clock=lambda: 1_700_000_000.25).message()
    nonce = message["nonce"]
    expected = hmac.new(SECRET.encode(), f"{KEY}:1700000000:{nonce}".encode(), hashlib.sha256).hexdigest()
    assert message == {
        "action": "auth",
        "api_key": KEY,
        "signature": expected,
        "timestamp": 1700000000,
        "nonce": "1700000000250000",
    }


def test_dnse_nonce_never_repeats_when_the_clock_stalls() -> None:
    auth = HmacHexAuth(CREDS, clock=lambda: 1_700_000_000.0)
    assert len({auth.message()["nonce"] for _ in range(5)}) == 5


def test_frames_round_trip_and_never_carry_secrets(tmp_path: Path) -> None:
    frames = asyncio.run(_run([_welcome(), _ok(), TRADE, QUOTE], stop_after=4))
    path = tmp_path / "frames.jsonl"
    save_frames(frames, path)
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert list(load_frames(path)) == frames
    text = path.read_text()
    assert KEY not in text and SECRET not in text and "signature" not in text and "auth" not in text
    schema = json.loads((SRC / "spec" / "frame.v1.json").read_text())
    for row in map(json.loads, text.splitlines()):
        assert set(schema["required"]) <= set(row) <= set(schema["properties"])


# -- the reference client ----------------------------------------------------------------------


class FakeSocket:
    def __init__(self, script: list[Any], log: list[dict[str, Any]]) -> None:
        self.script = list(script)
        self.log = log

    async def send(self, message: str) -> None:
        self.log.append(json.loads(message))

    async def recv(self) -> str:
        if not self.script:
            await asyncio.sleep(3600)
        item = self.script.pop(0)
        if isinstance(item, BaseException):
            raise item
        return item if isinstance(item, str) else json.dumps(item)


def _welcome() -> dict[str, Any]:
    return {"action": "welcome", "session_id": "s1", "timestamp": 1}


def _ok() -> dict[str, Any]:
    return {"action": "auth_success"}


def _fake(scripts: list[list[Any]], log: list[dict[str, Any]], urls: list[str] | None = None):  # type: ignore[no-untyped-def]
    @asynccontextmanager
    async def connect(url: str) -> AsyncIterator[FakeSocket]:
        if urls is not None:
            urls.append(url)
        if not scripts:
            raise ConnectionRefusedError("no more scripted connections")
        yield FakeSocket(scripts.pop(0), log)

    return connect


@asynccontextmanager
async def _never_connect(url: str) -> AsyncIterator[FakeSocket]:
    raise AssertionError("must not connect")
    yield  # pragma: no cover


async def _run(script: list[Any], *, stop_after: int, log: list[dict[str, Any]] | None = None) -> list[Frame]:
    log = [] if log is None else log
    client = StreamClient("dnse", auth=HmacHexAuth(CREDS), connect=_fake([script], log), clock_ns=lambda: 42)
    client.subscribe("tick.G1", ["fpt", "HPG"]).subscribe("tick.G1.json", ["FPT", "VCB"])
    client.subscribe("market_index.VNINDEX")
    out: list[Frame] = []
    async for frame in client.frames():
        out.append(frame)
        if len(out) == stop_after - 1:
            client.stop()
    return out


def test_client_handshake_subscribe_and_frames() -> None:
    log: list[dict[str, Any]] = []
    frames = asyncio.run(
        _run([_welcome(), _ok(), {"action": "subscribed"}, TRADE, QUOTE], stop_after=4, log=log)
    )
    assert log[0]["action"] == "auth"
    assert log[1:] == [
        {"action": "subscribe", "channels": [{"name": "tick.G1.json", "symbols": ["FPT", "HPG", "VCB"]}]},
        {"action": "subscribe", "channels": [{"name": "market_index.VNINDEX.json", "symbols": []}]},
    ]
    assert [(f.kind, f.channel, f.seq, f.conn_id) for f in frames] == [
        ("connected", "", 1, 1),
        ("data", "tick", 2, 1),
        ("data", "top_price", 3, 1),
        ("closed", "", 4, 1),
    ]
    assert frames[1].json() == TRADE and frames[1].recv_at_ns == 42


def test_client_answers_server_ping_and_drops_control_messages() -> None:
    log: list[dict[str, Any]] = []
    frames = asyncio.run(
        _run(
            [_welcome(), _ok(), {"action": "ping", "timestamp": 1}, {"action": "pong"}, TRADE],
            stop_after=3,
            log=log,
        )
    )
    assert {"action": "pong"} in log
    assert [f.kind for f in frames] == ["connected", "data", "closed"]


def test_client_sends_heartbeat_when_idle() -> None:
    async def scenario() -> list[dict[str, Any]]:
        log: list[dict[str, Any]] = []
        client = StreamClient(
            "dnse", auth=HmacHexAuth(CREDS), connect=_fake([[_welcome(), _ok()]], log), heartbeat_s=0.05
        )
        client.subscribe("ohlc.1", ["FPT"])
        asyncio.get_running_loop().call_later(0.3, client.stop)
        [frame async for frame in client.frames()]
        return log

    assert {"action": "ping"} in asyncio.run(scenario())


def test_client_sends_heartbeat_even_when_busy_and_deduplicates_symbols() -> None:
    class BusySocket(FakeSocket):
        async def recv(self) -> str:
            if self.script:
                item = self.script.pop(0)
                return item if isinstance(item, str) else json.dumps(item)
            await asyncio.sleep(0.01)
            return json.dumps(TRADE)

    async def scenario() -> list[dict[str, Any]]:
        log: list[dict[str, Any]] = []

        @asynccontextmanager
        async def connect(url: str) -> AsyncIterator[BusySocket]:
            yield BusySocket([_welcome(), _ok()], log)

        client = StreamClient("dnse", auth=HmacHexAuth(CREDS), connect=connect, heartbeat_s=0.03)
        client.subscribe("tick.G1", ["FPT", "fpt", "HPG"])
        assert client.subscriptions == {"tick.G1.json": ["FPT", "HPG"]}
        count = 0
        async for _frame in client.frames():
            count += 1
            if count >= 8:
                client.stop()
        return log

    assert {"action": "ping"} in asyncio.run(scenario())


def test_client_reconnects_resubscribes_and_reports_the_gap() -> None:
    async def scenario() -> tuple[list[Frame], list[dict[str, Any]], list[float]]:
        log: list[dict[str, Any]] = []
        sleeps: list[float] = []

        async def sleep(seconds: float) -> None:
            sleeps.append(seconds)

        ticks = iter(range(100, 10_000, 100))
        scripts = [[_welcome(), _ok(), TRADE, ConnectionResetError("reset")], [_welcome(), _ok(), QUOTE]]
        client = StreamClient(
            "dnse",
            auth=HmacHexAuth(CREDS),
            connect=_fake(scripts, log),
            clock_ns=lambda: next(ticks),
            sleep=sleep,
        )
        client.subscribe("tick.G1", ["FPT"])
        out: list[Frame] = []
        async for frame in client.frames():
            out.append(frame)
            if frame.channel == "top_price":
                client.stop()
        return out, log, sleeps

    frames, log, sleeps = asyncio.run(scenario())
    assert [(f.kind, f.conn_id) for f in frames] == [
        ("connected", 1),
        ("data", 1),
        ("closed", 1),
        ("gap", 2),
        ("reconnected", 2),
        ("data", 2),
        ("closed", 2),
    ]
    assert [f.seq for f in frames] == list(range(1, 8))
    gap = json.loads(frames[3].detail)
    assert gap["from_ns"] < gap["to_ns"]
    assert [m["action"] for m in log] == ["auth", "subscribe", "auth", "subscribe"]
    assert sleeps == [1.0]


def test_client_gives_up_after_max_reconnects() -> None:
    async def scenario() -> None:
        async def sleep(seconds: float) -> None:
            return None

        client = StreamClient(
            "dnse", auth=HmacHexAuth(CREDS), connect=_fake([], []), max_reconnects=2, sleep=sleep
        )
        client.subscribe("tick.G1", ["FPT"])
        [frame async for frame in client.frames()]

    with pytest.raises(NetworkError, match="2 lần"):
        asyncio.run(scenario())


def test_auth_refusal_is_not_retried() -> None:
    async def scenario() -> None:
        scripts = [[_welcome(), {"action": "auth_error", "message": "invalid signature"}]]
        client = StreamClient("dnse", auth=HmacHexAuth(CREDS), connect=_fake(scripts, []))
        client.subscribe("tick.G1", ["FPT"])
        [frame async for frame in client.frames()]

    with pytest.raises(AuthError, match="invalid signature"):
        asyncio.run(scenario())


def test_client_reads_keys_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DNSE_API_KEY", KEY)
    monkeypatch.setenv("DNSE_API_SECRET", SECRET)
    log: list[dict[str, Any]] = []

    async def scenario() -> None:
        client = StreamClient("dnse", connect=_fake([[_welcome(), _ok(), TRADE]], log))
        client.subscribe("tick.G1", ["FPT"])
        async for frame in client.frames():
            if frame.kind == "data":
                client.stop()

    asyncio.run(scenario())
    assert log[0]["api_key"] == KEY


def test_tcbs_stop_while_waiting_for_the_token_ends_at_once() -> None:
    """A token read that hangs (keyring dialog, slow refresh) never holds up stop()."""
    import threading
    import time as _time

    from vnstock_oapi.stream.tcbs import TcbsStreamClient

    release = threading.Event()

    def slow_token() -> str:
        release.wait(30)
        return "t"

    client = TcbsStreamClient(connect=_never_connect, token=slow_token)
    client.subscribe("match", ["FPT"])

    async def run() -> list[Any]:
        loop = asyncio.get_running_loop()
        loop.call_later(0.3, client.stop)
        return [f async for f in client.frames()]

    try:
        started = _time.monotonic()
        frames = asyncio.run(run())
        assert _time.monotonic() - started < 3
    finally:
        release.set()
    assert [(f.kind, f.detail) for f in frames] == [("closed", "token: stopped before connecting")]


def test_blocking_call_logs_a_long_wait_and_returns_the_result(caplog: pytest.LogCaptureFixture) -> None:
    import logging
    import time as _time

    from vnstock_oapi.stream._blocking import call_blocking

    def slow() -> str:
        _time.sleep(0.4)
        return "ok"

    async def run() -> str:
        return await call_blocking(
            slow,
            what="token",
            source="tcbs",
            stopping=lambda: False,
            logger=logging.getLogger("vnstock_oapi.stream"),
            warn_after_s=0.1,
            poll_s=0.05,
        )

    with caplog.at_level("WARNING", logger="vnstock_oapi.stream"):
        assert asyncio.run(run()) == "ok"
    assert any("đang chờ token" in r.getMessage() for r in caplog.records)
