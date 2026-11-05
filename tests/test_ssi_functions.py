"""Every SSI REST data function: the requests it makes, against a fake server.

Mọi hàm dữ liệu REST của SSI: các request nó tạo ra, trên máy chủ giả.
"""

from __future__ import annotations

import time

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import InputError
from vnstock_oapi.sources import ssi

NOW = int(time.time())


def _server(seen: list[httpx.Request]):  # type: ignore[no-untyped-def]
    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        path = request.url.path
        if path == "/api/v3/auth/token":
            return json_response(200, {"data": {"accessToken": "a", "expiresAt": NOW + 900}})
        if path in ("/api/v3/data/indexList", "/api/v3/data/indexSummary", "/api/v3/data/securitiesByBoard"):
            return json_response(200, [{"symbol": "X"}])  # bare arrays, as SSI sends
        page = int(request.url.params.get("pageIndex", 1))
        return json_response(
            200, {"code": 200, "data": [{"symbol": "X"}], "pageIndex": page, "pagesCount": 2}
        )

    return handler


@pytest.fixture
def source(monkeypatch):  # type: ignore[no-untyped-def]
    monkeypatch.setenv("SSI_API_KEY", "ssi-key-0001")
    monkeypatch.setenv("SSI_API_SECRET", "ssi-secret-0002")
    seen: list[httpx.Request] = []
    src = make_source(ssi.SPEC, _server(seen))
    src.seen = seen  # type: ignore[attr-defined]
    return src


def _data_calls(src) -> list[httpx.Request]:  # type: ignore[no-untyped-def]
    return [r for r in src.seen if not r.url.path.startswith("/api/v3/auth")]


def test_ops_lists_verbs_extras_and_raw(source) -> None:  # type: ignore[no-untyped-def]
    ops = source.ops
    assert ops["verbs"] == ["ohlc", "quote", "index", "foreign", "symbols", "events"]
    assert set(ops["extras"]) == {
        "summary",
        "index_summary",
        "index_list",
        "index_members",
        "security",
        "price_limits",
        "corporate_actions",
    }
    assert "index_members" in dir(source)
    with pytest.raises(AttributeError):
        source.place_order  # noqa: B018


def test_index_summary_is_one_call_per_weekday(source) -> None:  # type: ignore[no-untyped-def]
    out = source.index("VN30", start="2026-10-23", end="2026-10-27")  # Fri, Mon, Tue
    days = [r.url.params["tradingDate"] for r in _data_calls(source)]
    assert days == ["2026/10/23", "2026/10/26", "2026/10/27"] and len(out) == 3
    source.seen.clear()
    source.index_summary(board="HOSE", start="2026-10-27", end="2026-10-27")
    assert _data_calls(source)[0].url.params["board"] == "HOSE"


def test_corporate_actions_split_into_five_day_calls(source) -> None:  # type: ignore[no-untyped-def]
    source.corporate_actions("FPT", start="2026-10-01", end="2026-10-12", language="en")
    calls = _data_calls(source)
    windows = sorted({(r.url.params["from"], r.url.params["to"]) for r in calls})
    assert windows == [
        ("2026/10/01", "2026/10/05"),
        ("2026/10/06", "2026/10/10"),
        ("2026/10/11", "2026/10/12"),
    ]
    assert len(calls) == 6  # each window has 2 pages (pagesCount=2)
    assert {r.url.params["language"] for r in calls} == {"en"}
    with pytest.raises(InputError):
        source.corporate_actions(language="fr")


def test_symbols_and_reference_functions(source) -> None:  # type: ignore[no-untyped-def]
    source.symbols()
    assert [r.url.params["board"] for r in _data_calls(source)] == ["HOSE", "HNX", "UPCOM"]
    source.seen.clear()
    source.symbols(board="hnx")
    source.index_members("vn30")
    source.security("fpt")
    source.index_list(board="HOSE")
    params = [dict(r.url.params) for r in _data_calls(source)]
    assert params == [{"board": "HNX"}, {"index": "VN30"}, {"symbol": "FPT"}, {"board": "HOSE"}]


def test_summary_quote_foreign_and_price_limits(source) -> None:  # type: ignore[no-untyped-def]
    source.summary("FPT", start="2026-10-01", end="2026-10-27")
    source.summary(index="VN30", start="2026-10-27", end="2026-10-27")
    source.foreign("FPT", start="2026-10-20", end="2026-10-27")
    source.quote("FPT")
    out = source.price_limits(start="2026-10-27", end="2026-10-27")
    calls = _data_calls(source)
    assert {r.url.path for r in calls} == {"/api/v3/data/securitiesSummary", "/api/v3/data/masterdata"}
    assert calls[0].url.params["symbol"] == "FPT" and "index" not in calls[0].url.params
    assert len(out) == 2  # masterdata pages follow pagesCount
    with pytest.raises(InputError):
        source.summary("FPT", index="VN30")
    with pytest.raises(InputError):
        source.summary()



def test_symbols_takes_a_board_and_quote_spans_a_week(source) -> None:  # type: ignore[no-untyped-def]
    source.symbols("HOSE")
    source.quote("FPT")
    calls = _data_calls(source)
    board = next(r for r in calls if r.url.path.endswith("securitiesByBoard"))
    quote = next(r for r in calls if r.url.path.endswith("securitiesSummary"))
    assert board.url.params["board"] == "HOSE"
    assert quote.url.params["from"] != quote.url.params["to"]  # today has no row while the session is open

def test_preview_counts_calls(source) -> None:  # type: ignore[no-untyped-def]
    preview = source.preview("index_summary", "VNINDEX", start="2026-10-20", end="2026-10-27")
    assert preview["calls"] == 6 and preview["query"]["index"] == "VNINDEX"
    assert source.seen == []


def test_default_day_waits_for_the_session() -> None:
    from datetime import datetime

    from vnstock_oapi.core.timeutil import VN_TZ

    assert str(ssi._last_weekday(datetime(2026, 10, 28, 0, 30, tzinfo=VN_TZ))) == "2026-10-27"
    assert str(ssi._last_weekday(datetime(2026, 10, 28, 9, 5, tzinfo=VN_TZ))) == "2026-10-28"
    assert str(ssi._last_weekday(datetime(2026, 11, 1, 10, 0, tzinfo=VN_TZ))) == "2026-10-30"  # Sunday
