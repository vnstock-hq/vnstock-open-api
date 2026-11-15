"""Run a blocking call (token, negotiate) without freezing a stream that is asked to stop.

Chạy một lời gọi chặn (lấy token, negotiate) mà phiên vẫn dừng được khi được yêu cầu.

Reading the session from the OS keyring can wait on a permission dialog (macOS asks the first time
an interpreter reads an item another program wrote), and a token refresh waits on the network. Run
in a daemon thread, the call can neither hold up ``stop()`` nor keep the process alive at exit; a
long wait is logged once so the user knows what the stream is waiting for.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable
from typing import Any, TypeVar

__all__ = ["call_blocking", "Stopped"]

T = TypeVar("T")

WARN_AFTER_S = 5.0


class Stopped(Exception):
    """The stream was stopped while the call was still running."""


async def call_blocking(
    call: Callable[[], T],
    *,
    what: str,
    source: str,
    stopping: Callable[[], bool],
    logger: logging.Logger,
    warn_after_s: float = WARN_AFTER_S,
    poll_s: float = 0.5,
) -> T:
    loop = asyncio.get_running_loop()
    future: asyncio.Future[Any] = loop.create_future()

    def settle(result: Any, error: BaseException | None) -> None:
        if future.done():
            return
        if error is None:
            future.set_result(result)
        else:
            future.set_exception(error)

    def hand_back(result: Any, error: BaseException | None) -> None:
        try:
            loop.call_soon_threadsafe(settle, result, error)
        except RuntimeError:  # the stream stopped and its loop closed: nobody is waiting
            pass

    def run() -> None:
        try:
            result = call()
        except BaseException as error:  # noqa: BLE001 - handed to the awaiting coroutine
            hand_back(None, error)
        else:
            hand_back(result, None)

    threading.Thread(target=run, name=f"vnstock-oapi-{source}-{what}", daemon=True).start()
    waited, warned = 0.0, False
    while not future.done():
        await asyncio.wait({future}, timeout=poll_s)
        if future.done():
            break
        waited += poll_s
        if not warned and waited >= warn_after_s:
            warned = True
            logger.warning(
                "Stream %s: đang chờ %s (%.0f s). Nếu hệ điều hành hỏi quyền đọc keychain cho Python, "
                "hãy chọn cho phép.",
                source,
                what,
                waited,
            )
        if stopping():
            future.cancel()
            raise Stopped(what)
    return future.result()  # type: ignore[no-any-return]
