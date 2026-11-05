"""Stream specification: the official endpoint and the market-data channels of ``streams.toml``.

Đặc tả stream: chỉ cổng chính thức và các kênh dữ liệu thị trường khai trong ``streams.toml``.

Every check runs before any byte goes out. A source without ``streams.toml`` has no stream.
"""

from __future__ import annotations

import re
import sys
from dataclasses import dataclass, field
from functools import cache
from importlib import resources
from urllib.parse import urlsplit

from vnstock_oapi.errors import PolicyError, UnsupportedError

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover
    import tomli as tomllib  # type: ignore[import-not-found]

__all__ = ["ChannelRule", "StreamSpec", "load_stream_spec", "stream_sources"]

# Wire names end with the payload encoding; the server's acks may echo a different one.
_ENCODINGS = (".json", ".msgpack")
_PLACEHOLDER = re.compile(r"\{(\w+)\}")


@dataclass(frozen=True)
class ChannelRule:
    """One declared channel family, e.g. ``tick`` with pattern ``tick.{board}``.

    Một họ kênh đã khai, ví dụ ``tick`` với mẫu ``tick.{board}``.
    """

    name: str
    pattern: str
    symbols: bool
    message_types: tuple[str, ...]
    description: str = ""

    def regex(self, params: dict[str, tuple[str, ...] | str]) -> re.Pattern[str]:
        def group(name: str) -> str:
            rule = params.get(name)
            if rule is None:
                raise ValueError(f"streams.toml: {self.pattern} dùng tham số chưa khai {name!r}")
            if isinstance(rule, str):
                return f"(?:{rule.strip('^$')})"
            return "(?:" + "|".join(re.escape(value) for value in rule) + ")"

        # split() with a capture group alternates literal text and placeholder names.
        parts = _PLACEHOLDER.split(self.pattern)
        body = "".join(group(part) if index % 2 else re.escape(part) for index, part in enumerate(parts))
        return re.compile(f"^{body}$")


@dataclass(frozen=True)
class StreamSpec:
    """The parsed ``streams.toml`` of a source.

    ``streams.toml`` của một nguồn, đã đọc.
    """

    source: str
    title: str
    url: str
    encoding: str
    auth: str
    heartbeat_s: float
    type_field: str
    control_field: str
    forbidden: tuple[str, ...]
    channels: tuple[ChannelRule, ...]
    params: dict[str, tuple[str, ...] | str]
    docs_url: str = ""
    checked_at: str = ""
    protocol: str = "json"
    _compiled: tuple[tuple[re.Pattern[str], ChannelRule], ...] = field(
        default=(), init=False, repr=False, compare=False
    )
    _families: dict[str, str] = field(default_factory=dict, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        compiled = tuple((rule.regex(self.params), rule) for rule in self.channels)
        families = {
            msg_type: rule.name for rule in self.channels for msg_type in rule.message_types
        }
        object.__setattr__(self, "_compiled", compiled)
        object.__setattr__(self, "_families", families)

    @property
    def host(self) -> str:
        return urlsplit(self.url).hostname or ""

    def check_url(self, url: str) -> None:
        """Refuse anything but the declared ``wss://`` endpoint. Query strings are allowed.

        Từ chối mọi URL khác cổng ``wss://`` đã khai. Cho phép phần query.
        """
        want, got = urlsplit(self.url), urlsplit(url)
        if (got.scheme, got.hostname, got.port, got.path) != (
            want.scheme,
            want.hostname,
            want.port,
            want.path,
        ):
            raise PolicyError(
                f"{self.source} chỉ được mở stream tới {self.url}", source=self.source, url=url
            )

    def resolve(self, channel: str) -> ChannelRule:
        """Return the rule of a declared market-data channel, or raise ``PolicyError``.

        Trả quy tắc của kênh dữ liệu thị trường đã khai; kênh khác thì ``PolicyError``.

        ``channel`` may carry the encoding suffix (``tick.G1.json``) or not (``tick.G1``).
        """
        name = strip_encoding(channel)
        head = name.split(".", 1)[0].lower()
        if head in self.forbidden:
            raise PolicyError(
                f"Kênh {channel!r} là kênh lệnh hoặc tài khoản, không bao giờ được đăng ký",
                source=self.source,
            )
        for pattern, rule in self._compiled:
            if pattern.match(name):
                return rule
        raise PolicyError(
            f"Kênh {channel!r} chưa khai trong streams.toml của {self.source}. "
            f"Kênh có: {', '.join(rule.pattern for rule in self.channels)}",
            source=self.source,
        )

    def wire_name(self, channel: str) -> str:
        """``tick.G1`` → ``tick.G1.json``, after checking the channel.

        ``tick.G1`` → ``tick.G1.json``, sau khi đã kiểm kênh.
        """
        self.resolve(channel)
        return f"{strip_encoding(channel)}.{self.encoding}"

    def family_of(self, message_type: str) -> str | None:
        """Channel family of a data message type, e.g. ``"t"`` → ``"tick"``.

        Họ kênh của một loại thông điệp dữ liệu, ví dụ ``"t"`` → ``"tick"``.
        """
        return self._families.get(message_type)


def strip_encoding(channel: str) -> str:
    for suffix in _ENCODINGS:
        if channel.endswith(suffix):
            return channel[: -len(suffix)]
    return channel


def stream_sources() -> list[str]:
    """Sources that declare a stream endpoint.

    Các nguồn có khai cổng stream.
    """
    root = resources.files("vnstock_oapi") / "spec" / "sources"
    return sorted(entry.name for entry in root.iterdir() if (entry / "streams.toml").is_file())


@cache
def load_stream_spec(source: str) -> StreamSpec:
    """Read ``spec/sources/<source>/streams.toml``. No file means the source has no stream.

    Đọc ``spec/sources/<source>/streams.toml``. Không có tệp nghĩa là nguồn không có stream.
    """
    path = resources.files("vnstock_oapi") / "spec" / "sources" / source / "streams.toml"
    if not path.is_file():
        raise UnsupportedError(f"{source} chưa có cổng stream được khai báo", source=source)
    data = tomllib.loads(path.read_text(encoding="utf-8"))
    params = {
        key: value if isinstance(value, str) else tuple(value)
        for key, value in data.get("params", {}).items()
    }
    channels = tuple(
        ChannelRule(
            name=row["name"],
            pattern=row["pattern"],
            symbols=bool(row.get("symbols", False)),
            message_types=tuple(row.get("message_types", ())),
            description=row.get("description", ""),
        )
        for row in data.get("channels", [])
    )
    spec = StreamSpec(
        source=data["source"],
        title=data.get("title", source),
        url=data["url"],
        encoding=data.get("encoding", "json"),
        auth=data["auth"],
        heartbeat_s=float(data.get("heartbeat_s", 25)),
        type_field=data.get("type_field", "T"),
        control_field=data.get("control_field", "action"),
        forbidden=tuple(name.lower() for name in data.get("forbidden", ())),
        channels=channels,
        params=params,
        docs_url=data.get("docs_url", ""),
        checked_at=data.get("checked_at", ""),
        protocol=data.get("protocol", "json"),
    )
    if urlsplit(spec.url).scheme != "wss":
        raise PolicyError(f"Cổng stream của {source} phải là wss://", source=source)
    for rule in channels:
        if rule.pattern.split(".", 1)[0].lower() in spec.forbidden:
            raise PolicyError(f"streams.toml của {source} khai kênh cấm {rule.pattern!r}", source=source)
        rule.regex(params)  # unknown placeholders fail at load time, not at subscribe time
    return spec
