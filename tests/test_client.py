import threading
from types import SimpleNamespace

from openai.types.chat import (
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from src.api.client import ChatClient, make_client, stream_completion
from src.api.tools import ToolRegistry
from src.config import Config, Provider
from src.core.event_bus import EventBus, EventTypes


def _chunk(content: str):
    delta = SimpleNamespace(content=content, tool_calls=None)
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _tool_chunk(index: int, tool_id: str, name: str, arguments: str):
    function = SimpleNamespace(name=name, arguments=arguments)
    tool_call = SimpleNamespace(index=index, id=tool_id, function=function)
    delta = SimpleNamespace(content=None, tool_calls=[tool_call])
    return SimpleNamespace(choices=[SimpleNamespace(delta=delta)])


def _provider(
    name: str = "test",
    api_key: str = "test-key",
    api_url: str = "https://api.example.com/v1",
    models: list[str] | None = None,
) -> Provider:
    return Provider(
        name=name,
        api_key=api_key,
        api_url=api_url,
        models=list(models or []),
        default_model=(models or ["m"])[0],
    )


def _use_provider(
    monkeypatch, name="test", api_key="test-key", api_url="https://api.example.com/v1"
):
    provider = _provider(name=name, api_key=api_key, api_url=api_url)
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", name)
    monkeypatch.setattr(Config, "ACTIVE_MODEL", None)
    monkeypatch.setattr(Config, "providers", lambda: {name: provider})
    return provider


def _client(monkeypatch, bus: EventBus) -> ChatClient:
    _use_provider(monkeypatch)
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
    provider_a = _provider(name="a", api_key="k1", api_url="https://api.example.com/v1")
    provider_b = _provider(name="b", api_key="k2", api_url="https://api.example.com/v2")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "a")
    monkeypatch.setattr(Config, "providers", lambda: {"a": provider_a})
    client = ChatClient(Config, EventBus(), ToolRegistry())
    first = client._client
    monkeypatch.setattr(Config, "providers", lambda: {"b": provider_b})
    second = client._ensure_client()
    assert first is not second
    assert client._ensure_client() is second


def test_client_constructed_without_provider(monkeypatch):
    """未配置任何 provider 时，ChatClient 仍可创建，但不建立底层 OpenAI 客户端。"""
    monkeypatch.setattr(Config, "providers", lambda: {})
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", None)
    monkeypatch.setattr(Config, "ACTIVE_MODEL", None)
    client = ChatClient(Config, EventBus(), ToolRegistry())
    assert client._client is None


def test_ensure_client_builds_after_provider_configured(monkeypatch):
    monkeypatch.setattr(Config, "providers", lambda: {})
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", None)
    client = ChatClient(Config, EventBus(), ToolRegistry())
    assert client._client is None
    _use_provider(monkeypatch)
    built = client._ensure_client()
    assert built is not None
    assert client._ensure_client() is built


def test_stream_passes_variant_extra_body(monkeypatch):
    bus = EventBus()
    _use_provider(monkeypatch)
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "high")
    client = ChatClient(Config, bus, ToolRegistry())
    captured = {}

    def create(**kwargs):
        captured["extra_body"] = kwargs.get("extra_body")
        return iter([_chunk("hi")])

    monkeypatch.setattr(client._client.chat.completions, "create", create)
    client._run("hi", "system", [])
    events = bus.drain()
    assert captured["extra_body"] == {"reasoning_effort": "high"}
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
    _use_provider(monkeypatch)
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


# ---------- 中断（ESC 双击） ----------


def test_cancel_method_sets_flag(monkeypatch):
    client = _client(monkeypatch, EventBus())
    assert client._cancel.is_set() is False
    client.cancel()
    assert client._cancel.is_set() is True


def test_cancel_preset_skips_request(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)
    cancel = threading.Event()
    cancel.set()

    def boom(**kwargs):
        raise AssertionError("已取消时不应发起请求")

    monkeypatch.setattr(client._client.chat.completions, "create", boom)
    made, text = stream_completion(
        Config, bus, client._client, client._tools, [], "auto", cancel_event=cancel
    )
    assert made is False
    assert text == ""


def test_cancel_during_stream_stops_and_done(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)
    cancel = threading.Event()

    def stream():
        yield _chunk("第一段")
        cancel.set()
        yield _chunk("第二段")

    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    made, text = stream_completion(
        Config, bus, client._client, client._tools, [], "auto", cancel_event=cancel
    )
    assert made is False
    assert text == "第一段"
    events = bus.drain()
    assert [e.type for e in events] == [EventTypes.TOKEN]
    assert events[0].data == "第一段"


def test_run_breaks_loop_when_cancelled(monkeypatch):
    bus = EventBus()
    client = _client(monkeypatch, bus)
    rounds = {"n": 0}

    def stream():
        rounds["n"] += 1
        if rounds["n"] == 1:
            yield _chunk("a")
            yield _tool_chunk(0, "call_1", "shell", '{"command": "echo x"}')
        else:
            client.cancel()
            yield _chunk("b")

    monkeypatch.setattr(client._client.chat.completions, "create", lambda **kwargs: stream())
    client._run("任务", "system", [])
    events = bus.drain()
    types = [e.type for e in events]
    assert EventTypes.TOOL_CALL in types
    assert events[-1].type == EventTypes.ASSISTANT_DONE
    tokens = [e.data for e in events if e.type == EventTypes.TOKEN]
    assert "b" not in tokens


# ---------- 传输分发（provider.type） ----------


def _provider_with_type(
    name: str,
    ptype: str,
    api_key: str = "test-key",
    api_url: str = "https://api.example.com/v1",
    models: list[str] | None = None,
) -> Provider:
    return Provider(
        name=name,
        api_key=api_key,
        api_url=api_url,
        models=list(models or ["m"]),
        default_model=(models or ["m"])[0],
        type=ptype,
    )


def _use_provider_with(monkeypatch, provider: Provider):
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", provider.name)
    monkeypatch.setattr(Config, "ACTIVE_MODEL", None)
    monkeypatch.setattr(Config, "providers", lambda: {provider.name: provider})


def test_make_client_anthropic_returns_anthropic_instance(monkeypatch):
    from anthropic import Anthropic

    provider = _provider_with_type(
        "ant", "anthropic", api_url="https://api.anthropic.com", models=["claude-x"]
    )
    _use_provider_with(monkeypatch, provider)
    client = make_client(Config)
    assert isinstance(client, Anthropic)


def test_make_client_openai_default(monkeypatch):
    from openai import OpenAI

    _use_provider(monkeypatch)
    client = make_client(Config)
    assert isinstance(client, OpenAI)


def test_ensure_client_recreates_on_type_change(monkeypatch):
    provider_openai = _provider_with_type("o", "openai")
    provider_anthropic = _provider_with_type(
        "a", "anthropic", api_url="https://api.anthropic.com", models=["claude-x"]
    )
    _use_provider_with(monkeypatch, provider_openai)
    client = ChatClient(Config, EventBus(), ToolRegistry())
    first = client._client
    _use_provider_with(monkeypatch, provider_anthropic)
    second = client._ensure_client()
    assert first is not second


def test_stream_anthropic_emits_text_tokens(monkeypatch):
    from anthropic import Anthropic

    bus = EventBus()
    provider = _provider_with_type(
        "ant", "anthropic", api_url="https://api.anthropic.com", models=["claude-x"]
    )
    _use_provider_with(monkeypatch, provider)
    anthropic_client = Anthropic(api_key="x")

    msg_start = SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(usage=SimpleNamespace(input_tokens=10, output_tokens=0)),
    )
    block_start = SimpleNamespace(
        type="content_block_start",
        index=0,
        content_block=SimpleNamespace(type="text", text=""),
    )
    delta_1 = SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="text_delta", text="你"),
    )
    delta_2 = SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="text_delta", text="好"),
    )
    block_stop = SimpleNamespace(type="content_block_stop", index=0)
    msg_delta = SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=5))
    msg_stop = SimpleNamespace(type="message_stop")

    def stream():
        yield msg_start
        yield block_start
        yield delta_1
        yield delta_2
        yield block_stop
        yield msg_delta
        yield msg_stop

    captured = {}

    def create(**kwargs):
        captured["system"] = kwargs.get("system")
        captured["model"] = kwargs.get("model")
        captured["messages"] = kwargs.get("messages")
        captured["max_tokens"] = kwargs.get("max_tokens")
        return stream()

    monkeypatch.setattr(anthropic_client.messages, "create", create)

    messages = [
        ChatCompletionSystemMessageParam(role="system", content="你是助手"),
        ChatCompletionUserMessageParam(role="user", content="hi"),
    ]
    made, text = stream_completion(Config, bus, anthropic_client, ToolRegistry(), messages, "auto")

    assert made is False
    assert text == "你好"
    assert captured["model"] == "claude-x"
    assert captured["max_tokens"]  # 必须传 max_tokens
    assert captured["system"] == "你是助手" or (
        captured["system"] and "你是助手" in captured["system"]
    )
    tokens = [e.data for e in bus.drain() if e.type == EventTypes.TOKEN]
    assert tokens == ["你", "好"]


def test_stream_anthropic_tool_call(monkeypatch):
    from anthropic import Anthropic

    bus = EventBus()
    provider = _provider_with_type(
        "ant", "anthropic", api_url="https://api.anthropic.com", models=["claude-x"]
    )
    _use_provider_with(monkeypatch, provider)
    anthropic_client = Anthropic(api_key="x")

    tool_block_start = SimpleNamespace(
        type="content_block_start",
        index=0,
        content_block=SimpleNamespace(type="tool_use", id="toolu_1", name="shell", input={}),
    )
    args_delta_1 = SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="input_json_delta", partial_json='{"command":'),
    )
    args_delta_2 = SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="input_json_delta", partial_json=' "echo hi"}'),
    )
    block_stop = SimpleNamespace(type="content_block_stop", index=0)
    msg_stop = SimpleNamespace(type="message_stop")

    def stream():
        yield tool_block_start
        yield args_delta_1
        yield args_delta_2
        yield block_stop
        yield msg_stop

    monkeypatch.setattr(anthropic_client.messages, "create", lambda **kw: stream())
    monkeypatch.setattr(ToolRegistry, "execute", lambda self, name, args, bus: "echo hi output")

    tools = ToolRegistry()
    messages = [
        ChatCompletionSystemMessageParam(role="system", content="s"),
        ChatCompletionUserMessageParam(role="user", content="run echo hi"),
    ]
    made, _ = stream_completion(Config, bus, anthropic_client, tools, messages, "auto")
    assert made is True
    events = bus.drain()
    tool_call = next(e for e in events if e.type == EventTypes.TOOL_CALL)
    assert tool_call.data["name"] == "shell"
    assert "echo hi" in tool_call.data["arguments"]
    tool_result = next(e for e in events if e.type == EventTypes.TOOL_RESULT)
    assert "echo hi output" in tool_result.data


def test_stream_anthropic_records_usage(monkeypatch):
    from anthropic import Anthropic

    bus = EventBus()
    provider = _provider_with_type(
        "ant", "anthropic", api_url="https://api.anthropic.com", models=["claude-x"]
    )
    _use_provider_with(monkeypatch, provider)
    anthropic_client = Anthropic(api_key="x")

    msg_start = SimpleNamespace(
        type="message_start",
        message=SimpleNamespace(usage=SimpleNamespace(input_tokens=42, output_tokens=0)),
    )
    block_start = SimpleNamespace(
        type="content_block_start",
        index=0,
        content_block=SimpleNamespace(type="text", text=""),
    )
    delta = SimpleNamespace(
        type="content_block_delta",
        index=0,
        delta=SimpleNamespace(type="text_delta", text="hi"),
    )
    block_stop = SimpleNamespace(type="content_block_stop", index=0)
    msg_delta = SimpleNamespace(type="message_delta", usage=SimpleNamespace(output_tokens=7))
    msg_stop = SimpleNamespace(type="message_stop")

    def stream():
        yield msg_start
        yield block_start
        yield delta
        yield block_stop
        yield msg_delta
        yield msg_stop

    monkeypatch.setattr(anthropic_client.messages, "create", lambda **kw: stream())

    client = ChatClient(Config, bus, ToolRegistry())
    monkeypatch.setattr(client, "_ensure_client", lambda: anthropic_client)
    client._run("hi", "sys", [])
    usage = client.usage_summary()
    assert usage["input_tokens"] == 42
    assert usage["output_tokens"] == 7
    assert usage["last_input_tokens"] == 42
    assert usage["calls"] == 1


def test_stream_responses_emits_text_tokens(monkeypatch):
    from openai import OpenAI

    bus = EventBus()
    provider = _provider_with_type("resp", "responses", models=["gpt-x"])
    _use_provider_with(monkeypatch, provider)
    openai_client = OpenAI(api_key="x")

    text_delta_1 = SimpleNamespace(type="response.output_text.delta", delta="你")
    text_delta_2 = SimpleNamespace(type="response.output_text.delta", delta="好")
    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=3, output_tokens=4, total_tokens=7)
        ),
    )

    def stream():
        yield text_delta_1
        yield text_delta_2
        yield completed

    captured = {}

    def create(**kwargs):
        captured["instructions"] = kwargs.get("instructions")
        captured["model"] = kwargs.get("model")
        captured["stream"] = kwargs.get("stream")
        return stream()

    monkeypatch.setattr(openai_client.responses, "create", create)

    messages = [
        ChatCompletionSystemMessageParam(role="system", content="你是助手"),
        ChatCompletionUserMessageParam(role="user", content="hi"),
    ]
    made, text = stream_completion(Config, bus, openai_client, ToolRegistry(), messages, "auto")
    assert made is False
    assert text == "你好"
    assert captured["model"] == "gpt-x"
    assert captured["stream"] is True
    tokens = [e.data for e in bus.drain() if e.type == EventTypes.TOKEN]
    assert tokens == ["你", "好"]


def test_stream_responses_tool_call(monkeypatch):
    from openai import OpenAI

    bus = EventBus()
    provider = _provider_with_type("resp", "responses", models=["gpt-x"])
    _use_provider_with(monkeypatch, provider)
    openai_client = OpenAI(api_key="x")

    args_delta_1 = SimpleNamespace(
        type="response.function_call_arguments.delta",
        item_id="fc_1",
        delta='{"command":',
    )
    args_delta_2 = SimpleNamespace(
        type="response.function_call_arguments.delta",
        item_id="fc_1",
        delta=' "echo hi"}',
    )
    output_item_done = SimpleNamespace(
        type="response.output_item.done",
        item=SimpleNamespace(
            type="function_call",
            call_id="fc_1",
            name="shell",
            arguments='{"command": "echo hi"}',
        ),
    )
    completed = SimpleNamespace(
        type="response.completed",
        response=SimpleNamespace(
            usage=SimpleNamespace(input_tokens=1, output_tokens=2, total_tokens=3)
        ),
    )

    def stream():
        yield args_delta_1
        yield args_delta_2
        yield output_item_done
        yield completed

    monkeypatch.setattr(openai_client.responses, "create", lambda **kw: stream())
    monkeypatch.setattr(ToolRegistry, "execute", lambda self, name, args, bus: "ok")

    tools = ToolRegistry()
    messages = [ChatCompletionUserMessageParam(role="user", content="run")]
    made, _ = stream_completion(Config, bus, openai_client, tools, messages, "auto")
    assert made is True
    events = bus.drain()
    tool_call = next(e for e in events if e.type == EventTypes.TOOL_CALL)
    assert tool_call.data["name"] == "shell"
    assert "echo hi" in tool_call.data["arguments"]
    assert next(e for e in events if e.type == EventTypes.TOOL_RESULT).data == "ok"
