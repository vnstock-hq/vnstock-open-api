from __future__ import annotations

import copy
import json
import os
import pickle
import re

import httpx
import pytest

from vnstock_oapi.core import credentials
from vnstock_oapi.core.envelope import Envelope, Envelopes, load, wrap
from vnstock_oapi.core.redact import mask_bearer, redact, safe_url
from vnstock_oapi.core.secret import Secret
from vnstock_oapi.core.session_store import SessionStore
from vnstock_oapi.core.transport import USER_AGENT, Transport, _wait_seconds
from vnstock_oapi.errors import CredentialError, PolicyError, RateLimitError


def test_secret_never_prints_itself() -> None:
    secret = Secret("super-secret-value-1234")
    for text in (repr(secret), str(secret), f"{secret}", f"{secret!r}", "%s" % secret):  # noqa: UP031
        assert "super-secret" not in text
        assert text.endswith("1234')")
    assert copy.deepcopy(secret) is secret
    with pytest.raises(TypeError):
        pickle.dumps(secret)
    assert secret.reveal() == "super-secret-value-1234"


def test_redaction() -> None:
    data = {
        "apiKey": "abcdefghijk",
        "api-key": "abcdefghijk",
        "access-token": "zzzzzzzzzzzz",
        "nested": [{"accessToken": "zzzzzzzzzzzz"}],
        "symbol": "FPT",
    }
    out = redact(data)
    assert out["apiKey"] == "***hijk" and out["api-key"] == "***hijk"
    assert out["access-token"] == "***zzzz" and out["nested"][0]["accessToken"] == "***zzzz"
    assert out["symbol"] == "FPT"
    assert "eyJhbGciOi" not in mask_bearer("Authorization: Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig")
    assert safe_url("https://u:p@api.ssi.com.vn/x?apiKey=1#f") == "https://api.ssi.com.vn/x"


def test_credentials_order_and_profile(tmp_path) -> None:
    fields = (credentials.CredentialField("api_key", "API_KEY"),)
    (tmp_path / ".env").write_text('export SSI_WORK_API_KEY="from-dotenv"\n')
    os.chmod(tmp_path / ".env", 0o600)
    loaded = credentials.load("ssi", "work", fields, environ={})
    assert loaded["api_key"].reveal() == "from-dotenv" and loaded.origins["api_key"] == ".env"
    loaded = credentials.load("ssi", "work", fields, environ={"SSI_WORK_API_KEY": "from-env"})
    assert loaded.origins["api_key"] == "env"
    with pytest.raises(CredentialError, match="SSI_API_KEY"):
        credentials.load("ssi", "default", fields, environ={})


def test_envelope_roundtrip_and_request_guard(tmp_path) -> None:
    env = Envelope.from_response(
        source="ssi", operation="data-ohlc", request={"symbol": "FPT"}, status=200,
        content=b'{"t":[1],"c":[100]}',
    )
    assert env.body == {"t": [1], "c": [100]} and len(env.body_sha256) == 64
    path = Envelopes([env]).save(tmp_path / "out.jsonl")
    assert oct(path.stat().st_mode & 0o777) == "0o600"
    assert load(path)[0] == env
    with pytest.raises(PolicyError):
        wrap("ssi", "data-ohlc", body={}, request={"apiSecret": "x"})
    not_json = Envelope.from_response(source="x", operation="y", request={}, status=200,
                                      content=b"<html>")
    assert not_json.body is None and not_json.body_base64


def test_envelope_matches_schema_keys() -> None:
    from importlib import resources

    from vnstock_oapi.core.redact import SENSITIVE_KEYS, is_sensitive

    schema = json.loads(
        resources.files("vnstock_oapi").joinpath("spec", "envelope.v1.json").read_text()
    )
    env = wrap("ssi", "data-ohlc", body=[1], request={"symbol": "FPT"})
    assert set(env.to_dict()) <= set(schema["properties"])
    assert set(schema["required"]) <= set(env.to_dict())
    # The schema forbids exactly what is_sensitive() refuses, case and - / _ variants included.
    forbidden = re.compile(schema["properties"]["request"]["propertyNames"]["not"]["pattern"])
    variants = set()
    for key in SENSITIVE_KEYS:
        bare = key.replace("-", "").replace("_", "")
        variants |= {key, key.upper(), bare, bare.upper(), f"_{key}_", key.replace("-", "_")}
    variants |= {"accessToken", "Api-Key", "X_FH_APIKEY", "Trading_Token"}
    for name in variants:
        assert is_sensitive(name) and forbidden.match(name), name
    for name in ("symbol", "from", "to", "pageSize", "tokens", "pinned", "x-request-id"):
        assert not is_sensitive(name) and not forbidden.match(name), name


def test_session_store_file_permissions() -> None:
    store = SessionStore("ssi", backend="file")
    assert store.put({"access_token": "t"}) == "file"
    assert oct(store.path.stat().st_mode & 0o777) == "0o600"
    assert oct(store.path.parent.stat().st_mode & 0o777) == "0o700"
    assert store.get() == {"access_token": "t"}
    store.clear()
    assert store.get() is None


def _transport(handler, sleeps=None) -> Transport:
    return Transport("ssi", frozenset({"api.ssi.com.vn"}),
                     client=httpx.Client(transport=httpx.MockTransport(handler)),
                     sleep=(sleeps.append if sleeps is not None else None))


def test_transport_policy() -> None:
    seen: list[httpx.Request] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request)
        return httpx.Response(200, json={})

    t = _transport(handler)
    with pytest.raises(PolicyError):
        t.request("GET", "https://evil.example.com/x")
    with pytest.raises(PolicyError):
        t.request("GET", "http://api.ssi.com.vn/x")
    with pytest.raises(PolicyError):
        t.request("POST", "https://api.ssi.com.vn/orders")
    with pytest.raises(PolicyError):
        t.request("DELETE", "https://api.ssi.com.vn/orders/1", allow_post=True)
    t.request("GET", "https://api.ssi.com.vn/api/v3/data/indexList")
    assert seen[0].headers["User-Agent"] == USER_AGENT
    assert "vnstock" not in USER_AGENT.lower() and "oapi" not in USER_AGENT.lower()
    assert "Mozilla" not in USER_AGENT  # an official API: never a browser's string


@pytest.mark.parametrize(
    "where",
    [
        {"params": {"client": "vnstock-oapi"}},
        {"headers": {"x-app": "VNSTOCK"}},
        {"json": {"note": "sent by vnstock"}},
    ],
)
def test_transport_refuses_requests_that_name_the_project(where: dict) -> None:
    seen: list[httpx.Request] = []
    t = _transport(lambda r: seen.append(r) or httpx.Response(200, json={}))
    method = "POST" if "json" in where else "GET"
    with pytest.raises(PolicyError, match="dự án"):
        t.request(method, "https://api.ssi.com.vn/api/v3/data/indexList", allow_post=True, **where)
    assert seen == []


def test_transport_429_waits_once_then_stops() -> None:
    calls = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429, headers={"Retry-After": "2"})

    sleeps: list[float] = []
    with pytest.raises(RateLimitError):
        _transport(handler, sleeps).request("GET", "https://api.ssi.com.vn/x")
    assert len(calls) == 2 and sleeps == [2.0]

    calls.clear()

    def no_header(request: httpx.Request) -> httpx.Response:
        calls.append(1)
        return httpx.Response(429)

    with pytest.raises(RateLimitError):
        _transport(no_header).request("GET", "https://api.ssi.com.vn/x")
    assert len(calls) == 1


def test_transport_refuses_redirect_and_verifies_tls() -> None:
    t = Transport("ssi", frozenset({"api.ssi.com.vn"}))
    assert t._client.follow_redirects is False
    # httpx verifies TLS by default; there is no verify knob to turn it off.
    assert "verify" not in Transport.__init__.__code__.co_varnames
    t.close()


def test_source_profile_cache_and_context_manager() -> None:
    import vnstock_oapi as oapi
    from vnstock_oapi.sources import ssi

    assert oapi.source("ssi", "work") is oapi.source("ssi", "work")
    sleeps: list[float] = []
    client = httpx.Client(transport=httpx.MockTransport(lambda r: httpx.Response(200, json={})))
    t = Transport("ssi", ssi.SPEC.hosts, rate_per_second=10.0, client=client, sleep=sleeps.append)
    with oapi.Source(ssi.SPEC, transport=t) as src:
        src.transport.request("GET", "https://api.ssi.com.vn/api/v3/data/indexList")
        src.transport.request("GET", "https://api.ssi.com.vn/api/v3/data/indexList")
    assert len(sleeps) == 1 and sleeps[0] > 0
    assert client.is_closed
    # The shared instance stays usable after ``with``: a fresh transport replaces the closed one.
    with oapi.source("ssi", "work") as shared:
        before = shared.transport
    assert oapi.source("ssi", "work").transport is not before
    assert not oapi.source("ssi", "work").transport._client.is_closed
    oapi.source("ssi", "work").close()


def test_rate_limit_reset_header_is_read() -> None:
    now = 1_800_000_000.0
    assert _wait_seconds(httpx.Headers({"X-RateLimit-Reset": str(int(now) + 7)}), now) == 7
    assert _wait_seconds(httpx.Headers({"X-RateLimit-Reset": "3"}), now) == 3
    assert _wait_seconds(httpx.Headers({"Retry-After": "2", "X-RateLimit-Reset": "9"}), now) == 2
    assert _wait_seconds(httpx.Headers({}), now) is None


def test_no_source_signs_in_with_the_account_password() -> None:
    # Mật khẩu tài khoản chứng khoán mở cả tài sản, không riêng dữ liệu. Gói không có đường nào nhận nó:
    # không kiểu xác thực, không tham số, không trường khoá nào mang tên mật khẩu. Thêm một nguồn chỉ cho
    # đăng nhập bằng mật khẩu phải làm đỏ test này.
    import inspect

    from vnstock_oapi.core import auth
    from vnstock_oapi.core.source import Source
    from vnstock_oapi.sources import SPECS

    assert not [name for name in dir(auth) if "password" in name.lower()]
    assert "password" not in inspect.signature(Source.authenticate).parameters
    for spec in SPECS.values():
        assert Source(spec).auth.asks in (None, "OTP"), spec.name
        assert not [f.name for f in spec.credentials if "pass" in f.name.lower()], spec.name
