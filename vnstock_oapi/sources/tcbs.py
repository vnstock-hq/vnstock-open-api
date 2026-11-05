"""TCBS iFlash Open API: exchange ``apiKey`` + Smart OTP (TCInvest iOTP) for a JWT, up to 24 hours.

TCBS iFlash Open API: đổi ``apiKey`` + Smart OTP từ app TCInvest lấy JWT, tối đa 24 giờ.

There is no refresh token: when it expires the user types a new OTP (``vnstock-oapi auth tcbs``). The
OTP is never read from the environment, a file, email or SMS.
Docs: https://developers.tcbs.com.vn/docs/v1.0.0/auth/token/
"""

from __future__ import annotations

import time
from datetime import datetime
from typing import Any

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.auth import OtpTokenAuth, TokenSet, fallback_expiry, jwt_expiry
from vnstock_oapi.core.credentials import CredentialField, Credentials
from vnstock_oapi.core.source import Builder, Op, Plan, SourceSpec
from vnstock_oapi.core.timeutil import default_range, epoch
from vnstock_oapi.errors import InputError, SourceError

NAME = "tcbs"
BASE_URL = "https://openapi.tcbs.com.vn"
DOC = "https://developers.tcbs.com.vn/docs/v1.0.0"
TOKEN_MAX_AGE_S = 24 * 3600  # fallback when the JWT carries no exp; the token's own exp wins

# Intervals, asset types and index names: spec/sources/tcbs/mapping.toml, through core.vocabulary.
# Daily and longer intervals go to bars-long-term, the rest to bars.
LONG_TERM = ("1D", "1W", "1M")
# Approximate bars per trading day, used only to size countBack when limit is not given.
_BARS_PER_DAY = {"1": 270, "5": 54, "15": 18, "30": 9, "60": 5}


def _login_body(creds: Credentials, otp: str | None) -> dict[str, Any]:
    return {"apiKey": creds["api_key"].reveal(), "otp": otp or ""}


def _parse_token(data: Any, now: float) -> TokenSet:
    token = data.get("token") if isinstance(data, dict) else None
    if not token:
        raise SourceError("TCBS không trả token", source=NAME, operation="auth")
    return TokenSet(
        access_token=token,
        expires_at=fallback_expiry(jwt_expiry(token), now, TOKEN_MAX_AGE_S),
    )


# Basket codes used by tickerCommons, tickerSnaps (docs 5.1, 5.3) and floors by putThroughSnaps (5.4).
BASKETS = {
    "HOSE": 1,
    "VN30": 2,
    "HNX": 3,
    "HNX30": 4,
    "UPCOM": 5,
    "MIDCAP": 10,
    "VN100": 11,
    "VNALLSHARE": 12,
    "VNSMALLCAP": 13,
    "VNXALLSHARE": 14,
    "VN50": 15,
    "VNSI": 16,
}
FLOORS = {"HOSE": 1, "HNX": 2, "UPCOM": 3}
INVESTOR_TYPES = ("all", "sheep", "wolf", "shark")
HISTORY_PAGE_SIZE = 100
# Bars per call; longer requests walk back page by page (``_next_bars_page``).
BARS_PAGE_CAP = 250


def _next_history_page(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """price-history pages from 0; stop when the pages seen cover ``total``."""
    if not isinstance(body, dict):
        return None
    page, size, total = int(params.get("page", 0)), int(params.get("size", 0)), body.get("total")
    rows = body.get("data")
    if not size or not isinstance(total, int) or not isinstance(rows, list) or not rows:
        return None
    return {**params, "page": page + 1} if (page + 1) * size < total else None


def _bar_epoch(row: Any) -> int | None:
    raw = row.get("tradingDate") if isinstance(row, dict) else None
    if not isinstance(raw, str):
        return None
    try:
        return int(datetime.fromisoformat(raw.replace("Z", "+00:00")).timestamp())
    except ValueError:
        return None


def _next_bars_page(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """bars return at most ``BARS_PAGE_CAP`` bars whatever ``countBack`` asks; walk back with ``to``.

    bars trả tối đa ``BARS_PAGE_CAP`` nến dù ``countBack`` lớn hơn; lùi tiếp bằng ``to``.
    """
    rows = body.get("data") if isinstance(body, dict) else None
    wanted = int(params.get("countBack") or 0)
    if not isinstance(rows, list) or len(rows) < BARS_PAGE_CAP or wanted <= len(rows):
        return None
    candidates = [ts for ts in (_bar_epoch(rows[0]), _bar_epoch(rows[-1])) if ts is not None]
    if not candidates:
        return None
    earliest = min(candidates)
    if earliest >= int(params.get("to") or 0):
        return None
    return {**params, "to": earliest - 1, "countBack": wanted - len(rows)}


OPS = {
    op.name: op
    for op in (
        Op("stock/market-symbol", "/tartarus/v1/tickerCommons", f"{DOC}/stock/market-symbol/"),
        Op("stock/foreign-room", "/tartarus/v1/tickerSnaps", f"{DOC}/stock/foreign-room/"),
        Op("stock/put-through", "/tartarus/v1/putThroughSnaps", f"{DOC}/stock/put-through/"),
        Op(
            "stock/price-history",
            "/nyx/v1/intraday/{ticker}/his/paging",
            f"{DOC}/stock/price-history/",
            next_page=_next_history_page,
            page_params=lambda page: {"page": page},
            max_pages=200,
        ),
        Op(
            "stock/supply-demand-intraday",
            "/nyx/v1/intraday/{ticker}/bsa-ext",
            f"{DOC}/stock/supply-demand-intraday/",
        ),
        Op("stock/supply-demand-daily", "/nyx/v1/intraday/{ticker}/bsa", f"{DOC}/stock/supply-demand-daily/"),
        Op(
            "stock/supply-demand-monthly",
            "/nyx/v1/intraday/{ticker}/bsa-month",
            f"{DOC}/stock/supply-demand-monthly/",
        ),
        Op("stock/securities-info", "/ananke/v1/securities", f"{DOC}/stock/securities-info/"),
        Op("stock/bars", "/nyx/v2/stock/bars", f"{DOC}/stock/bars/", next_page=_next_bars_page),
        Op(
            "stock/bars-long-term",
            "/nyx/v2/stock/bars-long-term",
            f"{DOC}/stock/bars-long-term/",
            next_page=_next_bars_page,
        ),
        Op("derivative/market-symbol", "/tartarus/v1/derivatives", f"{DOC}/derivative/market-symbol/"),
    )
}


def _basket(name: str | int) -> int:
    if isinstance(name, int):
        return name
    code = BASKETS.get(name.upper())
    if code is None:
        raise InputError(f"Rổ {name!r} không có. Có: {', '.join(BASKETS)}", source=NAME)
    return code


def _trading_days(start_dt: datetime, end_dt: datetime) -> int:
    total = max((end_dt.date() - start_dt.date()).days + 1, 1)
    weeks, rem = divmod(total, 7)
    start_wd = start_dt.date().weekday()
    weekdays = weeks * 5 + sum(1 for offset in range(rem) if (start_wd + offset) % 7 < 5)
    return max(weekdays, 1)


def _ohlc(
    symbol: str,
    *,
    start: Any = None,
    end: Any = None,
    interval: str = "1D",
    limit: int | None = None,
    asset: str = "equity",
) -> Plan:
    start_dt, end_dt = default_range(start, end)
    days = max((end_dt - start_dt).days + 1, 1)
    asset = vocabulary.canonical("asset", asset)
    interval = vocabulary.canonical("interval", interval)
    params: dict[str, Any] = {
        "ticker": vocabulary.symbol(NAME, symbol) if asset == "index" else symbol.upper(),
        "type": vocabulary.native(NAME, "asset", asset),
        "to": epoch(end_dt),
    }
    resolution = vocabulary.native(NAME, "interval", interval)
    if interval in LONG_TERM:
        params["resolution"] = resolution
        params["countBack"] = limit or {"1D": days, "1W": days // 7 + 1, "1M": days // 28 + 1}[interval]
        return Plan("stock/bars-long-term", params)
    params["resolution"] = resolution
    params["countBack"] = limit or _trading_days(start_dt, end_dt) * _BARS_PER_DAY[resolution]
    return Plan("stock/bars", params)


def _quote(symbol: str) -> Plan:
    """Price board row of a symbol: prices, 3 levels of bid and offer, foreign flow, room (5.1).

    Dòng bảng giá của một mã: giá, 3 bước giá mua bán, khối ngoại, room.
    """
    return Plan("stock/market-symbol", {"tickers": symbol.upper()})


def _trades(symbol: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> Plan:
    """Matched trades of the current or latest session, newest first, paged by 100 (5.5).

    Lịch sử khớp lệnh của phiên hiện tại hoặc gần nhất, mới nhất trước, mỗi trang 100 lệnh.

    The source has no date range: ``start`` and ``end`` are ignored; ``limit`` caps the page size.
    """
    return Plan(
        "stock/price-history",
        {
            "ticker": symbol.upper(),
            "page": 0,
            "size": min(limit or HISTORY_PAGE_SIZE, HISTORY_PAGE_SIZE),
            "headIndex": -1,
        },
    )


def _board(*symbols: str, basket: str | int | None = None) -> Plan:
    """Price board of several symbols, or of a basket such as VN30 or HOSE (5.1).

    Bảng giá nhiều mã, hoặc của một rổ như VN30, HOSE.
    """
    if bool(symbols) == (basket is not None):
        raise InputError("Cần danh sách mã hoặc basket, không cả hai", source=NAME)
    if symbols:
        return Plan("stock/market-symbol", {"tickers": ",".join(s.upper() for s in symbols)})
    return Plan("stock/market-symbol", {"index": _basket(basket)})  # type: ignore[arg-type]


def _foreign_room(basket: str | int = "HOSE") -> Plan:
    """Foreign room snapshot of a basket, with price board fields (5.3).

    Ảnh chụp room khối ngoại của một rổ, kèm các trường bảng giá.
    """
    return Plan("stock/foreign-room", {"index": _basket(basket)})


def _put_through(floor: str = "HOSE") -> Plan:
    """Put-through adverts and matches of a floor: HOSE, HNX or UPCOM (5.4).

    Giao dịch thoả thuận (chào mua, chào bán, đã khớp) của một sàn.
    """
    code = FLOORS.get(floor.upper())
    if code is None:
        raise InputError(f"Sàn {floor!r} không có. Có: {', '.join(FLOORS)}", source=NAME)
    return Plan("stock/put-through", {"floor": code})


def _supply_demand(symbol: str, *, window: str = "intraday", investor: str = "all") -> Plan:
    """Active buy and sell flow by investor size: ``intraday`` (15m), ``daily`` or ``monthly`` (5.6-5.8).

    Cung cầu chủ động theo nhóm nhà đầu tư: trong phiên (15 phút), theo ngày, theo tháng.
    """
    if investor not in INVESTOR_TYPES:
        raise InputError(f"investor phải là một trong {', '.join(INVESTOR_TYPES)}", source=NAME)
    ticker = symbol.upper()
    if window == "intraday":
        return Plan(
            "stock/supply-demand-intraday",
            {"ticker": ticker, "timeWindow": 15, "tWindow": 15, "type": investor},
        )
    if window == "daily":
        return Plan("stock/supply-demand-daily", {"ticker": ticker, "type": investor})
    if window == "monthly":
        return Plan("stock/supply-demand-monthly", {"ticker": ticker, "timeWindow": "1M", "type": investor})
    raise InputError("window phải là intraday, daily hoặc monthly", source=NAME)


def _securities(symbol: str | None = None, *, filter: str | None = None, fields: str = "all") -> Plan:
    """Security master data: issuer, type, listing, warrant terms, margin limits, room (5.11).

    Thông tin chứng khoán: tổ chức phát hành, loại, niêm yết, điều khoản chứng quyền, margin, room.
    """
    if bool(symbol) == bool(filter):
        # The endpoint takes ``filter``; ``symbol`` is a shortcut that builds it.
        raise InputError(
            "Cần đúng một trong hai: symbol hoặc filter (ví dụ filter='symbol=TCB')", source=NAME
        )
    return Plan(
        "stock/securities-info",
        {
            "fields": fields,
            "filter": f"symbol={symbol.upper()}" if symbol else filter,
        },
    )


def _derivatives() -> Plan:
    """Price board of every derivative contract (7.1).

    Bảng giá mọi hợp đồng phái sinh.
    """
    return Plan("derivative/market-symbol", {})


VERBS: dict[str, Builder] = {"ohlc": _ohlc, "quote": _quote, "trades": _trades}

EXTRAS: dict[str, Builder] = {
    "board": _board,
    "foreign_room": _foreign_room,
    "put_through": _put_through,
    "supply_demand": _supply_demand,
    "securities": _securities,
    "derivatives": _derivatives,
}

def _samples(symbol: str, index: str) -> list[tuple[str, Plan]]:
    return [("foreign_room_vn30", Plan("stock/foreign-room", {"index": BASKETS["VN30"]}))]


SPEC = SourceSpec(
    name=NAME,
    title="TCBS iFlash Open API",
    base_url=BASE_URL,
    hosts=frozenset({"openapi.tcbs.com.vn"}),
    credentials=(CredentialField("api_key", "API_KEY", label="API Key"),),
    auth=lambda source: OtpTokenAuth(
        source,
        login_path="/gaia/v1/oauth2/openapi/token",
        login_body=_login_body,
        parse_token=_parse_token,
    ),
    ops=OPS,
    verbs=VERBS,
    extras=EXTRAS,
    api_version="v1.0.0",
    # Every TCBS doc lists this header.
    static_headers=lambda: {"Content-Type": "application/json"},
    # Well under the documented limit, so paging never trips 429.
    rate_per_second=2,
    check=lambda: Plan(
        "stock/bars-long-term",
        {
            "ticker": "VNINDEX",
            "type": "index",
            "resolution": "D",
            "to": int(time.time()),
            "countBack": 1,
        },
    ),
    docs=DOC,
    samples=_samples,
)
