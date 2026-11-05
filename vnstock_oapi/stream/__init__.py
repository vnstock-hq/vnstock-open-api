"""Streams over official broker Open API endpoints, market-data channels only.

Stream qua cổng Open API chính thức của công ty chứng khoán, chỉ kênh dữ liệu thị trường.

Importing this package needs no extra dependency; connecting needs ``vnstock-oapi[stream]``.
"""

from __future__ import annotations

from typing import Any

from vnstock_oapi.stream.auth import StreamAuth, stream_auth
from vnstock_oapi.stream.client import StreamClient, collect
from vnstock_oapi.stream.frame import FRAME_VERSION, Frame, load_frames, save_frames
from vnstock_oapi.stream.spec import ChannelRule, StreamSpec, load_stream_spec, stream_sources


def client_for(source: str, *, profile: str = "default") -> Any:
    """The stream client that speaks the ``protocol`` declared in the source's ``streams.toml``.

    Client stream nói đúng ``protocol`` khai trong ``streams.toml`` của nguồn.
    """
    protocol = load_stream_spec(source).protocol
    if protocol == "tcbs_pipe":
        from vnstock_oapi.stream.tcbs import TcbsStreamClient

        return TcbsStreamClient(source, profile=profile)
    if protocol == "signalr":
        from vnstock_oapi.stream.ssi import SsiStreamClient

        return SsiStreamClient(source, profile=profile)
    if protocol == "binance":
        from vnstock_oapi.stream.binance import BinanceStreamClient

        return BinanceStreamClient(source, profile=profile)
    return StreamClient(source, profile=profile)


__all__ = [
    "FRAME_VERSION",
    "ChannelRule",
    "Frame",
    "StreamAuth",
    "StreamClient",
    "StreamSpec",
    "client_for",
    "collect",
    "load_frames",
    "load_stream_spec",
    "save_frames",
    "stream_auth",
    "stream_sources",
]
