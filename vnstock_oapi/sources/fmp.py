"""Financial Modeling Prep (FMP) stable API: a fixed key in the ``apikey`` header, no session.

Financial Modeling Prep (FMP) stable API: khoá cố định trong header ``apikey``, không phiên.

FMP accepts the key as a header or as ``?apikey=``; only the header is used, so the key never lands
in a URL, a log line or an envelope. Global data (mostly US markets): directory, profiles, quotes,
financial statements, prices, economics, calendars, funds, ownership, indexes, market hours.
Not declared by default: analyst estimates, ratings, grades and price targets, TipRanks content,
news and articles, earnings call transcripts, source-computed technicals and DCF valuations, bulk
downloads and report files, congressional trades, crowdfunding.
Access depends on the user's FMP plan (Basic: 250 calls a day, 5 years of history); a call outside
the plan is answered with an error the source layer turns into ``UnsupportedError``.
Docs: https://site.financialmodelingprep.com/developer/docs (stable API, surveyed 28/10/2026).
Terms (personal plan): the key is personal; data may not be shared, displayed to or made accessible
by other people without a separate FMP agreement.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.auth import ApiKeyHeaderAuth
from vnstock_oapi.core.credentials import CredentialField
from vnstock_oapi.core.source import Builder, Op, Plan, SourceSpec
from vnstock_oapi.core.timeutil import default_range, map_interval
from vnstock_oapi.errors import AuthError, InputError, OapiError, RateLimitError, UnsupportedError

NAME = "fmp"
BASE_URL = "https://financialmodelingprep.com/stable"
DOC = "https://site.financialmodelingprep.com/developer/docs"
PAGE_LIMIT = 100
MAX_PAGES = 20  # the Basic plan allows 250 calls a day; never walk further silently

# Intraday intervals: spec/sources/fmp/mapping.toml, through core.vocabulary.
EOD = {
    "full": "historical-price-eod/full",
    "light": "historical-price-eod/light",
    "unadjusted": "historical-price-eod/non-split-adjusted",
    "dividend_adjusted": "historical-price-eod/dividend-adjusted",
}
STATEMENTS = {
    "income": "income-statement",
    "balance": "balance-sheet-statement",
    "cashflow": "cash-flow-statement",
}
METRICS = {
    "key-metrics": "key-metrics",
    "ratios": "ratios",
    "scores": "financial-scores",
    "enterprise-values": "enterprise-values",
    "growth": "financial-growth",
    "owner-earnings": "owner-earnings",
}
PERIODS = ("annual", "quarter", "FY", "Q1", "Q2", "Q3", "Q4")
BATCH_QUOTES = {
    "index": "batch-index-quotes",
    "etf": "batch-etf-quotes",
    "mutualfund": "batch-mutualfund-quotes",
    "commodity": "batch-commodity-quotes",
    "forex": "batch-forex-quotes",
    "crypto": "batch-crypto-quotes",
}
MOVERS = {"gainers": "biggest-gainers", "losers": "biggest-losers", "actives": "most-actives"}
CONSTITUENTS = {
    "sp500": "sp500-constituent",
    "nasdaq": "nasdaq-constituent",
    "dowjones": "dowjones-constituent",
}


def _next_page(params: dict[str, Any], body: Any) -> dict[str, Any] | None:
    """Pages count from 0; a full page (``len == limit``) means there may be another.

    Trang đếm từ 0; trang đầy (đủ ``limit`` dòng) nghĩa là có thể còn trang sau.
    """
    limit = int(params.get("limit") or 0)
    if not isinstance(body, list) or not limit or len(body) < limit:
        return None
    return {**params, "page": int(params.get("page") or 0) + 1}


def _page(page: int) -> dict[str, Any]:
    return {"page": page}


# (operation = path under /stable/, docs page under developer/docs/stable/, paged)
_TABLE = (
    # directory
    ("search-symbol", "search-symbol", False),
    ("search-name", "search-name", False),
    ("search-cik", "search-cik", False),
    ("search-isin", "search-isin", False),
    ("search-exchange-variants", "search-exchange-variants", False),
    ("company-screener", "search-company-screener", False),
    ("stock-list", "company-symbols-list", False),
    ("etf-list", "etfs-list", False),
    ("actively-trading-list", "actively-trading-list", False),
    ("available-exchanges", "available-exchanges", False),
    ("available-sectors", "available-sectors", False),
    ("available-industries", "available-industries", False),
    ("available-countries", "available-countries", False),
    ("symbol-change", "symbol-changes-list", False),
    ("delisted-companies", "delisted-companies", True),
    # company
    ("profile", "profile-symbol", False),
    ("stock-peers", "peers", False),
    ("employee-count", "employee-count", False),
    ("historical-employee-count", "historical-employee-count", False),
    ("market-capitalization", "market-cap", False),
    ("market-capitalization-batch", "batch-market-cap", False),
    ("historical-market-capitalization", "historical-market-cap", False),
    ("shares-float", "shares-float", False),
    ("key-executives", "company-executives", False),
    ("governance-executive-compensation", "executive-compensation", False),
    ("mergers-acquisitions-latest", "latest-mergers-acquisitions", True),
    ("mergers-acquisitions-search", "search-mergers-acquisitions", False),
    # quotes
    ("quote", "quote", False),
    ("quote-short", "quote-short", False),
    ("batch-quote", "batch-quote", False),
    ("batch-quote-short", "batch-quote-short", False),
    ("stock-price-change", "quote-change", False),
    ("batch-exchange-quote", "full-exchange-quotes", False),
    ("batch-index-quotes", "full-index-quotes", False),
    ("batch-etf-quotes", "full-etf-quotes", False),
    ("batch-mutualfund-quotes", "full-mutualfund-quotes", False),
    ("batch-commodity-quotes", "full-commodities-quotes", False),
    ("batch-forex-quotes", "full-forex-quotes", False),
    ("batch-crypto-quotes", "full-cryptocurrency-quotes", False),
    # statements
    ("income-statement", "income-statement", False),
    ("balance-sheet-statement", "balance-sheet-statement", False),
    ("cash-flow-statement", "cashflow-statement", False),
    ("income-statement-ttm", "income-statements-ttm", False),
    ("balance-sheet-statement-ttm", "balance-sheet-statements-ttm", False),
    ("cash-flow-statement-ttm", "cashflow-statements-ttm", False),
    ("latest-financial-statements", "latest-financial-statements", True),
    ("key-metrics", "key-metrics", False),
    ("ratios", "metrics-ratios", False),
    ("key-metrics-ttm", "key-metrics-ttm", False),
    ("ratios-ttm", "metrics-ratios-ttm", False),
    ("financial-scores", "financial-scores", False),
    ("owner-earnings", "owner-earnings", False),
    ("enterprise-values", "enterprise-values", False),
    ("income-statement-growth", "income-statement-growth", False),
    ("balance-sheet-statement-growth", "balance-sheet-statement-growth", False),
    ("cash-flow-statement-growth", "cashflow-statement-growth", False),
    ("financial-growth", "financial-statement-growth", False),
    ("financial-reports-dates", "financial-reports-dates", False),
    ("revenue-product-segmentation", "revenue-product-segmentation", False),
    ("revenue-geographic-segmentation", "revenue-geographic-segments", False),
    ("income-statement-as-reported", "as-reported-income-statements", False),
    ("balance-sheet-statement-as-reported", "as-reported-balance-statements", False),
    ("cash-flow-statement-as-reported", "as-reported-cashflow-statements", False),
    ("financial-statement-full-as-reported", "as-reported-financial-statements", False),
    # prices
    ("historical-price-eod/light", "historical-price-eod-light", False),
    ("historical-price-eod/full", "historical-price-eod-full", False),
    ("historical-price-eod/non-split-adjusted", "historical-price-eod-non-split-adjusted", False),
    ("historical-price-eod/dividend-adjusted", "historical-price-eod-dividend-adjusted", False),
    ("historical-chart/1min", "intraday-1-min", False),
    ("historical-chart/5min", "intraday-5-min", False),
    ("historical-chart/15min", "intraday-15-min", False),
    ("historical-chart/30min", "intraday-30-min", False),
    ("historical-chart/1hour", "intraday-1-hour", False),
    ("historical-chart/4hour", "intraday-4-hour", False),
    # economics
    ("treasury-rates", "treasury-rates", False),
    ("economic-indicators", "economics-indicators", False),
    ("economic-calendar", "economics-calendar", False),
    ("market-risk-premium", "market-risk-premium", False),
    # calendar
    ("dividends", "dividends-company", False),
    ("dividends-calendar", "dividends-calendar", False),
    ("earnings", "earnings-company", False),
    ("earnings-calendar", "earnings-calendar", False),
    ("ipos-calendar", "ipos-calendar", False),
    ("splits", "splits-company", False),
    ("splits-calendar", "splits-calendar", False),
    # performance
    ("sector-performance-snapshot", "sector-performance-snapshot", False),
    ("industry-performance-snapshot", "industry-performance-snapshot", False),
    ("historical-sector-performance", "historical-sector-performance", False),
    ("historical-industry-performance", "historical-industry-performance", False),
    ("sector-pe-snapshot", "sector-pe-snapshot", False),
    ("industry-pe-snapshot", "industry-pe-snapshot", False),
    ("historical-sector-pe", "historical-sector-pe", False),
    ("historical-industry-pe", "historical-industry-pe", False),
    ("biggest-gainers", "biggest-gainers", False),
    ("biggest-losers", "biggest-losers", False),
    ("most-actives", "most-active", False),
    # funds
    ("etf/holdings", "holdings", False),
    ("etf/info", "information", False),
    ("etf/country-weightings", "country-weighting", False),
    ("etf/asset-exposure", "etf-asset-exposure", False),
    ("etf/sector-weightings", "sector-weighting", False),
    ("funds/disclosure-holders-latest", "latest-disclosures", False),
    ("funds/disclosure", "mutual-fund-disclosures", False),
    ("funds/disclosure-dates", "disclosures-dates", False),
    # ownership
    ("insider-trading/latest", "latest-insider-trade", True),
    ("insider-trading/search", "search-insider-trades", True),
    ("insider-trading/statistics", "insider-trade-statistics", False),
    ("acquisition-of-beneficial-ownership", "acquisition-ownership", False),
    ("institutional-ownership/latest", "latest-filings", True),
    ("institutional-ownership/symbol-positions-summary", "positions-summary", False),
    ("institutional-ownership/extract-analytics/holder", "filings-extract-with-analytics-by-holder", True),
    # indexes
    ("index-list", "indexes-list", False),
    ("sp500-constituent", "sp-500", False),
    ("nasdaq-constituent", "nasdaq", False),
    ("dowjones-constituent", "dow-jones", False),
    ("historical-sp500-constituent", "historical-sp-500", False),
    ("historical-nasdaq-constituent", "historical-nasdaq", False),
    ("historical-dowjones-constituent", "historical-dow-jones", False),
    # markets
    ("exchange-market-hours", "exchange-market-hours", False),
    ("holidays-by-exchange", "holidays-by-exchange", False),
    ("all-exchange-market-hours", "all-exchange-market-hours", False),
    ("commodities-list", "commodities-list", False),
    ("forex-list", "forex-list", False),
    ("cryptocurrency-list", "cryptocurrency-list", False),
    # filings
    ("sec-filings-search/symbol", "search-by-symbol", True),
    ("sec-profile", "sec-company-full-profile", False),
    ("esg-ratings", "esg-ratings", False),
    ("esg-disclosures", "esg-search", False),
    ("commitment-of-traders-report", "cot-report", False),
    ("commitment-of-traders-list", "cot-report-list", False),
)

OPS = {
    name: Op(
        name,
        f"/{name}",
        f"{DOC}/stable/{doc}",
        next_page=_next_page if paged else None,
        page_params=_page if paged else None,
        max_pages=MAX_PAGES,
    )
    for name, doc, paged in _TABLE
}


def body_error(body: Any) -> tuple[type[OapiError], Any, str] | None:
    """FMP reports errors as ``{"Error Message": "..."}``, sometimes with HTTP 200.

    FMP báo lỗi bằng ``{"Error Message": "..."}``, có khi kèm HTTP 200.
    """
    if not isinstance(body, dict) or "Error Message" not in body:
        return None
    message = str(body.get("Error Message") or "")
    lowered = message.lower()
    if "api key" in lowered or "apikey" in lowered:
        return AuthError, "invalid_api_key", message
    if "limit reach" in lowered:
        return RateLimitError, "limit_reached", message
    if any(word in lowered for word in ("restricted", "premium", "subscription", "exclusive", "plan")):
        return UnsupportedError, "plan", message
    return InputError, "error", message


def _day(value: datetime) -> str:
    return value.strftime("%Y-%m-%d")


def _symbol(symbol: str) -> str:
    if not symbol or not symbol.strip():
        raise InputError("Cần mã", source=NAME)
    return symbol.strip().upper()


def _range(start: Any, end: Any) -> dict[str, str]:
    start_dt, end_dt = default_range(start, end)
    return {"from": _day(start_dt), "to": _day(end_dt)}


def _ohlc(
    symbol: str,
    *,
    start: Any = None,
    end: Any = None,
    interval: str = "1D",
    limit: int | None = None,
    asset: str = "equity",
) -> Plan:
    """Bars of any FMP symbol: stocks (``AAPL``), indexes (``^GSPC``), forex (``EURUSD``), crypto
    (``BTCUSD``), commodities (``GCUSD``). ``asset`` is accepted for the common signature only: FMP
    tells assets apart by symbol. Daily bars are split- and dividend-adjusted as FMP serves them.

    Nến của mọi mã FMP. ``asset`` chỉ để giữ chữ ký chung; FMP phân biệt loại tài sản qua mã.
    """
    if limit is not None:
        raise InputError("FMP không nhận limit cho nến; dùng start, end", source=NAME)
    params = {"symbol": _symbol(symbol), **_range(start, end)}
    if vocabulary.canonical("interval", interval) == "1D":
        return Plan(EOD["full"], params)
    return Plan(vocabulary.native(NAME, "interval", interval), params)


def _quote(symbol: str) -> Plan:
    """Latest quote of a symbol.

    Báo giá mới nhất của một mã.
    """
    return Plan("quote", {"symbol": _symbol(symbol)})


def _index(name: str, *, start: Any = None, end: Any = None, limit: int | None = None) -> Plan:
    """Daily bars of an index, FMP code with a caret: ``^GSPC``, ``^DJI``, ``^IXIC``…

    Nến ngày của một chỉ số, mã FMP có dấu mũ.
    """
    return _ohlc(name, start=start, end=end, limit=limit)


def _symbols() -> Plan:
    """Every stock symbol FMP lists, with name and exchange.

    Mọi mã cổ phiếu FMP có, kèm tên và sàn.
    """
    return Plan("stock-list", {})


def _events(symbol: str | None = None, *, start: Any = None, end: Any = None) -> list[Plan]:
    """Dividends and splits: of one symbol, or the market calendars between ``start`` and ``end``.

    Cổ tức và chia tách: của một mã, hoặc lịch toàn thị trường trong khoảng ``start``, ``end``.
    """
    if symbol:
        params = {"symbol": _symbol(symbol)}
        return [Plan("dividends", params), Plan("splits", params)]
    window = _range(start, end)
    return [Plan("dividends-calendar", window), Plan("splits-calendar", window)]


def _profile(symbol: str) -> Plan:
    """Company profile: name, exchange, sector, industry, country, market cap, description.

    Hồ sơ doanh nghiệp.
    """
    return Plan("profile", {"symbol": _symbol(symbol)})


def _search(query: str, *, by: str = "symbol") -> Plan:
    """Search instruments by ``symbol``, ``name``, ``cik`` or ``isin``.

    Tìm mã theo ký hiệu, tên, CIK hoặc ISIN.
    """
    names = {"symbol": "search-symbol", "name": "search-name", "cik": "search-cik", "isin": "search-isin"}
    if by not in names:
        raise InputError(f"by phải là một trong {', '.join(names)}", source=NAME)
    key = {"symbol": "query", "name": "query", "cik": "cik", "isin": "isin"}[by]
    return Plan(names[by], {key: query.strip()})


def _financials(
    symbol: str,
    *,
    statement: str = "income",
    period: str = "annual",
    limit: int | None = None,
    ttm: bool = False,
    as_reported: bool = False,
) -> Plan:
    """Financial statements: ``income``, ``balance``, ``cashflow``; annual or quarterly, TTM, as reported.

    Báo cáo tài chính: kết quả kinh doanh, cân đối kế toán, lưu chuyển tiền tệ; năm, quý, TTM, nguyên bản.
    """
    if statement not in STATEMENTS:
        raise InputError(f"statement phải là một trong {', '.join(STATEMENTS)}", source=NAME)
    if ttm and as_reported:
        raise InputError("Chọn ttm hoặc as_reported, không cả hai", source=NAME)
    if period not in PERIODS:
        raise InputError(f"period phải là một trong {', '.join(PERIODS)}", source=NAME)
    name = STATEMENTS[statement] + ("-ttm" if ttm else "-as-reported" if as_reported else "")
    params: dict[str, Any] = {"symbol": _symbol(symbol)}
    if not ttm:
        params["period"] = period
    if limit is not None:
        params["limit"] = limit
    return Plan(name, params)


def _metrics(symbol: str, *, kind: str = "key-metrics", period: str = "annual", ttm: bool = False) -> Plan:
    """Key metrics, ratios, scores (Altman Z, Piotroski), enterprise values, growth, owner earnings.

    Chỉ số tài chính, tỉ số, điểm, giá trị doanh nghiệp, tăng trưởng.
    """
    if kind not in METRICS:
        raise InputError(f"kind phải là một trong {', '.join(METRICS)}", source=NAME)
    if ttm:
        if kind not in ("key-metrics", "ratios"):
            raise InputError("ttm chỉ có cho key-metrics và ratios", source=NAME)
        return Plan(f"{METRICS[kind]}-ttm", {"symbol": _symbol(symbol)})
    params: dict[str, Any] = {"symbol": _symbol(symbol)}
    if kind not in ("scores", "owner-earnings"):
        params["period"] = period
    return Plan(METRICS[kind], params)


def _quotes(*symbols: str, asset: str | None = None, exchange: str | None = None) -> Plan:
    """Several quotes at once: listed symbols, every quote of one asset class, or of one exchange.

    Nhiều báo giá một lần: theo danh sách mã, theo loại tài sản, hoặc theo sàn.
    """
    if sum(bool(x) for x in (symbols, asset, exchange)) != 1:
        raise InputError("Chọn đúng một: danh sách mã, asset hoặc exchange", source=NAME)
    if symbols:
        return Plan("batch-quote", {"symbols": ",".join(_symbol(s) for s in symbols)})
    if exchange:
        return Plan("batch-exchange-quote", {"exchange": exchange.strip().upper()})
    return Plan(map_interval(asset or "", BATCH_QUOTES, NAME), {})


def _economy(indicator: str | None = None, *, start: Any = None, end: Any = None) -> Plan:
    """Economic series (``GDP``, ``CPI``, ``unemploymentRate``…); None gives US Treasury rates.

    Chuỗi kinh tế; không có ``indicator`` thì trả lợi suất trái phiếu Kho bạc Mỹ.
    """
    window = _range(start, end)
    if indicator is None:
        return Plan("treasury-rates", window)
    return Plan("economic-indicators", {"name": indicator, **window})


def _calendar_of(kind: str = "earnings", *, start: Any = None, end: Any = None) -> Plan:
    """Market calendars: ``earnings``, ``dividends``, ``splits``, ``ipos``, ``economic``.

    Lịch thị trường: kết quả kinh doanh, cổ tức, chia tách, IPO, kinh tế.
    """
    names = {
        "earnings": "earnings-calendar",
        "dividends": "dividends-calendar",
        "splits": "splits-calendar",
        "ipos": "ipos-calendar",
        "economic": "economic-calendar",
    }
    if kind not in names:
        raise InputError(f"kind phải là một trong {', '.join(names)}", source=NAME)
    return Plan(names[kind], _range(start, end))


def _movers(kind: str = "gainers") -> Plan:
    """Biggest gainers, losers or most active stocks today.

    Mã tăng mạnh, giảm mạnh, giao dịch nhiều nhất hôm nay.
    """
    return Plan(map_interval(kind, MOVERS, NAME), {})


def _sectors(*, date: str | None = None, industry: bool = False, pe: bool = False) -> Plan:
    """Sector (or industry) performance or P/E snapshot of a day.

    Hiệu suất hoặc P/E theo ngành của một ngày.
    """
    level = "industry" if industry else "sector"
    measure = "pe" if pe else "performance"
    return Plan(f"{level}-{measure}-snapshot", {"date": date} if date else {})


def _etf(symbol: str, *, part: str = "holdings") -> Plan:
    """ETF or fund data: ``holdings``, ``info``, ``country-weightings``, ``asset-exposure``,
    ``sector-weightings``.

    Dữ liệu ETF, quỹ: danh mục, thông tin, tỉ trọng quốc gia, tài sản, ngành.
    """
    parts = ("holdings", "info", "country-weightings", "asset-exposure", "sector-weightings")
    if part not in parts:
        raise InputError(f"part phải là một trong {', '.join(parts)}", source=NAME)
    return Plan(f"etf/{part}", {"symbol": _symbol(symbol)})


def _insider(symbol: str, *, statistics: bool = False) -> Plan:
    """Insider trades of a symbol (Form 4), or their quarterly statistics.

    Giao dịch nội bộ của một mã, hoặc thống kê theo quý.
    """
    if statistics:
        return Plan("insider-trading/statistics", {"symbol": _symbol(symbol)})
    return Plan("insider-trading/search", {"symbol": _symbol(symbol), "page": 0, "limit": PAGE_LIMIT})


def _institutional(symbol: str, *, year: int, quarter: int) -> Plan:
    """Institutional (13F) positions summary of a symbol for one quarter.

    Tóm tắt nắm giữ của tổ chức (13F) với một mã trong một quý.
    """
    if quarter not in (1, 2, 3, 4):
        raise InputError("quarter phải là 1, 2, 3 hoặc 4", source=NAME)
    return Plan(
        "institutional-ownership/symbol-positions-summary",
        {"symbol": _symbol(symbol), "year": year, "quarter": quarter},
    )


def _index_members(index: str = "sp500", *, historical: bool = False) -> Plan:
    """Constituents of the S&P 500, Nasdaq or Dow Jones; ``historical`` gives additions and removals.

    Thành phần S&P 500, Nasdaq, Dow Jones; ``historical`` cho lịch sử thêm, bớt.
    """
    name = map_interval(index, CONSTITUENTS, NAME)
    return Plan(f"historical-{name}" if historical else name, {})


def _market_hours(exchange: str | None = None, *, holidays: bool = False) -> Plan:
    """Trading hours of an exchange (or of all exchanges), or its holidays.

    Giờ giao dịch của một sàn (hoặc mọi sàn), hoặc ngày nghỉ của sàn.
    """
    if holidays:
        if not exchange:
            raise InputError("Cần exchange để lấy ngày nghỉ", source=NAME)
        return Plan("holidays-by-exchange", {"exchange": exchange.strip().upper()})
    if exchange:
        return Plan("exchange-market-hours", {"exchange": exchange.strip().upper()})
    return Plan("all-exchange-market-hours", {})


VERBS: dict[str, Builder] = {
    "ohlc": _ohlc,
    "quote": _quote,
    "index": _index,
    "symbols": _symbols,
    "events": _events,
}

EXTRAS: dict[str, Builder] = {
    "profile": _profile,
    "search": _search,
    "financials": _financials,
    "metrics": _metrics,
    "quotes": _quotes,
    "economy": _economy,
    "calendar_of": _calendar_of,
    "movers": _movers,
    "sectors": _sectors,
    "etf": _etf,
    "insider": _insider,
    "institutional": _institutional,
    "index_members": _index_members,
    "market_hours": _market_hours,
}

def _samples(symbol: str, index: str) -> list[tuple[str, Plan]]:
    return [
        ("profile", Plan("profile", {"symbol": symbol})),
        ("market_hours", Plan("all-exchange-market-hours", {})),
    ]


SPEC = SourceSpec(
    name=NAME,
    title="Financial Modeling Prep",
    base_url=BASE_URL,
    hosts=frozenset({"financialmodelingprep.com"}),
    credentials=(CredentialField("api_key", "API_KEY", label="API Key"),),
    auth=lambda source: ApiKeyHeaderAuth(source, header="apikey"),
    ops=OPS,
    verbs=VERBS,
    extras=EXTRAS,
    api_version="stable",
    # Basic: 250 calls a day; Starter: 300 a minute. Stay low until the user's plan is known.
    rate_per_second=2,
    check=lambda: Plan("quote-short", {"symbol": "AAPL"}),
    docs=DOC,
    body_error=body_error,
    market="global",
    samples=_samples,
)
