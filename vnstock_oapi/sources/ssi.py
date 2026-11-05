"""SSI FastConnect API v3: exchange ``api_key`` + ``api_secret`` for a token, renew with a refresh token.

SSI FastConnect API v3: đổi ``api_key`` + ``api_secret`` lấy token, làm mới bằng refresh token.

No OTP is needed for data. ``SSI_PRIVATE_KEY`` (RSA) only signs orders, so it is never read.
Paths and parameters follow ``ssi-sdk`` 3.3.2 (``constant.py``, ``models/market_data.py``).

SSI allows one session per API key and revokes the whole session when a refresh token is reused,
which is why tokens live in the shared session store (``core/session_store.py``).
"""

from __future__ import annotations

from datetime import date, datetime, timedelta
from typing import Any

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.auth import (
    UNKNOWN_EXPIRY_FALLBACK_S,
    TokenAuth,
    TokenSet,
    fallback_expiry,
    to_epoch_seconds,
)
from vnstock_oapi.core.credentials import CredentialField, Credentials
from vnstock_oapi.core.source import Builder, Op, Plan, SourceSpec
from vnstock_oapi.core.timeutil import VN_TZ, default_range
from vnstock_oapi.errors import InputError, SourceError

NAME = "ssi"
BASE_URL = "https://api.ssi.com.vn"
DOC = "https://developers.ssi.com.vn/docs/api-reference"
PAGE_SIZE = 1000
BOARDS = ("HOSE", "HNX", "UPCOM")

# data-ohlc uses its own name for the UPCoM index.
# Intervals and the data-ohlc index name: spec/sources/ssi/mapping.toml, through core.vocabulary.


def _login_body(creds: Credentials, otp: str | None) -> dict[str, Any]:
    body = {"apiKey": creds["api_key"].reveal(), "apiSecret": creds["api_secret"].reveal()}
    if otp:
        body["otp"] = otp
    return body


def _parse_token(data: Any, now: float) -> TokenSet:
    payload = data.get("data", data) if isinstance(data, dict) else None
    if not isinstance(payload, dict) or not payload.get("accessToken"):
        raise SourceError("SSI không trả accessToken", source=NAME, operation="auth")
    return TokenSet(
        access_token=payload["accessToken"],
        expires_at=fallback_expiry(
            to_epoch_seconds(payload.get("expiresAt")), now, UNKNOWN_EXPIRY_FALLBACK_S
        ),
        refresh_token=payload.get("refreshToken", ""),
        refresh_expires_at=to_epoch_seconds(payload.get("refreshExpiresAt")),
    )


def _rows(body: Any) -> list[Any]:
    if isinstance(body, dict):
        rows = body.get("data")
        return rows if isinstance(rows, list) else []
    return body if isinstance(body, list) else []


def _next_page_index(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    page = int(params.get("pageIndex") or 1)
    pages = body.get("pagesCount") if isinstance(body, dict) else None
    if isinstance(pages, int):  # paged endpoints report it; data-ohlc does not
        return {**params, "pageIndex": page + 1} if page < pages else None
    size = int(params.get("pageSize") or 0)
    if size and len(_rows(body)) >= size:
        return {**params, "pageIndex": page + 1}
    return None


def _ssi_time(value: datetime) -> str:
    return value.strftime("%Y/%m/%d %H:%M:%S")


OPS = {
    op.name: op
    for op in (
        Op(
            "data-ohlc",
            "/api/v3/data/ohlc",
            DOC,
            next_page=_next_page_index,
            page_params=lambda page: {"pageIndex": page},
        ),
        Op("data-indexList", "/api/v3/data/indexList", DOC),
        Op("data-indexSummary", "/api/v3/data/indexSummary", DOC),
        Op("data-securitiesByBoard", "/api/v3/data/securitiesByBoard", DOC),
        Op(
            "data-securitiesSummary",
            "/api/v3/data/securitiesSummary",
            DOC,
            next_page=_next_page_index,
            page_params=lambda page: {"pageIndex": page},
        ),
        Op(
            "data-masterdata",
            "/api/v3/data/masterdata",
            DOC,
            next_page=_next_page_index,
            page_params=lambda page: {"pageIndex": page},
        ),
        Op(
            "data-corporateActions",
            "/api/v3/data/corporateActions",
            DOC,
            next_page=_next_page_index,
            page_params=lambda page: {"pageIndex": page},
        ),
    )
}


# -- dates ----------------------------------------------------------------------

EVENTS_MAX_DAYS = 5  # data-corporateActions: one request spans at most 5 days (ssi-sdk 3.3.2)
SUMMARY_PAGE_SIZE = 100
INDEX_SUMMARY_MAX_DAYS = 400  # one call per day; refuse silly ranges instead of hammering the source


def _day(value: datetime | date) -> str:
    return value.strftime("%Y/%m/%d")


def _last_weekday(now: datetime | None = None) -> date:
    """The latest weekday with a session: today from 09:00 Vietnam time, else the weekday before.

    Ngày làm việc gần nhất đã có phiên: hôm nay từ 09:00, trước đó thì lấy ngày làm việc liền trước.
    """
    now = now or datetime.now(VN_TZ)
    day = now.date() if now.hour >= 9 else now.date() - timedelta(days=1)
    while day.weekday() >= 5:
        day -= timedelta(days=1)
    return day


def _range(start: Any, end: Any, days: int) -> tuple[date, date]:
    if start is None and end is None and days == 0:
        day = _last_weekday()
        return day, day
    start_dt, end_dt = default_range(start, end, days=days)
    return start_dt.date(), end_dt.date()


def _weekdays(first: date, last: date) -> list[date]:
    out = []
    day = first
    while day <= last:
        if day.weekday() < 5:
            out.append(day)
        day += timedelta(days=1)
    return out


def _one_of(name_a: str, a: Any, name_b: str, b: Any) -> None:
    if (a is None) == (b is None):
        raise InputError(f"Cần đúng một trong hai: {name_a} hoặc {name_b}", source=NAME)


# -- common verbs -------------------------------------------------------------------


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
    return Plan(
        "data-ohlc",
        {
            "symbol": vocabulary.symbol(NAME, symbol, "data-ohlc"),
            "from": _ssi_time(start_dt),
            "to": _ssi_time(end_dt),
            "timeFrame": vocabulary.native(NAME, "interval", interval),
            "pageIndex": 1,
            "pageSize": min(limit, PAGE_SIZE) if limit else PAGE_SIZE,
        },
    )


def _summary(
    symbol: str | None = None, *, index: str | None = None, start: Any = None, end: Any = None, days: int = 30
) -> Plan:
    """Daily session summary of a symbol or of an index basket (data-securitiesSummary).

    Tóm tắt phiên theo ngày của một mã hoặc của rổ chỉ số: giá, khớp lệnh, thoả thuận, khối ngoại, room.
    """
    _one_of("symbol", symbol, "index", index)
    first, last = _range(start, end, days)
    return Plan(
        "data-securitiesSummary",
        {
            "symbol": symbol.upper() if symbol else None,
            "index": index.upper() if index else None,
            "from": _day(first),
            "to": _day(last),
            "pageIndex": 1,
            "pageSize": SUMMARY_PAGE_SIZE,
        },
    )


def _quote(symbol: str) -> Plan:
    """Session summaries of a symbol over the last week; the last row is the latest SSI has.

    Tóm tắt các phiên trong tuần gần nhất của một mã; dòng cuối là phiên gần nhất SSI đã có.

    A week back, so the latest session SSI has published is always in range.
    """
    last = _last_weekday()
    return _summary(symbol, start=last - timedelta(days=7), end=last)


def _foreign(symbol: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> Plan:
    """Foreign buy, sell and room per day: the foreign fields of data-securitiesSummary.

    Mua, bán và room khối ngoại theo ngày: các trường khối ngoại của data-securitiesSummary.
    """
    return _summary(symbol, start=start, end=end)


def _index_summary(
    index: str | None = None, *, board: str | None = None, start: Any = None, end: Any = None
) -> list[Plan]:
    """Index summary per trading day: value, breadth, foreign and proprietary flows.

    Tóm tắt chỉ số theo ngày: giá trị, độ rộng thị trường, khối ngoại, tự doanh.

    The source takes one day per call, so a range becomes one call per weekday.
    """
    if index is None and board is None:
        raise InputError("Cần index hoặc board", source=NAME)
    first, last = _range(start, end, 0)
    days = _weekdays(first, last)
    if len(days) > INDEX_SUMMARY_MAX_DAYS:
        raise InputError(f"Tối đa {INDEX_SUMMARY_MAX_DAYS} ngày mỗi lần (mỗi ngày một request)", source=NAME)
    params = {"index": index.upper() if index else None, "board": board.upper() if board else None}
    return [Plan("data-indexSummary", {**params, "tradingDate": _day(day)}) for day in days]


def _index(name: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> list[Plan]:
    return _index_summary(name, start=start, end=end)


def _symbols(board: str | None = None, *, symbol: str | None = None, index: str | None = None) -> list[Plan]:
    """Listed securities with reference fields (data-securitiesByBoard). No filter: all three boards.

    Danh sách mã kèm thông tin tham chiếu. Không lọc thì lấy cả ba sàn HOSE, HNX, UPCOM.
    """
    if board is None and symbol is None and index is None:
        return [Plan("data-securitiesByBoard", {"board": b}) for b in BOARDS]
    return [
        Plan(
            "data-securitiesByBoard",
            {
                "board": board.upper() if board else None,
                "symbol": symbol.upper() if symbol else None,
                "index": index.upper() if index else None,
            },
        )
    ]


def _corporate_actions(
    symbol: str | None = None, *, start: Any = None, end: Any = None, language: str = "vn"
) -> list[Plan]:
    """Corporate actions (dividends, rights, meetings...); ranges split into 5-day calls.

    Sự kiện doanh nghiệp (cổ tức, quyền mua, đại hội...); khoảng dài được chia thành từng đoạn 5 ngày.
    """
    if language not in ("vn", "en"):
        raise InputError("language phải là 'vn' hoặc 'en'", source=NAME)
    first, last = _range(start, end, EVENTS_MAX_DAYS - 1)
    plans = []
    chunk = first
    while chunk <= last:
        chunk_end = min(chunk + timedelta(days=EVENTS_MAX_DAYS - 1), last)
        plans.append(
            Plan(
                "data-corporateActions",
                {
                    "symbol": symbol.upper() if symbol else None,
                    "from": _day(chunk),
                    "to": _day(chunk_end),
                    "language": language,
                    "pageIndex": 1,
                    "pageSize": SUMMARY_PAGE_SIZE,
                },
            )
        )
        chunk = chunk_end + timedelta(days=1)
    return plans


def _events(symbol: str | None = None, *, start: Any = None, end: Any = None) -> list[Plan]:
    return _corporate_actions(symbol, start=start, end=end)


# -- SSI-specific functions -------------------------------------------------------------


def _index_list(board: str | None = None) -> Plan:
    """Indices with their code, name and board (data-indexList).

    Danh sách chỉ số: mã, tên, sàn.
    """
    return Plan("data-indexList", {"board": board.upper() if board else None})


def _index_members(index: str) -> Plan:
    """Constituents of an index, with reference fields (data-securitiesByBoard by index).

    Thành phần của một chỉ số, kèm thông tin tham chiếu.
    """
    return Plan("data-securitiesByBoard", {"index": index.upper()})


def _security(symbol: str) -> Plan:
    """Reference fields of one security: names, ICB, listed shares, lot size, warrant terms.

    Thông tin tham chiếu của một mã: tên, ngành ICB, khối lượng niêm yết, lô, điều khoản chứng quyền.
    """
    return Plan("data-securitiesByBoard", {"symbol": symbol.upper()})


def _price_limits(start: Any = None, end: Any = None) -> Plan:
    """Reference, ceiling and floor prices of every symbol per day (data-masterdata).

    Giá tham chiếu, trần, sàn của mọi mã theo ngày.
    """
    first, last = _range(start, end, 0)
    return Plan(
        "data-masterdata", {"from": _day(first), "to": _day(last), "pageIndex": 1, "pageSize": PAGE_SIZE}
    )


VERBS: dict[str, Builder] = {
    "ohlc": _ohlc,
    "quote": _quote,
    "foreign": _foreign,
    "index": _index,
    "symbols": _symbols,
    "events": _events,
}

EXTRAS: dict[str, Builder] = {
    "summary": _summary,
    "index_summary": _index_summary,
    "index_list": _index_list,
    "index_members": _index_members,
    "security": _security,
    "price_limits": _price_limits,
    "corporate_actions": _corporate_actions,
}

def _samples(symbol: str, index: str) -> list[tuple[str, Plan]]:
    return [
        ("index_list", Plan("data-indexList", {})),
        ("securities_by_board", Plan("data-securitiesByBoard", {"symbol": symbol})),
    ]


SPEC = SourceSpec(
    name=NAME,
    title="SSI FastConnect API",
    base_url=BASE_URL,
    hosts=frozenset({"api.ssi.com.vn"}),
    credentials=(
        CredentialField("api_key", "API_KEY", label="API Key"),
        CredentialField("api_secret", "API_SECRET", label="API Secret"),
    ),
    auth=lambda source: TokenAuth(
        source,
        login_path="/api/v3/auth/token",
        login_body=_login_body,
        parse_token=_parse_token,
        refresh_path="/api/v3/auth/refresh",
        refresh_body=lambda tokens: {"refreshToken": tokens.refresh_token},
    ),
    ops=OPS,
    verbs=VERBS,
    extras=EXTRAS,
    api_version="v3",
    rate_per_second=10,
    check=lambda: Plan("data-indexList", {}),
    docs=DOC,
    samples=_samples,
)
