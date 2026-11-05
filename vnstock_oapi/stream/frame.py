"""Frame v1: one WebSocket message from a source, verbatim, plus where and when it arrived.

Frame v1: một thông điệp WebSocket của nguồn, nguyên văn, kèm nơi và lúc nhận.

A frame never carries a key, token, signature or handshake message (``spec/frame.v1.json``).
Control frames (``connected``, ``reconnected``, ``gap``, ``closed``) tell a consumer about the
connection so it never has to guess whether data is missing.
"""

from __future__ import annotations

import base64
import json
from collections.abc import Iterable, Iterator
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from vnstock_oapi._version import __version__
from vnstock_oapi.core.envelope import open_private

__all__ = ["FRAME_VERSION", "PRODUCER", "Frame", "save_frames", "load_frames"]

FRAME_VERSION = 1
PRODUCER = f"vnstock-oapi@{__version__}"

Kind = Literal["data", "connected", "reconnected", "gap", "closed"]


@dataclass(frozen=True, slots=True)
class Frame:
    """One frame. ``payload`` is empty and ``detail`` is set on control frames.

    Một frame. Frame điều khiển có ``payload`` rỗng và có ``detail``.
    """

    source: str
    channel: str
    conn_id: int
    seq: int
    recv_at_ns: int
    payload: bytes
    encoding: Literal["text", "binary"] = "text"
    kind: Kind = "data"
    detail: str = ""
    producer: str = PRODUCER
    frame_version: int = FRAME_VERSION

    @property
    def text(self) -> str:
        return self.payload.decode("utf-8")

    def json(self) -> Any:
        """The payload parsed as JSON (text frames only).

        Payload đọc thành JSON (chỉ frame văn bản).
        """
        return json.loads(self.payload)

    def to_dict(self) -> dict[str, Any]:
        payload = self.text if self.encoding == "text" else base64.b64encode(self.payload).decode("ascii")
        return {
            "frame_version": self.frame_version,
            "kind": self.kind,
            "source": self.source,
            "channel": self.channel,
            "conn_id": self.conn_id,
            "seq": self.seq,
            "recv_at_ns": self.recv_at_ns,
            "encoding": self.encoding,
            "payload": payload,
            "detail": self.detail,
            "producer": self.producer,
        }

    @classmethod
    def from_dict(cls, row: dict[str, Any]) -> Frame:
        encoding = row.get("encoding", "text")
        raw = row.get("payload", "")
        payload = raw.encode("utf-8") if encoding == "text" else base64.b64decode(raw)
        return cls(
            source=row["source"],
            channel=row["channel"],
            conn_id=int(row["conn_id"]),
            seq=int(row["seq"]),
            recv_at_ns=int(row["recv_at_ns"]),
            payload=payload,
            encoding=encoding,
            kind=row.get("kind", "data"),
            detail=row.get("detail", ""),
            producer=row.get("producer", PRODUCER),
            frame_version=int(row.get("frame_version", FRAME_VERSION)),
        )


def save_frames(frames: Iterable[Frame], path: str | Path) -> int:
    """Write frames as JSON Lines with mode 600. Returns how many were written.

    Ghi frame ra JSON Lines với quyền 600. Trả số frame đã ghi.
    """
    count = 0
    with open_private(path) as handle:
        for frame in frames:
            handle.write(json.dumps(frame.to_dict(), ensure_ascii=False) + "\n")
            count += 1
    return count


def load_frames(path: str | Path) -> Iterator[Frame]:
    with Path(path).open(encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                yield Frame.from_dict(json.loads(line))
