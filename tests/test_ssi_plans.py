"""What SSI verbs send: index flows by board, price limits of one day, a limit that needs one page.

Những gì các hàm SSI gửi đi: dòng tiền chỉ số theo sàn, trần sàn một ngày, giới hạn chỉ cần một trang.
"""

from __future__ import annotations

from vnstock_oapi.sources import ssi


def test_upcom_index_flows_are_asked_by_board() -> None:
    plan = ssi._index_flows("upcomindex", start="2026-11-06", end="2026-11-06")[0]
    assert plan.params["board"] == "UPCOM" and plan.params["index"] is None


def test_other_index_flows_are_asked_by_index() -> None:
    plan = ssi._index_flows("VNINDEX", start="2026-11-06", end="2026-11-06")[0]
    assert plan.params["index"] == "VNINDEX" and plan.params["board"] is None


def test_price_limits_of_one_date() -> None:
    plan = ssi._price_limits(date="2026-11-03")
    assert plan.params["from"] == plan.params["to"] == "2026/11/03"


def test_a_limit_one_page_holds_takes_one_page() -> None:
    assert ssi._ohlc("FPT", start="2026-11-02", end="2026-11-06", limit=3).only_page == 1
    assert ssi._ohlc("FPT", start="2026-11-02", end="2026-11-06").only_page is None
