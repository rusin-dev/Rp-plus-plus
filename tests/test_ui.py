import io
from typing import Any

from src.config import Config
from src.core.event_bus import Event, EventBus, EventTypes
from src.ui.app import ChatApp


def _make_app(monkeypatch) -> tuple[ChatApp, EventBus]:
    monkeypatch.setattr(Config, "RICH_COLOR_SYSTEM", "auto")
    bus = EventBus()
    app = ChatApp(Config, bus, None, "system prompt")
    return app, bus


def test_token_appends_to_current(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "你"))
    app._handle(Event(EventTypes.TOKEN, "好"))
    assert app._current == "你好"
    assert app._messages == []


def test_reply_prefix_opened_on_first_token(monkeypatch):
    app, bus = _make_app(monkeypatch)
    assert app._reply_open is False
    app._handle(Event(EventTypes.TOKEN, "a"))
    assert app._reply_open is True


def test_drain_one_consumes_single_event(monkeypatch):
    app, bus = _make_app(monkeypatch)
    bus.publish(Event(EventTypes.TOKEN, "a"))
    bus.publish(Event(EventTypes.TOKEN, "b"))
    app._drain_one()
    assert app._current == "a"
    app._drain_one()
    assert app._current == "ab"


def test_assistant_done_flushes_message(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "完成"))
    app._handle(Event(EventTypes.ASSISTANT_DONE))
    assert app._current == ""
    assert app._messages[-1] == {"role": "assistant", "content": "完成"}
    assert app._busy is False
    assert app._reply_open is False


def test_tool_call_flushes_and_records(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "先调用工具"))
    app._handle(
        Event(
            EventTypes.TOOL_CALL,
            {"name": "ask", "arguments": '{"question": "x"}'},
        )
    )
    assert app._messages[-1]["role"] == "tool"
    assert "ask" in app._messages[-1]["content"]


def test_error_sets_flag(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.ERROR, "boom"))
    assert app._error == "boom"
    assert app._busy is False


def test_history_params_excludes_tool(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
        {"role": "tool", "content": "调用 ask"},
    ]
    history = app._history_params()
    assert [m["role"] for m in history] == ["user", "assistant"]


def test_single_shot_exits_after_done(monkeypatch):
    monkeypatch.setattr(Config, "RICH_COLOR_SYSTEM", "auto")
    bus = EventBus()
    app = ChatApp(Config, bus, None, "system", initial_message="hi")
    assert app._single_shot is True
    app._handle(Event(EventTypes.TOKEN, "OK"))
    app._handle(Event(EventTypes.ASSISTANT_DONE))
    assert app._shutdown is True


def test_single_shot_exits_after_error(monkeypatch):
    monkeypatch.setattr(Config, "RICH_COLOR_SYSTEM", "auto")
    bus = EventBus()
    app = ChatApp(Config, bus, None, "system", initial_message="hi")
    app._handle(Event(EventTypes.ERROR, "boom"))
    assert app._shutdown is True


def test_interactive_keeps_running_after_done(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.ASSISTANT_DONE))
    assert app._shutdown is False


# ---------- 斜杠命令 ----------


def _run_command_capture(app, command: str) -> str:
    from rich.console import Console

    original = app._console
    app._console = Console(force_terminal=False, width=100)
    try:
        with app._console.capture() as capture:
            app._run_command(command)
        return capture.get()
    finally:
        app._console = original


def test_command_help_lists_commands(monkeypatch):
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/help")
    assert "/session" in output
    assert "/clear" in output
    assert "/exit" in output


def test_command_session_empty(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/session")
    assert "暂无已保存的会话" in output


def test_command_session_lists_and_resumes(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)

    app._messages = [
        {"role": "user", "content": "设计一个模块"},
        {"role": "assistant", "content": "好的，方案如下"},
    ]
    app._save_session()

    output = _run_command_capture(app, "/session")
    assert "设计一个模块" in output

    app._messages = []
    assert app._session is not None
    app._run_command("/session " + app._session.session_id)
    assert len(app._messages) == 2
    assert app._messages[0]["content"] == "设计一个模块"


def test_command_session_resume_unknown(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/session nope")
    assert "未找到会话" in output


def test_command_session_saved_after_reply(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "OK"))
    app._handle(Event(EventTypes.ASSISTANT_DONE))
    sessions = app._session_store.list()
    assert len(sessions) == 1
    assert sessions[0].message_count == 1


def test_command_clear_empties_history(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._messages = [
        {"role": "user", "content": "hi"},
        {"role": "assistant", "content": "hello"},
    ]
    app._run_command("/clear")
    assert app._messages == []


def test_command_exit_shuts_down(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._run_command("/exit")
    assert app._shutdown is True


def test_command_unknown_shows_hint(monkeypatch):
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/nope")
    assert "未知命令" in output


# ---------- 供应商 / 模型 / 思考强度 ----------


def _clear_provider_env(monkeypatch) -> None:
    import os

    for key in list(os.environ):
        if key.startswith("PROVIDER_"):
            monkeypatch.delenv(key, raising=False)


def _set_provider_env(monkeypatch, name, api_key, api_url, models, default):
    up = name.upper()
    monkeypatch.setenv(f"PROVIDER_{up}_API_KEY", api_key)
    monkeypatch.setenv(f"PROVIDER_{up}_API_URL", api_url)
    monkeypatch.setenv(f"PROVIDER_{up}_MODELS", models)
    monkeypatch.setenv(f"PROVIDER_{up}_DEFAULT_MODEL", default)


def test_command_variants_lists_and_switches(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ACTIVE_VARIANT", "default")
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/variants")
    assert "fast" in output
    assert "deep" in output
    app._run_command("/variants deep")
    assert Config.ACTIVE_VARIANT == "deep"
    app._run_command("/variants nope")
    assert Config.ACTIVE_VARIANT == "deep"


def test_command_connect_switches_provider(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    _set_provider_env(
        monkeypatch, "deepseek", "k1", "https://api.deepseek.com", "chat,reasoner", "chat"
    )
    _set_provider_env(monkeypatch, "openai", "k2", "https://api.openai.com/v1", "gpt-4o", "gpt-4o")
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/connect")
    assert "deepseek" in output
    assert "openai" in output
    app._run_command("/connect openai")
    assert Config.ACTIVE_PROVIDER == "openai"
    assert Config.CUSTOM_API_KEY == "k2"
    app._run_command("/connect nope")
    assert Config.ACTIVE_PROVIDER == "openai"


def test_command_models_switches_model(monkeypatch, tmp_path):
    _clear_provider_env(monkeypatch)
    _set_provider_env(
        monkeypatch, "deepseek", "k1", "https://api.deepseek.com", "chat,reasoner", "chat"
    )
    monkeypatch.setattr(Config, "ACTIVE_PROVIDER", "deepseek")
    monkeypatch.setattr(Config, "ACTIVE_MODEL", "chat")
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    output = _run_command_capture(app, "/models")
    assert "chat" in output
    assert "reasoner" in output
    app._run_command("/models reasoner")
    assert Config.ACTIVE_MODEL == "reasoner"
    app._run_command("/models nope")
    assert Config.ACTIVE_MODEL == "reasoner"


def test_command_compact_truncates(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    app._messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
    app._run_command("/compact")
    assert len(app._messages) == 21
    assert app._messages[0]["content"].startswith("（上下文已压缩）")
    assert app._messages[1]["content"] == "msg10"


def test_command_compact_keeps_custom_count(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")
    app, bus = _make_app(monkeypatch)
    app._messages = [{"role": "user", "content": f"msg{i}"} for i in range(30)]
    app._run_command("/compact 5")
    assert len(app._messages) == 6
    assert app._messages[1]["content"] == "msg25"


def test_command_compact_noop_when_small(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._messages = [
        {"role": "user", "content": "a"},
        {"role": "assistant", "content": "b"},
    ]
    app._run_command("/compact")
    assert len(app._messages) == 2


def test_command_init_creates_agents_md(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "ROOT_DIR", tmp_path)
    app, bus = _make_app(monkeypatch)
    app._run_command("/init")
    target = tmp_path / "AGENTS.md"
    assert target.is_file()
    assert "AGENTS.md" in target.read_text(encoding="utf-8")
    original = target.read_text(encoding="utf-8")
    app._run_command("/init")
    assert target.read_text(encoding="utf-8") == original
    target.write_text("custom content", encoding="utf-8")
    app._run_command("/init -f")
    assert "custom content" not in target.read_text(encoding="utf-8")


def test_command_usage_shows_stats(monkeypatch, tmp_path):
    monkeypatch.setattr(Config, "SESSION_DIR", tmp_path / "sessions")

    class _UsageClient:
        def usage_summary(self):
            return {
                "input_tokens": 1000,
                "output_tokens": 500,
                "calls": 3,
                "last_input_tokens": 800,
                "last_total_tokens": 830,
            }

    app, bus = _make_app(monkeypatch)
    app._client = _UsageClient()  # type: ignore[assignment]
    output = _run_command_capture(app, "/usage")
    assert "上下文窗口" in output
    assert "输入" in output
    assert "1,000" in output
    assert "3 次请求" in output


# ---------- 流式回复 Markdown 渲染 ----------


class _FakeLive:
    instances: list["_FakeLive"] = []

    def __init__(self, *args, **kwargs):
        self.kwargs = kwargs
        self.started = False
        self.stopped = False
        self.updated = []
        _FakeLive.instances.append(self)

    def start(self):
        self.started = True

    def update(self, renderable):
        self.updated.append(renderable)

    def stop(self):
        self.stopped = True


def _terminal_app(monkeypatch) -> tuple[ChatApp, EventBus]:
    from rich.console import Console

    app, bus = _make_app(monkeypatch)
    app._console = Console(force_terminal=True, width=100)
    _FakeLive.instances.clear()
    monkeypatch.setattr("src.ui.app.Live", _FakeLive)
    return app, bus


def test_markdown_live_rendering_on_terminal(monkeypatch):
    from rich.console import Group
    from rich.markdown import Markdown
    from rich.text import Text

    app, bus = _terminal_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "# 标题"))
    app._handle(Event(EventTypes.TOKEN, "\n\n- 第一项"))

    assert app._live is not None
    assert app._live.started is True  # type: ignore[attr-defined]
    assert len(app._live.updated) >= 1  # type: ignore[attr-defined]
    renderable = app._live.updated[-1]  # type: ignore[attr-defined]
    live = app._live
    assert isinstance(renderable, Group)
    prefix, markdown = renderable.renderables
    assert isinstance(prefix, Text)
    assert ">>>" in prefix.plain
    assert isinstance(markdown, Markdown)

    app._handle(Event(EventTypes.ASSISTANT_DONE))
    assert app._live is None
    assert live.stopped is True  # type: ignore[attr-defined]
    assert app._reply_open is False
    assert app._messages[-1]["content"] == "# 标题\n\n- 第一项"


def test_plain_stream_without_terminal(monkeypatch):
    app, bus = _make_app(monkeypatch)
    _FakeLive.instances.clear()
    monkeypatch.setattr("src.ui.app.Live", _FakeLive)
    app._handle(Event(EventTypes.TOKEN, "hi"))
    app._handle(Event(EventTypes.ASSISTANT_DONE))
    assert app._live is None
    assert _FakeLive.instances == []
    assert app._messages[-1]["content"] == "hi"


def test_live_start_failure_falls_back(monkeypatch):
    from rich.console import Console

    def boom(*args, **kwargs):
        raise RuntimeError("no live")

    app, bus = _make_app(monkeypatch)
    app._console = Console(force_terminal=True, width=100)
    monkeypatch.setattr("src.ui.app.Live", boom)
    app._handle(Event(EventTypes.TOKEN, "hi"))
    assert app._live is None
    assert app._markdown_unavailable is True
    app._handle(Event(EventTypes.ASSISTANT_DONE))
    assert app._messages[-1]["content"] == "hi"


def test_tool_call_stops_live(monkeypatch):
    app, bus = _terminal_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "我先查一下"))
    app._handle(
        Event(
            EventTypes.TOOL_CALL,
            {"name": "web_search", "arguments": "{}"},
        )
    )
    assert app._live is None
    assert app._reply_open is False
    assert app._messages[-1]["role"] == "tool"


def test_render_reply_contains_current(monkeypatch):
    from rich.console import Group
    from rich.markdown import Markdown

    app, bus = _make_app(monkeypatch)
    app._current = "# hi\n\ncode"
    renderable = app._render_reply()
    assert isinstance(renderable, Group)
    assert isinstance(renderable.renderables[1], Markdown)


def test_error_stops_live(monkeypatch):
    app, bus = _terminal_app(monkeypatch)
    app._handle(Event(EventTypes.TOKEN, "正在处理"))
    app._handle(Event(EventTypes.ERROR, "boom"))
    assert app._live is None
    assert app._error == "boom"


# ---------- 交互输入 ----------


class _FakeClient:
    def __init__(self):
        self.calls = []

    def submit(self, message, system_prompt, history):
        self.calls.append((message, history))


def test_handle_input_text_submits_message(monkeypatch):
    app, bus = _make_app(monkeypatch)
    client = _FakeClient()
    app._client = client  # type: ignore[assignment]
    app._handle_input_text(" 你好 ", echo=False)
    assert len(client.calls) == 1
    message, history = client.calls[0]
    assert message == "你好"
    assert [m["role"] for m in history] == ["user"]


def test_handle_input_text_runs_command(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle_input_text("/clear", echo=False)
    assert app._messages == []


def test_handle_input_text_exit(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle_input_text("exit", echo=False)
    assert app._shutdown is True


def test_handle_input_text_q_exits(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle_input_text("q", echo=False)
    assert app._shutdown is True


def test_handle_input_text_blank_ignored(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle_input_text("   ", echo=False)
    assert app._busy is False
    assert app._shutdown is False


def test_read_input_falls_back_without_tty(monkeypatch):
    from rich.console import Console

    app, bus = _make_app(monkeypatch)
    original = app._console
    app._console = Console(force_terminal=False, width=100)
    monkeypatch.setattr("sys.stdin", io.TextIOWrapper(io.BytesIO(b"/clear\n")))
    monkeypatch.setattr("sys.stdin.isatty", lambda: False)
    try:
        app._read_input()
    finally:
        app._console = original
    assert app._messages == []


def test_read_input_calls_prompt_session(monkeypatch):
    from prompt_toolkit import PromptSession as _PromptSession
    from prompt_toolkit.input import DummyInput
    from prompt_toolkit.output import DummyOutput

    app, bus = _make_app(monkeypatch)

    class _FakePromptSession:
        def __new__(cls, *args, **kwargs):
            return _PromptSession(input=DummyInput(), output=DummyOutput(), **kwargs)

        def __class_getitem__(cls, item):
            return cls

    monkeypatch.setattr("src.ui.app.PromptSession", _FakePromptSession)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    app._read_input()
    assert app._input is not None
    assert app._shutdown is True


def test_ensure_input_falls_back_when_no_console(monkeypatch):
    app, bus = _make_app(monkeypatch)
    monkeypatch.setattr("src.ui.app.PromptSession", _raise_no_console)
    assert app._ensure_input() is None
    assert app._input_unavailable is True


def _raise_no_console(*args, **kwargs):
    raise RuntimeError("no console")


def test_prompt_message_has_mode_badge_and_gap(monkeypatch):
    from prompt_toolkit.formatted_text import to_plain_text

    app, bus = _make_app(monkeypatch)
    monkeypatch.setattr(Config, "ACTIVE_MODE", "plan")
    captured: dict[str, Any] = {}

    class _FakePromptSession:
        def __new__(cls, *args, **kwargs):
            return object.__new__(cls)

        def __class_getitem__(cls, item):
            return cls

        def prompt(self, **kwargs):
            captured["message"] = kwargs.get("message")
            captured["style"] = kwargs.get("style")
            raise EOFError

    monkeypatch.setattr("src.ui.app.PromptSession", _FakePromptSession)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    app._read_input()
    prompt_message: Any = captured["message"]
    assert callable(prompt_message)
    message = prompt_message()
    assert message[0] == ("", " ")  # type: ignore[index]
    assert message[1] == ("class:user-mode-plan", "[PLAN]")  # type: ignore[index]
    assert message[2] == ("", " ")  # type: ignore[index]
    assert message[3] == ("class:user-prompt", "> ")  # type: ignore[index]
    assert to_plain_text(message) == " [PLAN] > "  # type: ignore[arg-type]
    rules = dict(captured["style"].style_rules)  # type: ignore[attr-defined]
    assert "user-mode-plan" in rules
    assert "user-mode-auto" in rules


# ---------- 子 Agent 滚动回退 ----------


def test_subagent_scroll_accumulates_and_flushes(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._subagent_mode = "scroll"
    app._subagent_agent = "librarian"
    app._handle(Event(EventTypes.SUBAGENT_TOKEN, {"agent_id": "librarian", "text": "内容"}))
    app._handle(Event(EventTypes.SUBAGENT_DONE, {"agent_id": "librarian", "result": "结果"}))
    assert app._subagent_mode == "idle"
    assert app._subagent_agent is None
    assert app._subagent_buffer == ""
    assert app._messages[-1]["role"] == "tool"
    assert "内容" in app._messages[-1]["content"]


def test_subagent_start_in_non_terminal_uses_scroll(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._handle(Event(EventTypes.SUBAGENT_START, {"agent_id": "librarian", "task": "t"}))
    assert app._subagent_mode == "scroll"
    assert app._subagent_agent == "librarian"
    assert app._subagent_buffer == ""


def test_subagent_error_ends_scroll(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._subagent_mode = "scroll"
    app._handle(Event(EventTypes.SUBAGENT_ERROR, {"agent_id": "librarian", "error": "boom"}))
    assert app._subagent_mode == "idle"


def test_subagent_tool_events_render_in_scroll(monkeypatch):
    app, bus = _make_app(monkeypatch)
    app._subagent_mode = "scroll"
    app._handle(
        Event(
            EventTypes.SUBAGENT_TOOL_CALL,
            {"agent_id": "librarian", "name": "grep", "arguments": "{}"},
        )
    )
    app._handle(
        Event(
            EventTypes.SUBAGENT_TOOL_RESULT,
            {"agent_id": "librarian", "result": "match line"},
        )
    )
    assert app._subagent_mode == "scroll"
