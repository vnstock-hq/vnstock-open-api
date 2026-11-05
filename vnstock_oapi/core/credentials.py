"""Read keys: environment → ``.env`` in the current directory → keyring.

Đọc khoá: biến môi trường → tệp ``.env`` ở thư mục hiện tại → keyring.

No public function takes a key as an argument. ``.env`` is read from the current directory only,
never from parent directories, and never overrides a variable that is already set.
"""

from __future__ import annotations

import logging
import os
import stat
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from vnstock_oapi.core.secret import Secret
from vnstock_oapi.core.terms import load_terms
from vnstock_oapi.errors import CredentialError

__all__ = [
    "KEYRING_SERVICE",
    "CredentialField",
    "Credentials",
    "env_name",
    "read_dotenv",
    "load",
    "keyring_set",
    "keyring_delete",
]

logger = logging.getLogger("vnstock_oapi.credentials")

KEYRING_SERVICE = "vnstock-oapi"


@dataclass(frozen=True)
class CredentialField:
    """One credential field of a source, e.g. ``api_secret`` read from ``SSI_API_SECRET``.

    Một trường khoá của nguồn, ví dụ ``api_secret`` đọc từ ``SSI_API_SECRET``.
    """

    name: str
    env_suffix: str
    required: bool = True
    label: str = ""


@dataclass(frozen=True)
class Credentials:
    values: Mapping[str, Secret]
    origins: Mapping[str, str]

    def __getitem__(self, name: str) -> Secret:
        return self.values[name]

    def get(self, name: str) -> Secret | None:
        return self.values.get(name)


def env_name(source: str, profile: str, suffix: str) -> str:
    """``SSI_API_KEY`` for the default profile, ``SSI_WORK_API_KEY`` for profile ``work``.

    ``SSI_API_KEY`` cho hồ sơ mặc định, ``SSI_WORK_API_KEY`` cho hồ sơ ``work``.
    """
    parts = [source.upper()]
    if profile != "default":
        parts.append(profile.upper().replace("-", "_"))
    parts.append(suffix)
    return "_".join(parts)


def read_dotenv(path: Path | None = None) -> dict[str, str]:
    """Read ``KEY=VALUE`` lines from ``.env``; skip comments, ``export`` and surrounding quotes.

    Đọc ``KEY=VALUE`` từ ``.env``; bỏ dòng chú thích, ``export`` và dấu nháy bao ngoài.
    """
    path = path or Path.cwd() / ".env"
    if not path.is_file():
        return {}
    if os.name == "posix" and path.stat().st_mode & (stat.S_IRWXG | stat.S_IRWXO):
        logger.warning(
            "%s đọc được bởi người dùng khác trên máy. Nên chạy: chmod 600 %s", path, path
        )
    values: dict[str, str] = {}
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        if line.startswith("export "):
            line = line[len("export ") :]
        key, _, value = line.partition("=")
        value = value.strip()
        if len(value) >= 2 and value[0] == value[-1] and value[0] in "\"'":
            value = value[1:-1]
        values[key.strip()] = value
    return values


def _keyring_get(source: str, profile: str, field: str) -> str | None:
    try:
        import keyring

        return keyring.get_password(KEYRING_SERVICE, f"{source}/{profile}/{field}")
    except Exception:  # noqa: BLE001 (no keyring backend on headless machines)
        return None


def keyring_set(source: str, profile: str, field: str, value: str) -> None:
    import keyring

    keyring.set_password(KEYRING_SERVICE, f"{source}/{profile}/{field}", value)


def keyring_delete(source: str, profile: str, field: str) -> None:
    try:
        import keyring

        keyring.delete_password(KEYRING_SERVICE, f"{source}/{profile}/{field}")
    except Exception:  # noqa: BLE001, S110 (nothing stored is not an error)
        pass


def load(
    source: str,
    profile: str,
    fields: tuple[CredentialField, ...],
    *,
    environ: Mapping[str, str] | None = None,
    dotenv: Mapping[str, str] | None = None,
    use_keyring: bool = True,
) -> Credentials:
    """Read every credential field of a source. Raise ``CredentialError`` if a required one is missing.

    Đọc mọi trường khoá của một nguồn; thiếu trường bắt buộc thì ``CredentialError``.
    """
    environ = os.environ if environ is None else environ
    dotenv = read_dotenv() if dotenv is None else dotenv
    values: dict[str, Secret] = {}
    origins: dict[str, str] = {}
    missing: list[str] = []
    for field in fields:
        name = env_name(source, profile, field.env_suffix)
        raw: str | None = None
        origin = ""
        if environ.get(name):
            raw, origin = environ[name], "env"
        elif dotenv.get(name):
            raw, origin = dotenv[name], ".env"
        elif use_keyring:
            raw = _keyring_get(source, profile, field.name)
            origin = "keyring" if raw else ""
        if raw:
            try:
                values[field.name] = Secret(raw)
            except ValueError:
                raise CredentialError(f"{name} rỗng", source=source) from None
            origins[field.name] = origin
        elif field.required:
            missing.append(name)
    if missing:
        raise CredentialError(
            f"Chưa có khoá: {', '.join(missing)}. Đặt trong biến môi trường, tệp .env, "
            f"hoặc chạy `vnstock-oapi keys set {source}`" + _signup_hint(source),
            source=source,
        )
    return Credentials(values=values, origins=origins)


def _signup_hint(source: str) -> str:
    """Where to get a key, from the source's ``terms.toml``; empty when it names none."""
    try:
        terms = load_terms(source)
    except (FileNotFoundError, KeyError, OSError):
        return ""
    if not terms.signup_url:
        return ""
    note = f" ({terms.signup_note})" if terms.signup_note else ""
    return f". Chưa có tài khoản: {terms.signup_url}{note}"
