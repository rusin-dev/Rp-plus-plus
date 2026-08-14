import pytest

from src.config import Config, Provider
from src.core.event_bus import Event, EventBus, EventTypes
from src.core.logger import get_logger
from src.core.prompt import get_prompt, list_prompts


def _use_provider(monkeypatch, api_key="k", api_url="https://api.example.com/v1"):
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "test")
    monkeypatch.setattr(
        Config,
        "providers",
        lambda: {
            "test": Provider(
                name="test",
                api_key=api_key,
                api_url=api_url,
                models=["m"],
                default_model="m",
            )
        },
    )


def test_validate_missing_api_key_raises(monkeypatch):
    _use_provider(monkeypatch, api_key="")
    with pytest.raises(ValueError, match="API key"):
        Config.validate()


def test_validate_invalid_url_raises(monkeypatch):
    _use_provider(monkeypatch, api_url="not-a-url")
    with pytest.raises(ValueError, match="API 地址"):
        Config.validate()


def test_validate_ok(monkeypatch):
    _use_provider(monkeypatch)
    Config.validate()


def test_get_prompt_general():
    content = get_prompt("SYSTEM_PROMPT.md")
    assert "SYSTEM_PROMPT" in content


def test_get_prompt_missing_file():
    with pytest.raises(FileNotFoundError):
        get_prompt("not_exists.md")


def test_get_prompt_rejects_traversal():
    with pytest.raises(ValueError):
        get_prompt("../config.py")


def test_get_prompt_rejects_wrong_extension(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    (tmp_path / "general").mkdir()
    (tmp_path / "general" / "evil.py").write_text("print(1)", encoding="utf-8")
    with pytest.raises(ValueError):
        get_prompt("evil.py")


def test_list_prompts_general():
    names = [p.name for p in list_prompts("general")]
    assert "SYSTEM_PROMPT.md" in names


def test_list_prompts_missing_level(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "DATA_DIR", tmp_path)
    assert list_prompts("nope") == []


def test_get_logger_writes_file(tmp_path, monkeypatch):
    monkeypatch.setattr(Config, "LOG_DIR", tmp_path / "logs")
    monkeypatch.setattr(Config, "LOG_LEVEL", "INFO")
    log = get_logger("test_get_logger_writes_file", include_console=False)
    log.info("hello")
    log.handlers[0].flush()
    assert (tmp_path / "logs" / "log.log").exists()
    content = (tmp_path / "logs" / "log.log").read_text(encoding="utf-8")
    assert "hello" in content


def test_event_bus_publish_get():
    bus = EventBus()
    bus.publish(Event(EventTypes.TOKEN, "hi"))
    event = bus.get(timeout=0.5)
    assert event is not None
    assert event.type == EventTypes.TOKEN
    assert event.data == "hi"
    assert bus.get(timeout=0.01) is None


def test_event_bus_await_skips_others():
    bus = EventBus()
    bus.publish(Event("other", 1))
    bus.publish(Event(EventTypes.USER_ANSWER, "yes"))
    event = bus.await_event(EventTypes.USER_ANSWER, timeout=1)
    assert event.data == "yes"


def test_event_bus_await_timeout():
    bus = EventBus()
    with pytest.raises(TimeoutError):
        bus.await_event(EventTypes.USER_ANSWER, timeout=0.05)


def test_event_bus_drain():
    bus = EventBus()
    bus.publish(Event("a"))
    bus.publish(Event("b"))
    assert [e.type for e in bus.drain()] == ["a", "b"]
    assert bus.drain() == []


def test_event_bus_close():
    bus = EventBus()
    bus.close()
    bus.publish(Event("ignored"))
    events = [e.type for e in bus.drain()]
    assert events == [EventTypes.SHUTDOWN]
