"""FMP (api_key_header with the ``apikey`` header): request shape, paging, body errors, scope.

FMP: dựng request, phân trang, lỗi trong body, phạm vi. Không gọi mạng.
"""

from __future__ import annotations

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import AuthError, InputError, RateLimitError, UnsupportedError
from vnstock_oapi.sources import fmp

KEY = "fmp-key-0123456789abcdef"


def test_key_goes_in_the_header_never_the_url(monkeypatch) -> None:
    monkeypatch.setenv("FMP_API_KEY", KEY)
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, [{"symbol": "AAPL", "date": "2026-10-27", "close": 1.0}])

    out = make_source(fmp.SPEC, handler).ohlc("aapl", start="2026-10-01", end="2026-10-27")
    request = seen[0]
    assert str(request.url).startswith("https://financialmodelingprep.com/stable/historical-price-eod/full?")
    assert request.url.params["symbol"] == "AAPL"
    assert request.url.params["from"] == "2026-10-01" and request.url.params["to"] == "2026-10-27"
    assert request.headers["apikey"] == KEY
    assert "apikey" not in request.url.params and KEY not in str(request.url)
    assert KEY not in out[0].to_json()


def test_intraday_intervals_map_to_chart_paths() -> None:
    source = make_source(fmp.SPEC, lambda r: json_response(200, []))
    (plan,) = source.plan("ohlc", "EURUSD", interval="4h", start="2026-10-01", end="2026-10-02")
    assert plan.operation == "historical-chart/4hour"
    with pytest.raises(InputError):
        source.plan("ohlc", "AAPL", interval="1W")
    with pytest.raises(InputError):
        source.plan("ohlc", "AAPL", limit=10)


@pytest.mark.parametrize(
    ("message", "error"),
    [
        ("Invalid API KEY. Feel free to create a Free API Key.", AuthError),
        ("Limit Reach . Please upgrade your plan.", RateLimitError),
        (
            "Restricted Endpoint: This endpoint is not available under your current subscription",
            UnsupportedError,
        ),
        ("Something unexpected", InputError),
    ],
)
def test_error_message_body_is_classified(monkeypatch, message, error) -> None:
    monkeypatch.setenv("FMP_API_KEY", KEY)
    source = make_source(fmp.SPEC, lambda r: json_response(200, {"Error Message": message}))
    with pytest.raises(error):
        source.quote("AAPL")


def test_list_pages_stop_on_a_short_page(monkeypatch) -> None:
    monkeypatch.setenv("FMP_API_KEY", KEY)
    seen: list[int] = []

    def handler(request: httpx.Request) -> httpx.Response:
        page = int(request.url.params["page"])
        seen.append(page)
        rows = fmp.PAGE_LIMIT if page < 2 else 3
        return json_response(200, [{"n": i} for i in range(rows)])

    out = make_source(fmp.SPEC, handler).insider("aapl")
    assert seen == [0, 1, 2] and len(out) == 3


def test_events_of_a_symbol_are_dividends_and_splits() -> None:
    source = make_source(fmp.SPEC, lambda r: json_response(200, []))
    assert [p.operation for p in source.plan("events", "AAPL")] == ["dividends", "splits"]
    plans = source.plan("events", start="2026-10-01", end="2026-10-31")
    assert [p.operation for p in plans] == ["dividends-calendar", "splits-calendar"]


def test_financials_options() -> None:
    source = make_source(fmp.SPEC, lambda r: json_response(200, []))
    (plan,) = source.plan("financials", "msft", statement="cashflow", period="quarter", limit=8)
    assert plan.operation == "cash-flow-statement" and plan.params["period"] == "quarter"
    (plan,) = source.plan("financials", "msft", ttm=True)
    assert plan.operation == "income-statement-ttm" and "period" not in plan.params
    with pytest.raises(InputError):
        source.plan("financials", "msft", ttm=True, as_reported=True)


def test_quotes_need_exactly_one_selector() -> None:
    source = make_source(fmp.SPEC, lambda r: json_response(200, []))
    (plan,) = source.plan("quotes", "aapl", "msft")
    assert plan.operation == "batch-quote" and plan.params["symbols"] == "AAPL,MSFT"
    (plan,) = source.plan("quotes", asset="forex")
    assert plan.operation == "batch-forex-quotes"
    with pytest.raises(InputError):
        source.plan("quotes", "AAPL", asset="forex")


def test_excluded_groups_are_not_declared() -> None:
    names = set(fmp.SPEC.ops)
    banned = (
        "analyst",
        "ratings-",
        "grade",
        "price-target",
        "tipranks",
        "technical-indicators",
        "discounted-cash-flow",
        "news",
        "articles",
        "transcript",
        "bulk",
        "financial-reports-json",
        "financial-reports-xlsx",
        "senate",
        "house",
        "crowdfunding",
        "fundraising",
    )
    assert not [n for n in names if any(word in n for word in banned)]
    assert fmp.SPEC.hosts == frozenset({"financialmodelingprep.com"})
    assert all(op.max_pages <= fmp.MAX_PAGES for op in fmp.SPEC.ops.values())


def test_preview_masks_the_key(monkeypatch) -> None:
    monkeypatch.setenv("FMP_API_KEY", KEY)
    shown = make_source(fmp.SPEC, lambda r: json_response(200, [])).preview("quote", "AAPL")
    assert KEY not in str(shown)
