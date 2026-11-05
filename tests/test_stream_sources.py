"""Streams of SSI (SignalR), Binance (public combined streams) and the TCBS history endpoint.

Stream SSI (SignalR), Binance (stream gộp công khai) và cổng lịch sử của TCBS.

Every test uses a scripted fake socket and fake HTTP; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import base64
import json
from typing import Any

import pytest

from vnstock_oapi.core.credentials import Credentials
from vnstock_oapi.core.secret import Secret
from vnstock_oapi.errors import AuthError, InputError, NetworkError, PolicyError, RateLimitError
from vnstock_oapi.stream import Frame, client_for, load_stream_spec
from vnstock_oapi.stream.auth import SsiFcToken
from vnstock_oapi.stream.binance import BinanceStreamClient
from vnstock_oapi.stream.ssi import SsiStreamClient
from vnstock_oapi.stream.tcbs import TcbsStreamClient

TOKEN = "header.eyJleHAiOjQxMDI0NDQ4MDB9.sig"  # fake, exp 2100-01-01. release-scan: ignore
CONN_TOKEN = "conn-token-0001"


class FakeSocket:
    """A socket that hands out scripted messages, then raises ``close_with`` (or waits forever)."""

    def __init__(self, inbox: list[str], close_with: BaseException | None = None) -> None:
        self.inbox = list(inbox)
        self.sent: list[str] = []
        self.close_with = close_with

    async def __aenter__(self) -> FakeSocket:
        return self

    async def __aexit__(self, *exc: object) -> None:
        return None

    async def send(self, text: str) -> None:
        self.sent.append(text)

    async def recv(self) -> str:
        if self.inbox:
            return self.inbox.pop(0)
        if self.close_with is not None:
            raise self.close_with
        await asyncio.sleep(3600)
        return ""


def _broadcast(data_type: str, content: dict[str, Any]) -> str:
    argument = json.dumps({"DataType": data_type, "Content": json.dumps(content)})
    return json.dumps(
        {"C": "d-1,0|A,0", "M": [{"H": "FcMarketDataV2Hub", "M": "Broadcast", "A": [argument]}]}
    )


def _negotiate(token: str) -> dict[str, Any]:
    assert token == TOKEN
    return {
        "ConnectionToken": CONN_TOKEN,
        "ProtocolVersion": "1.5",
        "KeepAliveTimeout": 20.0,
        "DisconnectTimeout": 30.0,
    }


async def _take(client: Any, until: int) -> list[Frame]:
    out: list[Frame] = []
    async for frame in client.frames():
        out.append(frame)
        if sum(f.kind == "data" for f in out) >= until:
            client.stop()
            break
    return out


# -- SSI ---------------------------------------------------------------------------------------


def test_ssi_channels_are_declared_and_order_channels_refused() -> None:
    spec = load_stream_spec("ssi")
    assert {rule.name for rule in spec.channels} == {
        "status",
        "snapshot",
        "quote",
        "trade",
        "foreign",
        "index",
        "ohlcv",
        "odd_lot",
    }
    assert spec.protocol == "signalr" and spec.host == "fc-datahub.ssi.com.vn"
    client = SsiStreamClient(connect=lambda u, h: FakeSocket([]), token=lambda: TOKEN, negotiate=_negotiate)
    for channel in ("order", "orders", "position", "account"):
        with pytest.raises(PolicyError):
            client.subscribe(channel, ["FPT"])
    with pytest.raises(PolicyError, match="chưa khai"):
        client.subscribe("news", ["FPT"])
    with pytest.raises(InputError):
        client.subscribe("trade")
    with pytest.raises(InputError):
        client.subscribe("trade", ["FPT:ALL"])
    assert client.subscriptions == {}
    assert isinstance(client_for("ssi"), SsiStreamClient)


def test_ssi_switch_channels_merge_symbols_and_all_wins() -> None:
    client = SsiStreamClient(connect=lambda u, h: FakeSocket([]), token=lambda: TOKEN, negotiate=_negotiate)
    client.subscribe("trade", ["fpt", "HPG"]).subscribe("trade", ["HPG", "ACB"])
    client.subscribe("index", ["VN30", "upcomindex"])  # SSI's UPCoM index id is HNXUpcomIndex
    client.subscribe("status", ["FPT"]).subscribe("status", ["ALL"])
    assert client.switch_channels() == ["X-TRADE:FPT-HPG-ACB", "MI:VN30-HNXUPCOMINDEX", "F:ALL"]


def test_ssi_only_declared_http_and_socket_endpoints() -> None:
    client = SsiStreamClient(connect=lambda u, h: FakeSocket([]), token=lambda: TOKEN, negotiate=_negotiate)
    client._check_http("https://fc-data.ssi.com.vn/api/v2/Market/AccessToken")
    client._check_http("https://fc-datahub.ssi.com.vn/v2.0/signalr/negotiate")
    for url in (
        "https://fc-tradeapi.ssi.com.vn/api/v2/Trading/NewOrder",
        "https://fc-data.ssi.com.vn/api/v2/Market/Securities",
        "http://fc-data.ssi.com.vn/api/v2/Market/AccessToken",
        "https://fc-datahub.ssi.com.vn.evil.example/v2.0/signalr/negotiate",
    ):
        with pytest.raises(PolicyError):
            client._check_http(url)
    with pytest.raises(PolicyError):
        load_stream_spec("ssi").check_url("wss://fc-datahub.ssi.com.vn/v2.0/other/connect")


def test_ssi_handshake_frames_verbatim_and_never_a_token() -> None:
    socket = FakeSocket(
        [
            '{"C":"d-1,0|A,0","S":1,"M":[]}',
            '{"I":"0"}',
            "{}",
            _broadcast("X-TRADE", {"RType": "X-TRADE", "Symbol": "FPT", "LastPrice": 50400.0}),
            _broadcast("MI", {"RType": "MI", "IndexId": "VN30", "IndexValue": 1815.6}),
            json.dumps(
                {
                    "M": [
                        {
                            "H": "FcMarketDataV2Hub",
                            "M": "Broadcast",
                            "A": [{"DataType": "x-quote", "Content": "{}"}],
                        }
                    ]
                }
            ),
        ]
    )
    seen: dict[str, Any] = {}

    def connect(url: str, headers: dict[str, str]) -> FakeSocket:
        seen.update(url=url, headers=headers)
        return socket

    client = SsiStreamClient(connect=connect, token=lambda: TOKEN, negotiate=_negotiate)
    client.subscribe("trade", ["FPT"]).subscribe("index", ["VN30"])
    frames = asyncio.run(_take(client, 3))
    assert seen["headers"] == {"Authorization": f"Bearer {TOKEN}"}
    assert seen["url"].startswith("wss://fc-datahub.ssi.com.vn/v2.0/signalr/connect?transport=webSockets")
    assert [json.loads(m)["A"] for m in socket.sent] == [["X-TRADE:FPT"], ["MI:VN30"]]
    assert [f.kind for f in frames] == ["connected", "data", "data", "data"]
    assert [f.channel for f in frames[1:]] == ["trade", "index", "quote"]
    assert json.loads(frames[1].payload)["DataType"] == "X-TRADE"
    assert json.loads(json.loads(frames[1].payload)["Content"])["LastPrice"] == 50400.0
    text = json.dumps([f.to_dict() for f in frames])
    assert TOKEN not in text and CONN_TOKEN not in text


def test_ssi_reconnects_with_a_gap_and_resubscribes() -> None:
    from websockets.exceptions import ConnectionClosedError

    init = '{"C":"x","S":1,"M":[]}'
    sockets = [
        FakeSocket([init, _broadcast("R", {"Symbol": "FPT"})], close_with=ConnectionClosedError(None, None)),
        FakeSocket([init, _broadcast("R", {"Symbol": "FPT"})]),
    ]
    tokens: list[str] = []
    clock = iter(range(1, 10_000))

    def token() -> str:
        tokens.append(TOKEN)
        return TOKEN

    async def no_sleep(_: float) -> None:
        return None

    client = SsiStreamClient(
        connect=lambda u, h: sockets.pop(0),
        token=token,
        negotiate=_negotiate,
        sleep=no_sleep,
        clock_ns=lambda: next(clock),
    )
    client.subscribe("foreign", ["FPT"])
    first, second = sockets
    frames = asyncio.run(_take(client, 2))
    assert [f.kind for f in frames] == ["connected", "data", "closed", "gap", "reconnected", "data"]
    gap = json.loads(frames[3].detail)
    assert gap["from_ns"] < gap["to_ns"]
    assert (
        first.sent
        == second.sent
        == [json.dumps({"H": "FcMarketDataV2Hub", "M": "SwitchChannels", "A": ["R:FPT"], "I": 0})]
    )
    assert len(tokens) == 2  # asked again on reconnect (renewed only when close to expiry)


def test_ssi_hub_silence_counts_as_a_lost_connection() -> None:
    init = '{"C":"x","S":1,"M":[]}'
    sockets = [FakeSocket([init]), FakeSocket([init, _broadcast("F", {"Symbol": "FPT"})])]

    async def no_sleep(_: float) -> None:
        return None

    client = SsiStreamClient(
        connect=lambda u, h: sockets.pop(0),
        token=lambda: TOKEN,
        negotiate=_negotiate,
        sleep=no_sleep,
        silence_s=0.05,
    )
    client.subscribe("status", ["FPT"])
    frames = asyncio.run(_take(client, 1))
    assert [f.kind for f in frames] == ["connected", "closed", "gap", "reconnected", "data"]


def test_ssi_hub_error_and_refused_upgrade_are_not_retried() -> None:
    client = SsiStreamClient(
        connect=lambda u, h: FakeSocket(['{"S":1,"M":[]}', '{"I":"0","E":"Invalid channel"}']),
        token=lambda: TOKEN,
        negotiate=_negotiate,
    )
    client.subscribe("trade", ["FPT"])
    with pytest.raises(Exception, match="Invalid channel"):
        asyncio.run(_take(client, 1))

    class Refused(Exception):
        response = type("R", (), {"status_code": 401})()

    def refuse(url: str, headers: dict[str, str]) -> Any:
        raise Refused()

    client = SsiStreamClient(connect=refuse, token=lambda: TOKEN, negotiate=_negotiate)
    client.subscribe("trade", ["FPT"])
    with pytest.raises(AuthError):
        asyncio.run(_take(client, 1))


def test_ssi_fc_token_posts_the_key_and_renews_near_expiry() -> None:
    creds = Credentials(values={"api_key": Secret("cid"), "api_secret": Secret("csecret")}, origins={})
    posts: list[dict[str, Any]] = []
    now = [1_000.0]
    exp = 1_000 + 7200
    jwt = "h." + base64.urlsafe_b64encode(json.dumps({"exp": exp}).encode()).decode().rstrip("=") + ".s"

    def post(body: dict[str, Any]) -> tuple[int, Any]:
        posts.append(body)
        return 200, {"status": 200, "message": "Success", "data": {"accessToken": jwt}}

    auth = SsiFcToken(creds, post, clock=lambda: now[0])
    assert auth.token() == jwt and auth.token() == jwt
    assert posts == [{"consumerID": "cid", "consumerSecret": "csecret"}]
    now[0] = exp - 1800  # less than an hour left
    auth.token()
    assert len(posts) == 2
    refused = SsiFcToken(creds, lambda body: (400, {"message": "This connection is invalid", "data": None}))
    with pytest.raises(AuthError, match="invalid"):
        refused.token()


# -- Binance -------------------------------------------------------------------------------------


def test_binance_wire_names_and_market_data_only_endpoint() -> None:
    spec = load_stream_spec("binance")
    assert spec.url == "wss://data-stream.binance.vision/stream" and spec.auth == "public"
    client = BinanceStreamClient(connect=lambda u: FakeSocket([]))
    client.subscribe("trade", ["BTCUSDT", "ethusdt"]).subscribe("agg_trade", ["BTCUSDT"])
    client.subscribe("kline.1m", ["BTCUSDT"]).subscribe("kline.1M", ["BTCUSDT"])
    client.subscribe("ticker_window.4h", ["BTCUSDT"]).subscribe("depth.10", ["BTCUSDT"])
    client.subscribe("depth.20.100ms", ["BTCUSDT"]).subscribe("diff_depth", ["BTCUSDT"])
    client.subscribe("diff_depth.100ms", ["BTCUSDT"]).subscribe("all_mini_ticker").subscribe(
        "all_ticker_window.1d"
    )
    client.subscribe("book_ticker", ["BTCUSDT"]).subscribe("avg_price", ["BTCUSDT"])
    client.subscribe("reference_price", ["BTCUSDT"]).subscribe("block_trade", ["BTCUSDT"]).subscribe(
        "trade", ["BTCUSDT"]
    )
    assert client.streams == [
        "btcusdt@trade",
        "ethusdt@trade",
        "btcusdt@aggTrade",
        "btcusdt@kline_1m",
        "btcusdt@kline_1M",
        "btcusdt@ticker_4h",
        "btcusdt@depth10",
        "btcusdt@depth20@100ms",
        "btcusdt@depth",
        "btcusdt@depth@100ms",
        "!miniTicker@arr",
        "!ticker_1d@arr",
        "btcusdt@bookTicker",
        "btcusdt@avgPrice",
        "btcusdt@referencePrice",
        "btcusdt@blockTrade",
    ]
    assert client.connections() == [
        "wss://data-stream.binance.vision/stream?streams=" + "/".join(client.streams)
    ]
    assert isinstance(client_for("binance"), BinanceStreamClient)


@pytest.mark.parametrize(
    "channel", ["user_data", "listenkey", "order", "account", "kline.2m", "depth.7", "depth.5.250ms"]
)
def test_binance_refuses_user_data_and_undeclared_streams(channel: str) -> None:
    with pytest.raises(PolicyError):
        BinanceStreamClient(connect=lambda u: FakeSocket([])).subscribe(channel, ["BTCUSDT"])


def test_binance_symbol_rules() -> None:
    client = BinanceStreamClient(connect=lambda u: FakeSocket([]))
    with pytest.raises(InputError):
        client.subscribe("trade")
    with pytest.raises(InputError):
        client.subscribe("all_mini_ticker", ["BTCUSDT"])
    with pytest.raises(InputError):
        client.subscribe("trade", ["btc/usdt"])
    with pytest.raises(PolicyError):
        load_stream_spec("binance").check_url("wss://stream.binance.com:9443/stream?streams=btcusdt@trade")


def test_binance_splits_streams_across_connections() -> None:
    client = BinanceStreamClient(connect=lambda u: FakeSocket([]), streams_per_connection=2)
    client.subscribe("trade", ["BTCUSDT", "ETHUSDT", "BNBUSDT"])
    urls = client.connections()
    assert len(urls) == 2 and urls[1].endswith("streams=bnbusdt@trade")


def test_binance_frames_are_verbatim_and_sends_nothing_upstream() -> None:
    trade = '{"stream":"btcusdt@trade","data":{"e":"trade","s":"BTCUSDT","p":"77466.01","q":"0.002"}}'
    depth = '{"stream":"btcusdt@depth5","data":{"lastUpdateId":1,"bids":[],"asks":[]}}'
    socket = FakeSocket([trade, depth, '{"stream":"other@trade","data":{}}'])
    urls: list[str] = []

    def connect(url: str) -> FakeSocket:
        urls.append(url)
        return socket

    client = BinanceStreamClient(connect=connect)
    client.subscribe("trade", ["BTCUSDT"]).subscribe("depth.5", ["BTCUSDT"])
    frames = asyncio.run(_take(client, 3))
    assert urls == ["wss://data-stream.binance.vision/stream?streams=btcusdt@trade/btcusdt@depth5"]
    assert socket.sent == []
    data = [f for f in frames if f.kind == "data"]
    assert [f.channel for f in data] == ["trade", "depth", "unknown"]
    assert data[0].payload.decode() == trade


def test_binance_server_shutdown_and_lifetime_rotate_the_connection() -> None:
    shutdown = '{"stream":"!serverShutdown","data":{"e":"serverShutdown","E":1}}'
    trade = '{"stream":"btcusdt@trade","data":{"e":"trade"}}'
    sockets = [FakeSocket([shutdown]), FakeSocket([trade])]
    client = BinanceStreamClient(connect=lambda u: sockets.pop(0))
    client.subscribe("trade", ["BTCUSDT"])
    frames = asyncio.run(_take(client, 2))
    assert [(f.kind, f.channel) for f in frames] == [
        ("connected", ""),
        ("data", "server_shutdown"),
        ("closed", ""),
        ("gap", ""),
        ("reconnected", ""),
        ("data", "trade"),
    ]
    assert frames[2].detail == "rotate: serverShutdown"

    sockets = [FakeSocket([]), FakeSocket([trade])]
    client = BinanceStreamClient(connect=lambda u: sockets.pop(0), lifetime_s=0.01)
    client.subscribe("trade", ["BTCUSDT"])
    frames = asyncio.run(_take(client, 1))
    assert [f.kind for f in frames] == ["connected", "closed", "gap", "reconnected", "data"]


@pytest.mark.parametrize(
    ("status", "error"), [(429, RateLimitError), (418, RateLimitError), (451, Exception), (403, Exception)]
)
def test_binance_refusal_stops_without_retry(status: int, error: type[BaseException]) -> None:
    calls: list[str] = []

    class Refused(Exception):
        response = type("R", (), {"status_code": status})()

    def refuse(url: str) -> Any:
        calls.append(url)
        raise Refused()

    client = BinanceStreamClient(connect=refuse)
    client.subscribe("trade", ["BTCUSDT"])
    with pytest.raises(error, match=f"HTTP {status}"):
        asyncio.run(_take(client, 1))
    assert len(calls) == 1


def test_binance_gives_up_after_max_reconnects() -> None:
    async def no_sleep(_: float) -> None:
        return None

    client = BinanceStreamClient(
        connect=lambda u: FakeSocket([], close_with=OSError("down")), max_reconnects=2, sleep=no_sleep
    )
    client.subscribe("trade", ["BTCUSDT"])
    with pytest.raises(NetworkError):
        asyncio.run(_take(client, 1))


# -- TCBS history endpoint -------------------------------------------------------------------------


def test_tcbs_history_lines_become_frames() -> None:
    line = 'C001|TCB|{"symbol":"TCB","closePrice":38600.0,"closeVol":1000.0}'
    socket = FakeSocket(
        ['d|0|{"success":true,"error":null}', "d|33|15", line, 'C002S60|TCB|{"symbol":"TCB"}']
    )
    client = TcbsStreamClient(connect=lambda url: socket, token=lambda: "jwt")
    client.subscribe("history_1m", ["TCB"]).subscribe("supply_demand_1m", ["TCB"])
    frames = asyncio.run(_take(client, 2))
    data = [f for f in frames if f.kind == "data"]
    assert [f.channel for f in data] == ["history_1m", "supply_demand_1m"]
    assert data[0].payload.decode() == line
    assert socket.sent[1] == "d|st|C001+C002S60|TCB"


def test_tcbs_match_history_takes_one_symbol() -> None:
    client = TcbsStreamClient(connect=lambda url: FakeSocket([]), token=lambda: "jwt")
    client.subscribe("match_history", ["FPT"])
    with pytest.raises(InputError, match="một mã"):
        client.subscribe("match_history", ["FPT", "HPG"])
