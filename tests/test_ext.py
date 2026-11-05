"""Extension API: a source declared outside the package, registered explicitly, under the same floor.

API mở rộng: nguồn khai ngoài gói, đăng ký tường minh, chạy dưới cùng sàn an toàn.
"""

from __future__ import annotations

from collections.abc import Iterator
from dataclasses import replace
from typing import Any

import httpx
import pytest
from conftest import json_response

import vnstock_oapi as oapi
from vnstock_oapi import ext
from vnstock_oapi.core import registry
from vnstock_oapi.core.transport import USER_AGENT, Transport
from vnstock_oapi.errors import InputError, PolicyError, UnsupportedError

HOST = "api.example.com"
PRODUCER = ("vnstock-oapi-demo", "0.1.0")
TERMS = ext.Terms(
    source="demo",
    title="Demo API",
    terms_url="https://example.com/terms",
    docs_url="https://example.com/docs",
    checked_at="2026-11-01",
    summary=("Personal use only.",),
    note="",
)


def _bars(symbol: str, **_: Any) -> ext.Plan:
    return ext.Plan("bars", {"symbol": ext.symbol("demo", symbol)})


SPEC = ext.SourceSpec(
    name="demo",
    title="Demo",
    base_url=f"https://{HOST}/v1",
    hosts=frozenset({HOST}),
    credentials=(),
    auth=ext.PublicAuth,
    ops={"bars": ext.Op("bars", "/bars/{symbol}")},
    verbs={"ohlc": _bars},
)


@pytest.fixture(autouse=True)
def clean_registry() -> Iterator[None]:
    yield
    for name in list(registry._REGISTERED):
        oapi._forget(name)
        registry.remove(name)


def _serve(source: ext.Source, handler: Any) -> None:
    source._transport = Transport(
        source.name, source.spec.hosts, client=httpx.Client(transport=httpx.MockTransport(handler))
    )


def test_extension_api_surface_is_locked() -> None:
    # Renaming or dropping one of these breaks every package built on EXT_API 1.
    assert ext.EXT_API == 1
    assert set(ext.__all__) == {
        "EXT_API", "register", "SourceSpec", "Op", "Plan", "Builder", "BodyError", "Samples", "VERBS",
        "Terms", "CredentialField", "Auth", "PublicAuth", "ApiKeyHeaderAuth", "SignedAuth", "SignHeaders",
        "TokenAuth", "OtpTokenAuth", "TokenSet", "Credentials", "jwt_expiry",
        "fallback_expiry", "to_epoch_seconds", "canonical", "native", "symbol", "Source", "Envelope",
        "Envelopes", "redact",
    }
    assert all(hasattr(ext, name) for name in ext.__all__)


def test_registered_source_fetches_with_its_own_producer() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return json_response(200, {"t": [1], "c": [10.5]})

    source = oapi.register(SPEC, terms=TERMS, producer=PRODUCER, mapping={"symbol": {"VNINDEX": "VNI"}})
    assert source is oapi.demo and "demo" in oapi.sources() and "demo" in dir(oapi)
    _serve(source, handler)

    envelopes = oapi.demo.ohlc("vnindex")

    assert seen[0].method == "GET" and seen[0].url.path == "/v1/bars/VNI"
    assert seen[0].headers["user-agent"] == USER_AGENT
    assert envelopes[0].producer == {"name": "vnstock-oapi-demo", "version": "0.1.0"}
    assert envelopes[0].body == {"t": [1], "c": [10.5]}
    assert oapi.terms("demo") is TERMS


def test_registered_source_keeps_the_transport_floor() -> None:
    source = oapi.register(SPEC, terms=TERMS, producer=PRODUCER)
    _serve(source, lambda request: json_response(200, {}))
    with pytest.raises(PolicyError):
        source.transport.request("POST", f"https://{HOST}/v1/bars/FPT")
    with pytest.raises(PolicyError):
        source.transport.request("GET", "https://elsewhere.example.org/v1/bars/FPT")


def test_unregistered_name_is_unsupported() -> None:
    with pytest.raises(UnsupportedError):
        oapi.source("demo")


@pytest.mark.parametrize(
    "change",
    [
        {"name": "ssi"},  # built-in name
        {"name": "status"},  # package attribute
        {"name": "ext"},  # submodule
        {"hosts": frozenset({"api.ssi.com.vn", HOST})},  # host of a built-in source
        {"ops": {"orders": ext.Op("orders", "/accounts/{id}/orders")}},  # order path
        {"ops": {"tt": ext.Op("tt", "/auth/trading-token")}},
        {"ops": {"pf": ext.Op("pf", "/v1/portfolio")}},
        {"ops": {"cash": ext.Op("cash", "/me/balance?currency=VND")}},
        {"ops": {"sub": ext.Op("sub", "/sub/{accountNo}/summary")}},
        {"credentials": (ext.CredentialField("private_key", "PRIVATE_KEY"),)},
    ],
)
def test_register_refuses_what_crosses_the_floor(change: dict[str, Any]) -> None:
    with pytest.raises(PolicyError):
        oapi.register(replace(SPEC, **change), terms=replace(TERMS, source=change.get("name", "demo")),
                      producer=PRODUCER)


def test_register_refuses_an_order_path_as_token_endpoint() -> None:
    spec = replace(SPEC, auth=lambda source: ext.TokenAuth(
        source, login_path="/v1/order", login_body=lambda creds, otp: {}, parse_token=lambda b, n: None,
    ))
    with pytest.raises(PolicyError):
        oapi.register(spec, terms=TERMS, producer=PRODUCER)


def test_market_paths_that_only_look_like_orders_pass() -> None:
    # Whole segments only: an order book, foreign trading and session state are market data.
    ops = {
        name: ext.Op(name, path)
        for name, path in {
            "book": "/stocks/{symbol}/order-book",
            "book2": "/orderbook/{symbol}",
            "foreign": "/stocks/{symbol}/trading/foreign",
            "session": "/trading/market/session",
        }.items()
    }
    oapi.register(replace(SPEC, ops=ops), terms=TERMS, producer=PRODUCER)
    assert registry.market_paths("demo") == {}


def test_market_paths_exempt_one_exact_path_with_its_reason() -> None:
    path = "/stocks/{symbol}/trading/orders"
    spec = replace(SPEC, ops={"stats": ext.Op("stats", path)})
    with pytest.raises(PolicyError, match="market_paths"):
        oapi.register(spec, terms=TERMS, producer=PRODUCER)
    reason = "Thống kê lệnh toàn thị trường, không phải lệnh của người dùng"
    oapi.register(spec, terms=TERMS, producer=PRODUCER, market_paths={path: reason})
    assert registry.market_paths("demo") == {path: reason}


@pytest.mark.parametrize(
    ("path", "error"),
    [
        ("/accounts/{accountNo}/orders", PolicyError),  # an account parameter is never market data
        ("/auth/trading-token", PolicyError),
        ("/stocks/{symbol}/trading", InputError),  # not a declared op path: no prefixes
    ],
)
def test_market_paths_never_exempt_accounts_tokens_or_prefixes(path: str, error: type[Exception]) -> None:
    ops = {"x": ext.Op("x", "/accounts/{accountNo}/orders"), "t": ext.Op("t", "/auth/trading-token")}
    with pytest.raises(error):
        oapi.register(replace(SPEC, ops=ops), terms=TERMS, producer=PRODUCER,
                      market_paths={path: "Dữ liệu thị trường, đã đọc tài liệu nguồn"})


def test_market_paths_need_a_reason() -> None:
    path = "/stocks/{symbol}/trading/orders"
    with pytest.raises(InputError):
        oapi.register(replace(SPEC, ops={"stats": ext.Op("stats", path)}), terms=TERMS,
                      producer=PRODUCER, market_paths={path: "ok"})


@pytest.mark.parametrize("producer", [("vnstock-oapi", "0.1.0"), ("VNSTOCK-OAPI", "1")])
def test_register_refuses_vnstock_oapi_as_producer(producer: tuple[str, str]) -> None:
    with pytest.raises(PolicyError):
        oapi.register(SPEC, terms=TERMS, producer=producer)


@pytest.mark.parametrize(
    "kwargs",
    [
        {"producer": ("", "0.1.0")},
        {"terms": replace(TERMS, terms_url="http://example.com/terms")},
        {"terms": replace(TERMS, source="other")},
        {"mapping": {"interval": {"1h": "60"}}},  # common side must be canonical: 1H
    ],
)
def test_register_refuses_incomplete_declarations(kwargs: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        oapi.register(SPEC, **{"terms": TERMS, "producer": PRODUCER, **kwargs})


@pytest.mark.parametrize(
    "change",
    [
        {"base_url": "http://api.example.com/v1"},
        {"base_url": "https://other.example.com/v1"},
        {"ops": {}},
        {"verbs": {"orderbook": _bars}},  # not a common verb: belongs in extras
        {"extras": {"raw": _bars}},  # shadows a Source method
    ],
)
def test_register_refuses_malformed_specs(change: dict[str, Any]) -> None:
    with pytest.raises(InputError):
        oapi.register(replace(SPEC, **change), terms=TERMS, producer=PRODUCER)


def test_reregistering_is_for_the_same_package_only() -> None:
    first = oapi.register(SPEC, terms=TERMS, producer=PRODUCER)
    again = oapi.register(replace(SPEC, title="Demo 2"), terms=TERMS, producer=PRODUCER)
    assert again is not first and oapi.demo.spec.title == "Demo 2"
    with pytest.raises(PolicyError):
        oapi.register(SPEC, terms=TERMS, producer=("someone-else", "1.0"))
