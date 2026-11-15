"""The day each key was stored with ``keys set``, so a source that retires keys can track days remaining.

Ngày cất từng khoá bằng ``keys set``, để tính số ngày còn lại tới hạn đổi khoá của nguồn có hạn dùng khoá.

A source that makes the user issue a new key every N days declares ``key_valid_days`` in its
``terms.toml``. The day stored here is taken as the day the key was issued: a key typed into
``keys set`` today has its full N days. Only the day is kept, never the key: one JSON file in the user
state directory (``key_dates.json``, directory 700, file 600), keyed ``<source>/<profile>``.

A key read from the environment or ``.env`` has no day here: vnstock-oapi did not see it arrive.
"""

from __future__ import annotations

import contextlib
import json
import os
import tempfile
from datetime import date, datetime, timedelta
from pathlib import Path
from typing import Any

from vnstock_oapi.core.session_store import _private_dir, state_dir
from vnstock_oapi.core.timeutil import VN_TZ

__all__ = ["path", "record", "forget", "stored_on", "rotation"]


def path() -> Path:
    return state_dir() / "key_dates.json"


def today() -> date:
    return datetime.now(VN_TZ).date()


def _read() -> dict[str, str]:
    try:
        data = json.loads(path().read_text(encoding="utf-8"))
    except (FileNotFoundError, ValueError):
        return {}
    return {str(k): str(v) for k, v in data.items()} if isinstance(data, dict) else {}


def _write(data: dict[str, str]) -> None:
    directory = _private_dir(path().parent)
    fd, tmp = tempfile.mkstemp(dir=directory, prefix=".key-dates-", suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(data, handle, ensure_ascii=False, indent=1, sort_keys=True)
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path())
    except BaseException:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp)
        raise


def record(source: str, profile: str, day: date | None = None) -> date:
    """Note that the key of ``source`` was stored on ``day`` (today in Vietnam when ``None``)."""
    day = day or today()
    data = _read()
    data[f"{source}/{profile}"] = day.isoformat()
    _write(data)
    return day


def forget(source: str, profile: str) -> None:
    data = _read()
    if data.pop(f"{source}/{profile}", None) is not None:
        _write(data)


def stored_on(source: str, profile: str) -> date | None:
    raw = _read().get(f"{source}/{profile}")
    try:
        return date.fromisoformat(raw) if raw else None
    except ValueError:
        return None


def rotation(valid_days: int, stored: date | None, *, now: date | None = None) -> dict[str, Any]:
    """Countdown for a key that lives ``valid_days``: ``valid_days``, and when the day is known
    ``stored_on``, ``due_on`` (stored + valid_days) and ``days_left`` (negative once past due)."""
    info: dict[str, Any] = {"valid_days": valid_days}
    if stored is None:
        return info
    due = stored + timedelta(days=valid_days)
    info.update(
        stored_on=stored.isoformat(), due_on=due.isoformat(), days_left=(due - (now or today())).days
    )
    return info
