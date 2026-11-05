"""DNSE: signing, request shape, windows, paging, clock skew.

DNSE: ký, dựng request, chia cửa sổ, phân trang, lệch đồng hồ. Không gọi mạng.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import (
    ClockSkewError,
    InputError,
)
from vnstock_oapi.sources import dnse


def test_dnse_signature_matches_known_vector() -> None:
    value = dnse.dnse_signature(
        "key", "secret", "GET", "/price/FPT/quotes/latest",
        "Tue, 26 Aug 2026 12:00:00 +0000", "001122", "Date",
    )
    assert value == (
        'Signature keyId="key",algorithm="hmac-sha256",headers="(request-target) date",'
        'signature="8xi97ifsgViTJoEvNX1MRVqBmnWhKv2xS49LoH3wj%2BU%3D",nonce="001122"'
    )


def test_dnse_ohlc_maps_params_and_signs(monkeypatch) -> None:
    monkeypatch.setenv("DNSE_API_KEY", "dnse-key-0001")
    monkeypatch.setenv("DNSE_API_SECRET", "dnse-secret-0002")
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {"t": [1], "o": [1], "h": [1], "l": [1], "c": [1], "v": [1]})

    source = make_source(dnse.SPEC, handler)
    out = source.ohlc("fpt", start="2026-10-01", end="2026-10-27", interval="1D")
    request = seen[0]
    assert request.url.path == "/price/ohlc"
    assert request.url.params["symbol"] == "FPT"
    assert request.url.params["resolution"] == "1D"
    assert request.url.params["type"] == "STOCK"
    assert request.headers["x-api-key"] == "dnse-key-0001"
    assert request.headers["version"] == dnse.DEFAULT_API_VERSION
    assert request.headers["X-Signature"].startswith('Signature keyId="dnse-key-0001"')
    env = out[0]
    assert env.operation == "price/ohlc" and env.body["c"] == [1]
    assert "dnse-secret" not in env.to_json() and "X-Signature" not in env.to_json()
    assert env.transport["domain"] == "openapi.dnse.com.vn"


def test_dnse_follows_next_page_token(monkeypatch) -> None:
    monkeypatch.setenv("DNSE_API_KEY", "k" * 10)
    monkeypatch.setenv("DNSE_API_SECRET", "s" * 10)
    pages = iter([{"trades": [1], "nextPageToken": "p2"}, {"trades": [2], "nextPageToken": None}])
    source = make_source(dnse.SPEC, lambda r: json_response(200, next(pages)))
    out = source.trades("FPT", start="2026-10-27", end="2026-10-27")
    assert len(out) == 2 and out[1].request["nextPageToken"] == "p2"


def test_dnse_tick_pages_ask_for_the_largest_page(monkeypatch) -> None:
    source = make_source(dnse.SPEC, lambda r: json_response(200, {}))
    (plan,) = source.plan("trades", "FPT", start="2026-10-27", end="2026-10-27")
    assert plan.params["limit"] == dnse.PAGE_LIMIT
    (plan,) = source.plan("trades", "FPT", start="2026-10-27", end="2026-10-27", limit=5000)
    assert plan.params["limit"] == dnse.PAGE_LIMIT


def test_dnse_instruments_follow_numbered_pages(monkeypatch) -> None:
    monkeypatch.setenv("DNSE_API_KEY", "k" * 10)
    monkeypatch.setenv("DNSE_API_SECRET", "s" * 10)
    seen = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params.get("page", "1"))
        seen.append(page)
        return json_response(200, {"data": [page], "page": page, "pageSize": 2, "total": 5})

    out = make_source(dnse.SPEC, handler).symbols()
    assert seen == [1, 2, 3] and [e.body["data"] for e in out] == [[1], [2], [3]]


def test_dnse_clock_skew(monkeypatch) -> None:
    monkeypatch.setenv("DNSE_API_KEY", "k" * 10)
    monkeypatch.setenv("DNSE_API_SECRET", "s" * 10)
    old = "Tue, 01 Jan 2030 00:00:00 GMT"
    source = make_source(dnse.SPEC, lambda r: json_response(
        400, {"code": "OA-400", "message": "Authorization field invalid"}, {"Date": old}))
    with pytest.raises(ClockSkewError):
        source.calendar()


def test_dnse_tick_windows_split_per_24_hours() -> None:
    source = make_source(dnse.SPEC, lambda r: json_response(200, {}))
    plans = source.plan("foreign", "FPT", start="2026-10-21", end="2026-10-27")
    assert len(plans) == 7
    assert all(p.params["to"] - p.params["from"] < dnse.MAX_RANGE_S for p in plans)
    assert plans[1].params["from"] == plans[0].params["to"] + 1
    (one,) = source.plan("index", "VN30", start="2026-10-27", end="2026-10-27")
    assert one.params["indexName"] == "VN30"
    with pytest.raises(InputError, match=str(dnse.MAX_RANGED_DAYS)):
        source.plan("trades", "FPT", start="2024-01-01", end="2026-10-27")


def test_dnse_instruments_numbered_pagination() -> None:
    assert dnse._next_numbered_page({"page": 1}, {"page": 1, "pageSize": 1000, "total": 1500}) == {
        "page": 2
    }
    assert dnse._next_numbered_page({"page": 2}, {"page": 2, "pageSize": 1000, "total": 1500}) is None
    assert dnse._next_numbered_page({"page": 1}, {"page": None, "pageSize": 1000, "total": 1500}) is None


def test_trades_keep_one_board_when_asked() -> None:
    """Without ``board`` DNSE returns every board; ``board="G1"`` sends ``boardId`` on every window."""
    source = make_source(dnse.SPEC, lambda r: json_response(200, {}))
    (plan,) = source.plan("trades", "FPT", start="2026-10-27", end="2026-10-27")
    assert "boardId" not in plan.params
    plans = source.plan("trades", "fpt", start="2026-10-27", end="2026-10-28", board="g1")
    assert len(plans) == 2 and all(p.params["boardId"] == "G1" for p in plans)


def test_common_trades_verb_passes_board_only_where_the_source_has_boards(monkeypatch) -> None:
    """``board`` reaches DNSE as ``boardId``; a source without boards refuses it instead of dropping it."""
    monkeypatch.setenv("DNSE_API_KEY", "k" * 10)
    monkeypatch.setenv("DNSE_API_SECRET", "s" * 10)
    seen: list[dict[str, str]] = []

    def answer(request: httpx.Request) -> httpx.Response:
        seen.append(dict(request.url.params))
        return json_response(200, {"trades": [], "nextPageToken": None})

    make_source(dnse.SPEC, answer).trades("FPT", start="2026-10-27", end="2026-10-27", board="G1")
    assert seen and seen[0]["boardId"] == "G1"
    from vnstock_oapi.sources import tcbs

    with pytest.raises(InputError):
        make_source(tcbs.SPEC, answer).trades("FPT", board="G1")
