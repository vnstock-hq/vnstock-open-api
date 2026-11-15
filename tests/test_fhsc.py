"""FHSC (api_key_header): request shape, paging, body errors.

FHSC: dựng request, phân trang, lỗi trong body. Không gọi mạng.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import (
    AuthError,
    CredentialError,
    InputError,
    SourceError,
    UnsupportedError,
)
from vnstock_oapi.sources import fhsc

FHSC_KEY = "fhsc-key-0123456789"


def _ok(data: object) -> dict:
    return {"error_code": "0", "message": "success", "data": data}


# -- FHSC -----------------------------------------------------------------------


def test_fhsc_ohlc_sends_key_header_and_maps_params(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, _ok({"symbol": "FPT", "series": []}))

    out = make_source(fhsc.SPEC, handler).ohlc("fpt", start="2026-10-01", end="2026-10-27", interval="30m")
    request = seen[0]
    assert request.url.host == "open-api.fhsc.com.vn"
    assert request.url.path == "/market/quotes/stocks/FPT/history"
    assert request.url.params["resolution"] == "30"
    assert int(request.url.params["from"]) < int(request.url.params["to"])
    assert request.headers["X-FH-APIKEY"] == FHSC_KEY
    assert "X-FH-SIGNATURE" not in request.headers  # Tier 1 only: no secret, no signing
    assert FHSC_KEY not in out[0].to_json()


def test_fhsc_has_no_one_minute_bars_and_funds_have_no_resolution() -> None:
    source = make_source(fhsc.SPEC, lambda r: json_response(200, _ok({})))
    with pytest.raises(InputError):
        source.plan("ohlc", "FPT", interval="1m")
    # 1H and 4H are not declared for this source.
    for interval in ("1H", "4H"):
        with pytest.raises(InputError):
            source.plan("ohlc", "FPT", interval=interval)
    (plan,) = source.plan("ohlc", "VESAF", asset="fund", start="2026-01-01", end="2026-10-01")
    assert plan.operation == "quotes/funds/{symbol}/history" and "resolution" not in plan.params


def test_fhsc_macro_range_is_sent_as_months() -> None:
    source = make_source(fhsc.SPEC, lambda r: json_response(200, _ok({})))
    (plan,) = source.plan("macro", "vn-cpi-yoy", start="2021-01-15", end="2026-10-29")
    assert (plan.params["from"], plan.params["to"]) == ("2021-01", "2026-10")


def test_fhsc_unknown_symbol_is_an_input_error(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    body = {"error_code": "DF001", "message": "Not found symbol VN30F1M"}
    with pytest.raises(InputError, match="DF001"):
        make_source(fhsc.SPEC, lambda r: json_response(200, body)).quote("VN30F1M")
    bad = {"error_code": "400", "message": "resolution must be one of: 1D"}
    with pytest.raises(InputError):
        make_source(fhsc.SPEC, lambda r: json_response(400, bad)).quote("FPT")


def test_fhsc_reads_only_the_key(monkeypatch) -> None:
    source = make_source(fhsc.SPEC, lambda r: json_response(200, _ok({})))
    with pytest.raises(CredentialError, match="FHSC_API_KEY"):
        source.quote("FPT")
    assert [f.env_suffix for f in fhsc.SPEC.credentials] == ["API_KEY"]


def test_fhsc_error_code_in_body_raises_even_on_200(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    body = {"error_code": "ORDER_BOOK_NOT_SUPPORTED", "message": "no order book"}
    source = make_source(fhsc.SPEC, lambda r: json_response(200, body))
    with pytest.raises(UnsupportedError, match="ORDER_BOOK_NOT_SUPPORTED"):
        source.order_book("VESAF")
    bad = {"error_code": "AUTH_API_KEY_INVALID", "message": "invalid"}
    source = make_source(fhsc.SPEC, lambda r: json_response(401, bad))
    with pytest.raises(AuthError):
        source.quote("FPT")
    odd = {"error_code": "SOMETHING_NEW", "message": "?"}
    with pytest.raises(SourceError):
        make_source(fhsc.SPEC, lambda r: json_response(200, odd)).quote("FPT")


def test_fhsc_follows_page_total(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        return json_response(200, _ok({"items": [page], "page": page, "page_size": 50, "total": 120}))

    out = make_source(fhsc.SPEC, handler).index_members("VN30")
    assert seen == [1, 2, 3] and len(out) == 3


def test_fhsc_follows_next_page(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    pages = iter([_ok({"results": [1], "next_page": 2}), _ok({"results": [2], "next_page": None})])
    out = make_source(fhsc.SPEC, lambda r: json_response(200, next(pages))).raw("calendar", page=1)
    assert len(out) == 2 and out[1].request["page"] == 2


def test_fhsc_follows_nested_pagination(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        paging = {"page": page, "page_size": 5, "total_items": 14, "total_pages": 3}
        return json_response(200, _ok({"symbol": "FPT", "pagination": paging, "events": [page]}))

    out = make_source(fhsc.SPEC, handler).events("FPT")
    assert seen == [1, 2, 3] and len(out) == 3


def test_fhsc_list_pages_stop_at_the_first_empty_page(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        return json_response(200, _ok([{"year": 2026 - page}] if page <= 2 else []))

    out = make_source(fhsc.SPEC, handler).financials("FPT")
    assert seen == [1, 2, 3] and [len(e.body["data"]) for e in out] == [1, 1, 0]


def test_fhsc_financials_take_the_shared_report_period() -> None:
    """``year``/``quarter`` (and their legacy spellings) become FHSC's ``annual``/``quarterly``."""
    build = fhsc.SPEC.extras["financials"]
    cases = (("year", "annual"), ("quarter", "quarterly"), ("quarterly", "quarterly"), ("y", "annual"))
    for given, sent in cases:
        assert build("FPT", statement="balance-sheet", period=given).params["period"] == sent, given
    with pytest.raises(InputError):
        build("FPT", period="month")


def test_fhsc_session_is_the_only_trading_path() -> None:
    paths = [op.path for op in fhsc.SPEC.ops.values()]
    assert [p for p in paths if p.startswith("/trading/")] == ["/trading/market/session"]
    assert all(p.startswith("/market/") for p in paths if p != "/trading/market/session")
    excluded = ("analyst-reports", "market-news", "technicals")
    assert not [p for p in paths if any(word in p for word in excluded)]


def test_fhsc_preview_masks_the_key(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    shown = make_source(fhsc.SPEC, lambda r: json_response(200, {})).preview("quote", "FPT")
    assert FHSC_KEY not in str(shown)
    assert shown["url"] == "https://open-api.fhsc.com.vn/market/quotes/stocks/FPT"


def test_fhsc_429_waits_for_reset_once(monkeypatch) -> None:
    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    sleeps: list[float] = []
    replies = iter(
        [
            json_response(429, {"error_code": "RATE_LIMIT_EXCEEDED"}, {"X-RateLimit-Reset": "1"}),
            json_response(200, _ok({})),
        ]
    )
    out = make_source(fhsc.SPEC, lambda r: next(replies), sleeps=sleeps).quote("FPT")
    assert sleeps == [1.0] and out[0].transport["request_count"] == 2


def test_body_error_source_parses_json_once_per_response(monkeypatch) -> None:
    import json

    monkeypatch.setenv("FHSC_API_KEY", FHSC_KEY)
    loads_calls = 0
    real_loads = json.loads

    def counting_loads(s, *args, **kwargs):
        nonlocal loads_calls
        loads_calls += 1
        return real_loads(s, *args, **kwargs)

    source = make_source(fhsc.SPEC, lambda r: json_response(200, _ok({"symbol": "FPT"})))
    _ = source.credentials
    monkeypatch.setattr(json, "loads", counting_loads)
    out = source.quote("FPT")
    assert out[0].body["data"] == {"symbol": "FPT"}
    assert loads_calls == 1
