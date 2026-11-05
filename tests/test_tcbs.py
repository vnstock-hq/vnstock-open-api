"""TCBS: OTP session, request shape, paging.

TCBS: phiên OTP, dựng request, phân trang. Không gọi mạng.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import (
    InputError,
    OtpRequiredError,
)
from vnstock_oapi.sources import tcbs


def test_tcbs_needs_otp_and_never_relogs(monkeypatch) -> None:
    monkeypatch.setenv("TCBS_API_KEY", "tcbs-key-0001")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path.endswith("/token"):
            assert b'"otp": "123456"' in request.content or b'"otp":"123456"' in request.content
            return json_response(200, {"token": "h.eyJleHAiOjQxMDI0NDQ4MDB9.s"})
        return json_response(200, {"ticker": "FPT", "data": []})

    source = make_source(tcbs.SPEC, handler)
    with pytest.raises(OtpRequiredError):
        source.ohlc("FPT")
    with pytest.raises(OtpRequiredError):
        source.authenticate()
    info = source.authenticate(otp="123456")
    assert info["session"] == "còn hạn"
    out = source.ohlc("FPT", start="2026-10-01", end="2026-10-27", interval="1D")
    assert calls[-1] == "/nyx/v2/stock/bars-long-term"
    assert out[0].request["resolution"] == "D" and out[0].request["countBack"] == 27


def test_tcbs_functions_build_documented_requests(monkeypatch) -> None:

    monkeypatch.setenv("TCBS_API_KEY", "tcbs-key-0001")
    source = make_source(tcbs.SPEC, lambda r: json_response(200, {}))
    url = lambda name, *a, **k: source.preview(name, *a, **k)  # noqa: E731
    assert url("quote", "fpt")["query"] == {"tickers": "FPT"}
    assert url("board", "FPT", "VNM")["query"] == {"tickers": "FPT,VNM"}
    assert url("board", basket="VN30")["query"] == {"index": 2}
    assert url("foreign_room", "HNX")["query"] == {"index": 3}
    assert url("put_through", "UPCOM")["query"] == {"floor": 3}
    assert url("trades", "FPT")["url"].endswith("/nyx/v1/intraday/FPT/his/paging")
    assert url("supply_demand", "FPT", window="daily")["url"].endswith("/nyx/v1/intraday/FPT/bsa")
    assert url("securities", "tcb")["query"] == {"fields": "all", "filter": "symbol=TCB"}
    assert url("derivatives")["url"].endswith("/tartarus/v1/derivatives")
    with pytest.raises(InputError):
        source.plan("board", "FPT", basket="VN30")
    with pytest.raises(InputError):
        source.plan("supply_demand", "FPT", window="weekly")


def test_tcbs_price_history_pages_until_total() -> None:
    from vnstock_oapi.sources.tcbs import _next_history_page

    params = {"ticker": "FPT", "page": 0, "size": 100, "headIndex": -1}
    assert _next_history_page(params, {"total": 250, "data": [1]})["page"] == 1
    assert _next_history_page({**params, "page": 2}, {"total": 250, "data": [1]}) is None
    assert _next_history_page(params, {"total": 250, "data": []}) is None


def test_tcbs_bars_walk_back_past_the_250_cap() -> None:
    from vnstock_oapi.sources.tcbs import BARS_PAGE_CAP, _next_bars_page

    rows = [{"tradingDate": "2026-01-02T00:00:00.000Z"}] + [{"tradingDate": "x"}] * (BARS_PAGE_CAP - 1)
    params = {"ticker": "FPT", "to": 1_800_000_000, "countBack": 600}
    following = _next_bars_page(params, {"data": rows})
    assert following["to"] == 1_767_312_000 - 1 and following["countBack"] == 600 - BARS_PAGE_CAP
    assert _next_bars_page(params, {"data": rows[:-1]}) is None  # short page: no more history
    assert _next_bars_page({**params, "countBack": BARS_PAGE_CAP}, {"data": rows}) is None


def test_a_source_page_parameter_is_not_taken_for_only_page(monkeypatch) -> None:
    monkeypatch.setenv("TCBS_API_KEY", "tcbs-key-0001")
    pages: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path.endswith("/token"):
            return json_response(200, {"token": "h.eyJleHAiOjQxMDI0NDQ4MDB9.s"})
        pages.append(request.url.params["page"])
        return json_response(200, {"page": 0, "size": 100, "total": 250, "data": [{"p": 1}]})

    source = make_source(tcbs.SPEC, handler)
    source.authenticate(otp="123456")
    store_reads = 0
    orig_get = source.auth.store.get  # type: ignore[attr-defined]

    def count_get():
        nonlocal store_reads
        store_reads += 1
        return orig_get()

    monkeypatch.setattr(source.auth.store, "get", count_get)  # type: ignore[attr-defined]
    out = source.raw("stock/price-history", ticker="FPT", page=0, size=100, headIndex=-1)
    assert pages == ["0", "1", "2"] and len(out) == 3
    assert store_reads == 0  # in-memory token cache serves all 3 pages without hitting SessionStore
    assert len(source.raw("stock/price-history", ticker="FPT", page=0, size=100, only_page=1)) == 1


def test_tcbs_intraday_countback_uses_weekdays() -> None:
    source = make_source(tcbs.SPEC, lambda r: json_response(200, {}))
    # 2026-10-21 (Wed) to 2026-10-27 (Tue) is 7 calendar days but 5 weekdays.
    (plan,) = source.plan("ohlc", "FPT", start="2026-10-21", end="2026-10-27", interval="1m")
    assert plan.params["countBack"] == 5 * 270
