"""Aggregate trades walked forward by time. Khớp lệnh gộp đi tiếp theo thời gian."""

from __future__ import annotations

from vnstock_oapi.sources.binance import _next_aggregates


def test_a_full_page_continues_at_the_last_millisecond() -> None:
    page = [{"T": 100}, {"T": 200}]
    assert _next_aggregates({"startTime": 1, "limit": 2}, page) == {"startTime": 200, "limit": 2}


def test_a_page_within_one_millisecond_moves_past_it() -> None:
    page = [{"T": 200}, {"T": 200}]
    assert _next_aggregates({"startTime": 1, "limit": 2}, page) == {"startTime": 201, "limit": 2}


def test_it_stops_on_a_short_page_past_the_end_or_without_a_window() -> None:
    assert _next_aggregates({"startTime": 1, "limit": 3}, [{"T": 1}]) is None
    assert _next_aggregates({"startTime": 1, "limit": 2, "endTime": 150}, [{"T": 100}, {"T": 200}]) is None
    assert _next_aggregates({"limit": 2}, [{"T": 100}, {"T": 200}]) is None
