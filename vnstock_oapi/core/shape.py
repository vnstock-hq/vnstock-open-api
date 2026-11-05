"""Infer the shape of bodies (field paths and types) without keeping any value.

Suy ra hình dạng body (đường dẫn trường và kiểu), không giữ giá trị nào.

Used by ``vnstock-oapi dump`` to show what a source returns without copying the data itself: the shape
report lists field paths, types and how often each appears, never values.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable
from typing import Any

__all__ = ["shape_of", "render_markdown"]


def _type(value: Any) -> str:
    if value is None:
        return "null"
    if isinstance(value, bool):
        return "bool"
    if isinstance(value, int):
        return "int"
    if isinstance(value, float):
        return "float"
    if isinstance(value, str):
        return "str"
    if isinstance(value, list):
        return "array"
    if isinstance(value, dict):
        return "object"
    return type(value).__name__


def _walk(value: Any, path: str, out: dict[str, dict[str, int]]) -> None:
    out[path or "$"][_type(value)] += 1
    if isinstance(value, dict):
        for key, child in value.items():
            _walk(child, f"{path}.{key}" if path else key, out)
    elif isinstance(value, list):
        for child in value:
            _walk(child, f"{path}[]", out)


def shape_of(bodies: Iterable[Any]) -> dict[str, dict[str, int]]:
    """``{path: {type: count}}`` merged over every body.

    ``{đường dẫn: {kiểu: số lần}}`` gộp trên mọi body.
    """
    out: dict[str, dict[str, int]] = defaultdict(lambda: defaultdict(int))
    for body in bodies:
        _walk(body, "", out)
    return {path: dict(types) for path, types in sorted(out.items())}


def render_markdown(title: str, shape: dict[str, dict[str, int]]) -> str:
    lines = [f"### {title}", "", "| Trường | Kiểu (số lần) |", "| --- | --- |"]
    for path, types in shape.items():
        kinds = ", ".join(f"{name} ({count})" for name, count in sorted(types.items()))
        lines.append(f"| `{path}` | {kinds} |")
    return "\n".join(lines) + "\n"
