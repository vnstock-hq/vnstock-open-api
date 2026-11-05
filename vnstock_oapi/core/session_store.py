"""Session store shared by every process on the machine.

Kho phiên dùng chung cho mọi tiến trình trên máy.

Some sources allow one session per API key and treat a reused refresh token as a leak; others need a
new OTP for every token. So a notebook, a pipeline and an MCP server on the same machine must share one
token instead of each logging in. This store does that:

- keyring first (item ``<source>/<profile>/session``);
- otherwise a file in the user state directory: directory 700, file 600, atomic replace;
- a file lock around every refresh, so only one process refreshes and the others read its result.

``VNSTOCK_OAPI_SESSION_BACKEND=file`` forces the file backend (tests, servers without keyring).
``VNSTOCK_OAPI_STATE_DIR`` moves the state directory.
"""

from __future__ import annotations

import contextlib
import json
import os
import sys
import tempfile
from collections.abc import Iterator
from pathlib import Path
from typing import Any

import platformdirs

from vnstock_oapi.core.credentials import KEYRING_SERVICE

__all__ = ["state_dir", "SessionStore"]


def state_dir() -> Path:
    override = os.environ.get("VNSTOCK_OAPI_STATE_DIR")
    return Path(override) if override else platformdirs.user_state_path("vnstock-oapi")


def _private_dir(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        path.chmod(0o700)
    return path


class SessionStore:
    """Session storage for one source and profile.

    Phiên của một nguồn, một hồ sơ.
    """

    def __init__(self, source: str, profile: str = "default", backend: str | None = None) -> None:
        self.source = source
        self.profile = profile
        self.backend = backend or os.environ.get("VNSTOCK_OAPI_SESSION_BACKEND") or "keyring"
        if self.backend not in ("keyring", "file"):
            raise ValueError("backend must be 'keyring' or 'file'")

    @property
    def _item(self) -> str:
        return f"{self.source}/{self.profile}/session"

    @property
    def path(self) -> Path:
        return state_dir() / "sessions" / f"{self.source}-{self.profile}.json"

    @property
    def _lock_path(self) -> Path:
        return state_dir() / "sessions" / f"{self.source}-{self.profile}.lock"

    # keyring ------------------------------------------------------------

    def _keyring_get(self) -> str | None:
        try:
            import keyring

            return keyring.get_password(KEYRING_SERVICE, self._item)
        except Exception:  # noqa: BLE001
            return None

    def _keyring_set(self, text: str) -> bool:
        try:
            import keyring

            keyring.set_password(KEYRING_SERVICE, self._item, text)
            return True
        except Exception:  # noqa: BLE001 (fall back to the file backend)
            return False

    def _keyring_delete(self) -> None:
        try:
            import keyring

            keyring.delete_password(KEYRING_SERVICE, self._item)
        except Exception:  # noqa: BLE001, S110
            pass

    # file ---------------------------------------------------------------

    def _file_get(self) -> str | None:
        try:
            return self.path.read_text(encoding="utf-8")
        except FileNotFoundError:
            return None

    def _file_set(self, text: str) -> None:
        directory = _private_dir(self.path.parent)
        fd, tmp = tempfile.mkstemp(dir=directory, prefix=".session-", suffix=".tmp")
        try:
            if os.name == "posix":
                os.fchmod(fd, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                handle.write(text)
            os.replace(tmp, self.path)
        except BaseException:
            with contextlib.suppress(FileNotFoundError):
                os.unlink(tmp)
            raise

    # public -------------------------------------------------------------

    def get(self) -> dict[str, Any] | None:
        text = self._keyring_get() if self.backend == "keyring" else None
        if text is None:
            text = self._file_get()
        if not text:
            return None
        try:
            data = json.loads(text)
        except ValueError:
            return None
        return data if isinstance(data, dict) else None

    def put(self, session: dict[str, Any]) -> str:
        """Store the session. Return where it went: ``keyring`` or ``file``.

        Lưu phiên. Trả về nơi đã lưu: ``keyring`` hoặc ``file``.
        """
        text = json.dumps(session, separators=(",", ":"))
        if self.backend == "keyring" and self._keyring_set(text):
            with contextlib.suppress(FileNotFoundError):
                self.path.unlink()
            return "keyring"
        self._file_set(text)
        return "file"

    def clear(self) -> None:
        self._keyring_delete()
        with contextlib.suppress(FileNotFoundError):
            self.path.unlink()

    @contextlib.contextmanager
    def lock(self) -> Iterator[None]:
        """Cross-process lock around login and refresh.

        Khoá liên tiến trình quanh việc đăng nhập và làm mới phiên.
        """
        _private_dir(self._lock_path.parent)
        with open(self._lock_path, "a+") as handle:
            if sys.platform == "win32":  # pragma: no cover
                import msvcrt

                handle.seek(0)
                msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
                try:
                    yield
                finally:
                    handle.seek(0)
                    msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                import fcntl

                fcntl.flock(handle.fileno(), fcntl.LOCK_EX)
                try:
                    yield
                finally:
                    fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
