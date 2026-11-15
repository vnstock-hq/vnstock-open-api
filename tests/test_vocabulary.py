"""The shared vocabulary is named once and every source maps to it, never around it.

Từ vựng chung chỉ đặt tên một lần; mọi nguồn ánh xạ vào nó, không đi đường vòng.
"""

from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

import pytest

from vnstock_oapi.core import vocabulary
from vnstock_oapi.core.redact import SENSITIVE_KEYS
from vnstock_oapi.errors import InputError
from vnstock_oapi.sources import SPECS

SOURCES_DIR = Path(str(resources.files("vnstock_oapi"))) / "sources"


def test_canonical_values_and_aliases() -> None:
    assert vocabulary.canonical("interval", "1h") == "1H"
    assert vocabulary.canonical("interval", "1H") == "1H"
    assert vocabulary.canonical("asset", "stock") == "equity"
    assert vocabulary.canonical("asset", "cw") == "warrant"
    with pytest.raises(InputError):
        vocabulary.canonical("interval", "1Y")
    # A minute is never a month, whatever the case.
    assert vocabulary.canonical("interval", "1m") == "1m" and vocabulary.canonical("interval", "1M") == "1M"


def test_every_mapping_uses_only_canonical_values() -> None:
    for name in SPECS:
        mapping = vocabulary.mapping(name)
        assert mapping.get("source") == name, name
        for kind in vocabulary.KINDS:
            unknown = set(mapping.get(kind, {})) - set(vocabulary.values(kind))
            assert not unknown, (name, kind, unknown)


def test_a_source_spells_common_values_its_own_way() -> None:
    assert vocabulary.native("ssi", "interval", "1h") == vocabulary.native("ssi", "interval", "1H") == "1h"
    assert vocabulary.symbol("ssi", "upcomindex", "data-ohlc") == "HNXUPCOMINDEX"
    assert vocabulary.symbol("ssi", "FPT") == "FPT"
    with pytest.raises(InputError, match="1H"):
        vocabulary.native("ssi", "interval", "1M")  # offered values are listed in the error


def test_no_source_module_keeps_its_own_copy_of_the_vocabulary() -> None:
    copies = re.compile(r'^\s*"(1h|4h|1m|1D|stock|derivative|cw)"\s*:', re.M)
    for path in sorted(SOURCES_DIR.glob("*.py")):
        assert not copies.search(path.read_text(encoding="utf-8")), path.name


def test_secret_fields_come_from_the_vocabulary() -> None:
    assert frozenset(vocabulary.vocabulary()["sensitive"]["keys"]) == SENSITIVE_KEYS
    assert {"apikey", "x-fh-apikey", "username", "password", "otp"} <= SENSITIVE_KEYS


def test_codes_read_from_bodies_land_on_common_values() -> None:
    for name in SPECS:
        for kind, codes in vocabulary.mapping(name).get("read", {}).items():
            allowed = set(vocabulary.vocabulary()[kind]["values"])
            assert set(codes.values()) <= allowed, (name, kind, set(codes.values()) - allowed)


def test_assets_keep_their_old_names() -> None:
    from vnstock_oapi.core.source import Source

    assert Source.ohlc.__kwdefaults__["asset"] == "equity"
    assert vocabulary.native("ssi", "interval", "1d") == vocabulary.native("ssi", "interval", "1D")


def test_every_legacy_interval_spelling_still_works() -> None:
    legacy = {"m": "1m", "minute": "1m", "5M": "5m", "h": "1H", "hour": "1H", "1h": "1H", "4h": "4H",
            "d": "1D", "day": "1D", "1d": "1D", "w": "1W", "week": "1W", "M": "1M", "1M": "1M", "month": "1M"}
    for spelling, common in legacy.items():
        assert vocabulary.canonical("interval", spelling) == common, spelling
