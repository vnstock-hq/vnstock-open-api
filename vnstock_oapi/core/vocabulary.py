"""The shared vocabulary (``spec/vocabulary.toml``) and each source's spelling of it (``mapping.toml``).

Từ vựng chung và cách từng nguồn viết từ vựng đó.

Every common value (interval, asset, exchange, common symbol, secret field name) is named once, in
``spec/vocabulary.toml``. A source module never keeps its own table of them: it asks :func:`native`
for its spelling, read from ``spec/sources/<source>/mapping.toml``.
"""

from __future__ import annotations

import sys
from functools import cache
from importlib import resources
from typing import Any

from vnstock_oapi.errors import InputError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = [
    "KINDS", "vocabulary", "values", "canonical", "mapping", "native", "symbol", "sensitive_keys", "register",
]

KINDS = ("interval", "asset", "exchange", "report_period")


def _read(*parts: str) -> dict[str, Any]:
    node = resources.files("vnstock_oapi").joinpath("spec")
    for part in parts:
        node = node.joinpath(part)
    data: dict[str, Any] = tomllib.loads(node.read_text(encoding="utf-8"))
    return data


@cache
def vocabulary() -> dict[str, Any]:
    """The whole of ``spec/vocabulary.toml``.

    Toàn bộ tệp từ vựng chung.
    """
    return _read("vocabulary.toml")


def values(kind: str) -> tuple[str, ...]:
    """Canonical values of ``kind`` (``interval``, ``asset``, ``exchange``), in order.

    Các giá trị chuẩn của một loại từ vựng.
    """
    return tuple(vocabulary()[kind]["values"])


def canonical(kind: str, value: str) -> str:
    """``value`` in its canonical spelling, from the value itself or one of its aliases.

    Cách viết chuẩn của ``value``, nhận cả bí danh.

    Raises:
        InputError: Neither a value nor an alias of ``kind``.
    """
    entry = vocabulary()[kind]
    text = str(value).strip()
    if text in entry["values"]:
        return text
    alias = entry.get("aliases", {}).get(text)
    if alias is not None:
        return str(alias)
    raise InputError(f"{entry['vi']} {value!r} không hợp lệ. Giá trị chuẩn: {', '.join(entry['values'])}")


# Mappings of sources registered by an extension package (``core/registry.py``), by source name.
_REGISTERED: dict[str, dict[str, Any]] = {}


def register(source: str, table: dict[str, Any] | None) -> None:
    if table is None:
        _REGISTERED.pop(source, None)
    else:
        _REGISTERED[source] = table


def mapping(source: str) -> dict[str, Any]:
    """``spec/sources/<source>/mapping.toml``, or the mapping a registered source brought; empty when
    the source has none.

    Bảng ánh xạ của một nguồn.
    """
    registered = _REGISTERED.get(source)
    return registered if registered is not None else _packaged_mapping(source)


@cache
def _packaged_mapping(source: str) -> dict[str, Any]:
    try:
        return _read("sources", source, "mapping.toml")
    except FileNotFoundError:
        return {}


def native(source: str, kind: str, value: str) -> str:
    """How ``source`` writes ``value`` of ``kind``. A value already in the source's spelling, or listed
    under ``native_only``, passes as is.

    Cách ``source`` viết một giá trị chung.

    Raises:
        InputError: Not a value of ``kind``, or one the source does not offer.
    """
    table: dict[str, str] = mapping(source).get(kind, {})
    passthrough = [*mapping(source).get("native_only", {}).get(kind, []), *table.values()]
    if str(value).strip() in passthrough:
        return str(value).strip()  # already the source's own spelling
    common = canonical(kind, value)
    if common not in table:
        offered = ", ".join([*table, *mapping(source).get("native_only", {}).get(kind, [])]) or "không có"
        raise InputError(f"{source} không có {kind} {common!r}. Nguồn này có: {offered}", source=source)
    return table[common]


def symbol(source: str, code: str, operation: str | None = None) -> str:
    """How ``source`` names the instrument with common code ``code`` (``UPCOMINDEX`` → ``UPCOM``).

    Tên nguồn dùng cho một mã chung; mã không có trong bảng giữ nguyên, viết hoa.
    """
    common = code.strip().upper()
    table = mapping(source)
    by_operation = table.get("symbol_by_operation", {}).get(operation or "", {})
    return str(by_operation.get(common) or table.get("symbol", {}).get(common) or common)


def sensitive_keys() -> frozenset[str]:
    """Field names that must never be logged or written into an envelope.

    Tên trường không bao giờ được ghi ra log hay phong bì.
    """
    return frozenset(vocabulary()["sensitive"]["keys"])
