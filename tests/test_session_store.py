"""Where a session is kept, and what clearing it may touch.

Phiên được lưu ở đâu, và xoá phiên được phép đụng tới gì.
"""

from __future__ import annotations


def test_clearing_a_file_session_leaves_the_keyring_alone(monkeypatch, tmp_path) -> None:
    import keyring

    from vnstock_oapi.core.session_store import SessionStore

    deleted: list[str] = []
    monkeypatch.setattr(keyring, "delete_password", lambda _service, name: deleted.append(name))
    monkeypatch.setenv("VNSTOCK_OAPI_STATE_DIR", str(tmp_path))
    store = SessionStore("tcbs", backend="file")
    store.put({"access_token": "x"})
    store.clear()
    assert store.get() is None and deleted == []
    SessionStore("tcbs", backend="keyring").clear()
    assert deleted == ["tcbs/default/session"]
