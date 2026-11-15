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


def test_keys_set_on_a_machine_without_keyring_explains_itself(monkeypatch, capsys) -> None:
    import keyring
    from keyring.errors import NoKeyringError

    from vnstock_oapi.cli import main

    def refuse(*_args: object) -> None:
        raise NoKeyringError("no backend")

    monkeypatch.setattr(keyring, "set_password", refuse)
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "canary-secret-value")
    assert main(["keys", "set", "fhsc"]) == 1
    seen = capsys.readouterr()
    assert "NoKeyringError" in seen.err and ".env" in seen.err
    assert "canary-secret-value" not in seen.out + seen.err


def test_keys_on_a_source_without_keys_says_so(capsys) -> None:
    from vnstock_oapi.cli import main

    assert main(["keys", "set", "binance"]) == 0
    assert "không cần khoá" in capsys.readouterr().out


def test_auth_without_a_session_still_requires_the_key(capsys) -> None:
    from vnstock_oapi.cli import main

    for name in ("dnse", "fhsc", "fmp"):
        assert main(["auth", name]) == 1
        assert "CredentialError" in capsys.readouterr().err
    assert main(["auth", "binance"]) == 0  # public data: nothing to have


def test_keys_set_drops_the_key_read_before(monkeypatch) -> None:
    import keyring

    import vnstock_oapi as oapi
    from vnstock_oapi.cli import main

    stored: dict[str, str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda _s, name, value: stored.__setitem__(name, value))
    monkeypatch.setattr(
        "vnstock_oapi.core.credentials._keyring_get", lambda s, p, f: stored.get(f"{s}/{p}/{f}")
    )
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "first")
    assert main(["keys", "set", "fmp"]) == 0
    assert oapi.source("fmp").credentials["api_key"].reveal() == "first"
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "second")
    assert main(["keys", "set", "fmp"]) == 0
    assert oapi.source("fmp").credentials["api_key"].reveal() == "second"


def test_a_source_that_retires_keys_is_counted_down_from_keys_set(monkeypatch, capsys) -> None:
    """FHSC keeps a key 30 days: the day `keys set` stores it is its first day. Only the day is written."""
    from datetime import date, timedelta

    import keyring

    import vnstock_oapi as oapi
    from vnstock_oapi.cli import main
    from vnstock_oapi.core import key_dates

    assert oapi.terms("fhsc").key_valid_days == 30
    assert oapi.terms("fmp").key_valid_days == 0 and oapi.source("fmp").key_rotation() is None

    stored: dict[str, str] = {}
    monkeypatch.setattr(keyring, "set_password", lambda _s, name, value: stored.__setitem__(name, value))
    monkeypatch.setattr(keyring, "delete_password", lambda _s, name: stored.pop(name, None))
    monkeypatch.setattr(
        "vnstock_oapi.core.credentials._keyring_get", lambda s, p, f: stored.get(f"{s}/{p}/{f}")
    )
    monkeypatch.setattr("getpass.getpass", lambda _prompt: "fhsc-canary-key")
    monkeypatch.setattr(key_dates, "today", lambda: date(2026, 11, 15))

    assert main(["keys", "set", "fhsc"]) == 0
    out = capsys.readouterr().out
    assert "30 ngày" in out and "15/12/2026" in out and "fhsc-canary-key" not in out
    assert "fhsc-canary-key" not in key_dates.path().read_text(encoding="utf-8")
    rotation = oapi.source("fhsc").key_rotation()
    assert rotation == {"valid_days": 30, "stored_on": "2026-11-15", "due_on": "2026-12-15", "days_left": 30}
    assert oapi.source("fhsc").status()["key_rotation"]["days_left"] == 30

    # Counted from the stored day, past due is negative.
    later = key_dates.rotation(30, date(2026, 11, 15), now=date(2026, 11, 15) + timedelta(days=33))
    assert later["days_left"] == -3

    # A key from the environment has no known day: the row says where it came from instead.
    monkeypatch.setenv("FHSC_API_KEY", "from-env")
    oapi.source("fhsc").forget_credentials()
    assert oapi.source("fhsc").key_rotation() == {"valid_days": 30, "origin": "env"}
    monkeypatch.delenv("FHSC_API_KEY")

    assert main(["keys", "delete", "fhsc"]) == 0
    assert key_dates.stored_on("fhsc", "default") is None
