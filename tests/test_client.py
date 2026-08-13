from types import SimpleNamespace

from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from src.api.client import ChatClient, stream_completion
from src.api.tools import ToolRegistry
from src.config import Config
from src.core.event_bus import EventBus, EventTypes


def _chunk(content: str):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_chunk(index: int, tool_id: str, name: str, arguments: str):
    function = SimpleNamespace(name=name, arguments=arguments)
    tool_call = SimpleNamespace(index=index, id=tool_id, function=function)
    delta = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _client(monkeypatch, bus: EventBus) -> ChatClient:
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://api.example.com/v1")
    return ChatClient(Config, bus, ToolRegistry())


def _collect(monkeypatch, bus: EventBus, stream, user_message="你好"):
    client = _client(monkeypatch, bus)
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    client._run(user_message, "system prompt", [])
    return bus.drain()


def test_stream_emits_tokens(monkeypatch):
    bus = EventBus()

    def stream():
        yield _chunk("你")
        yield _chunk("好")

    events = _collect(monkeypatch, bus, stream)
    types = [e.type for e in events]
    assert types == [EventTypes.TOKEN, EventTypes.TOKEN, EventTypes.ASSISTANT_DONE]
    assert [e.data for e in events if e.type == EventTypes.TOKEN] == ["你", "好"]


def test_tool_loop(monkeypatch):
    bus = EventBus()
    round_ = {"n": 0}

    def stream():
        n = round_["n"]
        round_["n"] += 1
        if n == 0:
            yield _chunk("准备调用")
            yield _tool_chunk(0, "call_1", "shell", '{"command": "echo hi"}')
        yield _chunk("完成")

    events = _collect(monkeypatch, bus, stream)
    types = [e.type for e in events]
    assert EventTypes.TOOL_CALL in types
    assert EventTypes.TOOL_RESULT in types
    tool_call = next(e for e in events if e.type == EventTypes.TOOL_CALL)
    assert tool_call.data["name"] == "shell"
    tool_result = next(e for e in events if e.type == EventTypes.TOOL_RESULT)
    assert "hi" in tool_result.data
    assert events[-1].type == EventTypes.ASSISTANT_DONE


def test_many_tool_rounds_conclude_with_final_answer(monkeypatch):
    """多轮工具调用（超出旧上限）后，模型给出的最终回复必须完整呈现。"""
    bus = EventBus()
    round_ = {"n": 0}

    def stream():
        n = round_["n"]
        round_["n"] += 1
        if n < 25:
            yield _tool_chunk(0, f"call_{n}", "shell", '{"command": "echo x"}')
        else:
            yield _chunk("收尾总结")

    client = _client(monkeypatch, bus)
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    client._run("完成一个需要很多命令的任务", "system", [])
    events = bus.drain()
    assert events[-1].type == EventTypes.ASSISTANT_DONE
    tokens = [e.data for e in events if e.type == EventTypes.TOKEN]
    assert "收尾总结" in tokens


def test_error_emitted_on_failure(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)

    def boom(**kwargs):
        raise RuntimeError("network down")

    monkeypatch.setattr(client._client.chat.completions, "create", boom)
    client._run("hi", "system", [])
    events = bus.drain()
    assert events[-1].type == EventTypes.ERROR
    assert "network down" in events[-1].data


def test_client_recreated_on_provider_change(monkeypatch):
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "k1")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://api.example.com/v1")
    client = ChatClient(Config, EventBus(), ToolRegistry())
    first = client._client
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "k2")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://api.example.com/v2")
    second = client._ensure_client()
    assert first is not second
    assert client._ensure_client() is second


def test_stream_passes_variant_extra_body(monkeypatch):
    bus = EventBus()
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://api.example.com/v1")
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "fast")
    client = ChatClient(Config, bus, ToolRegistry())
    captured = {}

    def create(**kwargs):
        captured["extra_body"] = kwargs.get("extra_body")
        return iter([_chunk("hi")])

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    client._run("hi", "system", [])
    events = bus.drain()
    assert captured["extra_body"] == {"temperature": 0.9}
    assert events[-1].type == EventTypes.ASSISTANT_DONE


def _usage_chunk(prompt: int, completion: int):
    usage = SimpleNamespace(
        prompt_tokens=prompt,
        completion_tokens=completion,
        total_tokens=prompt + completion,
    )
    return SimpleNamespace(choices=[], usage=usage)


def test_stream_requests_usage(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)
    captured = {}

    def create(**kwargs):
        captured["stream_options"] = kwargs.get("stream_options")
        return iter([_chunk("hi")])

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    client._run("hi", "system", [])
    assert captured["stream_options"] == {"include_usage": True}


def test_stream_captures_usage(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)

    def stream():
        yield _chunk("你")
        yield _usage_chunk(100, 20)

    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    client._run("hi", "system", [])
    usage = client.usage_summary()
    assert usage["input_tokens"] == 100
    assert usage["output_tokens"] == 20
    assert usage["calls"] == 1
    assert usage["last_input_tokens"] == 100


def test_usage_accumulates_across_rounds(monkeypatch):
    bus = EventBus()
    round_ = {"n": 0}

    def stream():
        n = round_["n"]
        round_["n"] += 1
        if n == 0:
            yield _tool_chunk(0, "call_1", "shell", '{"command": "echo hi"}')
            yield _chunk("调用工具")
            yield _usage_chunk(50, 30)
        else:
            yield _chunk("完成")
            yield _usage_chunk(10, 5)

    client = _client(monkeypatch, bus)
    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    client._run("hi", "system", [])
    usage = client.usage_summary()
    assert usage["input_tokens"] == 60
    assert usage["output_tokens"] == 35
    assert usage["calls"] == 2
    assert usage["last_input_tokens"] == 10


def test_stream_uses_active_model(monkeypatch):
    bus = EventBus()
    monkeypatch.setattr(Config, "CUSTOM_API_KEY", "test-key")
    monkeypatch.setattr(Config, "CUSTOM_API_URL", "https://api.example.com/v1")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "my-model")
    client = ChatClient(Config, bus, ToolRegistry())
    captured = {}

    def create(**kwargs):
        captured["model"] = kwargs.get("model")
        return iter([_chunk("hi")])

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    client._run("hi", "system", [])
    assert captured["model"] == "my-model"


def test_stream_completion_subagent_events(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)
    monkeypatch.setattr(client._tools, "execute", lambda name, args, bus: "found: line1")

    def stream():
        yield _chunk("子")
        yield _tool_chunk(0, "call_9", "grep", '{"pattern": "x"}')
        yield _chunk("结果")

    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="角色"),
        ChatCompletionUserMessageParam(role="user", content="任务"),
    ]
    made, text = stream_completion(
        Config,
        bus,
        client._client,
        client._tools,
        messages,
        "auto",
        agent_id="librarian",
    )
    events = bus.drain()
    assert made is True
    assert text == "子结果"
    types = [e.type for e in events]
    assert types == [
        EventTypes.SUBAGENT_TOKEN,
        EventTypes.SUBAGENT_TOKEN,
        EventTypes.SUBAGENT_TOOL_CALL,
        EventTypes.SUBAGENT_TOOL_RESULT,
    ]
    assert events[0].data == {"agent_id": "librarian", "text": "子"}
    assert events[-1].data == {"agent_id": "librarian", "result": "found: line1"}
    assert EventTypes.TOKEN not in types


def test_stream_completion_main_events_unchanged(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)
    monkeypatch.setattr(client._tools, "execute", lambda name, args, bus: "ok")

    def stream():
        yield _tool_chunk(0, "call_1", "read", '{"file_path": "a.py"}')

    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="s"),
        ChatCompletionUserMessageParam(role="user", content="u"),
    ]
    made, _ = stream_completion(Config, bus, client._client, client._tools, messages, "auto")
    events = bus.drain()
    assert made is True
    types = [e.type for e in events]
    assert types == [EventTypes.TOOL_CALL, EventTypes.TOOL_RESULT]
    assert events[0].data == {"name": "read", "arguments": '{"file_path": "a.py"}'}
    assert events[1].data == "ok"
