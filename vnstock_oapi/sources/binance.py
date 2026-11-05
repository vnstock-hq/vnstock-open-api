"""Binance spot public market data on ``data-api.binance.vision``: no key, no session, GET only.

Dữ liệu thị trường giao ngay công khai của Binance trên ``data-api.binance.vision``: không khoá, không phiên.

Binance documents ``https://data-api.binance.vision`` as the base endpoint for APIs that only send public
market data (rest-api.md "General API Information"; faqs/market_data_only.md lists the endpoints it serves).
Only that host is allowed: the main API hosts answer ``robots.txt`` with ``Disallow: /`` and are never
called. No API key is read or sent (auth kind ``public``).

Declared: exchange info, server time and ping, price tickers (last, 24 hours, rolling window, trading
day, best bid/ask), average price, klines and UI klines, order book depth, recent and aggregate trades.
Not declared: anything that needs a key or touches an account (orders, account, user data stream, sapi),
``historicalTrades``, ``historicalBlockTrades`` and ``referencePrice`` (not served on this host).
``ticker/tradingDay`` answers 200 here although the market-data-only page does not list it.

Limits are per IP: ``REQUEST_WEIGHT`` 6000 a minute, ``RAW_REQUESTS`` 61000 every 5 minutes. A 429 must
be backed off; repeated violations end in an automated IP ban (418), 2 minutes to 3 days. Errors come as
``{"code": -1121, "msg": "Invalid symbol."}`` with a 4xx status. Times are UTC epoch milliseconds.
Docs: https://developers.binance.com/docs/binance-spot-api-docs/rest-api (market data endpoints, limits),
https://developers.binance.com/docs/binance-spot-api-docs/errors,
https://github.com/binance/binance-spot-api-docs (rest-api.md, errors.md, enums.md, CHANGELOG.md),
surveyed 31/10/2026. Terms: personal use; no redistribution without Binance's written licence.
"""

from __future__ import annotations

import re
from typing import Any

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.auth import PublicAuth
from vnstock_oapi.core.source import Builder, Op, Plan, SourceSpec
from vnstock_oapi.core.timeutil import to_datetime
from vnstock_oapi.errors import AuthError, InputError, OapiError, RateLimitError, SourceError

NAME = "binance"
BASE_URL = "https://data-api.binance.vision/api/v3"
DOC = "https://developers.binance.com/docs/binance-spot-api-docs/rest-api"
MAX_LIMIT = 1000  # klines, uiKlines, trades, aggTrades
MAX_DEPTH = 5000
MAX_PAGES = 20  # klines walked forward from startTime; never further silently
_SYMBOL = re.compile(r"^\S{1,32}$")
_WINDOW = re.compile(r"^([1-9]|[1-5][0-9])m$|^([1-9]|1[0-9]|2[0-3])h$|^[1-7]d$")


def _next_bars(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """Klines walked forward: a full page started at ``startTime`` continues after its last open time.

    Nến đi tiếp: trang đầy tính từ ``startTime`` thì trang sau bắt đầu sau thời điểm mở của nến cuối.
    """
    limit = int(params.get("limit") or 500)
    if "startTime" not in params or not isinstance(body, list) or len(body) < limit:
        return None
    last = body[-1]
    if not isinstance(last, list) or not last:
        return None
    following = int(last[0]) + 1
    end = params.get("endTime")
    if end is not None and following > int(end):
        return None
    return {**params, "startTime": following}


# (operation = path under /api/v3/, anchor on the REST API page, paged)
_TABLE = (
    ("ping", "test-connectivity", False),
    ("time", "check-server-time", False),
    ("exchangeInfo", "exchange-information", False),
    ("depth", "order-book", False),
    ("trades", "recent-trades-list", False),
    ("aggTrades", "compressedaggregate-trades-list", False),
    ("klines", "klinecandlestick-data", True),
    ("uiKlines", "uiklines", True),
    ("avgPrice", "current-average-price", False),
    ("ticker/24hr", "24hr-ticker-price-change-statistics", False),
    ("ticker/tradingDay", "trading-day-ticker", False),
    ("ticker/price", "symbol-price-ticker", False),
    ("ticker/bookTicker", "symbol-order-book-ticker", False),
    ("ticker", "rolling-window-price-change-statistics", False),
)

OPS = {
    name: Op(
        name,
        f"/{name}",
        f"{DOC}#{anchor}",
        next_page=_next_bars if paged else None,
        max_pages=MAX_PAGES,
    )
    for name, anchor, paged in _TABLE
}

# errors.md: -10xx are server or network issues, -11xx request issues.
_RATE = {-1003, -1015}
_AUTH = {-1002, -1022, -2014, -2015}


def body_error(body: Any) -> tuple[type[OapiError], Any, str] | None:
    """Binance reports errors as ``{"code": <negative int>, "msg": "..."}`` (errors.md).

    Binance báo lỗi bằng ``{"code": <số âm>, "msg": "..."}``. -1003: vượt hạn mức, kể cả khi bị cấm IP (418).
    """
    if not isinstance(body, dict) or "msg" not in body:
        return None
    code = body.get("code")
    if not isinstance(code, int) or isinstance(code, bool) or code >= 0:
        return None
    message = str(body.get("msg") or "")
    if code in _RATE:
        return RateLimitError, code, message
    if code in _AUTH:
        return AuthError, code, message
    if -1100 < code <= -1000:
        return SourceError, code, message
    return InputError, code, message


def _symbol(symbol: str) -> str:
    """Binance pair code, upper case without separator: ``BTCUSDT``.

    Mã cặp của Binance, viết hoa, không dấu phân cách.
    """
    text = (symbol or "").strip().upper().replace("/", "")
    if not text or not _SYMBOL.match(text):
        raise InputError(f"Cần mã cặp, ví dụ BTCUSDT (nhận {symbol!r})", source=NAME)
    return text


def _ms(value: Any, *, end: bool = False) -> int:
    return int(to_datetime(value, end=end).timestamp() * 1000)


def _limit(limit: int | None, top: int = MAX_LIMIT) -> dict[str, int]:
    if limit is None:
        return {}
    if not 1 <= int(limit) <= top:
        raise InputError(f"limit phải từ 1 đến {top}", source=NAME)
    return {"limit": int(limit)}


def _window(start: Any, end: Any) -> dict[str, int]:
    out: dict[str, int] = {}
    if start is not None:
        out["startTime"] = _ms(start)
    if end is not None:
        out["endTime"] = _ms(end, end=True)
    if "startTime" in out and "endTime" in out and out["startTime"] > out["endTime"]:
        raise InputError("start phải trước end", source=NAME)
    return out


def _bars(
    operation: str,
    symbol: str,
    start: Any,
    end: Any,
    interval: str,
    limit: int | None,
) -> Plan:
    params: dict[str, Any] = {
        "symbol": _symbol(symbol),
        "interval": vocabulary.native(NAME, "interval", interval),
        **_window(start, end),
        **_limit(limit),
    }
    if "startTime" in params and "limit" not in params:
        params["limit"] = MAX_LIMIT  # walk a range in as few pages as the source allows
    return Plan(operation, params)


def _ohlc(
    symbol: str,
    *,
    start: Any = None,
    end: Any = None,
    interval: str = "1D",
    limit: int | None = None,
    asset: str = "crypto",
) -> Plan:
    """Klines of a spot pair (``BTCUSDT``), UTC open times in milliseconds. No ``start`` and ``end``:
    the latest ``limit`` bars (500 by default). With ``start``: walked forward, 1000 bars a page.
    ``asset`` is accepted for the common signature only, as with FMP: every pair here is spot crypto, and
    a code that is not a Binance pair is refused by the source (-1121 Invalid symbol).

    Nến của một cặp giao ngay. Không có ``start``, ``end``: ``limit`` nến mới nhất. Có ``start``: đi tiếp
    theo trang 1000 nến. ``asset`` chỉ để giữ chữ ký chung; mã không phải cặp Binance thì nguồn từ chối.
    """
    return _bars("klines", symbol, start, end, interval, limit)


def _ui_ohlc(
    symbol: str,
    *,
    start: Any = None,
    end: Any = None,
    interval: str = "1D",
    limit: int | None = None,
) -> Plan:
    """UI klines: same request and shape as klines, values tuned by Binance for chart display.

    Nến cho biểu đồ: cùng tham số và hình dạng như klines, giá trị Binance chỉnh để hiển thị.
    """
    return _bars("uiKlines", symbol, start, end, interval, limit)


def _trades(symbol: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> Plan:
    """Recent trades (up to 1000). With ``start`` or ``end``: aggregate trades in that window.

    Khớp lệnh gần nhất (tối đa 1000). Có ``start`` hoặc ``end``: khớp lệnh gộp trong khoảng đó.
    """
    window = _window(start, end)
    if window:
        return Plan("aggTrades", {"symbol": _symbol(symbol), **window, **_limit(limit)})
    return Plan("trades", {"symbol": _symbol(symbol), **_limit(limit)})


def _agg_trades(
    symbol: str,
    *,
    start: Any = None,
    end: Any = None,
    from_id: int | None = None,
    limit: int | None = None,
) -> Plan:
    """Aggregate trades: by time window, or from an aggregate trade id.

    Khớp lệnh gộp: theo khoảng thời gian, hoặc từ một mã khớp gộp.
    """
    params: dict[str, Any] = {"symbol": _symbol(symbol), **_window(start, end), **_limit(limit)}
    if from_id is not None:
        params["fromId"] = int(from_id)
    return Plan("aggTrades", params)


def _quote(symbol: str) -> Plan:
    """24-hour statistics of a pair: last, open, high, low, volume, best bid and ask.

    Thống kê 24 giờ của một cặp: giá cuối, mở, cao, thấp, khối lượng, giá mua bán tốt nhất.
    """
    return Plan("ticker/24hr", {"symbol": _symbol(symbol)})


def _symbols(*, asset: str = "crypto", status: str | None = None) -> Plan:
    """Exchange information: every spot pair with its status, assets, precisions and filters.

    Thông tin sàn: mọi cặp giao ngay kèm trạng thái, tài sản, độ chính xác và bộ lọc.
    """
    params: dict[str, Any] = {"permissions": vocabulary.native(NAME, "asset", asset)}
    if status:
        params["symbolStatus"] = status.strip().upper()
    return Plan("exchangeInfo", params)


def _last_price(symbol: str) -> Plan:
    """Latest price of a pair.

    Giá mới nhất của một cặp.
    """
    return Plan("ticker/price", {"symbol": _symbol(symbol)})


def _book_ticker(symbol: str) -> Plan:
    """Best bid and ask, with quantities.

    Giá mua, bán tốt nhất kèm khối lượng.
    """
    return Plan("ticker/bookTicker", {"symbol": _symbol(symbol)})


def _order_book(symbol: str, *, limit: int | None = None) -> Plan:
    """Order book depth (100 levels by default, up to 5000; weight grows with ``limit``).

    Sổ lệnh theo mức giá (mặc định 100 mức, tối đa 5000; trọng số tăng theo ``limit``).
    """
    return Plan("depth", {"symbol": _symbol(symbol), **_limit(limit, MAX_DEPTH)})


def _avg_price(symbol: str) -> Plan:
    """Current average price over the last few minutes (``mins`` in the body).

    Giá trung bình hiện tại trong vài phút gần nhất.
    """
    return Plan("avgPrice", {"symbol": _symbol(symbol)})


def _daily_stats(symbol: str, *, time_zone: str | None = None) -> Plan:
    """Statistics of the current trading day (UTC unless ``time_zone`` such as ``7``).

    Thống kê ngày giao dịch hiện tại (theo UTC, trừ khi truyền ``time_zone`` như ``7``).
    """
    params: dict[str, Any] = {"symbol": _symbol(symbol)}
    if time_zone is not None:
        params["timeZone"] = str(time_zone)
    return Plan("ticker/tradingDay", params)


def _rolling_stats(symbol: str, *, window: str = "1d") -> Plan:
    """Price change statistics over a rolling window: ``1m``…``59m``, ``1h``…``23h``, ``1d``…``7d``.

    Thống kê biến động giá trong cửa sổ trượt.
    """
    if not _WINDOW.match(window or ""):
        raise InputError("window phải là 1m…59m, 1h…23h hoặc 1d…7d", source=NAME)
    return Plan("ticker", {"symbol": _symbol(symbol), "windowSize": window})


VERBS: dict[str, Builder] = {
    "ohlc": _ohlc,
    "trades": _trades,
    "quote": _quote,
    "symbols": _symbols,
}

EXTRAS: dict[str, Builder] = {
    "ui_ohlc": _ui_ohlc,
    "agg_trades": _agg_trades,
    "last_price": _last_price,
    "book_ticker": _book_ticker,
    "order_book": _order_book,
    "avg_price": _avg_price,
    "daily_stats": _daily_stats,
    "rolling_stats": _rolling_stats,
}


def _samples(symbol: str, index: str) -> list[tuple[str, Plan]]:
    return [
        ("last_price", _last_price(symbol)),
        ("order_book", _order_book(symbol, limit=5)),
    ]


def _check() -> Plan:
    return Plan("ping", {})


SPEC = SourceSpec(
    name=NAME,
    title="Binance Spot (public market data)",
    base_url=BASE_URL,
    hosts=frozenset({"data-api.binance.vision"}),
    credentials=(),
    auth=PublicAuth,
    ops=OPS,
    verbs=VERBS,
    extras=EXTRAS,
    api_version="v3",
    # 6000 weight a minute per IP; the heaviest declared call here weighs 20 (exchangeInfo).
    rate_per_second=2,
    check=_check,
    docs=DOC,
    body_error=body_error,
    market="global",
    samples=_samples,
)
