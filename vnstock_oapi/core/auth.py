"""The auth kinds shared by every source.

Các kiểu xác thực dùng chung cho mọi nguồn.

- :class:`SignedAuth`: sign every request with a scheme the source supplies, no session.
- :class:`ApiKeyHeaderAuth`: a fixed key in one header, no signature, no session.
- :class:`TokenAuth`: exchange keys for a token, renew it with a refresh token.
- :class:`OtpTokenAuth`: exchange keys plus a user-typed OTP for a token, no refresh.
- :class:`PublicAuth`: public market data, no credential at all: nothing read, nothing sent.

This is the only module allowed to call :meth:`Secret.reveal`. A new source picks one of these
kinds; a new kind is added here, never in a source module.

There is no kind that takes the account password. A source that only signs in with the password of a
brokerage account is not supported: that password also opens the account's assets, and a tampered
copy of this package would need one line to send it elsewhere.

Không có kiểu nào nhận mật khẩu tài khoản. Nguồn chỉ cho đăng nhập bằng mật khẩu tài khoản chứng khoán
thì không được hỗ trợ: mật khẩu đó mở cả tài sản trong tài khoản, và một bản gói bị sửa chỉ cần thêm
một dòng là gửi được nó đi.
"""

from __future__ import annotations

import base64
import json
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass
from typing import TYPE_CHECKING, Any

from vnstock_oapi.core.credentials import Credentials
from vnstock_oapi.core.session_store import SessionStore
from vnstock_oapi.errors import InputError, LoginRequiredError, OtpRequiredError, SourceError

if TYPE_CHECKING:
    from vnstock_oapi.core.source import Source

__all__ = [
    "Auth",
    "SignedAuth",
    "ApiKeyHeaderAuth",
    "TokenSet",
    "TokenAuth",
    "OtpTokenAuth",
    "PublicAuth",
    "SignHeaders",
    "to_epoch_seconds",
    "jwt_expiry",
    "fallback_expiry",
]

TOKEN_EXPIRY_SKEW_S = 30
UNKNOWN_EXPIRY_FALLBACK_S = 300


class Auth:
    """Common interface of the auth kinds.

    Giao diện chung của các kiểu xác thực.
    """

    kind = "base"
    needs_otp = False
    # What the user must type to open a session ("OTP"), or None if nothing.
    asks: str | None = None

    def __init__(self, source: Source) -> None:
        self.source = source

    @property
    def credentials(self) -> Credentials:
        return self.source.credentials

    def headers(self, method: str, path: str) -> dict[str, str]:
        raise NotImplementedError

    def login(self, otp: str | None = None) -> dict[str, Any]:
        return self.status()

    def logout(self) -> None:
        return None

    def status(self) -> dict[str, Any]:
        return {"auth": self.kind}

    def on_unauthorized(self) -> bool:
        """Called on a 401. Return True if a new session exists and the request may be sent once more.

        Gọi khi nguồn trả 401. Trả True nếu đã có phiên mới và được gửi lại đúng một lần.
        """
        return False


# -- signed -------------------------------------------------------------------


# Builds the signed headers of one request from the revealed key and secret, the method and the path.
SignHeaders = Callable[[str, str, str, str], dict[str, str]]


class SignedAuth(Auth):
    """Every request signed with the key and secret; the source module supplies the signing scheme.

    Ký từng request bằng khoá và secret; module nguồn cung cấp cách ký.
    """

    kind = "signed"

    def __init__(self, source: Source, *, sign: SignHeaders) -> None:
        super().__init__(source)
        self.sign = sign

    def headers(self, method: str, path: str) -> dict[str, str]:
        creds = self.credentials
        return self.sign(creds["api_key"].reveal(), creds["api_secret"].reveal(), method, path)

    def login(self, otp: str | None = None) -> dict[str, Any]:
        if otp:
            raise InputError(
                f"{self.source.name} không cần OTP để lấy dữ liệu; OTP của nguồn này chỉ dùng cho "
                "lệnh giao dịch, mà vnstock-oapi không đặt lệnh",
                source=self.source.name,
            )
        return self.status()

    def status(self) -> dict[str, Any]:
        return {"auth": self.kind, "session": "không cần phiên: ký từng request"}


# -- public -------------------------------------------------------------------


class PublicAuth(Auth):
    """Public market data: the source needs no key. No credential is read, no auth header is sent.

    Dữ liệu thị trường công khai: nguồn không cần khoá. Không đọc khoá nào, không gửi header xác thực nào.

    A source with this kind declares ``credentials=()``; the transport floor (host allowlist, GET only,
    fixed User-Agent, one wait on 429) is the same as for every other kind.
    """

    kind = "public"

    def headers(self, method: str, path: str) -> dict[str, str]:
        return {}

    def login(self, otp: str | None = None) -> dict[str, Any]:
        if otp:
            raise InputError(
                f"{self.source.name} là nguồn truy cập công khai, không cần OTP",
                source=self.source.name,
            )
        return self.status()

    def status(self) -> dict[str, Any]:
        return {"auth": self.kind, "session": "không cần khoá, không cần phiên: nguồn truy cập công khai"}


# -- api key header -----------------------------------------------------------


class ApiKeyHeaderAuth(Auth):
    kind = "api_key_header"

    def __init__(self, source: Source, *, header: str) -> None:
        super().__init__(source)
        self.header = header

    def headers(self, method: str, path: str) -> dict[str, str]:
        return {self.header: self.credentials["api_key"].reveal()}

    def status(self) -> dict[str, Any]:
        return {"auth": self.kind, "session": f"không cần phiên: khoá gửi trong header {self.header}"}


# -- token --------------------------------------------------------------------


@dataclass
class TokenSet:
    access_token: str
    expires_at: float
    refresh_token: str = ""
    refresh_expires_at: float = 0.0
    obtained_at: float = 0.0

    def valid(self, now: float) -> bool:
        return bool(self.access_token) and now < self.expires_at - TOKEN_EXPIRY_SKEW_S

    def refreshable(self, now: float) -> bool:
        if not self.refresh_token:
            return False
        return self.refresh_expires_at <= 0 or now < self.refresh_expires_at - TOKEN_EXPIRY_SKEW_S

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> TokenSet | None:
        try:
            return cls(**{key: data[key] for key in cls.__dataclass_fields__ if key in data})
        except TypeError:
            return None


def to_epoch_seconds(value: object) -> float:
    """Epoch seconds; millisecond values are divided by 1000, garbage becomes 0.

    Epoch giây; giá trị mili giây được chia 1000; giá trị hỏng thành 0.
    """
    try:
        number = float(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return 0.0
    if number >= 1e11:
        number /= 1000
    return max(number, 0.0)


def jwt_expiry(token: str) -> float:
    """Read ``exp`` from a JWT without verifying it (only to know when it expires).

    Đọc ``exp`` trong JWT mà không kiểm chữ ký, chỉ để biết khi nào hết hạn.
    """
    try:
        payload = token.split(".")[1]
        payload += "=" * (-len(payload) % 4)
        return to_epoch_seconds(json.loads(base64.urlsafe_b64decode(payload)).get("exp"))
    except (IndexError, ValueError, AttributeError):
        return 0.0


# The second argument is the OTP the user typed, or None.
LoginBody = Callable[[Credentials, Any], dict[str, Any]]
ParseToken = Callable[[Any, float], TokenSet]


class TokenAuth(Auth):
    kind = "token"

    def __init__(
        self,
        source: Source,
        *,
        login_path: str,
        login_body: LoginBody,
        parse_token: ParseToken,
        refresh_path: str | None = None,
        refresh_body: Callable[[TokenSet], dict[str, Any]] | None = None,
    ) -> None:
        super().__init__(source)
        self.login_path = login_path
        self.login_body = login_body
        self.parse_token = parse_token
        self.refresh_path = refresh_path
        self.refresh_body = refresh_body
        self.store = SessionStore(source.name, source.profile)
        self._tokens: TokenSet | None = None

    def _load(self) -> TokenSet | None:
        data = self.store.get()
        return TokenSet.from_dict(data) if data else None

    def _save(self, tokens: TokenSet) -> None:
        self._tokens = tokens
        self.store.put(asdict(tokens))

    def _post(self, path: str, body: dict[str, Any], operation: str) -> Any:
        # A login endpoint may live on another host of the same source; it must be in ``hosts``.
        url = path if path.startswith("https://") else self.source.spec.base_url + path
        response = self.source.transport.request(
            "POST", url, json=body, allow_post=True, headers=self.source.static_headers(),
        )
        self.source.raise_for_status(response, operation)
        try:
            return response.json()
        except ValueError:
            raise SourceError(
                "Phản hồi đăng nhập không phải JSON", source=self.source.name, operation=operation
            ) from None

    def _login(self, otp: str | None) -> TokenSet:
        now = time.time()
        data = self._post(self.login_path, self.login_body(self.credentials, otp), "auth.login")
        tokens = self.parse_token(data, now)
        tokens.obtained_at = now
        return tokens

    def _refresh(self, tokens: TokenSet) -> TokenSet:
        assert self.refresh_path and self.refresh_body
        now = time.time()
        data = self._post(self.refresh_path, self.refresh_body(tokens), "auth.refresh")
        fresh = self.parse_token(data, now)
        fresh.obtained_at = now
        return fresh

    def access_token(self) -> str:
        now = time.time()
        if self._tokens and self._tokens.valid(now):
            return self._tokens.access_token
        tokens = self._load()
        if tokens and tokens.valid(now):
            self._tokens = tokens
            return tokens.access_token
        with self.store.lock():
            tokens = self._load()  # another process may have refreshed while we waited
            if tokens and tokens.valid(now):
                self._tokens = tokens
                return tokens.access_token
            if tokens and self.refresh_path and tokens.refreshable(now):
                tokens = self._refresh(tokens)
            elif self.asks:
                state = "đã hết hạn" if tokens else "chưa mở"
                error = OtpRequiredError if self.needs_otp else LoginRequiredError
                raise error(
                    f"Phiên {self.source.name} {state}. Chạy `vnstock-oapi auth "
                    f"{self.source.name}` và nhập {self.asks}",
                    source=self.source.name,
                )
            else:
                tokens = self._login(None)
            self._save(tokens)
            return tokens.access_token

    def headers(self, method: str, path: str) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.access_token()}"}

    def login(self, otp: str | None = None) -> dict[str, Any]:
        if self.asks and not otp:
            error = OtpRequiredError if self.needs_otp else LoginRequiredError
            raise error(f"{self.source.name} cần {self.asks} để mở phiên", source=self.source.name)
        with self.store.lock():
            self._save(self._login(otp))
        return self.status()

    def logout(self) -> None:
        self._tokens = None
        self.store.clear()

    def on_unauthorized(self) -> bool:
        # A cached token can be revoked by a login elsewhere (one session per key). Re-login
        # once is re-authentication, not retrying a refused credential. OTP sources cannot.
        # The stored session may be newer than the refused one (another process logged in): adopt it
        # rather than clear it, or two processes on one key keep revoking each other.
        rejected = self._tokens.access_token if self._tokens else None
        self._tokens = None
        with self.store.lock():
            stored = self._load()
            if stored and stored.access_token != rejected and stored.valid(time.time()):
                self._tokens = stored
                return True
            self.store.clear()
        return not self.asks

    def status(self) -> dict[str, Any]:
        tokens = self._load()
        now = time.time()
        info: dict[str, Any] = {"auth": self.kind, "storage": self.store.backend}
        if not tokens:
            info["session"] = "chưa có phiên"
            return info
        info["session"] = "còn hạn" if tokens.valid(now) else "hết hạn"
        info["expires_in_s"] = int(tokens.expires_at - now)
        if tokens.refresh_token:
            info["refreshable_for_s"] = (
                int(tokens.refresh_expires_at - now) if tokens.refresh_expires_at else None
            )
        return info


class OtpTokenAuth(TokenAuth):
    kind = "otp_token"
    needs_otp = True
    asks = "OTP"


def fallback_expiry(expires_at: float, now: float, default_s: float) -> float:
    """The source's expiry, or ``now + default_s`` only when the source sent none (0).

    Hạn do nguồn trả; chỉ khi nguồn không trả hạn (0) mới dùng ``now + default_s``.
    """
    return expires_at if expires_at > 0 else now + default_s

