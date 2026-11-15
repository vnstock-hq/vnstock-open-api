"""Raw data envelope: the contract between vnstock-oapi and every reader of its data.

Phong bì dữ liệu thô: hợp đồng giữa vnstock-oapi và mọi bên đọc dữ liệu.

One envelope per HTTP response, one symbol per envelope. The body is kept exactly as the source sent
it: parsed JSON, or ``body_base64`` when the body is not JSON. Schema: ``spec/envelope.v1.json``.
"""

from __future__ import annotations

import base64
import contextlib
import hashlib
import json
import os
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, TextIO

from vnstock_oapi._version import __version__
from vnstock_oapi.core.redact import is_sensitive
from vnstock_oapi.errors import InputError, PolicyError

__all__ = ["ENVELOPE_VERSION", "Envelope", "Envelopes", "wrap", "load", "now_rfc3339", "open_private"]

ENVELOPE_VERSION = "1"
PRODUCER = {"name": "vnstock-oapi", "version": __version__}
_UNSET: Any = object()


def now_rfc3339() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def open_private(path: str | Path, *, append: bool = False) -> TextIO:
    """Open a file for writing with mode 0o600 from creation.

    Mở tệp để ghi với quyền 0o600 ngay từ lúc tạo.
    """
    target = Path(path)
    target.parent.mkdir(parents=True, exist_ok=True)
    flags = os.O_WRONLY | os.O_CREAT | (os.O_APPEND if append else os.O_TRUNC)
    fd = os.open(target, flags, 0o600)
    if os.name == "posix":
        with contextlib.suppress(OSError):
            os.fchmod(fd, 0o600)
    try:
        return os.fdopen(fd, "a" if append else "w", encoding="utf-8")
    except BaseException:
        os.close(fd)
        raise


def _check_request(request: Mapping[str, Any]) -> dict[str, Any]:
    leaked = [key for key in request if is_sensitive(key)]
    if leaked:
        raise PolicyError(f"request của phong bì không được chứa {', '.join(leaked)}")
    return dict(request)


def _canonical_json(value: Any) -> bytes:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode()


@dataclass(frozen=True)
class Envelope:
    """One raw response plus metadata.

    Phong bì của một response: body nguyên văn kèm metadata.
    """

    source: str
    operation: str
    request: dict[str, Any]
    fetched_at: str
    http_status: int
    body: Any
    body_sha256: str
    api_version: str = ""
    body_base64: str | None = None
    producer: dict[str, Any] = field(default_factory=lambda: dict(PRODUCER))
    transport: dict[str, Any] | None = None
    # Set on the last page when the answer is short of what the source had: ``{"reason", "pages"}``.
    truncated: dict[str, Any] | None = None
    envelope_version: str = ENVELOPE_VERSION

    @classmethod
    def from_response(
        cls,
        *,
        source: str,
        operation: str,
        request: Mapping[str, Any],
        status: int,
        content: bytes,
        api_version: str = "",
        transport: dict[str, Any] | None = None,
        parsed_body: Any = _UNSET,
        producer: Mapping[str, str] | None = None,
    ) -> Envelope:
        body: Any = None
        body_base64: str | None = None
        if parsed_body is not _UNSET:
            body = parsed_body
        else:
            try:
                body = json.loads(content) if content else None
            except ValueError:
                body_base64 = base64.b64encode(content).decode("ascii")
        return cls(
            source=source,
            operation=operation,
            request=_check_request(request),
            fetched_at=now_rfc3339(),
            http_status=status,
            body=body,
            body_base64=body_base64,
            body_sha256=hashlib.sha256(content).hexdigest(),
            api_version=api_version,
            transport=transport,
            producer=dict(producer) if producer else dict(PRODUCER),
        )

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        if data["body_base64"] is None:
            del data["body_base64"]
        if data["transport"] is None:
            del data["transport"]
        if data["truncated"] is None:
            del data["truncated"]
        return data

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> Envelope:
        if str(data.get("envelope_version")) != ENVELOPE_VERSION:
            raise InputError(f"envelope_version không hỗ trợ: {data.get('envelope_version')!r}")
        known = {name for name in cls.__dataclass_fields__}
        return cls(**{key: value for key, value in data.items() if key in known})

    def to_json(self) -> str:
        return json.dumps(self.to_dict(), ensure_ascii=False, separators=(",", ":"))


class Envelopes(list[Envelope]):
    """Envelopes of one call, one per page.

    Danh sách phong bì của một lần gọi, mỗi trang một phong bì.
    """

    @property
    def bodies(self) -> list[Any]:
        return [envelope.body for envelope in self]

    def save(self, path: str | Path, *, append: bool = False) -> Path:
        """Write JSONL, one envelope per line. A new file gets mode 600.

        Ghi JSONL, mỗi dòng một phong bì. Tệp mới có quyền 600.
        """
        target = Path(path)
        with open_private(target, append=append) as handle:
            for envelope in self:
                handle.write(envelope.to_json() + "\n")
        return target

    def summary(self) -> list[dict[str, Any]]:
        return [
            {
                "source": e.source,
                "operation": e.operation,
                "http_status": e.http_status,
                "body_sha256": e.body_sha256[:12],
                "bytes": len(_canonical_json(e.body)) if e.body is not None else 0,
            }
            for e in self
        ]


def wrap(
    source: str,
    operation: str,
    body: Any,
    request: Mapping[str, Any] | None = None,
    *,
    http_status: int = 200,
    api_version: str = "",
    fetched_at: str | None = None,
) -> Envelope:
    """Wrap a body you fetched yourself into an envelope.

    Bọc body tự lấy thành phong bì.

    The original bytes are unknown here, so ``body_sha256`` hashes the canonical JSON of ``body``.
    """
    return Envelope(
        source=source,
        operation=operation,
        request=_check_request(request or {}),
        fetched_at=fetched_at or now_rfc3339(),
        http_status=http_status,
        body=body,
        body_sha256=hashlib.sha256(_canonical_json(body)).hexdigest(),
        api_version=api_version,
        producer={"name": "user"},
    )


def load(path: str | Path) -> Envelopes:
    """Read a JSONL file of envelopes.

    Đọc tệp JSONL phong bì.
    """
    out = Envelopes()
    with open(path, encoding="utf-8") as handle:
        for line in handle:
            if line.strip():
                out.append(Envelope.from_dict(json.loads(line)))
    return out
