"""Errors raised by vnstock-oapi.

Các lỗi của vnstock-oapi.

Names follow the vnstock library where the meaning is the same, so one ``except`` clause works for
both. No error ever carries a key, token, signature or a full response body. Messages are in
Vietnamese because they are shown to end users.
"""

from __future__ import annotations

__all__ = [
    "OapiError",
    "InputError",
    "CredentialError",
    "AuthError",
    "LoginRequiredError",
    "OtpRequiredError",
    "RateLimitError",
    "ClockSkewError",
    "NetworkError",
    "SourceError",
    "UnsupportedError",
    "PolicyError",
]


class OapiError(Exception):
    """Base of every vnstock-oapi error.

    Gốc của mọi lỗi vnstock-oapi.
    """

    def __init__(
        self,
        message: str,
        *,
        source: str | None = None,
        operation: str | None = None,
        http_status: int | None = None,
        code: str | int | None = None,
        url: str | None = None,
    ) -> None:
        super().__init__(message)
        self.message = message
        self.source = source
        self.operation = operation
        self.http_status = http_status
        self.code = code
        self.url = url

    def __str__(self) -> str:
        context = [
            f"{name}={value}"
            for name, value in (
                ("source", self.source),
                ("operation", self.operation),
                ("http_status", self.http_status),
                ("code", self.code),
                ("url", self.url),
            )
            if value is not None
        ]
        return f"{self.message} ({', '.join(context)})" if context else self.message


class InputError(OapiError, ValueError):
    """Bad parameters, or an interval the source does not offer.

    Tham số sai, hoặc nguồn không có khung thời gian đó.
    """


class CredentialError(OapiError):
    """Missing or malformed credentials.

    Không tìm thấy khoá, hoặc khoá sai định dạng.
    """


class AuthError(OapiError):
    """The source refused the credentials (401, 403). Never retried.

    Nguồn từ chối khoá (401, 403). Không tự thử lại.
    """


class LoginRequiredError(AuthError):
    """No valid session, and opening one needs something the user types (an OTP).

    Chưa có phiên còn hạn, và mở phiên cần người dùng gõ (OTP).
    """


class OtpRequiredError(LoginRequiredError):
    """The session expired and the source needs a new OTP.

    Phiên hết hạn và nguồn cần OTP mới.
    """


class RateLimitError(OapiError):
    """Still rate limited (429) after waiting ``Retry-After`` once.

    Vẫn bị giới hạn tần suất (429) sau khi đã chờ ``Retry-After`` một lần.
    """

    def __init__(self, message: str, *, retry_after: float | None = None, **context: object) -> None:
        super().__init__(message, **context)  # type: ignore[arg-type]
        self.retry_after = retry_after


class ClockSkewError(OapiError):
    """The local clock is further from the source's than its signing window allows.

    Đồng hồ máy lệch so với nguồn quá giới hạn ký.
    """


class NetworkError(OapiError):
    """Timeout, connection or TLS failure.

    Hết thời gian chờ, mất kết nối, hoặc lỗi TLS.
    """


class SourceError(OapiError):
    """Server error (5xx), a redirect, or a body that does not match the docs.

    Nguồn lỗi 5xx, trả chuyển hướng, hoặc body không đúng tài liệu.
    """


class UnsupportedError(OapiError):
    """The source has no such operation.

    Nguồn không có thao tác này.
    """


class PolicyError(OapiError):
    """The request breaks vnstock-oapi's own policy: unknown host, non-GET method, a key in a request.

    Yêu cầu trái chính sách của vnstock-oapi: host lạ, method không phải GET, khoá trong request.

    This is a guard against bugs and edits, never something a user is expected to work around.
    """
