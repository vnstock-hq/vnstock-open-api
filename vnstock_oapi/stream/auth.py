"""StreamAuth: the first message a stream needs, built from the same keys as REST.

StreamAuth: thông điệp xác thực đầu tiên của stream, dựng từ chính khoá dùng cho REST.

The message goes to the source only. It is never put in a frame, a log or an error.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import threading
import time
from collections.abc import Callable
from typing import Any, Protocol

from vnstock_oapi.core.credentials import Credentials
from vnstock_oapi.errors import AuthError, SourceError, UnsupportedError

__all__ = ["StreamAuth", "HmacHexAuth", "SsiFcToken", "stream_auth"]


class StreamAuth(Protocol):
    """Builds the authentication message sent right after the server's welcome.

    Dựng thông điệp xác thực gửi ngay sau lời chào của máy chủ.
    """

    def message(self) -> dict[str, Any]: ...


class HmacHexAuth:
    """DNSE: HMAC-SHA256 hex of ``{api_key}:{timestamp}:{nonce}``, timestamp in seconds.

    DNSE: HMAC-SHA256 dạng hex của ``{api_key}:{timestamp}:{nonce}``, timestamp tính bằng giây.
    """

    def __init__(self, credentials: Credentials, *, clock: Callable[[], float] = time.time) -> None:
        self._credentials = credentials
        self._clock = clock
        self._last_nonce = 0

    def message(self) -> dict[str, Any]:
        now = self._clock()
        # Microseconds, as the SDK does; never repeat a nonce even if the clock stalls.
        nonce = max(int(now * 1_000_000), self._last_nonce + 1)
        self._last_nonce = nonce
        api_key = self._credentials["api_key"].reveal()
        timestamp = int(now)
        signature = hmac.new(
            self._credentials["api_secret"].reveal().encode("utf-8"),
            f"{api_key}:{timestamp}:{nonce}".encode(),
            hashlib.sha256,
        ).hexdigest()
        return {
            "action": "auth",
            "api_key": api_key,
            "signature": signature,
            "timestamp": timestamp,
            "nonce": str(nonce),
        }


class SsiFcToken:
    """SSI FastConnect Data: a Bearer token from ``consumerID`` and ``consumerSecret`` (the account's key).

    SSI FastConnect Data: token Bearer đổi từ ``consumerID`` và ``consumerSecret`` (khoá của tài khoản).

    The token is a JWT; it is kept in memory and renewed when less than ``renew_before_s`` is left, as
    the official SDK does. It goes only in the ``Authorization`` header of negotiate and of the
    WebSocket upgrade, never in a frame, a log or an error. ``post`` sends one JSON POST to the
    declared token endpoint and returns ``(status, body)``.
    """

    def __init__(
        self,
        credentials: Credentials,
        post: Callable[[dict[str, Any]], tuple[int, Any]],
        *,
        clock: Callable[[], float] = time.time,
        renew_before_s: float = 3600.0,
    ) -> None:
        self._credentials = credentials
        self._post = post
        self._clock = clock
        self._renew_before_s = renew_before_s
        self._token = ""
        self._expires_at = 0.0
        self._lock = threading.Lock()

    def token(self) -> str:
        with self._lock:
            if not self._token or self._expires_at - self._clock() < self._renew_before_s:
                self._token, self._expires_at = self._fetch()
            return self._token

    def forget(self) -> None:
        """Drop the token so the next call asks for a new one (after a 401)."""
        with self._lock:
            self._token, self._expires_at = "", 0.0

    def _fetch(self) -> tuple[str, float]:
        body = {
            "consumerID": self._credentials["api_key"].reveal(),
            "consumerSecret": self._credentials["api_secret"].reveal(),
        }
        status, reply = self._post(body)
        data = reply.get("data") if isinstance(reply, dict) else None
        token = data.get("accessToken") if isinstance(data, dict) else None
        if status in (401, 403) or (status == 200 and not token) or status == 400:
            message = str(reply.get("message") if isinstance(reply, dict) else "")[:200]
            raise AuthError(f"ssi từ chối cấp token stream (HTTP {status}): {message}", source="ssi")
        if status != 200 or not isinstance(token, str):
            raise SourceError(f"ssi trả token stream lạ (HTTP {status})", source="ssi", http_status=status)
        return token, _jwt_expiry(token, self._clock())


def _jwt_expiry(token: str, now: float) -> float:
    """``exp`` of a JWT (not verified: only to know when to renew); one hour from now if unreadable."""
    try:
        part = token.split(".")[1]
        claims = json.loads(base64.urlsafe_b64decode(part + "=" * (-len(part) % 4)))
        return float(claims["exp"])
    except (IndexError, ValueError, KeyError, TypeError):
        return now + 3600.0


_KINDS: dict[str, type[HmacHexAuth]] = {"hmac_hex": HmacHexAuth}


def stream_auth(kind: str, credentials: Credentials) -> StreamAuth:
    auth = _KINDS.get(kind)
    if auth is None:
        raise UnsupportedError(f"Chưa hỗ trợ kiểu xác thực stream {kind!r}")
    return auth(credentials)
