from __future__ import annotations

from collections.abc import Callable, Iterable

from prompt_toolkit.completion import CompleteEvent, Completer, Completion
from prompt_toolkit.document import Document
from prompt_toolkit.filters import Condition, has_completions
from prompt_toolkit.formatted_text.base import StyleAndTextTuples
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.key_binding.key_processor import KeyPressEvent
from prompt_toolkit.lexers import Lexer
from prompt_toolkit.shortcuts import CompleteStyle
from prompt_toolkit.styles import Style

from ..config import Config

COMMAND_DESCRIPTIONS: dict[str, str] = {
    "help": "显示所有可用命令",
    "variants": "查看/切换思考强度（low/medium/high/max）",
    "models": "查看/切换当前供应商的模型",
    "connect": "查看/切换 API 供应商",
    "mode": "查看/切换工作模式（plan/build/auto）",
    "compact": "压缩对话上下文",
    "usage": "查看 token 用量与上下文窗口",
    "init": "在工作区根目录创建 AGENTS.md",
    "checkpoints": "查看提交检查点；选择后回滚",
    "rollback": "回滚到指定提交：/rollback <hash>（git reset --hard）",
    "session": "列出已保存会话；/session <id> 恢复指定会话",
    "clear": "清空对话历史",
    "exit": "退出程序",
    "quit": "退出程序",
}

INPUT_STYLE = Style.from_dict(
    {
        "user-prompt": "bold cyan",
        "tool-prompt": "yellow",
        "input": "cyan",
        "command-text": "bold blue",
        "status-left": "bold",
        "status-right": "dim",
        "cmd-title": "bold cyan",
        "cmd-rule": "dim",
        "cmd-hint": "italic dim",
        "picker-selected": "bold black bg:ansibrightcyan",
        "picker-hint": "italic dim",
    }
)

MODE_LABELS: dict[str, str] = {
    "plan": "[PLAN]",
    "build": "[BUILD]",
    "auto": "[AUTO]",
}

MODE_STYLES: dict[str, str] = {
    "plan": "bold white bg:ansibrightblue",
    "build": "bold black bg:ansibrightgreen",
    "auto": "bold white bg:ansibrightmagenta",
}

MODE_RICH_STYLES: dict[str, str] = {
    "plan": "bold white on bright_blue",
    "build": "bold black on bright_green",
    "auto": "bold white on bright_magenta",
}


def mode_label(mode: str) -> str:
    return MODE_LABELS.get(mode, mode.upper())


def build_input_style() -> Style:
    """返回带全部模式徽标背景色的输入框样式（模式可运行时切换）。"""
    rules = dict(INPUT_STYLE.style_rules)
    for mode, style in MODE_STYLES.items():
        rules[f"user-mode-{mode}"] = style
    return Style.from_dict(rules)


class PickerHandle:
    """底部交互菜单（选择器）控制器，由 ChatApp 提供，供键位绑定回调。"""

    def __init__(
        self,
        active: Callable[[], bool],
        move: Callable[[int], None],
        confirm: Callable[[], None],
        cancel: Callable[[], None] | None = None,
    ) -> None:
        self.active = active
        self.move = move
        self.confirm = confirm
        self.cancel = cancel if cancel is not None else (lambda: None)


def build_key_bindings(
    on_interrupt: Callable[[], bool] | None = None,
    picker: PickerHandle | None = None,
    dismiss_panel: Callable[[], None] | None = None,
) -> KeyBindings:
    """构建输入键位绑定。

    - Shift+Tab 循环切换工作模式（plan→build→auto）
    - Ctrl-C：回调返回 True 时退出输入，否则仅刷新界面（用于“再按一次退出”）
    - 选择器激活时：↑/↓ 移动高亮，Enter 确认选择，Esc 关闭选择器
    - 提供 dismiss_panel 时：未在选择器中按 Esc 调用该回调（用于关闭命令展示）
    """

    kb = KeyBindings()

    @kb.add("s-tab", filter=~has_completions)
    def _cycle_mode(_event: KeyPressEvent) -> None:
        names = list(Config.MODES)
        current = Config.ACTIVE_MODE
        index = names.index(current) if current in names else -1
        Config.ACTIVE_MODE = names[(index + 1) % len(names)]

    @kb.add("c-c")
    def _interrupt(event: KeyPressEvent) -> None:
        if on_interrupt is not None and on_interrupt():
            event.app.exit(exception=KeyboardInterrupt())
        event.app.invalidate()

    if picker is not None:
        active = Condition(picker.active)

        @kb.add("up", filter=active & ~has_completions)
        def _picker_up(event: KeyPressEvent) -> None:
            picker.move(-1)
            event.app.invalidate()

        @kb.add("down", filter=active & ~has_completions)
        def _picker_down(event: KeyPressEvent) -> None:
            picker.move(1)
            event.app.invalidate()

        @kb.add("enter", filter=active)
        def _picker_enter(event: KeyPressEvent) -> None:
            picker.confirm()
            event.app.exit(result="")

        @kb.add("escape", filter=active)
        def _picker_cancel(event: KeyPressEvent) -> None:
            picker.cancel()
            event.app.invalidate()

    if dismiss_panel is not None:
        dismiss_active = Condition(lambda: picker is None or not picker.active())

        @kb.add("escape", filter=dismiss_active)
        def _dismiss(event: KeyPressEvent) -> None:
            dismiss_panel()
            event.app.invalidate()

    return kb


COMMAND_TEXT_STYLE = "command-text"


class SlashCommandLexer(Lexer):
    """输入以 `/` 开头时，将整行命令高亮为蓝色。"""

    def lex_document(self, document: Document):
        def get_line(lineno: int) -> StyleAndTextTuples:
            text = document.lines[lineno]
            if text.startswith("/"):
                return [(f"class:{COMMAND_TEXT_STYLE}", text)]
            return [("", text)]

        return get_line


class SlashCommandCompleter(Completer):
    """输入以 `/` 开头时，弹出命令候选框供选择。"""

    def get_completions(
        self, document: Document, complete_event: CompleteEvent
    ) -> Iterable[Completion]:
        text = document.text_before_cursor
        if not text.startswith("/"):
            return
        keyword = text[1:]
        if " " in keyword:
            return
        for name, desc in COMMAND_DESCRIPTIONS.items():
            if name.startswith(keyword):
                yield Completion(
                    text=f"/{name}",
                    start_position=-len(text),
                    display=name,
                    display_meta=desc,
                )


COMMAND_COMPLETE_STYLE = CompleteStyle.COLUMN
