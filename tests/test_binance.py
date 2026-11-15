"""Binance (public, no key): request shape, host policy, paging, body errors, scope.

Binance (công khai, không khoá): dựng request, chính sách host, phân trang, lỗi trong body, phạm vi.
Không gọi mạng.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.core.auth import PublicAuth
from vnstock_oapi.core.transport import USER_AGENT, Transport
from vnstock_oapi.errors import (
    InputError,
    PolicyError,
    RateLimitError,
    SourceError,
    UnsupportedError,
)
from vnstock_oapi.sources import binance

AUTH_HEADERS = ("authorization", "x-mbx-apikey", "apikey", "x-api-key")


def test_no_key_is_read_or_sent() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {"symbol": "BTCUSDT", "price": "1.0"})

    source = make_source(binance.SPEC, handler)
    out = source.last_price("btcusdt")
    request = seen[0]
    assert str(request.url) == "https://data-api.binance.vision/api/v3/ticker/price?symbol=BTCUSDT"
    assert request.method == "GET"
    assert not [h for h in AUTH_HEADERS if h in request.headers]
    assert request.headers["user-agent"] == USER_AGENT
    assert binance.SPEC.credentials == () and isinstance(source.auth, PublicAuth)
    assert source.status()["auth"] == "public"
    assert out[0].operation == "ticker/price"


def test_only_the_market_data_host_is_allowed() -> None:
    assert binance.SPEC.hosts == frozenset({"data-api.binance.vision"})
    assert binance.SPEC.base_url.startswith("https://data-api.binance.vision/")
    transport = Transport("binance", binance.SPEC.hosts, client=httpx.Client(
        transport=httpx.MockTransport(lambda r: json_response(200, {}))
    ))
    for url in ("https://api.binance.com/api/v3/ping", "https://api1.binance.com/api/v3/ping"):
        with pytest.raises(PolicyError):
            transport.request("GET", url)
    with pytest.raises(PolicyError):
        transport.request("POST", "https://data-api.binance.vision/api/v3/ping")


def test_scope_is_public_market_data_only() -> None:
    names = set(binance.SPEC.ops)
    assert names == {
        "ping", "time", "exchangeInfo", "depth", "trades", "aggTrades", "klines", "uiKlines",
        "avgPrice", "ticker/24hr", "ticker/tradingDay", "ticker/price", "ticker/bookTicker", "ticker",
    }
    banned = ("order", "account", "userDataStream", "myTrades", "sapi", "historical", "referencePrice")
    assert not [n for n in names if any(word.lower() in n.lower() for word in banned)]
    assert all(op.doc.startswith(binance.DOC) for op in binance.SPEC.ops.values())


def test_ohlc_maps_the_shared_intervals() -> None:
    source = make_source(binance.SPEC, lambda r: json_response(200, []))
    pairs = (("1H", "1h"), ("4h", "4h"), ("1D", "1d"), ("day", "1d"), ("1W", "1w"), ("1M", "1M"),
             ("3m", "3m"))
    for common, native in pairs:
        (plan,) = source.plan("ohlc", "BTCUSDT", interval=common, limit=10)
        assert plan.operation == "klines" and plan.params["interval"] == native, common
    with pytest.raises(InputError):
        source.plan("ohlc", "BTCUSDT", interval="1Y")
    (plan,) = source.plan("ohlc", "BTCUSDT", asset="crypto", limit=5)
    assert plan.params == {"symbol": "BTCUSDT", "interval": "1d", "limit": 5}
    with pytest.raises(InputError):
        source.plan("ohlc", "BTCUSDT", limit=5000)


def test_ohlc_range_walks_forward_and_stops() -> None:
    seen: list[dict[str, str]] = []
    day_ms = 86_400_000

    def handler(request: httpx.Request) -> httpx.Response:
        params = dict(request.url.params)
        seen.append(params)
        start = int(params["startTime"])
        rows = int(params["limit"]) if len(seen) == 1 else 3
        return json_response(200, [[start + i * day_ms, "1", "1", "1", "1", "1"] for i in range(rows)])

    out = make_source(binance.SPEC, handler).ohlc("ETHUSDT", start="2020-01-01", end="2026-10-30")
    assert len(out) == 2 and seen[0]["limit"] == str(binance.MAX_LIMIT)
    assert int(seen[1]["startTime"]) == int(seen[0]["startTime"]) + (binance.MAX_LIMIT - 1) * day_ms + 1
    assert seen[1]["endTime"] == seen[0]["endTime"]


def test_page_limit_is_marked_on_the_last_envelope() -> None:
    """Stopping at ``max_pages`` says so, so the caller can warn instead of serving a short answer.

    Dừng ở ``max_pages`` thì nói ra, để bên gọi cảnh báo thay vì trả kết quả thiếu như kết quả đủ.
    """
    minute_ms = 60_000
    sleeps: list[float] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # Always a full page, each one a thousand minutes: the walk never reaches endTime.
        start = int(dict(request.url.params)["startTime"])
        rows = [[start + i * minute_ms, "1", "1", "1", "1", "1"] for i in range(binance.MAX_LIMIT)]
        return json_response(200, rows)

    source = make_source(binance.SPEC, handler, sleeps=sleeps)
    out = source.ohlc("BTCUSDT", interval="1m", start="2020-01-01", end="2026-10-30")
    assert len(out) == binance.MAX_PAGES
    assert [e.truncated for e in out[:-1]] == [None] * (binance.MAX_PAGES - 1)
    assert out[-1].truncated == {"reason": "page_limit", "pages": binance.MAX_PAGES}
    assert "truncated" not in out[0].to_dict() and "truncated" in out[-1].to_dict()


def test_trades_switch_to_aggregate_with_a_window() -> None:
    source = make_source(binance.SPEC, lambda r: json_response(200, []))
    (plan,) = source.plan("trades", "BTCUSDT", limit=100)
    assert plan.operation == "trades" and plan.params == {"symbol": "BTCUSDT", "limit": 100}
    (plan,) = source.plan("trades", "BTCUSDT", start="2026-10-30 09:00:00", end="2026-10-30 09:30:00")
    assert plan.operation == "aggTrades" and plan.params["startTime"] < plan.params["endTime"]
    with pytest.raises(InputError):
        source.plan("trades", "BTCUSDT", start="2026-10-30", end="2026-10-01")


def test_extras_build_the_documented_calls() -> None:
    source = make_source(binance.SPEC, lambda r: json_response(200, {}))
    cases = {
        ("quote",): ("ticker/24hr", {"symbol": "BTCUSDT"}),
        ("book_ticker",): ("ticker/bookTicker", {"symbol": "BTCUSDT"}),
        ("avg_price",): ("avgPrice", {"symbol": "BTCUSDT"}),
        ("daily_stats",): ("ticker/tradingDay", {"symbol": "BTCUSDT"}),
    }
    for (name,), (operation, params) in cases.items():
        (plan,) = source.plan(name, "btc/usdt")
        assert (plan.operation, plan.params) == (operation, params), name
    (plan,) = source.plan("order_book", "BTCUSDT", limit=5000)
    assert plan.params == {"symbol": "BTCUSDT", "limit": 5000}
    (plan,) = source.plan("rolling_stats", "BTCUSDT", window="4h")
    assert plan.operation == "ticker" and plan.params["windowSize"] == "4h"
    with pytest.raises(InputError):
        source.plan("rolling_stats", "BTCUSDT", window="1d2h")
    (plan,) = source.plan("symbols")
    assert plan.operation == "exchangeInfo" and plan.params == {"permissions": "SPOT"}
    with pytest.raises(InputError):
        source.plan("last_price", "  ")
    with pytest.raises(InputError):
        source.plan("symbols", asset="equity")  # spot crypto only
    with pytest.raises(UnsupportedError):
        source.plan("index", "BTCUSDT")


@pytest.mark.parametrize(
    ("status", "code", "error"),
    [
        (400, -1121, InputError),
        (400, -1120, InputError),
        (429, -1003, RateLimitError),
        (418, -1003, RateLimitError),
        (503, -1008, SourceError),
    ],
)
def test_code_msg_body_is_classified(status, code, error) -> None:
    body = {"code": code, "msg": "message"}
    headers = {"Retry-After": "0"} if status == 429 else None
    source = make_source(binance.SPEC, lambda r: json_response(status, body, headers), sleeps=[])
    with pytest.raises(error) as caught:
        source.last_price("BTCUSDT")
    if status != 429:  # the transport answers a second 429 itself, after one wait
        assert caught.value.code == code


def test_successful_bodies_are_not_errors() -> None:
    assert binance.body_error({"symbol": "BTCUSDT", "price": "1"}) is None
    assert binance.body_error([[1, "1"]]) is None
    assert binance.body_error({"code": 0, "msg": "ok"}) is None


def test_preview_shows_no_auth_header() -> None:
    shown = make_source(binance.SPEC, lambda r: json_response(200, {})).preview("ohlc", "BTCUSDT", limit=3)
    assert shown["url"] == "https://data-api.binance.vision/api/v3/klines"
    assert not [h for h in shown["headers"] if h.lower() in AUTH_HEADERS]
