from prompt_toolkit.application import create_app_session
from prompt_toolkit.input import DummyInput
from prompt_toolkit.output import DummyOutput

from src.config import Config
from src.core.event_bus import Event, EventBus, EventTypes
from src.ui.app import ChatApp
from src.ui.subagent_panel import SubAgentPanel


def _panel(agent_id="librarian") -> SubAgentPanel:
    return SubAgentPanel(Config, EventBus(), agent_id, "task")


def _app() -> ChatApp:
    return ChatApp(Config, EventBus(), None, "system")


def test_panel_accumulates_and_done():
    panel = _panel()
    panel.handle_event(
        Event(EventTypes.SUBAGENT_TOKEN, {"agent_id": "librarian", "text": "第一行"})
    )
    panel.handle_event(
        Event(
            EventTypes.SUBAGENT_TOOL_CALL,
            {"agent_id": "librarian", "name": "grep", "arguments": "{}"},
        )
    )
    panel.handle_event(Event(EventTypes.SUBAGENT_DONE, {"agent_id": "librarian", "result": "结果"}))
    assert panel.is_done() is True
    assert panel.result() == "结果"
    assert "第一行" in "\n".join(panel._lines)


def test_panel_error_sets_result():
    panel = _panel()
    panel.handle_event(Event(EventTypes.SUBAGENT_ERROR, {"agent_id": "librarian", "error": "boom"}))
    assert panel.is_done() is True
    assert panel.result() == "error: boom"


def test_panel_collapse_toggles():
    panel = _panel()
    panel.handle_event(Event(EventTypes.SUBAGENT_TOKEN, {"agent_id": "librarian", "text": "line1"}))
    joined = "".join(frag[1] for frag in panel._fragments())
    assert "line1" in joined
    panel.toggle_collapse()
    assert panel._collapsed is True
    collapsed = "".join(frag[1] for frag in panel._fragments())
    assert "line1" not in collapsed


def test_panel_title_has_click_handler():
    panel = _panel()
    title = panel._fragments()[0]
    assert len(title) == 3
    assert "librarian" in title[1]
    assert callable(title[2])


def test_panel_builds_application():
    panel = _panel()
    # Windows 无控制台的 pytest 环境下，Application 构造需要显式注入
    # 假输入输出，否则 prompt_toolkit 自动探测 Win32Output 会抛异常。
    with create_app_session(input=DummyInput(), output=DummyOutput()):
        app = panel._build_app()
        assert app is not None


def test_record_subagent_result_appends_when_truthy():
    app = _app()
    app._record_subagent_result("librarian", "结果")
    assert app._messages[-1]["role"] == "tool"
    assert "librarian" in app._messages[-1]["content"]
    assert "结果" in app._messages[-1]["content"]


def test_record_subagent_result_skips_when_empty():
    app = _app()
    app._record_subagent_result("librarian", "")
    assert app._messages == []


def test_panel_truncates_tool_result():
    panel = _panel()
    panel.handle_event(
        Event(
            EventTypes.SUBAGENT_TOOL_RESULT,
            {"agent_id": "librarian", "result": "x" * 500},
        )
    )
    joined = "\n".join(panel._lines)
    assert len(joined) < 500
    assert "…" in joined
