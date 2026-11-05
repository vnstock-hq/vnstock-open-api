"""Mask sensitive data before it reaches a log, an error or an envelope.

Che dữ liệu nhạy cảm trước khi tới log, lỗi hay phong bì.

Field list follows ``ssi_sdk.utils.redact`` and adds the key and signature headers of the other sources.
"""

from __future__ import annotations

import logging
import re
from typing import Any
from urllib.parse import urlsplit, urlunsplit

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.secret import mask

__all__ = ["SENSITIVE_KEYS", "is_sensitive", "redact", "mask_bearer", "safe_url", "RedactFilter"]

# Named once, in spec/vocabulary.toml.
SENSITIVE_KEYS = vocabulary.sensitive_keys()
_NORMALIZED_SENSITIVE_KEYS = frozenset(key.replace("-", "").replace("_", "") for key in SENSITIVE_KEYS)

_BEARER = re.compile(r"(?i)\b(bearer)\s+([A-Za-z0-9._~+/=-]+)")
_SIGNATURE = re.compile(r'(?i)(signature=")([^"]+)(")')
_KEY_ID = re.compile(r'(?i)(keyId=")([^"]+)(")')


def is_sensitive(key: object) -> bool:
    return str(key).lower().replace("-", "").replace("_", "") in _NORMALIZED_SENSITIVE_KEYS


def mask_bearer(text: str) -> str:
    """Mask the token in ``Bearer <token>`` and the signature and keyId in ``X-Signature``.

    Che token trong ``Bearer <token>``, chữ ký và keyId trong ``X-Signature``.
    """
    text = _BEARER.sub(lambda m: f"{m.group(1)} {mask(m.group(2))}", text)
    text = _SIGNATURE.sub(lambda m: f"{m.group(1)}{mask(m.group(2))}{m.group(3)}", text)
    return _KEY_ID.sub(lambda m: f"{m.group(1)}{mask(m.group(2))}{m.group(3)}", text)


def redact(data: Any) -> Any:
    """A copy of ``data`` with every sensitive field masked, recursively. ``data`` is not changed.

    Bản sao của ``data`` đã che mọi trường nhạy cảm, đệ quy; không sửa ``data``.
    """
    if isinstance(data, dict):
        return {
            key: mask(value) if is_sensitive(key) and value is not None else redact(value)
            for key, value in data.items()
        }
    if isinstance(data, (list, tuple)):
        return [redact(item) for item in data]
    if isinstance(data, str):
        return mask_bearer(data)
    return data


def safe_url(url: str) -> str:
    """The URL without query, fragment or userinfo, safe to log.

    URL đã bỏ query, fragment và userinfo, an toàn để ghi log.
    """
    parts = urlsplit(url)
    host = parts.hostname or ""
    if parts.port:
        host = f"{host}:{parts.port}"
    return urlunsplit((parts.scheme, host, parts.path, "", ""))


class RedactFilter(logging.Filter):
    """Log filter that masks tokens and signatures in every ``vnstock_oapi`` record.

    Bộ lọc log che token và chữ ký trong mọi bản ghi của ``vnstock_oapi``.
    """

    def filter(self, record: logging.LogRecord) -> bool:
        record.msg = mask_bearer(str(record.msg))
        if record.args:
            if isinstance(record.args, dict):
                record.args = redact(record.args)
            else:
                record.args = tuple(redact(arg) for arg in record.args)
        return True
