"""Registry of installed sources: every module in this package that declares ``SPEC``.

Danh sách nguồn đã cài: mọi module trong gói này có khai báo ``SPEC``.

A build can ship a subset of sources by leaving modules out; nothing else names them.
"""

from __future__ import annotations

import importlib
import pkgutil

from vnstock_oapi.core.source import SourceSpec


def _discover() -> dict[str, SourceSpec]:
    names = sorted(info.name for info in pkgutil.iter_modules(__path__) if not info.name.startswith("_"))
    specs: dict[str, SourceSpec] = {}
    for name in names:
        spec = getattr(importlib.import_module(f"{__name__}.{name}"), "SPEC", None)
        if isinstance(spec, SourceSpec):
            specs[spec.name] = spec
    return specs


SPECS: dict[str, SourceSpec] = _discover()

__all__ = ["SPECS"]
