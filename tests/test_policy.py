"""Source scans for security policies (TLS, User-Agent, OTP, read-only market data, secret reveal).

Quét mã nguồn theo các chính sách bảo mật (TLS, User-Agent, OTP, chỉ đọc dữ liệu thị trường, che khoá).
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "vnstock_oapi"
FILES = {path: path.read_text(encoding="utf-8") for path in SRC.rglob("*.py")}

FORBIDDEN = {
    "TLS off": re.compile(r"verify\s*=\s*False|assert_hostname|CERT_NONE|check_hostname\s*=\s*False"),
    # The one User-Agent lives in core/transport.py; nobody else sets one.
    "own User-Agent": re.compile(r"user_agent_header\s*=\s*(?!USER_AGENT\b)|['\"]User-Agent['\"]\s*:", re.I),
    "OTP automation": re.compile(r"^\s*(import|from)\s+(imaplib|poplib|smtplib)\b", re.M),
    "reads a private key": re.compile(r"CredentialField\([^)]*PRIVATE", re.S),
}
# Order and trading paths must not appear in any source declaration. Deliberately broader than the
# segment rule registered sources get (core/registry.py): this scans the whole module text.
ORDER_PATHS = re.compile(r"/orders?\b|trading-token|/trading/|/accounts\b|/positions\b", re.I)
# Public market data whose path happens to match the pattern above, allowed one by one.
# Never add a prefix here: each entry is one exact path literal of one source.
PUBLIC_PATHS = {
    "fhsc.py": {
        '"/trading/market/session"',  # session state of an exchange
        '"trading/market/session"',
        '"stocks/{symbol}/trading/foreign"',
        '"stocks/{symbol}/trading/foreign/history"',
        '"stocks/{symbol}/trading/history"',
        '"stocks/{symbol}/trading/insider"',
        '"stocks/{symbol}/trading/orders"',  # market-wide order statistics, not the user's orders
        '"stocks/{symbol}/trading/orders/history"',
        '"stocks/{symbol}/trading/proprietary"',
        '"stocks/{symbol}/trading/proprietary/history"',
        '"indices/{symbol}/trading/foreign"',
        '"indices/{symbol}/trading/foreign/history"',
        '"sectors/{slug}/trading/foreign"',
        '"sectors/{slug}/trading/foreign/history"',
        '"quotes/bonds/{symbol}/order-book"',  # bid/ask book of the market
        '"quotes/etfs/{symbol}/order-book"',
        '"quotes/stocks/{symbol}/order-book"',
        '"quotes/stocks/{symbol}/order-book/history"',
    },
}


def _declared(path: Path, text: str) -> str:
    for literal in PUBLIC_PATHS.get(path.name, ()):
        text = text.replace(literal, '""')
    return text


def test_no_forbidden_patterns() -> None:
    hits = [
        f"{path.relative_to(SRC)}: {name}"
        for path, text in FILES.items()
        for name, pattern in FORBIDDEN.items()
        if pattern.search(text) and not (name == "own User-Agent" and path == SRC / "core" / "transport.py")
    ]
    hits += [
        f"{path.relative_to(SRC)}: order path"
        for path, text in FILES.items()
        if path.parent.name == "sources" and ORDER_PATHS.search(_declared(path, text))
    ]
    assert hits == []


def _reveal_outside_login_body(text: str) -> list[int]:
    """Lines of a source module calling ``.reveal()`` outside a ``_login_body`` function."""
    tree = ast.parse(text)
    allowed: set[int] = set()
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef) and node.name == "_login_body":
            allowed |= set(range(node.lineno, (node.end_lineno or node.lineno) + 1))
    return [
        node.lineno
        for node in ast.walk(tree)
        if isinstance(node, ast.Attribute) and node.attr == "reveal" and node.lineno not in allowed
    ]


def test_reveal_only_in_auth_layer() -> None:
    # Core and stream auth may reveal; a source module only inside its login body.
    allowed = {"core/auth.py", "core/secret.py", "stream/auth.py"}
    users = {
        str(p.relative_to(SRC))
        for p, text in FILES.items()
        if ".reveal()" in text and p.parent.name != "sources"
    }
    assert users <= allowed, users - allowed
    leaks = {
        p.name: lines
        for p, text in FILES.items()
        if p.parent.name == "sources" and (lines := _reveal_outside_login_body(text))
    }
    assert leaks == {}


def test_only_transport_imports_http_client() -> None:
    users = {str(p.relative_to(SRC)) for p, text in FILES.items()
             if re.search(r"^\s*import (httpx|requests|urllib3)|^\s*from (httpx|requests|urllib3)",
                          text, re.M)}
    assert users == {"core/transport.py"}


def test_import_has_no_side_effects(tmp_path, monkeypatch) -> None:
    import importlib
    import sys

    saved = {m: sys.modules.pop(m) for m in [m for m in sys.modules if m.startswith("vnstock_oapi")]}
    try:
        importlib.import_module("vnstock_oapi")
        assert not (tmp_path / "state").exists()
    finally:
        # Put the original modules back so later tests share one set of classes (errors, specs...).
        for name in [m for m in sys.modules if m.startswith("vnstock_oapi")]:
            del sys.modules[name]
        sys.modules.update(saved)
