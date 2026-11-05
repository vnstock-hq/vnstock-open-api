"""SSI: token session, request shape, paging.

SSI: phiên token, dựng request, phân trang. Không gọi mạng.
"""

from __future__ import annotations

import time

import httpx
import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import (
    AuthError,
)
from vnstock_oapi.sources import ssi


def test_ssi_ohlc_uses_its_own_upcom_index_name() -> None:
    source = make_source(ssi.SPEC, lambda r: json_response(200, {}))
    (plan,) = source.plan("ohlc", "upcomindex")
    assert plan.params["symbol"] == "HNXUPCOMINDEX"


def test_ssi_token_flow_shared_and_refreshed(monkeypatch) -> None:
    monkeypatch.setenv("SSI_API_KEY", "ssi-key-0001")
    monkeypatch.setenv("SSI_API_SECRET", "ssi-secret-0002")
    calls: list[str] = []
    now = int(time.time())

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v3/auth/token":
            return json_response(200, {"data": {
                "accessToken": "access-1", "expiresAt": now - 1,  # already expired
                "refreshToken": "refresh-1", "refreshExpiresAt": now + 3600}})
        if request.url.path == "/api/v3/auth/refresh":
            assert b"refresh-1" in request.content
            return json_response(200, {"data": {
                "accessToken": "access-2", "expiresAt": now + 900,
                "refreshToken": "refresh-2", "refreshExpiresAt": now + 3600}})
        assert request.headers["Authorization"] == "Bearer access-2"
        return json_response(200, {"data": [{"Open": 1}] * 3})

    source = make_source(ssi.SPEC, handler)
    source.authenticate()
    out = source.ohlc("FPT", start="2026-10-01", end="2026-10-27", limit=5)
    assert calls == ["/api/v3/auth/token", "/api/v3/auth/refresh", "/api/v3/data/ohlc"]
    assert len(out) == 1  # 3 rows < pageSize 5: no second page
    assert out[0].request["timeFrame"] == "1d"
    # A second Source object (another tool in the same process or another process) reuses the
    # stored session instead of logging in again.
    calls.clear()
    make_source(ssi.SPEC, handler).ohlc("FPT", start="2026-10-01", end="2026-10-27", limit=5)
    assert calls == ["/api/v3/data/ohlc"]


def test_ssi_401_relogs_once_then_stops(monkeypatch) -> None:
    monkeypatch.setenv("SSI_API_KEY", "ssi-key-0001")
    monkeypatch.setenv("SSI_API_SECRET", "ssi-secret-0002")
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        if request.url.path == "/api/v3/auth/token":
            return json_response(200, {"data": {"accessToken": "a", "expiresAt": time.time() + 900}})
        return json_response(401, {"code": 401001, "message": "token revoked"})

    with pytest.raises(AuthError):
        make_source(ssi.SPEC, handler).index("VNINDEX")
    assert calls.count("/api/v3/data/indexSummary") == 2
    assert calls.count("/api/v3/auth/token") == 2


def test_ssi_401_adopts_session_opened_by_another_process(monkeypatch) -> None:
    monkeypatch.setenv("SSI_API_KEY", "ssi-key-0001")
    monkeypatch.setenv("SSI_API_SECRET", "ssi-secret-0002")
    logins: list[str] = []
    sent: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        # One session per key: only the latest token is accepted.
        if request.url.path == "/api/v3/auth/token":
            logins.append(f"access-{len(logins) + 1}")
            return json_response(200, {"data": {"accessToken": logins[-1], "expiresAt": time.time() + 900}})
        sent.append(request.headers["Authorization"])
        if request.headers["Authorization"] != f"Bearer {logins[-1]}":
            return json_response(401, {"code": 401001, "message": "token revoked"})
        return json_response(200, {"data": [{"Open": 1}]})

    first, second = make_source(ssi.SPEC, handler), make_source(ssi.SPEC, handler)
    first.index("VNINDEX")
    second.authenticate()  # revokes the token cached in ``first``
    first.index("VNINDEX")
    second.index("VNINDEX")
    assert logins == ["access-1", "access-2"]  # neither process revoked the other again
    assert sent[-3:] == ["Bearer access-1", "Bearer access-2", "Bearer access-2"]


def test_ssi_pages_follow_pages_count() -> None:
    from vnstock_oapi.sources.ssi import _next_page_index

    params = {"pageIndex": 1, "pageSize": 10}
    assert _next_page_index(params, {"data": [1], "pagesCount": 3}) == {"pageIndex": 2, "pageSize": 10}
    assert _next_page_index({**params, "pageIndex": 3}, {"data": [1] * 10, "pagesCount": 3}) is None
    assert _next_page_index(params, {"data": [1] * 10}) == {"pageIndex": 2, "pageSize": 10}
