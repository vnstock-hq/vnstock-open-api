"""Sources declared outside this package, registered explicitly by the code that wants them.

Nguồn khai ngoài gói này, do chính mã cần dùng đăng ký tường minh.

A package built on vnstock-oapi declares a :class:`SourceSpec` with the extension API
(``vnstock_oapi.ext``) and calls ``oapi.register(...)`` when it is imported. Nothing is discovered: no
entry point, no scan of installed packages. A registered source runs under the same floor as the
built-in ones (``Transport``: https, host allowlist, GET only, no redirect; redaction; shared session
store), and on top of it:

- its hosts must not be hosts of a built-in source: one key, one owner of its session;
- no declared path names the user's orders, account or holdings; a market-data path that only looks
  like one is declared one by one in ``market_paths`` with the reason, never a prefix;
- no credential field reads a private key (only order signing needs one);
- its envelopes carry the extension's own ``producer``, never ``vnstock-oapi``;
- it must bring a terms summary with the link to the source's terms.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Mapping
from dataclasses import replace
from typing import Any
from urllib.parse import urlsplit

from vnstock_oapi.core import terms as terms_mod
from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.envelope import PRODUCER
from vnstock_oapi.core.source import VERBS, Source, SourceSpec
from vnstock_oapi.core.terms import Terms
from vnstock_oapi.errors import InputError, PolicyError
from vnstock_oapi.sources import SPECS

__all__ = ["account_like", "never_market", "add", "remove", "spec", "names", "is_builtin", "market_paths"]

logger = logging.getLogger("vnstock_oapi.registry")

# Whole path segments that name the user's orders, account or holdings. Whole segments only: ``order-book``,
# ``orderbook`` and ``/trading/foreign`` are market data and pass.
ACCOUNT_SEGMENTS = frozenset({
    "order", "orders", "account", "accounts", "position", "positions", "portfolio", "portfolios",
    "balance", "balances",
})
# Never market data, whatever reason is given: a trading token, or a path parameter naming an account.
_NEVER_MARKET = re.compile(r"trading[-_]?token|\{[^}]*account[^}]*\}", re.I)
_SEGMENT = re.compile(r"[/?&=]")
_NAME = re.compile(r"^[a-z][a-z0-9_]{1,31}$")
_MIN_REASON = 10

_REGISTERED: dict[str, SourceSpec] = {}
_MARKET_PATHS: dict[str, dict[str, str]] = {}


def never_market(path: str) -> bool:
    return _NEVER_MARKET.search(path) is not None


def account_like(path: str) -> bool:
    """Whether ``path`` names the user's orders, account, holdings or a trading token.

    Đường dẫn có nói tới lệnh, tài khoản, danh mục của người dùng hay trading token không.
    """
    segments = (segment for segment in _SEGMENT.split(path.lower()) if segment)
    return never_market(path) or any(segment in ACCOUNT_SEGMENTS for segment in segments)


def market_paths(name: str) -> dict[str, str]:
    """Paths a registered source declared as market data although they look like account paths, with why.

    Đường dẫn nguồn đăng ký khai là dữ liệu thị trường dù trông giống đường dẫn tài khoản, kèm lý do.
    """
    return dict(_MARKET_PATHS.get(name, {}))


def spec(name: str) -> SourceSpec | None:
    key = name.lower()
    return SPECS.get(key) or _REGISTERED.get(key)


def names() -> list[str]:
    return [*SPECS, *_REGISTERED]


def is_builtin(name: str) -> bool:
    return name.lower() in SPECS


def add(
    source: SourceSpec,
    *,
    terms: Terms,
    producer: tuple[str, str],
    mapping: Mapping[str, Any] | None = None,
    market_paths: Mapping[str, str] | None = None,
    reserved: frozenset[str] = frozenset(),
) -> SourceSpec:
    """Check and register one source; return the spec as registered (with its producer).

    Kiểm rồi đăng ký một nguồn; trả spec đã đăng ký (kèm producer).

    Raises:
        InputError: The declaration is incomplete or malformed.
        PolicyError: The declaration crosses the floor (built-in host, order path, private key,
            ``vnstock-oapi`` as producer).
    """
    name = source.name
    if not _NAME.fullmatch(name):
        raise InputError(f"Tên nguồn {name!r} phải viết thường, bắt đầu bằng chữ, 2–32 ký tự a-z 0-9 _")
    if name in SPECS or name in reserved:
        raise PolicyError(f"Tên {name!r} đã thuộc vnstock-oapi; chọn tên khác", source=name)
    owner = _REGISTERED.get(name)
    if owner is not None and owner.producer != _producer(producer):
        raise PolicyError(
            f"Nguồn {name!r} đã được {owner.producer['name']} đăng ký", source=name  # type: ignore[index]
        )
    _check_hosts(source)
    exempt = _check_market_paths(source, market_paths or {})
    _check_paths(source, exempt)
    _check_members(source)
    if any("PRIVATE" in field.env_suffix.upper() for field in source.credentials):
        raise PolicyError(
            f"{name}: vnstock-oapi không đọc khoá riêng (private key); khoá đó chỉ dùng để ký lệnh",
            source=name,
        )
    if terms.source != name or not terms.terms_url.startswith("https://"):
        raise InputError(f"{name}: terms phải cùng tên nguồn và có terms_url https://", source=name)
    checked = _check_mapping(name, mapping or {})
    registered = replace(source, producer=_producer(producer))
    _REGISTERED[name] = registered
    _MARKET_PATHS[name] = exempt
    for path, reason in exempt.items():
        logger.info("%s khai %s là dữ liệu thị trường: %s", name, path, reason)
    terms_mod.register(name, terms)
    vocabulary.register(name, checked)
    return registered


def remove(name: str) -> None:
    _REGISTERED.pop(name, None)
    _MARKET_PATHS.pop(name, None)
    terms_mod.register(name, None)
    vocabulary.register(name, None)


def _producer(producer: tuple[str, str]) -> dict[str, str]:
    name, version = (str(part).strip() for part in producer)
    if not name or not version:
        raise InputError("producer cần (tên gói, phiên bản), ví dụ ('vnstock-oapi-mysrc', '0.1.0')")
    if name.lower() == PRODUCER["name"]:
        raise PolicyError("producer phải là tên gói của bạn, không phải vnstock-oapi")
    return {"name": name, "version": version}


def _check_hosts(source: SourceSpec) -> None:
    if not source.hosts:
        raise InputError(f"{source.name}: cần ít nhất một host", source=source.name)
    base = urlsplit(source.base_url)
    if base.scheme != "https" or base.hostname not in source.hosts:
        raise InputError(
            f"{source.name}: base_url phải là https:// và host của nó nằm trong hosts", source=source.name
        )
    taken = {host: spec.name for spec in SPECS.values() for host in spec.hosts}
    clash = sorted(host for host in source.hosts if host in taken)
    if clash:
        raise PolicyError(
            f"{source.name}: host {', '.join(clash)} thuộc nguồn có sẵn {taken[clash[0]]}. Một khoá chỉ có "
            "một phiên; hãy đóng góp thao tác thiếu vào nguồn đó thay vì khai nguồn thứ hai",
            source=source.name,
        )


def _check_market_paths(source: SourceSpec, declared: Mapping[str, str]) -> dict[str, str]:
    op_paths = {op.path for op in source.ops.values()}
    exempt: dict[str, str] = {}
    for path, reason in declared.items():
        text = str(reason).strip()
        if path not in op_paths:
            raise InputError(
                f"{source.name}: market_paths chỉ nhận đúng đường dẫn của một Op đã khai; {path!r} không có",
                source=source.name,
            )
        if never_market(path):
            raise PolicyError(
                f"{source.name}: {path!r} có trading token hay tham số tài khoản, "
                "không bao giờ là dữ liệu thị trường",
                source=source.name,
            )
        if len(text) < _MIN_REASON:
            raise InputError(
                f"{source.name}: market_paths[{path!r}] cần lý do vì sao đây là dữ liệu thị trường",
                source=source.name,
            )
        exempt[path] = text
    return exempt


def _check_paths(source: SourceSpec, exempt: Mapping[str, str]) -> None:
    if not source.ops:
        raise InputError(f"{source.name}: cần ít nhất một thao tác (ops)", source=source.name)
    paths = {f"ops[{key!r}]": op.path for key, op in source.ops.items() if op.path not in exempt}
    # Token endpoints are the only POST the transport allows; they are checked like any other path.
    auth = source.auth(Source(source))
    for attribute in ("login_path", "refresh_path"):
        value = getattr(auth, attribute, None)
        if isinstance(value, str):
            paths[attribute] = value
    for where, path in paths.items():
        if account_like(path):
            raise PolicyError(
                f"{source.name}: {where} = {path!r} giống đường dẫn lệnh, tài khoản hay trading token. "
                "vnstock-oapi chỉ chở dữ liệu thị trường. Nếu đây là dữ liệu thị trường, khai đúng đường dẫn "
                "này trong market_paths kèm lý do",
                source=source.name,
            )


def _check_members(source: SourceSpec) -> None:
    unknown = sorted(set(source.verbs) - set(VERBS))
    if unknown:
        raise InputError(
            f"{source.name}: verbs chỉ gồm {', '.join(VERBS)}; {', '.join(unknown)} đặt vào extras",
            source=source.name,
        )
    shadowed = sorted(name for name in source.extras if hasattr(Source, name) or name.startswith("_"))
    if shadowed:
        raise InputError(
            f"{source.name}: extras {', '.join(shadowed)} trùng tên thuộc tính của Source", source=source.name
        )


def _check_mapping(name: str, mapping: Mapping[str, Any]) -> dict[str, Any]:
    # Same shape as spec/sources/<source>/mapping.toml; the common side must be a canonical value.
    table = {key: dict(value) for key, value in mapping.items()}
    for kind in vocabulary.KINDS:
        for common in table.get(kind, {}):
            if vocabulary.canonical(kind, common) != common:
                raise InputError(
                    f"{name}: mapping[{kind!r}] dùng {common!r}; khoá phải là giá trị chuẩn "
                    f"{vocabulary.canonical(kind, common)!r}",
                    source=name,
                )
    return table
