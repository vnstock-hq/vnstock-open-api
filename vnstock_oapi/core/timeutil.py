"""Turn the common ``start``, ``end`` and ``interval`` into what each source expects.

Chuyển ``start``, ``end``, ``interval`` chung sang dạng từng nguồn cần.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from vnstock_oapi.core import vocabulary
from vnstock_oapi.errors import InputError

__all__ = ["VN_TZ", "INTERVALS", "to_datetime", "epoch", "map_interval", "default_range"]

VN_TZ = ZoneInfo("Asia/Ho_Chi_Minh")
# The common intervals, from the shared vocabulary (spec/vocabulary.toml).
INTERVALS = vocabulary.values("interval")
_FORMATS = ("%Y-%m-%d %H:%M:%S", "%Y-%m-%d %H:%M", "%Y-%m-%dT%H:%M:%S", "%Y-%m-%dT%H:%M")


def to_datetime(value: str | date | datetime, *, end: bool = False) -> datetime:
    """Timezone-aware datetime. A bare date means start of day (``start``) or end of day (``end``).

    Ngày giờ có múi giờ. Chỉ có ngày thì lấy đầu ngày (``start``) hoặc cuối ngày (``end``).

    A naive time is read as Asia/Ho_Chi_Minh.
    """
    if isinstance(value, datetime):
        return value if value.tzinfo else value.replace(tzinfo=VN_TZ)
    if isinstance(value, date):
        return datetime.combine(value, time.max if end else time.min, tzinfo=VN_TZ).replace(
            microsecond=0
        )
    if isinstance(value, str):
        text = value.strip()
        try:
            day = date.fromisoformat(text)
        except ValueError:
            day = None
        if day is not None:
            return to_datetime(day, end=end)
        for fmt in _FORMATS:
            try:
                return datetime.strptime(text, fmt).replace(tzinfo=VN_TZ)
            except ValueError:
                continue
    raise InputError(f"Không đọc được thời điểm {value!r}; dùng YYYY-MM-DD hoặc YYYY-MM-DD HH:MM")


def epoch(value: datetime) -> int:
    return int(value.timestamp())


def default_range(
    start: str | date | datetime | None, end: str | date | datetime | None, *, days: int = 30
) -> tuple[datetime, datetime]:
    """``end`` defaults to today; ``start`` defaults to ``days`` days before ``end``.

    ``end`` mặc định là hôm nay; ``start`` mặc định lùi ``days`` ngày.
    """
    end_dt = to_datetime(end, end=True) if end is not None else to_datetime(date.today(), end=True)
    start_dt = (
        to_datetime(start)
        if start is not None
        else to_datetime((end_dt - timedelta(days=days)).date())
    )
    if start_dt > end_dt:
        raise InputError("start phải trước end")
    return start_dt, end_dt


def map_interval(value: str, table: Mapping[str, str], source: str) -> str:
    """Look ``value`` up in one of a source's own tables (movers, constituents, report kinds…).

    Tra một bảng riêng của nguồn. Từ vựng chung (khung thời gian, tài sản, sàn) không đi qua đây mà
    qua :func:`vnstock_oapi.core.vocabulary.native`.
    """
    if value not in table:
        raise InputError(
            f"{source} không hỗ trợ {value!r}. Giá trị hỗ trợ: {', '.join(table)}", source=source
        )
    return table[value]
