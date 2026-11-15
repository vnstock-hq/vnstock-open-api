"""Every operation names its own page of the source's API reference: the index of functions links to it.

Mỗi thao tác trỏ đúng trang API reference của nó: chỉ mục hàm liên kết tới đó.
"""

from __future__ import annotations

import pytest

import vnstock_oapi


@pytest.mark.parametrize("name", vnstock_oapi.sources())
def test_each_operation_has_its_own_reference_page(name: str) -> None:
    ops = vnstock_oapi.source(name).spec.ops
    pages = [op.doc for op in ops.values()]
    assert all(page.startswith("https://") for page in pages)
    assert len(set(pages)) == len(pages)
