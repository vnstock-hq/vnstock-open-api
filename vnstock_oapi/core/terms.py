"""Source terms summary, read from ``spec/sources/<source>/terms.toml``.

Tóm tắt điều khoản của nguồn, đọc từ ``spec/sources/<nguồn>/terms.toml``.

The one place a source's terms summary is written; ``vnstock-oapi terms <source>`` prints it.
"""

from __future__ import annotations

import sys
from dataclasses import dataclass
from functools import cache
from importlib import resources

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = ["Terms", "load_terms", "register"]


@dataclass(frozen=True)
class Terms:
    source: str
    title: str
    terms_url: str
    docs_url: str
    checked_at: str
    summary: tuple[str, ...]
    note: str
    # Where to get an account or a plan; a referral link says so in signup_note.
    signup_url: str = ""
    signup_note: str = ""

    def __str__(self) -> str:
        lines = [f"{self.title} · điều khoản rà ngày {self.checked_at}"]
        lines += [f"- {line}" for line in self.summary]
        lines.append(f"Điều khoản gốc: {self.terms_url}")
        if self.note:
            lines.append(self.note)
        if self.signup_url:
            note = f" ({self.signup_note})" if self.signup_note else ""
            lines.append(f"Đăng ký gói: {self.signup_url}{note}")
        return "\n".join(lines)


# Terms of sources registered by an extension package (``core/registry.py``), by source name.
_REGISTERED: dict[str, Terms] = {}


def register(source: str, terms: Terms | None) -> None:
    if terms is None:
        _REGISTERED.pop(source, None)
    else:
        _REGISTERED[source] = terms


def load_terms(source: str) -> Terms:
    registered = _REGISTERED.get(source)
    return registered if registered is not None else _packaged(source)


@cache
def _packaged(source: str) -> Terms:
    text = (
        resources.files("vnstock_oapi")
        .joinpath("spec").joinpath("sources").joinpath(source).joinpath("terms.toml")
        .read_text(encoding="utf-8")
    )
    data = tomllib.loads(text)
    return Terms(
        source=data["source"],
        title=data["title"],
        terms_url=data["terms_url"],
        docs_url=data.get("docs_url", ""),
        checked_at=data["checked_at"],
        summary=tuple(data["summary"]),
        note=data.get("note", ""),
        signup_url=data.get("signup_url", ""),
        signup_note=data.get("signup_note", ""),
    )
