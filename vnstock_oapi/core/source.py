"""The shared frame of a source: ``SourceSpec`` declares, ``Source`` runs.

Khuôn chung của một nguồn: ``SourceSpec`` khai báo, ``Source`` chạy.

A source module only declares data: hosts, credential fields, auth kind, operations and how the
common verbs map to operations. Everything that touches the network, keys or envelopes lives here
and in ``core/``, so every source gets the same security floor.
"""

from __future__ import annotations

import contextlib
import inspect
import logging
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass, field, replace
from datetime import date, datetime
from email.utils import parsedate_to_datetime
from typing import Any

from vnstock_oapi.core import credentials as credentials_mod
from vnstock_oapi.core.auth import Auth
from vnstock_oapi.core.credentials import CredentialField, Credentials
from vnstock_oapi.core.envelope import Envelope, Envelopes
from vnstock_oapi.core.redact import RedactFilter, mask_bearer, redact, safe_url
from vnstock_oapi.core.terms import Terms, load_terms
from vnstock_oapi.core.transport import Response, Transport
from vnstock_oapi.errors import (
    AuthError,
    ClockSkewError,
    InputError,
    OapiError,
    SourceError,
    UnsupportedError,
)

__all__ = ["BodyError", "Builder", "Op", "Plan", "Samples", "SourceSpec", "Source", "VERBS"]

logger = logging.getLogger("vnstock_oapi.source")
logger.addFilter(RedactFilter())

VERBS = ("ohlc", "trades", "quote", "index", "foreign", "symbols", "calendar", "events")
_PATH_PARAM = re.compile(r"\{(\w+)\}")
_MAX_MESSAGE = 200
CLOCK_SKEW_LIMIT_S = 60

NextPage = Callable[[dict[str, Any], Any], "dict[str, Any] | None"]
# Reads a parsed body; returns (error class, source code, message) when the body reports an error.
BodyError = Callable[[Any], "tuple[type[OapiError], Any, str] | None"]


@dataclass(frozen=True)
class Op:
    """One read-only operation of a source, named as the source's docs name it.

    Một thao tác chỉ đọc của nguồn, đặt tên theo tài liệu của nguồn.
    """

    name: str
    path: str
    doc: str = ""
    next_page: NextPage | None = None
    page_params: Callable[[int], dict[str, Any]] | None = None
    max_pages: int = 100

    def render(self, params: Mapping[str, Any]) -> tuple[str, dict[str, Any]]:
        query = {key: value for key, value in params.items() if value is not None}
        path_names = _PATH_PARAM.findall(self.path)
        missing = [name for name in path_names if name not in query]
        if missing:
            raise InputError(f"{self.name} cần tham số {', '.join(missing)}")
        path = _PATH_PARAM.sub(lambda m: str(query.pop(m.group(1))), self.path)
        return path, query


@dataclass(frozen=True)
class Plan:
    """What a verb builder resolves to: which operation, with which parameters.

    Kết quả của một builder: gọi thao tác nào, với tham số nào.
    """

    operation: str
    params: dict[str, Any]


# A builder turns common arguments into one or more calls (e.g. one per day, per board, per chunk).
Builder = Callable[..., "Plan | list[Plan]"]
# Source-specific sample calls for ``vnstock-oapi dump``, from a symbol and an index: (label, plan).
Samples = Callable[[str, str], "list[tuple[str, Plan]]"]


@dataclass(frozen=True)
class SourceSpec:
    name: str
    title: str
    base_url: str
    hosts: frozenset[str]
    credentials: tuple[CredentialField, ...]
    auth: Callable[[Source], Auth]
    ops: Mapping[str, Op]
    verbs: Mapping[str, Builder] = field(default_factory=dict)
    # Source-specific functions beyond the common verbs, reached as ``oapi.<source>.<name>(...)``.
    extras: Mapping[str, Builder] = field(default_factory=dict)
    api_version: str = ""
    static_headers: Callable[[], dict[str, str]] = dict
    rate_per_second: float | None = None
    check: Callable[[], Plan] | None = None
    docs: str = ""
    # Sources that report errors inside the body, even with HTTP 200.
    body_error: BodyError | None = None
    # "vn": Vietnamese market. "global": no Vietnamese symbol, so tools never send one.
    market: str = "vn"
    samples: Samples | None = None
    # Who made the envelopes: None for a built-in source (vnstock-oapi itself); set by ``register``
    # to the extension package that declared the source.
    producer: Mapping[str, str] | None = None


class Source:
    """One source for one credential profile.

    Một nguồn, một hồ sơ khoá.

    ``oapi.source("<source>")`` returns a ``Source``. Nothing is read or sent until the first call.

    ``oapi.source("<nguồn>")`` trả về một ``Source``. Chưa đọc khoá, chưa gửi gì cho tới lần gọi đầu.
    """

    def __init__(
        self, spec: SourceSpec, profile: str = "default", *, transport: Transport | None = None
    ) -> None:
        self.spec = spec
        self.profile = profile
        self._transport = transport
        self._credentials: Credentials | None = None
        self._auth: Auth | None = None

    def __repr__(self) -> str:
        return f"Source({self.spec.name!r}, profile={self.profile!r})"

    # -- wiring -----------------------------------------------------------

    @property
    def name(self) -> str:
        return self.spec.name

    @property
    def transport(self) -> Transport:
        if self._transport is None:
            self._transport = Transport(
                self.name, self.spec.hosts, rate_per_second=self.spec.rate_per_second
            )
        return self._transport

    @property
    def credentials(self) -> Credentials:
        if self._credentials is None:
            self._credentials = credentials_mod.load(self.name, self.profile, self.spec.credentials)
        return self._credentials

    @property
    def auth(self) -> Auth:
        if self._auth is None:
            self._auth = self.spec.auth(self)
        return self._auth

    def static_headers(self) -> dict[str, str]:
        return self.spec.static_headers()

    def close(self) -> None:
        # oapi.source() hands out one shared instance: the next call must get a fresh transport.
        if self._transport is not None:
            self._transport.close()
            self._transport = None

    def __enter__(self) -> Source:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- no network -------------------------------------------------------

    @property
    def ops(self) -> dict[str, list[str]]:
        """Common verbs and raw operations this source supports.

        Thao tác chung và thao tác gốc nguồn này hỗ trợ.
        """
        return {
            "verbs": [v for v in VERBS if v in self.spec.verbs],
            "extras": sorted(self.spec.extras),
            "raw": sorted(self.spec.ops),
        }

    def __getattr__(self, name: str) -> Any:
        # Only called for attributes not found normally: expose the source's extra functions.
        spec = self.__dict__.get("spec")
        if spec is not None and name in spec.extras:
            def call(*args: Any, **kwargs: Any) -> Envelopes:
                return self._run(self.plan(name, *args, **kwargs))

            call.__name__ = name
            call.__doc__ = spec.extras[name].__doc__
            return call
        raise AttributeError(name)

    def __dir__(self) -> list[str]:
        return sorted(set(super().__dir__()) | set(self.spec.extras))

    @property
    def terms(self) -> Terms:
        return load_terms(self.name)

    def status(self) -> dict[str, Any]:
        info: dict[str, Any] = {"source": self.name, "profile": self.profile}
        try:
            self._credentials = credentials_mod.load(self.name, self.profile, self.spec.credentials)
            info["keys"] = {k: v for k, v in self._credentials.origins.items()}
        except OapiError as exc:
            self._credentials = None
            info["keys"] = f"thiếu: {exc.message}"
            return info
        info.update(self.auth.status())
        return info

    def plan(self, name: str, *args: Any, **kwargs: Any) -> list[Plan]:
        """The calls a common verb or extra function would make, without making them.

        Các lần gọi mà một thao tác chung hay hàm riêng sẽ thực hiện, chưa gửi đi.
        """
        builder = self.spec.verbs.get(name) or self.spec.extras.get(name)
        if builder is None:
            raise UnsupportedError(
                f"{self.name} chưa có thao tác {name!r}. Dùng raw(); xem .ops",
                source=self.name,
                operation=name,
            )
        result = builder(*args, **kwargs)
        return list(result) if isinstance(result, list) else [result]

    def _run(self, plans: list[Plan]) -> Envelopes:
        out = Envelopes()
        for plan in plans:
            out.extend(self.raw(plan.operation, **plan.params))
        return out

    def preview(self, verb_or_op: str, *args: Any, **kwargs: Any) -> dict[str, Any]:
        """What would be sent, without sending it. Secrets are masked.

        Request sẽ gửi, nhưng không gửi. Khoá đã che.
        """
        named = verb_or_op in self.spec.verbs or verb_or_op in self.spec.extras
        page = None if named else kwargs.pop("only_page", None)
        plans = self.plan(verb_or_op, *args, **kwargs) if named else [Plan(verb_or_op, dict(kwargs))]
        plan = plans[0]
        op = self._op(plan.operation)
        params = dict(plan.params)
        if page is not None and op.page_params is not None:
            params.update(op.page_params(page))
        path, query = op.render(params)
        headers = dict(self.static_headers())
        try:
            if self.auth.kind in ("signed", "api_key_header", "public"):
                headers.update(self.auth.headers("GET", path))
            else:
                headers["Authorization"] = "Bearer *** (token lấy từ kho phiên lúc gửi)"
        except OapiError as exc:
            headers["(auth)"] = exc.message
        shown = {
            key: mask_bearer(value) if key.lower() in ("x-signature", "authorization") else redact(
                {key: value}
            )[key]
            for key, value in headers.items()
        }
        return {
            "method": "GET",
            "url": self.spec.base_url + path,
            "query": query,
            "headers": shown,
            "operation": op.name,
            "doc": op.doc,
            "calls": len(plans),
        }

    # -- network ----------------------------------------------------------

    def authenticate(self, otp: str | None = None) -> dict[str, Any]:
        """Authenticate with the source (open a session), with the OTP when the source asks for one.

        Xác thực với nguồn: mở phiên, kèm ``otp`` khi nguồn cần. Đây là xác thực của bên thứ ba, không phải
        tài khoản Vnstock.
        """
        return self.auth.login(otp)

    def clear_auth(self) -> None:
        """Drop the session held for this source. Xoá phiên đang giữ của nguồn."""
        self.auth.logout()

    def raw(self, operation: str, *, only_page: int | None = None, **params: Any) -> Envelopes:
        """Call a source operation by its own name, with its own parameters.

        Gọi thao tác gốc của nguồn, bằng tên và tham số gốc.

        Pages are followed until the source signals the end; each page is one envelope. Pass
        ``only_page`` to fetch exactly one page. (Not ``page``: several sources have a parameter of
        that name, and it must reach them untouched.)
        """
        op = self._op(operation)
        query_params = dict(params)
        if only_page is not None:
            if op.page_params is None:
                raise InputError(f"{self.name}.{op.name} không phân trang theo số trang")
            query_params.update(op.page_params(only_page))
        out = Envelopes()
        for _ in range(op.max_pages):
            envelope = self._fetch(op, query_params)
            out.append(envelope)
            if only_page is not None or op.next_page is None:
                return out
            following = op.next_page(query_params, envelope.body)
            if not following:
                return out
            query_params = following
        # The source still had pages. Say so in the envelope, so the core can warn instead of
        # handing over a short answer as if it were the whole one.
        logger.warning("%s.%s dừng ở %d trang (giới hạn max_pages)", self.name, op.name, op.max_pages)
        if out:
            out[-1] = replace(out[-1], truncated={"reason": "page_limit", "pages": op.max_pages})
        return out

    def _op(self, operation: str) -> Op:
        op = self.spec.ops.get(operation)
        if op is None:
            raise UnsupportedError(
                f"{self.name} không có thao tác {operation!r}. Có: {', '.join(sorted(self.spec.ops))}",
                source=self.name,
                operation=operation,
            )
        return op

    def _fetch(self, op: Op, params: Mapping[str, Any]) -> Envelope:
        path, query = op.render(params)
        _ = self.credentials  # missing keys are reported before any session or OTP question
        resent = False
        while True:
            headers = {**self.static_headers(), **self.auth.headers("GET", path)}
            response = self.transport.request(
                "GET", self.spec.base_url + path, params=query, headers=headers
            )
            if self._is_unauthorized(response) and not resent and self.auth.on_unauthorized():
                resent = True
                continue
            self.raise_for_status(response, op.name)
            parsed: dict[str, Any] = {}  # body already decoded by raise_for_status: reuse it
            if response.content:
                with contextlib.suppress(ValueError):
                    parsed["parsed_body"] = response.json()
            return Envelope.from_response(
                source=self.name,
                operation=op.name,
                request=dict(params),
                status=response.status,
                content=response.content,
                api_version=self.spec.api_version,
                producer=self.spec.producer,
                transport={
                    "domain": response.host,
                    "protocol": "https",
                    "method": "GET",
                    "latency_ms": response.latency_ms,
                    "request_count": response.request_count,
                },
                **parsed,
            )

    def _is_unauthorized(self, response: Response) -> bool:
        if response.status == 401:
            return True
        reported = self._body_error(response)
        return reported is not None and issubclass(reported[0], AuthError)

    def raise_for_status(self, response: Response, operation: str) -> None:
        status = response.status
        reported = self._body_error(response)
        if 200 <= status < 300 and reported is None:
            return
        code, message = _error_detail(response)
        context: dict[str, Any] = {
            "source": self.name,
            "operation": operation,
            "http_status": status,
            "code": code,
            "url": safe_url(response.url),
        }
        detail = f": {message}" if message else ""
        if 300 <= status < 400:
            raise SourceError("Nguồn trả chuyển hướng; vnstock-oapi không theo", **context)
        if reported is not None:
            error, code, message = reported
            context["code"] = code
            text = str(redact(message))[:_MAX_MESSAGE]
            raise error(f"{self.name} báo lỗi {code}" + (f": {text}" if text else ""), **context)
        skew = _clock_skew(response)
        if status in (400, 401) and skew is not None and abs(skew) > CLOCK_SKEW_LIMIT_S:
            raise ClockSkewError(
                f"Đồng hồ máy lệch {skew:+.0f} giây so với {self.name}. Đồng bộ giờ hệ điều hành",
                **context,
            )
        if status in (401, 403):
            raise AuthError(f"{self.name} từ chối xác thực{detail}", **context)
        if 400 <= status < 500:
            raise InputError(f"{self.name} từ chối yêu cầu{detail}", **context)
        raise SourceError(f"{self.name} lỗi phía máy chủ{detail}", **context)

    # -- common verbs -----------------------------------------------------

    def _verb(self, verb: str, *args: Any, **kwargs: Any) -> Envelopes:
        return self._run(self.plan(verb, *args, **kwargs))

    def ohlc(
        self,
        symbol: str,
        *,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        interval: str = "1D",
        limit: int | None = None,
        asset: str = "equity",
    ) -> Envelopes:
        """OHLC bars. The body stays exactly as the source sent it.

        Nến giá. Body giữ nguyên văn của nguồn.

        ``interval`` and ``asset`` take the shared vocabulary (``spec/vocabulary.toml``):
        ``1m 3m 5m 15m 30m 1H 4H 1D 1W 1M`` and ``equity index futures warrant…``; the old spellings
        (``1h``, ``stock``, ``derivative``, ``cw``) still work.
        """
        return self._verb(
            "ohlc", symbol, start=start, end=end, interval=interval, limit=limit, asset=asset
        )

    def trades(
        self,
        symbol: str,
        *,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        limit: int | None = None,
        board: str | None = None,
    ) -> Envelopes:
        """Tick-by-tick trades; ``board`` keeps one trading board where the source mixes several.

        Khớp lệnh từng lệnh; ``board`` chỉ lấy một bảng giao dịch ở nguồn trả lẫn nhiều bảng (DNSE: ``G1``
        lô chẵn, ``G4`` lô lẻ, ``T1`` thoả thuận). Nguồn không có khái niệm bảng thì báo lỗi, không bỏ qua.
        """
        if board is None:
            return self._verb("trades", symbol, start=start, end=end, limit=limit)
        builder = self.spec.verbs.get("trades")
        if builder is not None and "board" not in inspect.signature(builder).parameters:
            raise InputError(f"{self.name} không nhận tham số board cho trades", source=self.name)
        return self._verb("trades", symbol, start=start, end=end, limit=limit, board=board)

    def quote(self, symbol: str) -> Envelopes:
        """Latest price and order book.

        Giá và sổ lệnh mới nhất.
        """
        return self._verb("quote", symbol)

    def index(
        self,
        name: str,
        *,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        limit: int | None = None,
    ) -> Envelopes:
        """Index data.

        Dữ liệu chỉ số.
        """
        return self._verb("index", name, start=start, end=end, limit=limit)

    def foreign(
        self,
        symbol: str,
        *,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
        limit: int | None = None,
    ) -> Envelopes:
        """Foreign investor trading.

        Giao dịch khối ngoại.
        """
        return self._verb("foreign", symbol, start=start, end=end, limit=limit)

    def symbols(self, *args: Any, **filters: Any) -> Envelopes:
        """Instrument list. Arguments follow the source's builder, e.g. ``ssi.symbols("HOSE")``.

        Danh sách mã. Tham số theo builder của nguồn; bộ lọc dùng tên tham số gốc của nguồn.
        """
        return self._verb("symbols", *args, **filters)

    def calendar(self) -> Envelopes:
        """Trading calendar.

        Ngày giao dịch.
        """
        return self._verb("calendar")

    def events(
        self,
        symbol: str | None = None,
        *,
        start: str | date | datetime | None = None,
        end: str | date | datetime | None = None,
    ) -> Envelopes:
        """Corporate actions.

        Sự kiện doanh nghiệp.
        """
        return self._verb("events", symbol, start=start, end=end)


    def _body_error(self, response: Response) -> tuple[type[OapiError], Any, str] | None:
        if self.spec.body_error is None:
            return None
        try:
            body = response.json()
        except ValueError:
            return None
        return self.spec.body_error(body)


def _error_detail(response: Response) -> tuple[Any, str]:
    try:
        body = response.json()
    except ValueError:
        return None, ""
    if not isinstance(body, dict):
        return None, ""
    code = body.get("code") or body.get("errorCode") or body.get("error_code") or body.get("status")
    message = body.get("message") or body.get("msg") or body.get("error") or ""
    text = str(redact(message))[:_MAX_MESSAGE]
    return code, text


def _clock_skew(response: Response) -> float | None:
    server = response.headers.get("date") or response.headers.get("Date")
    if not server:
        return None
    try:
        server_dt = parsedate_to_datetime(server)
    except (TypeError, ValueError):
        return None
    return (datetime.now(server_dt.tzinfo) - server_dt).total_seconds()
