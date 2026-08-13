from src.config import Config
from src.core.session import Session, SessionStore


def _make_store(monkeypatch, tmp_path) -> SessionStore:
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    return SessionStore(Config)


def test_save_and_load_roundtrip(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    session = Session(
        session_id="20260813-100000",
        model="deepseek-v4-flash",
        system_prompt="system",
        messages=[{"role": "user", "content": "hi"}],
    )
    store.save(session)
    loaded = store.load("20260813-100000")
    assert loaded is not None
    assert loaded.session_id == "20260813-100000"
    assert loaded.model == "deepseek-v4-flash"
    assert loaded.system_prompt == "system"
    assert loaded.messages == [{"role": "user", "content": "hi"}]
    assert loaded.created_at != ""
    assert loaded.updated_at != ""


def test_save_updates_updated_at(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    session = Session(
        session_id="s1", model="m", system_prompt="p", messages=[]
    )
    store.save(session)
    first = session.updated_at
    session.messages = [{"role": "user", "content": "x"}]
    store.save(session)
    loaded = store.load("s1")
    assert loaded is not None
    assert loaded.messages != []
    assert loaded.updated_at >= first


def test_load_missing_returns_none(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    assert store.load("nope") is None


def test_list_sorted_newest_first(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    for session_id in ["20260813-100000", "20260813-110000", "20260813-120000"]:
        store.save(
            Session(session_id=session_id, model="m", system_prompt="p")
        )
    ids = [s.session_id for s in store.list()]
    assert ids == [
        "20260813-120000",
        "20260813-110000",
        "20260813-100000",
    ]


def test_delete(monkeypatch, tmp_path):
    store = _make_store(monkeypatch, tmp_path)
    store.save(Session(session_id="s1", model="m", system_prompt="p"))
    store.delete("s1")
    assert store.load("s1") is None
    assert store.list() == []


def test_message_count_and_summary():
    session = Session(
        session_id="s1",
        model="m",
        system_prompt="p",
        messages=[
            {"role": "tool", "content": "调用 ask"},
            {"role": "user", "content": "帮我写一个函数"},
            {"role": "assistant", "content": "好的"},
        ],
    )
    assert session.message_count == 2
    assert session.summary == "帮我写一个函数"
