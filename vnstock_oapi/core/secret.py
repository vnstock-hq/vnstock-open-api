"""``Secret``: holds a key without leaking it through repr, logs, f-strings or pickle.

``Secret``: giữ khoá mà không để lọt qua repr, log, f-string hay pickle.

Only ``core/auth.py`` and source login bodies call :meth:`Secret.reveal`; a test enforces that.
"""

from __future__ import annotations

import hmac
from typing import NoReturn

__all__ = ["Secret", "mask"]

_VISIBLE_TAIL = 4


def mask(value: object) -> str:
    """Mask a value, keeping at most the last 4 characters; 8 characters or fewer reveal nothing.

    Che một giá trị, giữ tối đa 4 ký tự cuối; chuỗi từ 8 ký tự trở xuống không lộ gì.
    """
    text = str(value)
    if len(text) <= 2 * _VISIBLE_TAIL:
        return "***"
    return "***" + text[-_VISIBLE_TAIL:]


class Secret:
    """A credential value that never prints itself.

    Một khoá bí mật không bao giờ tự in ra.
    """

    __slots__ = ("_value",)

    def __init__(self, value: str) -> None:
        if not isinstance(value, str) or not value.strip():
            raise ValueError("Secret cannot be empty")
        self._value = value.strip()

    def reveal(self) -> str:
        """Return the real value. Only for signing or sending the key to its source.

        Trả giá trị thật. Chỉ dùng khi ký hoặc gửi khoá tới đúng nguồn của nó.
        """
        return self._value

    def __repr__(self) -> str:
        return f"Secret('{mask(self._value)}')"

    __str__ = __repr__

    def __format__(self, spec: str) -> str:
        return repr(self)

    def __eq__(self, other: object) -> bool:
        if not isinstance(other, Secret):
            return NotImplemented
        return hmac.compare_digest(self._value, other._value)

    __hash__ = None  # type: ignore[assignment]

    def __reduce__(self) -> NoReturn:
        raise TypeError("Secret cannot be pickled")

    def __copy__(self) -> Secret:
        return self

    def __deepcopy__(self, memo: object) -> Secret:
        return self
