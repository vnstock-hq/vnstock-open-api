from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from vnstock_oapi.core.credentials import env_name
from vnstock_oapi.core.source import Source, SourceSpec
from vnstock_oapi.core.transport import Transport
from vnstock_oapi.sources import SPECS

Handler = Callable[[httpx.Request], httpx.Response]


def _no_network(self: Any, request: httpx.Request) -> httpx.Response:
    raise RuntimeError(f"Tests must not reach the network: {request.method} {request.url.host}")


@pytest.fixture(autouse=True)
def isolated(monkeypatch: pytest.MonkeyPatch, tmp_path: Any) -> None:
    """Never touch the machine's real keyring, .env or session store.

    Không chạm keyring, .env hay kho phiên thật của máy.
    """
    # Any request not served by httpx.MockTransport fails here instead of reaching a source.
    monkeypatch.setattr(httpx.HTTPTransport, "handle_request", _no_network)
    monkeypatch.setenv("VNSTOCK_OAPI_SESSION_BACKEND", "file")
    monkeypatch.setenv("VNSTOCK_OAPI_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.chdir(tmp_path)
    monkeypatch.setattr("vnstock_oapi.core.credentials._keyring_get", lambda *a: None)
    for spec in SPECS.values():
        for field in spec.credentials:
            name = env_name(spec.name, "default", field.env_suffix)
            monkeypatch.delenv(name, raising=False)


def make_source(spec: SourceSpec, handler: Handler, *, sleeps: list[float] | None = None) -> Source:
    client = httpx.Client(transport=httpx.MockTransport(handler))
    transport = Transport(
        spec.name, spec.hosts, client=client, sleep=(sleeps.append if sleeps is not None else None)
    )
    return Source(spec, transport=transport)


def json_response(status: int, body: Any, headers: dict[str, str] | None = None) -> httpx.Response:
    return httpx.Response(status, content=json.dumps(body).encode(), headers=headers or {})
