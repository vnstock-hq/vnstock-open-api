"""DNSE Lightspeed API: HMAC-SHA256 signature on every request, no session, no OTP for data.

DNSE Lightspeed API: ký HMAC-SHA256 từng request, không có phiên, không cần OTP để lấy dữ liệu.

Docs: https://developers.dnse.com.vn/docs/guide/intro/authentication/
Paths and parameters follow DNSE's OpenAPI description (``dnse-openapi-2026-09-15.yaml``).
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import os
import uuid
from datetime import date, timedelta
from email.utils import formatdate
from typing import Any
from urllib.parse import quote

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.auth import SignedAuth
from vnstock_oapi.core.credentials import CredentialField
from vnstock_oapi.core.source import Builder, Op, Plan, SourceSpec
from vnstock_oapi.core.timeutil import default_range, epoch
from vnstock_oapi.errors import InputError

NAME = "dnse"
BASE_URL = "https://openapi.dnse.com.vn"
DEFAULT_API_VERSION = "2026-07-23"
DOC = "https://developers.dnse.com.vn/docs"

# Intervals, asset types and index names: spec/sources/dnse/mapping.toml, through core.vocabulary.


def index_name(name: str) -> str:
    """DNSE's name for an index given its common code.

    Tên DNSE của một chỉ số, từ mã chung (HNXINDEX → HNX, UPCOMINDEX → UPCOM).
    """
    return vocabulary.symbol(NAME, name)


def dnse_signature(
    api_key: str, api_secret: str, method: str, path: str, date: str, nonce: str, date_header: str
) -> str:
    """Value of the ``X-Signature`` header, per DNSE Lightspeed's authentication docs.

    Giá trị header ``X-Signature`` theo tài liệu xác thực DNSE Lightspeed.

    Signs ``(request-target)``, the date header and the nonce. The query string is not signed (that
    is the source's design), so TLS must stay fully verified.
    """
    header_key = date_header.lower()
    signing = f"(request-target): {method.lower()} {path}\n{header_key}: {date}"
    if nonce:
        signing += f"\nnonce: {nonce}"
    digest = hmac.new(api_secret.encode(), signing.encode(), hashlib.sha256).digest()
    signature = quote(base64.b64encode(digest).decode(), safe="")
    value = (
        f'Signature keyId="{api_key}",algorithm="hmac-sha256",'
        f'headers="(request-target) {header_key}",signature="{signature}"'
    )
    return value + (f',nonce="{nonce}"' if nonce else "")


# Date header the signature covers: "Date" per the SDK (the OpenAPI description says "X-Aux-Date").
DATE_HEADER = "Date"


def _signed_headers(api_key: str, api_secret: str, method: str, path: str) -> dict[str, str]:
    date = formatdate(usegmt=True)
    nonce = uuid.uuid4().hex
    return {
        "x-api-key": api_key,
        DATE_HEADER: date,
        "X-Signature": dnse_signature(api_key, api_secret, method, path, date, nonce, DATE_HEADER),
    }


def _api_version() -> str:
    return os.environ.get("DNSE_API_VERSION") or DEFAULT_API_VERSION


def _next_token(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    token = body.get("nextPageToken") if isinstance(body, dict) else None
    return {**params, "nextPageToken": token} if token else None


def _next_numbered_page(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """``market/instruments`` pages by number: ``{"page", "pageSize", "total"}``."""
    if not isinstance(body, dict):
        return None
    page, size, total = body.get("page"), body.get("pageSize"), body.get("total")
    if not (isinstance(page, int) and isinstance(size, int) and isinstance(total, int)):
        return None
    if not size or page * size >= total:
        return None
    return {**params, "page": page + 1}


# Upper bound on pages fetched for one 24-hour window.
DAY_PAGES = 500

OPS = {
    op.name: op
    for op in (
        Op("price/ohlc", "/price/ohlc", DOC),
        Op("price/{symbol}/trades", "/price/{symbol}/trades", DOC,
           next_page=_next_token, max_pages=DAY_PAGES),
        Op("price/{symbol}/trades/latest", "/price/{symbol}/trades/latest", DOC),
        Op("price/{symbol}/quotes", "/price/{symbol}/quotes", DOC),
        Op("price/{symbol}/quotes/latest", "/price/{symbol}/quotes/latest", DOC),
        Op("price/{symbol}/foreign-trading", "/price/{symbol}/foreign-trading", DOC,
           next_page=_next_token, max_pages=DAY_PAGES),
        Op("price/{symbol}/secdef", "/price/{symbol}/secdef", DOC),
        Op("price/{symbol}/close", "/price/{symbol}/close", DOC),
        # Pages of 100 with nextPageToken, like trades: unpaged, a session stopped at its first 100 rows.
        Op("price/{symbol}/expected-price", "/price/{symbol}/expected-price", DOC,
           next_page=_next_token, max_pages=DAY_PAGES),
        Op("price/{indexName}/market-index", "/price/{indexName}/market-index", DOC,
           next_page=_next_token, max_pages=DAY_PAGES),
        Op("market/instruments", "/market/instruments", DOC,
           next_page=_next_numbered_page, page_params=lambda page: {"page": page}),
        Op("market/working-dates", "/market/working-dates", DOC),
        Op("market/trading-session", "/market/trading-session", DOC),
    )
}


def _ohlc(symbol: str, *, start: Any = None, end: Any = None, interval: str = "1D",
          limit: int | None = None, asset: str = "equity") -> Plan:
    start_dt, end_dt = default_range(start, end)
    asset = vocabulary.canonical("asset", asset)
    return Plan("price/ohlc", {
        "symbol": index_name(symbol) if asset == "index" else symbol.upper(),
        "type": vocabulary.native(NAME, "asset", asset),
        "resolution": vocabulary.native(NAME, "interval", interval),
        "from": epoch(start_dt),
        "to": epoch(end_dt),
    })


# trades, foreign-trading and market-index take a window of at most 24 hours: one request per window.
MAX_RANGE_S = 24 * 3600
MAX_RANGED_DAYS = 400
# Rows per page: always ask for the largest page, so a liquid stock's day of trades stays within max_pages.
PAGE_LIMIT = 1000


def _ranged(operation: str, key: str = "symbol"):  # type: ignore[no-untyped-def]
    def build(value: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> list[Plan]:
        if start is None and end is None:
            # No day asked: the latest session; a weekend steps back to Friday (holidays are not known).
            today = date.today()
            start = end = today - timedelta(days=max(0, today.weekday() - 4))
        start_dt, end_dt = default_range(start, end, days=1)
        first, last = epoch(start_dt), epoch(end_dt)
        windows = range(first, max(last, first + 1), MAX_RANGE_S)
        if len(windows) > MAX_RANGED_DAYS:
            raise InputError(
                f"Tối đa {MAX_RANGED_DAYS} ngày mỗi lần (mỗi 24 giờ một request)", source=NAME
            )
        name = index_name(value) if key == "indexName" else value.upper()
        return [
            Plan(operation, {key: name, "from": t, "to": min(t + MAX_RANGE_S - 1, last),
                             "limit": min(limit or PAGE_LIMIT, PAGE_LIMIT)})
            for t in windows
        ]
    return build


def _trades(
    symbol: str, *, start: Any = None, end: Any = None, limit: int | None = None, board: str | None = None
) -> list[Plan]:
    """Matched trades, one request per 24 hours; ``board`` keeps one board (``G1`` round lots).

    Lệnh khớp, mỗi 24 giờ một request; ``board`` chỉ lấy một bảng: lô chẵn ``G1``, lô lẻ ``G4``, thoả thuận
    ``T1``, sau giờ ``G3``.
    """
    plans: list[Plan] = _ranged("price/{symbol}/trades")(symbol, start=start, end=end, limit=limit)
    if board:
        for plan in plans:
            plan.params["boardId"] = board.strip().upper()
    return plans


# Operations with a 24-hour window and no common verb.
EXTRAS: dict[str, Builder] = {
    "expected_price": _ranged("price/{symbol}/expected-price"),
}

VERBS: dict[str, Builder] = {
    "ohlc": _ohlc,
    "trades": _trades,
    "quote": lambda symbol: Plan("price/{symbol}/quotes/latest", {"symbol": symbol.upper()}),
    "foreign": _ranged("price/{symbol}/foreign-trading"),
    "index": _ranged("price/{indexName}/market-index", key="indexName"),
    "symbols": lambda **filters: Plan("market/instruments", dict(filters)),
    "calendar": lambda: Plan("market/working-dates", {}),
}

def _samples(symbol: str, index: str) -> list[tuple[str, Plan]]:
    return [
        ("secdef", Plan("price/{symbol}/secdef", {"symbol": symbol})),
        ("close", Plan("price/{symbol}/close", {"symbol": symbol})),
        ("trading_session", Plan("market/trading-session", {})),
        ("instruments", Plan("market/instruments", {"symbol": symbol})),
    ]


SPEC = SourceSpec(
    name=NAME,
    title="DNSE Lightspeed API",
    base_url=BASE_URL,
    hosts=frozenset({"openapi.dnse.com.vn"}),
    credentials=(
        CredentialField("api_key", "API_KEY", label="API Key"),
        CredentialField("api_secret", "API_SECRET", label="API Secret"),
    ),
    auth=lambda source: SignedAuth(source, sign=_signed_headers),
    ops=OPS,
    verbs=VERBS,
    extras=EXTRAS,
    api_version=DEFAULT_API_VERSION,
    static_headers=lambda: {"version": _api_version()},
    check=lambda: Plan("market/working-dates", {}),
    docs=DOC,
    samples=_samples,
)
