"""vnstock-oapi: log in and fetch raw data through official broker Open APIs, with your own keys.

vnstock-oapi: đăng nhập và nạp dữ liệu thô qua Open API chính thức của công ty chứng khoán,
bằng khoá của chính bạn.

    import vnstock_oapi as oapi

    src = oapi.source("<nguồn>")    # the source you picked / nguồn bạn chọn
    bars = src.ohlc("FPT", start="2026-01-01", end="2026-10-27", interval="1D")
    bars[0].body            # the source's body, verbatim
    bars.save("fpt.jsonl")

Importing this package sends nothing, reads no key and writes no file.
"""

from __future__ import annotations

from typing import Any

from vnstock_oapi import errors
from vnstock_oapi._version import __version__
from vnstock_oapi.core import registry
from vnstock_oapi.core.envelope import Envelope, Envelopes, load, wrap
from vnstock_oapi.core.source import Source, SourceSpec
from vnstock_oapi.core.terms import Terms, load_terms
from vnstock_oapi.errors import UnsupportedError

__all__ = [
    "__version__",
    "source",
    "sources",
    "status",
    "terms",
    "Envelope",
    "Envelopes",
    "wrap",
    "load",
    "errors",
    "Source",
    "register",
]

# Submodules become package attributes once imported: a source must never take one of these names.
_SUBMODULES = frozenset({"cli", "core", "errors", "ext", "sources", "stream"})

_DEFAULT: dict[str, Source] = {}
_PROFILES: dict[tuple[str, str], Source] = {}


def source(name: str, profile: str = "default") -> Source:
    """Return the source object for ``name`` and a credential profile.

    Đối tượng nguồn theo tên và hồ sơ khoá.
    """
    spec = registry.spec(name)
    if spec is None:
        raise UnsupportedError(f"Không có nguồn {name!r}. Có: {', '.join(registry.names())}")
    if profile == "default":
        return _DEFAULT.setdefault(spec.name, Source(spec))
    return _PROFILES.setdefault((spec.name, profile), Source(spec, profile))


def sources() -> list[str]:
    """Built-in sources, then the ones registered in this process.

    Nguồn có sẵn, rồi các nguồn đã đăng ký trong tiến trình này.
    """
    return registry.names()


def status(profile: str = "default") -> list[dict[str, Any]]:
    """Keys and sessions of every source on this machine. No network call.

    Khoá và phiên của mọi nguồn trên máy. Không gọi mạng.
    """
    return [source(name, profile).status() for name in registry.names()]


def terms(name: str) -> Terms:
    return load_terms(name.lower())


def register(
    spec: SourceSpec,
    *,
    terms: Terms,
    producer: tuple[str, str],
    mapping: dict[str, Any] | None = None,
    market_paths: dict[str, str] | None = None,
) -> Source:
    """Register a source declared outside vnstock-oapi, for this process. Returns its default ``Source``.

    Đăng ký một nguồn khai ngoài vnstock-oapi, trong tiến trình này. Trả ``Source`` mặc định của nó.

    The source runs under the same floor as the built-in ones (https, host allowlist, GET only except
    its token endpoint, redaction, shared session store) and its envelopes name ``producer`` as their
    maker. See ``vnstock_oapi.ext``, ``docs/viet-nguon-cua-ban.md``, or ``docs/write-your-own-source.md``.

    Args:
        spec: The declaration, built with ``vnstock_oapi.ext``.
        terms: Terms summary of the source, with the link to its terms (``terms_url``).
        producer: ``(package name, version)`` of the package that declares the source.
        mapping: The source's spelling of shared values, same shape as ``mapping.toml``.
        market_paths: ``{op path: reason}`` for market-data paths that look like account paths
            (``stocks/{symbol}/trading/orders``: market-wide order statistics). Exact paths, never a
            prefix; a trading token or an account path parameter is never accepted.

    Raises:
        InputError: The declaration is incomplete or malformed.
        PolicyError: Built-in name or host, order or account path, private key, or ``vnstock-oapi``
            as producer.
    """
    registered = registry.add(
        spec,
        terms=terms,
        producer=producer,
        mapping=mapping,
        market_paths=market_paths,
        reserved=frozenset(globals()) | _SUBMODULES,
    )
    # A re-registration by the same package (module reload) must not keep serving the old spec.
    _forget(registered.name)
    return source(registered.name)


def _forget(name: str) -> None:
    for key in [key for key in _PROFILES if key[0] == name]:
        _PROFILES.pop(key).close()
    if name in _DEFAULT:
        _DEFAULT.pop(name).close()


def __getattr__(name: str) -> Source:
    # ``oapi.ssi`` and friends: one attribute per installed source, resolved on first use.
    if registry.spec(name) is not None:
        return source(name)
    raise AttributeError(f"module 'vnstock_oapi' has no attribute {name!r}")


def __dir__() -> list[str]:
    return sorted(set(globals()) | set(registry.names()))
