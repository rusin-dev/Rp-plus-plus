from prompt_toolkit.completion import CompleteEvent
from prompt_toolkit.document import Document

from src.config import Config
from src.ui.input import (
    COMMAND_DESCRIPTIONS,
    COMMAND_TEXT_STYLE,
    SlashCommandCompleter,
    SlashCommandLexer,
)

_EVENT = CompleteEvent(completion_requested=False)


def _completions(text: str, cursor_position: int | None = None):
    pos = cursor_position if cursor_position is not None else len(text)
    return list(
        SlashCommandCompleter().get_completions(Document(text=text, cursor_position=pos), _EVENT)
    )


def test_completer_offers_all_commands_on_bare_slash():
    texts = {c.text for c in _completions("/")}
    assert texts == {f"/{name}" for name in COMMAND_DESCRIPTIONS}


def test_completer_prefix_filters():
    assert [c.text for c in _completions("/se")] == ["/session"]


def test_completer_ignores_non_command_input():
    assert _completions("hello world") == []


def test_completer_hides_after_argument_space():
    assert _completions("/session abc") == []


def test_completer_hides_for_empty_input():
    assert _completions("") == []


def test_completion_replaces_whole_token():
    for c in _completions("/se"):
        assert c.start_position == -3
    assert [c.start_position for c in _completions("/")] == [-1] * len(COMMAND_DESCRIPTIONS)


def test_completion_has_meta_description():
    from prompt_toolkit.formatted_text import to_plain_text

    by_text = {c.text: c for c in _completions("/")}
    assert to_plain_text(by_text["/help"].display_meta) == COMMAND_DESCRIPTIONS["help"]


def test_slash_lexer_highlights_command_line():
    lexer = SlashCommandLexer()
    styled = lexer.lex_document(Document("/help"))(0)
    assert styled == [(f"class:{COMMAND_TEXT_STYLE}", "/help")]


def test_slash_lexer_keeps_command_with_args_highlighted():
    lexer = SlashCommandLexer()
    styled = lexer.lex_document(Document("/session abc"))(0)
    assert styled == [(f"class:{COMMAND_TEXT_STYLE}", "/session abc")]


def test_slash_lexer_ignores_normal_message():
    lexer = SlashCommandLexer()
    assert lexer.lex_document(Document("hello world"))(0) == [("", "hello world")]


def test_mode_label_and_style():
    from src.ui.input import MODE_STYLES, build_input_style, mode_label

    assert mode_label("plan") == "[PLAN]"
    assert mode_label("build") == "[BUILD]"
    assert mode_label("auto") == "[AUTO]"
    assert mode_label("nope") == "NOPE"
    style = build_input_style()
    rules = dict(style.style_rules)
    assert rules["user-mode-plan"] == MODE_STYLES["plan"]
    assert rules["user-mode-build"] == MODE_STYLES["build"]
    assert rules["user-mode-auto"] == MODE_STYLES["auto"]


def test_shift_tab_cycles_mode(monkeypatch):
    from types import SimpleNamespace

    from src.ui.input import build_key_bindings

    monkeypatch.setattr(Config, "ACTIVE_MODE", "auto")
    kb = build_key_bindings()
    s_tab = next(b for b in kb.bindings if "s-tab" in b.keys)
    event = SimpleNamespace(app=SimpleNamespace(invalidate=lambda: None))
    s_tab.handler(event)  # type: ignore[arg-type]
    assert Config.ACTIVE_MODE == "plan"
    s_tab.handler(event)  # type: ignore[arg-type]
    assert Config.ACTIVE_MODE == "build"
    s_tab.handler(event)  # type: ignore[arg-type]
    assert Config.ACTIVE_MODE == "auto"


def test_ctrl_c_binding_arms_then_exits():
    from types import SimpleNamespace

    from src.ui.input import build_key_bindings

    state = {"armed": False}

    def on_interrupt() -> bool:
        if state["armed"]:
            return True
        state["armed"] = True
        return False

    kb = build_key_bindings(on_interrupt=on_interrupt)
    ctrl_c = next(b for b in kb.bindings if "c-c" in b.keys)

    class _FakeApp:
        def __init__(self) -> None:
            self.exited: list[object] = []

        def invalidate(self) -> None:
            pass

        def exit(self, exception=None) -> None:  # type: ignore[no-untyped-def]
            self.exited.append(exception)

    app = _FakeApp()
    event = SimpleNamespace(app=app)

    ctrl_c.handler(event)  # type: ignore[arg-type]
    assert state["armed"] is True
    assert app.exited == []

    ctrl_c.handler(event)  # type: ignore[arg-type]
    assert len(app.exited) == 1
    assert isinstance(app.exited[0], KeyboardInterrupt)


def test_ctrl_c_binding_without_callback_does_not_exit():
    from types import SimpleNamespace

    from src.ui.input import build_key_bindings

    kb = build_key_bindings()
    ctrl_c = next(b for b in kb.bindings if "c-c" in b.keys)
    event = SimpleNamespace(app=SimpleNamespace(invalidate=lambda: None, exit=lambda exception=None: None))
    ctrl_c.handler(event)  # type: ignore[arg-type]
