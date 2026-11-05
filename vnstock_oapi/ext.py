"""The extension API: what a package needs to declare its own source on top of vnstock-oapi.

API mở rộng: những gì một gói cần để khai nguồn của riêng mình trên nền vnstock-oapi.

    import vnstock_oapi as oapi
    from vnstock_oapi import ext

    SPEC = ext.SourceSpec(
        name="mysrc", title="My source", base_url="https://api.example.com/v1",
        hosts=frozenset({"api.example.com"}), credentials=(), auth=ext.PublicAuth,
        ops={"bars": ext.Op("bars", "/bars/{symbol}", doc="https://example.com/docs/bars")},
    )
    oapi.register(SPEC, terms=ext.Terms(...), producer=("vnstock-oapi-mysrc", "0.1.0"))
    oapi.mysrc.raw("bars", symbol="FPT")

Only names listed in ``__all__`` are the extension API. They change only with ``EXT_API``: a package
checks ``ext.EXT_API == 1`` (or ``>=``) at import. Anything else under ``vnstock_oapi.core`` may change
in any release. A registered source runs under the same floor as the built-in ones; see
``oapi.register``, ``docs/viet-nguon-cua-ban.md``, or ``docs/write-your-own-source.md``.

Chỉ những tên trong ``__all__`` là API mở rộng, đổi cùng ``EXT_API``. Phần còn lại của
``vnstock_oapi.core`` có thể đổi ở bất kỳ bản phát hành nào.
"""

from __future__ import annotations

from vnstock_oapi import register
from vnstock_oapi.core.auth import (
    ApiKeyHeaderAuth,
    Auth,
    OtpTokenAuth,
    PublicAuth,
    SignedAuth,
    SignHeaders,
    TokenAuth,
    TokenSet,
    fallback_expiry,
    jwt_expiry,
    to_epoch_seconds,
)
from vnstock_oapi.core.credentials import CredentialField, Credentials
from vnstock_oapi.core.envelope import Envelope, Envelopes
from vnstock_oapi.core.redact import redact
from vnstock_oapi.core.source import VERBS, BodyError, Builder, Op, Plan, Samples, Source, SourceSpec
from vnstock_oapi.core.terms import Terms
from vnstock_oapi.core.vocabulary import canonical, native, symbol

__all__ = [
    "EXT_API",
    "register",
    # declaration
    "SourceSpec",
    "Op",
    "Plan",
    "Builder",
    "BodyError",
    "Samples",
    "VERBS",
    "Terms",
    "CredentialField",
    # auth kinds
    "Auth",
    "PublicAuth",
    "ApiKeyHeaderAuth",
    "SignedAuth",
    "SignHeaders",
    "TokenAuth",
    "OtpTokenAuth",
    "TokenSet",
    "Credentials",
    "jwt_expiry",
    "fallback_expiry",
    "to_epoch_seconds",
    # shared vocabulary, for builders
    "canonical",
    "native",
    "symbol",
    # output
    "Source",
    "Envelope",
    "Envelopes",
    "redact",
]

EXT_API = 1
