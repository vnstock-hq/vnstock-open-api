"""Source registry and command line, for whichever sources are installed.

Danh sách nguồn và dòng lệnh, với các nguồn đang cài. Không gọi mạng.
"""

from __future__ import annotations

import pytest
from conftest import json_response, make_source

from vnstock_oapi.errors import (
    CredentialError,
    InputError,
    UnsupportedError,
)
from vnstock_oapi.sources import ssi


def test_unsupported_and_missing_keys() -> None:
    source = make_source(ssi.SPEC, lambda r: json_response(200, {}))
    with pytest.raises(UnsupportedError):
        source.calendar()
    with pytest.raises(CredentialError, match="SSI_API_KEY"):
        source.ohlc("FPT")
    with pytest.raises(InputError):
        source.plan("ohlc", "FPT", interval="2h")


def test_dump_uses_the_last_closed_session() -> None:
    from datetime import datetime

    from vnstock_oapi.cli import last_session_day
    from vnstock_oapi.core.timeutil import VN_TZ

    assert str(last_session_day(datetime(2026, 10, 28, 0, 16, tzinfo=VN_TZ))) == "2026-10-27"
    assert str(last_session_day(datetime(2026, 10, 27, 15, 5, tzinfo=VN_TZ))) == "2026-10-27"
    assert str(last_session_day(datetime(2026, 11, 2, 9, 0, tzinfo=VN_TZ))) == "2026-10-30"  # Monday morning


def test_cli_parse_kv_and_dump_plans_for_all_sources() -> None:
    import vnstock_oapi as oapi
    from vnstock_oapi.cli import _dump_plans, _parse_kv, _sample_symbols, build_parser

    assert _parse_kv(["page=1", "headIndex=-1", "code=001", "flag=--5"]) == {
        "page": 1,
        "headIndex": -1,
        "code": "001",
        "flag": "--5",
    }
    parser = build_parser()
    args = parser.parse_args(["dump", *oapi.sources()])
    for name in oapi.sources():
        source = oapi.source(name)
        plans = _dump_plans(source, *_sample_symbols(source, args))
        if source.spec.market == "global":  # never a Vietnamese symbol to FMP
            sent = {str(v) for _, plan in plans for v in plan.params.values()}
            assert not sent & {"FPT", "VNINDEX"} and "AAPL" in sent
        assert len(plans) >= 2


def test_every_installed_source_is_a_package_attribute() -> None:
    import vnstock_oapi as oapi
    from vnstock_oapi.sources import SPECS

    assert oapi.sources() == list(SPECS) and oapi.sources()
    for name in oapi.sources():
        assert getattr(oapi, name) is oapi.source(name) and name in dir(oapi)
    with pytest.raises(AttributeError):
        _ = oapi.not_a_source
