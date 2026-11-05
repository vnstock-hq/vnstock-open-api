"""FHSC (Finhay Securities) Open API, Tier 1: a fixed key in ``X-FH-APIKEY``, no signature, no session.

FHSC Open API, Tier 1: khoá cố định trong header ``X-FH-APIKEY``, không ký, không phiên.

Only public market data (``/market/**`` and the exchange session endpoint). Tier 2 (user, accounts,
portfolio, orders) needs the secret and HMAC signing and is out of scope, so the secret is
never read. Not declared by default: analyst reports, full-text news and source-computed technicals.
Docs: https://developers.fhsc.com.vn/introduction (spec 0.2.0-preview.3, still preview)
Surveyed from the docs (28/10/2026).
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.auth import ApiKeyHeaderAuth
from vnstock_oapi.core.credentials import CredentialField
from vnstock_oapi.core.source import Builder, Op, Plan, SourceSpec
from vnstock_oapi.core.timeutil import default_range, epoch
from vnstock_oapi.errors import (
    AuthError,
    ClockSkewError,
    InputError,
    OapiError,
    RateLimitError,
    SourceError,
    UnsupportedError,
)

NAME = "fhsc"
BASE_URL = "https://open-api.fhsc.com.vn"
DOC = "https://developers.fhsc.com.vn"
REF = f"{DOC}/api-reference"
SPEC_VERSION = "0.2.0-preview.3"

# Intervals, asset segments and exchanges: spec/sources/fhsc/mapping.toml, through core.vocabulary.
STATEMENTS = ("income-statement", "balance-sheet", "cash-flow")
PAGE_SIZE = 50  # the largest page every paged operation accepts


def _next_page(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """Four paging styles, all on ``page``.

    Bốn kiểu phân trang, cùng tham số ``page``:

    - ``data`` is a bare list, empty past the last page (financial statements, analysis);
    - ``data.next_page`` (calendar);
    - ``data.pagination.page``/``total_pages`` (events, insider trades);
    - ``data.page``/``page_size``/``total`` (indices, sectors, screener, foreign room history).
    """
    data = body.get("data") if isinstance(body, dict) else None
    current = int(params.get("page") or 1)
    if isinstance(data, list):
        return {**params, "page": current + 1} if data else None
    if not isinstance(data, dict):
        return None
    following = data.get("next_page")
    if isinstance(following, int) and following > current:
        return {**params, "page": following}
    nested = data.get("pagination")
    if isinstance(nested, dict):
        page, pages = nested.get("page"), nested.get("total_pages")
        if not (isinstance(page, int) and isinstance(pages, int)):
            return None
        return {**params, "page": page + 1} if page < pages else None
    page, size, total = data.get("page"), data.get("page_size"), data.get("total")
    if not (isinstance(page, int) and isinstance(size, int) and isinstance(total, int)) or not size:
        return None
    return {**params, "page": page + 1} if page * size < total else None


def _page(page: int) -> dict[str, Any]:
    return {"page": page}


# (operation, docs page under api-reference/, paged). The operation is the path without "/market/".
# Docs page slugs are FHSC's own URLs, kept verbatim.
_TABLE = (
    ("calendar", "kinh-te-vi-mo/lich-su-kien-thi-truong", True),
    ("company-financial/analysis", "phan-tich-co-ban/phan-tich-tai-chinh-doanh-nghiep-theo-ky", True),
    ("company-financial/overview", "phan-tich-co-ban/chi-so-tai-chinh-tong-quan-cua-doanh-nghiep", False),
    ("financial-data/bank-interest-rates", "kinh-te-vi-mo/lai-suat-tien-gui-ngan-hang", False),
    ("financial-statement/statement", "phan-tich-co-ban/bao-cao-tai-chinh", True),
    ("indices", "bang-gia-thi-truong/bang-so-sanh-cac-chi-so", True),
    ("indices/{symbol}/breadth", "bang-gia-thi-truong/do-rong-theo-chi-so-phien-hien-tai", False),
    ("indices/{symbol}/constituents", "bang-gia-thi-truong/ro-cau-phan-chi-so", True),
    ("indices/{symbol}/trading/foreign", "bang-gia-thi-truong/khoi-ngoai-theo-chi-so-phien-gan-nhat", False),
    ("indices/{symbol}/trading/foreign/history", "bang-gia-thi-truong/lich-su-khoi-ngoai-theo-chi-so", False),
    ("macro/indicators", "kinh-te-vi-mo/danh-muc-chi-tieu-vi-mo", False),
    ("macro/indicators/{indicator}", "kinh-te-vi-mo/gia-tri-chi-tieu-vi-mo", False),
    ("quotes/bonds", "bang-gia-thi-truong/danh-muc-trai-phieu", False),
    ("quotes/bonds/{symbol}", "bang-gia-thi-truong/gia-realtime-trai-phieu", False),  # brand-scan: ignore
    ("quotes/bonds/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-trai-phieu", False),
    ("quotes/bonds/{symbol}/order-book", "bang-gia-thi-truong/so-lenh-bid-ask-trai-phieu", False),
    ("quotes/commodities", "bang-gia-thi-truong/danh-muc-hang-hoa", False),
    ("quotes/commodities/{symbol}", "bang-gia-thi-truong/gia-realtime-hang-hoa", False),  # brand-scan: ignore
    (
        "quotes/commodities/{symbol}/dealer-products",
        "bang-gia-thi-truong/san-pham-dealer-noi-dia-hang-hoa",
        False,
    ),
    (
        "quotes/commodities/{symbol}/dealer-products/{code}",
        "bang-gia-thi-truong/bao-gia-1-san-pham-dealer",
        False,
    ),
    (
        "quotes/commodities/{symbol}/dealer-products/{code}/history",
        "bang-gia-thi-truong/lich-su-gia-san-pham-dealer",
        False,
    ),
    ("quotes/commodities/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-hang-hoa", False),
    ("quotes/cryptos", "bang-gia-thi-truong/danh-muc-crypto", False),
    ("quotes/cryptos/{symbol}", "bang-gia-thi-truong/gia-realtime-crypto", False),  # brand-scan: ignore
    ("quotes/cryptos/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-crypto", False),
    ("quotes/etfs", "bang-gia-thi-truong/danh-muc-etf", False),
    ("quotes/etfs/{symbol}", "bang-gia-thi-truong/gia-realtime-etf", False),  # brand-scan: ignore
    ("quotes/etfs/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-etf", False),
    ("quotes/etfs/{symbol}/order-book", "bang-gia-thi-truong/so-lenh-bid-ask-etf", False),
    ("quotes/forex", "bang-gia-thi-truong/danh-muc-forex", False),
    ("quotes/forex/{symbol}", "bang-gia-thi-truong/gia-realtime-forex", False),  # brand-scan: ignore
    ("quotes/forex/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-forex", False),
    ("quotes/funds", "bang-gia-thi-truong/danh-muc-quy-mo", False),
    ("quotes/funds/{symbol}", "bang-gia-thi-truong/gia-realtime-quy-mo", False),  # brand-scan: ignore
    ("quotes/funds/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-quy-mo", False),
    ("quotes/indices", "bang-gia-thi-truong/danh-muc-chi-so", False),
    ("quotes/indices/{symbol}", "bang-gia-thi-truong/gia-realtime-chi-so", False),  # brand-scan: ignore
    ("quotes/indices/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-chi-so", False),
    ("quotes/stocks", "bang-gia-thi-truong/danh-muc-co-phieu", False),
    ("quotes/stocks/{symbol}", "bang-gia-thi-truong/gia-realtime-co-phieu", False),  # brand-scan: ignore
    ("quotes/stocks/{symbol}/history", "bang-gia-thi-truong/lich-su-gia-co-phieu", False),
    ("quotes/stocks/{symbol}/order-book", "bang-gia-thi-truong/so-lenh-bid-ask-co-phieu", False),
    (
        "quotes/stocks/{symbol}/order-book/history",
        "bang-gia-thi-truong/lich-su-so-lenh-co-phieu-trong-phien",
        False,
    ),
    ("sectors", "bang-gia-thi-truong/bang-so-sanh-cac-nganh", True),
    ("sectors/{slug}/breadth", "bang-gia-thi-truong/do-rong-nganh-phien-hien-tai", False),
    ("sectors/{slug}/constituents", "bang-gia-thi-truong/danh-sach-ma-trong-nganh", True),
    ("sectors/{slug}/performance", "bang-gia-thi-truong/chi-so-nganh-phien-hien-tai", False),
    ("sectors/{slug}/performance/history", "bang-gia-thi-truong/lich-su-chi-so-nganh", False),
    ("sectors/{slug}/trading/foreign", "bang-gia-thi-truong/khoi-ngoai-theo-nganh-phien-gan-nhat", False),
    ("sectors/{slug}/trading/foreign/history", "bang-gia-thi-truong/lich-su-khoi-ngoai-theo-nganh", False),
    ("stocks", "bang-gia-thi-truong/sang-loc-co-phieu-screener", True),
    ("stocks/{symbol}/events", "tin-tuc-su-kien/su-kien-quyen-cua-stock", True),
    ("stocks/{symbol}/listing", "phan-tich-co-ban/thong-tin-niem-yet", False),
    ("stocks/{symbol}/ownership", "phan-tich-co-ban/co-cau-so-huu", False),
    ("stocks/{symbol}/ownership/foreign-room", "bang-gia-thi-truong/room-ngoai-phien-gan-nhat", False),
    ("stocks/{symbol}/ownership/foreign-room/history", "bang-gia-thi-truong/lich-su-room-ngoai", True),
    ("stocks/{symbol}/profile", "phan-tich-co-ban/ho-so-doanh-nghiep", False),
    ("stocks/{symbol}/trading", "bang-gia-thi-truong/giao-dich-tong-phien-gan-nhat", False),
    ("stocks/{symbol}/trading/foreign", "bang-gia-thi-truong/giao-dich-khoi-ngoai-phien-gan-nhat", False),
    ("stocks/{symbol}/trading/foreign/history", "bang-gia-thi-truong/lich-su-giao-dich-khoi-ngoai", False),
    ("stocks/{symbol}/trading/history", "bang-gia-thi-truong/lich-su-giao-dich-tong", False),
    ("stocks/{symbol}/trading/insider", "tin-tuc-su-kien/giao-dich-noi-bo-co-dong-lon", True),
    (
        "stocks/{symbol}/trading/orders",
        "bang-gia-thi-truong/thong-ke-lenh-mua-ban-chu-dong-phien-gan-nhat",
        False,
    ),
    ("stocks/{symbol}/trading/orders/history", "bang-gia-thi-truong/lich-su-lenh-mua-ban-chu-dong", False),
    ("stocks/{symbol}/trading/proprietary", "bang-gia-thi-truong/giao-dich-tu-doanh-phien-gan-nhat", False),
    ("stocks/{symbol}/trading/proprietary/history", "bang-gia-thi-truong/lich-su-giao-dich-tu-doanh", False),
    ("trading/market/session", "phien-giao-dich/trang-thai-phien-giao-dich", False),
)
# Paths that are not "/market/" + operation.
_PATHS = {
    "financial-statement/statement": "/market/v2/financial-statement/statement",
    "trading/market/session": "/trading/market/session",
}

OPS = {
    name: Op(
        name,
        _PATHS.get(name, f"/market/{name}"),
        f"{REF}/{doc}",
        next_page=_next_page if paged else None,
        page_params=_page if paged else None,
    )
    for name, doc, paged in _TABLE
}

# error_code → error family (docs "Error codes", 28/10/2026). Unknown codes become SourceError.
ERRORS: dict[str, type[OapiError]] = {
    "AUTH_API_KEY_INVALID": AuthError,
    "AUTH_SIGNATURE_INVALID": AuthError,
    "AUTH_NONCE_REUSED": AuthError,
    "AUTH_BODYHASH_MISMATCH": AuthError,
    "AUTH_TIMESTAMP_OUT_OF_RANGE": ClockSkewError,
    "INVALID_PARAMETER": InputError,
    "MISSING_REQUIRED_PARAMETER": InputError,
    "MUTUALLY_EXCLUSIVE_PARAMETER": InputError,
    "ORDER_BOOK_NOT_SUPPORTED": UnsupportedError,
    "DF001": InputError,
    "400": InputError,
    "RATE_LIMIT_EXCEEDED": RateLimitError,
}


def body_error(body: Any) -> tuple[type[OapiError], Any, str] | None:
    """Every body carries ``error_code``: ``"0"`` on success, a name otherwise.

    Mọi body có ``error_code``: ``"0"`` khi thành công, tên lỗi khi thất bại.
    """
    if not isinstance(body, dict) or "error_code" not in body:
        return None
    code = body.get("error_code")
    if code in ("0", 0, None):
        return None
    return ERRORS.get(str(code), SourceError), code, str(body.get("message") or "")


def _day(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _period(value: datetime) -> str:
    # Macro series take periods (2026, 2026-Q1, 2026-05), not days.
    return value.strftime("%Y-%m")


def _symbol(symbol: str) -> str:
    if not symbol or not symbol.strip():
        raise InputError("Cần mã", source=NAME)
    return symbol.strip().upper()


def _ohlc(
    symbol: str,
    *,
    start: Any = None,
    end: Any = None,
    interval: str = "1D",
    limit: int | None = None,
    asset: str = "equity",
) -> Plan:
    """Bars of a stock, index, ETF, fund, bond, commodity, crypto or forex pair; times are Unix seconds.

    Nến giá của cổ phiếu, chỉ số, ETF, quỹ mở, trái phiếu, hàng hoá, tiền mã hoá, ngoại tệ.

    ``limit`` is not supported by the source: pass ``start`` and ``end`` instead.
    """
    if limit is not None:
        raise InputError("FHSC không nhận limit cho nến; dùng start, end", source=NAME)
    asset = vocabulary.canonical("asset", asset)
    segment = vocabulary.native(NAME, "asset", asset)
    start_dt, end_dt = default_range(start, end)
    params: dict[str, Any] = {"symbol": _symbol(symbol), "from": epoch(start_dt), "to": epoch(end_dt)}
    if asset != "fund":
        params["resolution"] = vocabulary.native(NAME, "interval", interval)
    return Plan(f"quotes/{segment}/{{symbol}}/history", params)


def _quote(symbol: str) -> Plan:
    """Latest quote of a stock in the current session.

    Báo giá mới nhất trong phiên của một cổ phiếu.
    """
    return Plan("quotes/stocks/{symbol}", {"symbol": _symbol(symbol)})


def _index(name: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> Plan:
    """Daily bars of an index (VNINDEX, VN30, HNXINDEX, HNX30, UPCOMINDEX…).

    Nến ngày của một chỉ số.
    """
    return _ohlc(name, start=start, end=end, limit=limit, asset="index")


def _foreign(symbol: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> Plan:
    """Daily foreign buy and sell of a stock, newest session first.

    Mua bán khối ngoại theo ngày của một cổ phiếu, phiên mới nhất trước.
    """
    if limit is not None:
        raise InputError("FHSC không nhận limit; dùng start, end", source=NAME)
    start_dt, end_dt = default_range(start, end)
    return Plan(
        "stocks/{symbol}/trading/foreign/history",
        {"symbol": _symbol(symbol), "from": _day(start_dt), "to": _day(end_dt), "resolution": "1D"},
    )


def _symbols() -> Plan:
    """Stock catalog (symbol and name; the catalog has no exchange column).

    Danh mục cổ phiếu (mã, tên; danh mục không có cột sàn).
    """
    return Plan("quotes/stocks", {})


def _events(symbol: str | None = None, *, start: Any = None, end: Any = None) -> Plan:
    """Corporate actions of a stock, filtered on the ex-date.

    Sự kiện quyền của một cổ phiếu, lọc theo ngày giao dịch không hưởng quyền.
    """
    if not symbol:
        raise InputError("FHSC cần mã để lấy sự kiện quyền", source=NAME)
    params: dict[str, Any] = {"symbol": _symbol(symbol), "page": 1, "page_size": PAGE_SIZE}
    if start is not None or end is not None:
        start_dt, end_dt = default_range(start, end)
        params.update({"from": _day(start_dt), "to": _day(end_dt)})
    return Plan("stocks/{symbol}/events", params)


def _session(exchange: str = "HOSE") -> Plan:
    """Current session of an exchange (OPEN, PROGRESS, BREAK, PRE_CLOSED, CLOSED…).

    Phiên hiện tại của một sàn.
    """
    return Plan("trading/market/session", {"exchange": vocabulary.native(NAME, "exchange", exchange.upper())})


def _order_book(symbol: str) -> Plan:
    """Order book of a stock. Not every symbol has one (``UnsupportedError``).

    Sổ lệnh của một cổ phiếu. Không phải mã nào cũng có.
    """
    return Plan("quotes/stocks/{symbol}/order-book", {"symbol": _symbol(symbol)})


def _daily(name: str, *, start: Any = None, end: Any = None) -> Plan:
    start_dt, end_dt = default_range(start, end)
    return Plan(name, {"from": _day(start_dt), "to": _day(end_dt), "resolution": "1D"})


def _trading(symbol: str, *, start: Any = None, end: Any = None) -> Plan:
    """Daily trading statistics (matched, put-through, totals), newest first; up to 5 years per call.

    Thống kê giao dịch theo ngày (khớp lệnh, thoả thuận, tổng), mới nhất trước; tối đa 5 năm mỗi lần.
    """
    plan = _daily("stocks/{symbol}/trading/history", start=start, end=end)
    return Plan(plan.operation, {"symbol": _symbol(symbol), **plan.params})


def _proprietary(symbol: str, *, start: Any = None, end: Any = None) -> Plan:
    """Daily proprietary (securities companies' own account) trading of a stock.

    Giao dịch tự doanh theo ngày của một cổ phiếu.
    """
    plan = _daily("stocks/{symbol}/trading/proprietary/history", start=start, end=end)
    return Plan(plan.operation, {"symbol": _symbol(symbol), **plan.params})


def _foreign_room(symbol: str, *, start: Any = None, end: Any = None) -> Plan:
    """Foreign ownership room: today's snapshot, or daily history when ``start`` or ``end`` is given.

    Room khối ngoại: ảnh chụp hôm nay, hoặc lịch sử theo ngày khi có ``start`` hay ``end``.
    """
    if start is None and end is None:
        return Plan("stocks/{symbol}/ownership/foreign-room", {"symbol": _symbol(symbol)})
    plan = _daily("stocks/{symbol}/ownership/foreign-room/history", start=start, end=end)
    return Plan(plan.operation, {"symbol": _symbol(symbol), **plan.params, "page": 1, "page_size": PAGE_SIZE})


def _order_stats(symbol: str, *, history: bool = False) -> Plan:
    """Matched volume by aggressor side of a stock (market-wide statistics, nothing of the user's).

    Khối lượng khớp theo bên chủ động mua, bán của một cổ phiếu (số liệu toàn thị trường).
    """
    name = "stocks/{symbol}/trading/orders/history" if history else "stocks/{symbol}/trading/orders"
    return Plan(name, {"symbol": _symbol(symbol)})


def _insider(symbol: str, *, start: Any = None, end: Any = None) -> Plan:
    """Insider and related-party trades of a stock.

    Giao dịch nội bộ và người liên quan của một cổ phiếu.
    """
    params: dict[str, Any] = {"symbol": _symbol(symbol), "page": 1, "page_size": PAGE_SIZE}
    if start is not None or end is not None:
        start_dt, end_dt = default_range(start, end)
        params.update({"from": _day(start_dt), "to": _day(end_dt)})
    return Plan("stocks/{symbol}/trading/insider", params)


def _company(symbol: str, *, part: str = "profile") -> Plan:
    """Company data: ``profile``, ``listing``, ``ownership``, ``overview`` (financial overview).

    Thông tin doanh nghiệp: hồ sơ, niêm yết, cơ cấu sở hữu, tổng quan tài chính.
    """
    names = {
        "profile": "stocks/{symbol}/profile",
        "listing": "stocks/{symbol}/listing",
        "ownership": "stocks/{symbol}/ownership",
        "overview": "company-financial/overview",
    }
    if part not in names:
        raise InputError(f"part phải là một trong {', '.join(names)}", source=NAME)
    return Plan(names[part], {"symbol": _symbol(symbol)})


def _financials(symbol: str, *, statement: str = "income-statement", period: str = "year") -> Plan:
    """Financial statements: ``income-statement``, ``balance-sheet``, ``cash-flow``; by year or by quarter.

    Báo cáo tài chính: kết quả kinh doanh, cân đối kế toán, lưu chuyển tiền tệ; theo năm hoặc quý.

    ``period`` is the shared ``report_period`` (``year``, ``quarter``, or an alias such as ``annual``).
    """
    if statement not in STATEMENTS:
        raise InputError(f"statement phải là một trong {', '.join(STATEMENTS)}", source=NAME)
    return Plan(
        "financial-statement/statement",
        {
            "symbol": _symbol(symbol),
            "type": statement,
            "period": vocabulary.native(NAME, "report_period", period),
            "page": 1,
        },
    )


def _index_members(name: str) -> Plan:
    """Constituents of an index with their weight.

    Thành phần của một chỉ số kèm tỉ trọng.
    """
    return Plan("indices/{symbol}/constituents", {"symbol": _symbol(name), "page": 1, "page_size": PAGE_SIZE})


def _indices(exchange: str | None = None) -> Plan:
    """Indices with their latest value, optionally of one exchange.

    Danh sách chỉ số kèm giá trị mới nhất, có thể lọc theo sàn.
    """
    return Plan("indices", {"exchange": exchange.upper()} if exchange else {})


def _breadth(name: str) -> Plan:
    """Advances, declines and unchanged of an index in the current session.

    Số mã tăng, giảm, đứng giá của một chỉ số trong phiên.
    """
    return Plan("indices/{symbol}/breadth", {"symbol": _symbol(name)})


def _macro(indicator: str | None = None, *, start: Any = None, end: Any = None) -> Plan:
    """Macro indicators: the list when ``indicator`` is None, else one series (newest period first).

    Chỉ tiêu vĩ mô: danh sách khi không có ``indicator``, hoặc chuỗi của một chỉ tiêu (kỳ mới nhất trước).

    Without ``start`` and ``end`` the source returns only the latest period.
    """
    if indicator is None:
        return Plan("macro/indicators", {})
    params: dict[str, Any] = {"indicator": indicator}
    if start is not None or end is not None:
        start_dt, end_dt = default_range(start, end)
        params.update({"from": _period(start_dt), "to": _period(end_dt)})
    return Plan("macro/indicators/{indicator}", params)


def _catalog(asset: str = "equity") -> Plan:
    """Catalog of an asset class: stock, index, etf, fund, bond, commodity, crypto, forex.

    Danh mục một loại tài sản.
    """
    return Plan(f"quotes/{vocabulary.native(NAME, 'asset', asset)}", {})


VERBS: dict[str, Builder] = {
    "ohlc": _ohlc,
    "quote": _quote,
    "index": _index,
    "foreign": _foreign,
    "symbols": _symbols,
    "events": _events,
}

EXTRAS: dict[str, Builder] = {
    "session": _session,
    "order_book": _order_book,
    "trading": _trading,
    "proprietary": _proprietary,
    "foreign_room": _foreign_room,
    "order_stats": _order_stats,
    "insider": _insider,
    "company": _company,
    "financials": _financials,
    "index_members": _index_members,
    "indices": _indices,
    "breadth": _breadth,
    "macro": _macro,
    "catalog": _catalog,
}


def _samples(symbol: str, index: str) -> list[tuple[str, Plan]]:
    return [
        ("session", Plan("trading/market/session", {"exchange": "HOSE"})),
        ("company_profile", Plan("stocks/{symbol}/profile", {"symbol": symbol})),
    ]


SPEC = SourceSpec(
    name=NAME,
    title="FHSC Open API",
    base_url=BASE_URL,
    hosts=frozenset({"open-api.fhsc.com.vn"}),
    credentials=(CredentialField("api_key", "API_KEY", label="API Key"),),
    auth=lambda source: ApiKeyHeaderAuth(source, header="X-FH-APIKEY"),
    ops=OPS,
    verbs=VERBS,
    extras=EXTRAS,
    api_version=SPEC_VERSION,
    # The docs give no number (it depends on the account tier): stay well under a minute's quota.
    rate_per_second=0.6,
    check=lambda: Plan("trading/market/session", {"exchange": "HOSE"}),
    docs=f"{DOC}/introduction",
    body_error=body_error,
    samples=_samples,
)
