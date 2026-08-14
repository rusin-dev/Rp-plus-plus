from __future__ import annotations

import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Literal

from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionUserMessageParam,
)
from prompt_toolkit import PromptSession
from prompt_toolkit.formatted_text import AnyFormattedText, StyleAndTextTuples
from rich.console import Console, Group, RenderableType
from rich.live import Live
from rich.markdown import Markdown
from rich.text import Text

from ..api.client import ChatClient
from ..config import Config
from ..core.event_bus import Event, EventBus, EventTypes
from ..core.git_ops import CheckpointStore, commit_changes, ensure_repo, rollback_to
from ..core.logger import get_logger
from ..core.session import Session, SessionStore
from .cancel_watcher import EscCancelWatcher
from .formatters import extract_read_path, format_grep_status, format_tool_call
from .input import (
    COMMAND_COMPLETE_STYLE,
    COMMAND_DESCRIPTIONS,
    MODE_RICH_STYLES,
    PickerHandle,
    SlashCommandCompleter,
    SlashCommandLexer,
    build_input_style,
    build_key_bindings,
    mode_label,
)

logger = get_logger(__name__)

_ColorSystemOption = Literal["auto", "standard", "256", "truecolor", "windows"]

_USER_STYLE = "cyan bold"
_REPLY_STYLE = "green bold"
_TOOL_STYLE = "yellow"
_DIM_STYLE = "dim"
_ERROR_STYLE = "red bold"
_OK_STYLE = "green"

_TOOL_RESULT_SUMMARY_LEN = 200
_DEFAULT_COMPACT_KEEP = 20
_LIVE_REFRESH_INTERVAL = 0.05
_WRITE_PREVIEW_LINES = 200


class ChatApp:
    """类 Claude Code 的日志式对话界面：内容直接滚动输出，不做全屏刷新。

    - `> 用户消息`：输入与用户消息
    - `>>> 回复`：AI 流式回复（打字机效果，终端下用 rich 实时渲染 Markdown）
    - `⎿ 工具调用 / → 结果`：工具执行记录
    - 退出后所有内容保留在终端中
    """

    def __init__(
        self,
        config: type[Config],
        bus: EventBus,
        client: ChatClient | None,
        system_prompt: str,
        initial_message: str | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._client = client
        self._system_prompt = system_prompt
        self._single_shot = initial_message is not None
        self._console = Console(color_system=_resolve_color_system(config.RICH_COLOR_SYSTEM))

        self._messages: list[dict] = []
        self._current = ""
        self._busy = False
        self._shutdown = False
        self._error: str | None = None
        self._reply_open = False
        self._initial_message = initial_message
        self._session_store = SessionStore(config)
        self._session: Session | None = None
        self._input: PromptSession[str] | None = None
        self._input_unavailable = False
        self._live: Live | None = None
        self._markdown_unavailable = False
        self._live_last_time = 0.0
        self._live_last_len = 0
        self._subagent_mode = "idle"
        self._subagent_agent: str | None = None
        self._subagent_buffer = ""
        self._cancel_watcher: EscCancelWatcher | None = None
        self._interrupted = False
        self._pending_read_path: str | None = None
        self._pending_subagent_read: str | None = None
        self._last_tool_name: str | None = None
        self._last_subagent_tool: str | None = None
        self._exit_armed = False
        self._command_display: tuple[str, list[tuple[str, str]]] | None = None
        self._picker_mode: str | None = None
        self._picker_title = ""
        self._picker_items: list[tuple[str, str]] = []
        self._picker_index = 0
        self._picker_pending: str | None = None
        self._awaiting_api_key: str | None = None
        self._git_ready = False
        self._checkpoint_store = CheckpointStore(config.ROOT_DIR)
        self._picker_kind: str | None = None
        self._awaiting_rollback: str | None = None

    # ---------- 主循环 ----------

    def _show_mascot(self) -> None:
        """启动时打印欢迎面板与分隔线（类 Claude Code 的欢迎框）。"""
        from .mascot import print_mascot

        print_mascot(
            self._console,
            model=self._config.active_model(),
            provider=self._config.ACTIVE_PROVIDER or "",
            cwd=str(self._config.ROOT_DIR),
        )
        self._console.rule(style="dim")

    def run(self) -> int:
        try:
            if self._console.is_terminal:
                self._show_mascot()
            self._auto_git_start()
            if self._initial_message:
                self._submit(self._initial_message)
                self._initial_message = None

            while not self._shutdown:
                self._drain_one()
                if not self._busy and not self._shutdown:
                    self._read_input()
        except KeyboardInterrupt:
            self._shutdown = True
        finally:
            self._stop_cancel_watcher()
            self._save_session()
            self._console.print()
        return 0

    # ---------- 事件消费 ----------

    def _drain_one(self) -> None:
        """每次只消费一个事件，实现逐 token 流式输出。"""
        event = self._bus.get(timeout=0.05)
        if event is not None:
            self._handle(event)

    def _flush_current(self) -> None:
        if self._current:
            self._messages.append({"role": "assistant", "content": self._current})
            self._current = ""

    # ---------- 流式回复展示（Markdown 实时渲染） ----------

    def _begin_reply_stream(self) -> None:
        """开始一条回复：终端下启用 rich Live + Markdown 实时渲染，否则回退到普通打字机输出。"""
        if self._console.is_terminal and not self._markdown_unavailable:
            try:
                self._live = Live(
                    console=self._console,
                    refresh_per_second=30.0,
                    vertical_overflow="visible",
                )
                self._live.start()
                self._live_last_time = time.monotonic()
                self._live_last_len = 0
                return
            except Exception:
                self._live = None
                self._markdown_unavailable = True
                logger.debug("无法启动 Markdown 实时渲染，回退到普通流式输出", exc_info=True)
        self._console.print(">>> ", style=_REPLY_STYLE, end="")

    def _extend_live_reply(self) -> None:
        """增量刷新 Markdown 渲染：出现换行或超过刷新间隔时更新。"""
        live = self._live
        if live is None:
            return
        now = time.monotonic()
        chunk = self._current[self._live_last_len :]
        if "\n" in chunk or now - self._live_last_time >= _LIVE_REFRESH_INTERVAL:
            try:
                live.update(self._render_reply())
            except Exception:
                logger.debug("Live.update 失败，回退到普通流式输出", exc_info=True)
                self._markdown_unavailable = True
                self._stop_live()
                sys.stdout.write(self._current)
                sys.stdout.flush()
                return
            self._live_last_time = now
        self._live_last_len = len(self._current)

    def _render_reply(self) -> RenderableType:
        return Group(
            Text(">>> ", style=_REPLY_STYLE),
            Markdown(self._current),
        )

    def _close_reply_stream(self) -> None:
        """结束当前流式回复：停止 Live 并复位前缀状态。"""
        had_live = self._live is not None
        self._stop_live()
        if had_live:
            self._reply_open = False

    def _stop_live(self) -> None:
        if self._live is not None:
            try:
                self._live.update(self._render_reply())
            except Exception:
                logger.debug("Live.update 失败", exc_info=True)
            try:
                self._live.stop()
            except Exception:
                logger.debug("Live.stop 失败", exc_info=True)
            self._live = None

    def _handle(self, event: Event) -> None:
        event_type = event.type
        if event_type == EventTypes.TOKEN:
            self._current += event.data
            if not self._reply_open:
                self._reply_open = True
                self._begin_reply_stream()
            if self._live is not None:
                self._extend_live_reply()
            else:
                sys.stdout.write(event.data)
                sys.stdout.flush()
        elif event_type == EventTypes.TOOL_CALL:
            self._close_reply_stream()
            self._flush_current()
            name = event.data["name"]
            args = event.data.get("arguments", "")
            self._messages.append({"role": "tool", "content": f"调用工具 {name}({args})"})
            self._last_tool_name = name
            self._pending_read_path = extract_read_path(args) if name == "read" else None
            self._print_tool_line(format_tool_call(name, args))
        elif event_type == EventTypes.FILE_WRITTEN:
            self._print_file_written(event.data or {})
        elif event_type == EventTypes.FILE_DIFF:
            self._print_file_diff(event.data or {})
        elif event_type == EventTypes.CANCEL:
            self._handle_cancel()
        elif event_type == EventTypes.TOOL_RESULT:
            last_tool = self._last_tool_name
            self._last_tool_name = None
            pending = self._pending_read_path
            self._pending_read_path = None
            raw = str(event.data)
            if pending is not None and not raw.startswith("error:"):
                self._console.print(f"  → 已读取 {pending}", style=_DIM_STYLE)
            elif last_tool == "grep":
                status = format_grep_status(raw)
                style = _ERROR_STYLE if raw.startswith("error:") else _DIM_STYLE
                self._console.print(f"  → {status}", style=style)
            else:
                summary = " ".join(raw.split())[:_TOOL_RESULT_SUMMARY_LEN]
                suffix = "…" if len(summary) >= _TOOL_RESULT_SUMMARY_LEN else ""
                self._console.print(f"  → {summary}{suffix}", style=_DIM_STYLE)
        elif event_type == EventTypes.ASSISTANT_DONE:
            self._close_reply_stream()
            self._flush_current()
            self._busy = False
            self._reply_open = False
            self._console.print()
            if self._interrupted:
                self._interrupted = False
                self._console.print("⏹ 回答已中断", style=_DIM_STYLE)
            self._stop_cancel_watcher()
            self._save_session()
            self._auto_commit()
            if self._single_shot:
                self._shutdown = True
        elif event_type == EventTypes.ERROR:
            self._close_reply_stream()
            self._flush_current()
            self._busy = False
            self._reply_open = False
            self._interrupted = False
            self._stop_cancel_watcher()
            self._error = event.data
            self._console.print(f"✖ {event.data}", style=_ERROR_STYLE)
            self._save_session()
            self._auto_commit()
            if self._single_shot:
                self._shutdown = True
        elif event_type == EventTypes.ASK_QUESTION:
            self._close_reply_stream()
            self._flush_current()
            self._ask(event.data)
        elif event_type == EventTypes.SUBAGENT_START:
            self._begin_subagent(event.data or {})
        elif event_type == EventTypes.SUBAGENT_TOKEN:
            self._handle_subagent_token(event.data or {})
        elif event_type == EventTypes.SUBAGENT_TOOL_CALL:
            self._handle_subagent_tool(event.data or {})
        elif event_type == EventTypes.SUBAGENT_TOOL_RESULT:
            self._handle_subagent_tool_result(event.data or {})
        elif event_type == EventTypes.SUBAGENT_DONE:
            self._finish_subagent(event.data or {})
        elif event_type == EventTypes.SUBAGENT_ERROR:
            self._fail_subagent(event.data or {})
        elif event_type == EventTypes.SHUTDOWN:
            self._shutdown = True

    def _print_tool_line(self, text: str, indent: int = 2) -> None:
        """渲染 `⎿` 状态行（类 Claude Code：缩进 + 亮图标 + 灰文本）。"""
        line = Text()
        line.append(" " * indent + "⎿ ", style="bold")
        line.append(text, style=_DIM_STYLE)
        self._console.print(line)

    def _print_file_written(self, data: dict) -> None:
        """把 write 工具写入的内容以 Markdown 代码框展示（超长时截断预览）。"""
        content = str(data.get("content", ""))
        if not content:
            return
        lines = content.splitlines()
        truncated = len(lines) > _WRITE_PREVIEW_LINES
        preview = "\n".join(lines[:_WRITE_PREVIEW_LINES])
        if truncated:
            preview += f"\n…（共 {len(lines)} 行，仅预览前 {_WRITE_PREVIEW_LINES} 行）"
        self._console.print(Markdown("```text\n" + preview + "\n```"))

    def _print_file_diff(self, data: dict) -> None:
        """把 edit 工具的修改以 git 风格 diff 的 Markdown 代码框展示。"""
        diff = str(data.get("diff", ""))
        if not diff:
            return
        self._console.print(Markdown("```diff\n" + diff + "\n```"))

    # ---------- 交互 ----------

    def _read_input(self) -> None:
        if not sys.stdin.isatty():
            self._read_input_plain()
            return
        session = self._ensure_input()
        if session is None:
            self._read_input_plain()
            return
        try:
            text = session.prompt(
                message=self._prompt_message,
                style=build_input_style(),
                bottom_toolbar=self._bottom_toolbar,
            )
        except (EOFError, KeyboardInterrupt):
            self._shutdown = True
            return
        self._exit_armed = False
        if self._awaiting_api_key is not None:
            self._finish_connect_api_key(self._awaiting_api_key, text.strip())
            return
        if self._consume_picker_pending():
            return
        self._handle_input_text(text, echo=False)

    def _prompt_message(self) -> AnyFormattedText:
        """构建提示符消息（可调用，模式切换后徽标实时更新；等待 API Key 时改为输入提示）。"""
        if self._awaiting_api_key is not None:
            return [
                ("class:tool-prompt", f"API Key for {self._awaiting_api_key}: "),
            ]
        mode = self._config.ACTIVE_MODE
        return [
            ("", " "),
            (f"class:user-mode-{mode}", mode_label(mode)),
            ("", " "),
            ("class:user-prompt", "❯ "),
        ]

    def _bottom_toolbar(self) -> AnyFormattedText:
        """底部状态栏（类 Claude Code）：选择器/命令显示内容 + 左侧模式提示 + 右侧模型。"""
        fragments: StyleAndTextTuples = []
        if self._picker_mode is not None:
            fragments.extend(self._picker_fragments())
        else:
            display = self._command_display
            if display is not None:
                title, lines = display
                width = self._console.width or 80
                head = f"── {title} "
                fragments.append(("class:cmd-title", head))
                fragments.append(("class:cmd-rule", "─" * max(width - len(head), 0)))
                fragments.append(("", "\n"))
                for style, text in lines:
                    fragments.append((style, text))
                    fragments.append(("", "\n"))
                fragments.append(("class:cmd-rule", "─" * width))
                fragments.append(("", "\n"))
        if self._exit_armed:
            left = "Press Ctrl-C again to exit"
        else:
            left = f"⏸ {self._config.ACTIVE_MODE} mode on · /help 查看快捷键"
        provider = self._config.ACTIVE_PROVIDER
        if provider:
            right = f"{self._config.active_model()} · {provider}"
        else:
            right = "未配置 · 运行 /connect"
        fragments.append(("class:status-left", left))
        fragments.append(("", "    "))
        fragments.append(("class:status-right", right))
        return fragments

    def _picker_fragments(self) -> StyleAndTextTuples:
        """渲染底部交互选择器：标题 + 可高亮条目列表 + 操作提示。"""
        fragments: StyleAndTextTuples = []
        width = self._console.width or 80
        head = f"── {self._picker_title} "
        fragments.append(("class:cmd-title", head))
        fragments.append(("class:cmd-rule", "─" * max(width - len(head), 0)))
        fragments.append(("", "\n"))
        for index, (_name, label) in enumerate(self._picker_items):
            marker = "▸" if index == self._picker_index else " "
            text = f"  {marker} {label}"
            if index == self._picker_index:
                fragments.append(("class:picker-selected", text.ljust(width)))
            else:
                fragments.append(("", text))
            fragments.append(("", "\n"))
        fragments.append(("class:picker-hint", "  ↑ ↓ 切换 · Enter 确认 · Esc 取消"))
        fragments.append(("", "\n"))
        fragments.append(("class:cmd-rule", "─" * width))
        fragments.append(("", "\n"))
        return fragments

    def _open_picker(
        self,
        title: str,
        items: list[tuple[str, str]],
        index: int = 0,
        kind: str = "connect",
    ) -> None:
        self._picker_mode = "menu"
        self._picker_title = title
        self._picker_items = items
        self._picker_index = index
        self._picker_kind = kind
        self._command_display = None

    def _picker_move(self, delta: int) -> None:
        if not self._picker_items:
            return
        self._picker_index = (self._picker_index + delta) % len(self._picker_items)

    def _picker_confirm(self) -> None:
        if not self._picker_items:
            return
        self._picker_pending = self._picker_items[self._picker_index][0]
        self._picker_mode = None
        self._picker_items = []

    def _consume_picker_pending(self) -> bool:
        """处理已确认的选择；返回 True 表示已消费（调用方应跳过输入文本处理）。"""
        pending = self._picker_pending
        self._picker_pending = None
        if pending is None:
            return False
        if self._picker_kind == "checkpoints":
            self._confirm_rollback(pending)
        else:
            self._connect_pick(pending)
        return True

    def _primary_interrupt(self) -> bool:
        """处理 Ctrl-C：第一次进入待退出状态，第二次返回 True 表示退出。"""
        if self._exit_armed:
            return True
        self._exit_armed = True
        return False

    def _ensure_input(self) -> PromptSession[str] | None:
        """惰性创建 prompt_toolkit 会话；无控制台时回退。"""
        if self._input_unavailable:
            return None
        if self._input is None:
            try:
                self._input = PromptSession[str](
                    completer=SlashCommandCompleter(),
                    complete_while_typing=True,
                    complete_style=COMMAND_COMPLETE_STYLE,
                    key_bindings=build_key_bindings(
                        on_interrupt=self._primary_interrupt,
                        picker=PickerHandle(
                            active=lambda: self._picker_mode is not None,
                            move=self._picker_move,
                            confirm=self._picker_confirm,
                        ),
                    ),
                    lexer=SlashCommandLexer(),
                )
            except Exception:
                self._input_unavailable = True
                logger.debug("prompt_toolkit 初始化失败，回退到普通输入", exc_info=True)
                return None
        return self._input

    def _read_input_plain(self) -> None:
        """stdin 非 TTY（如管道）时的回退输入。"""
        if self._awaiting_api_key is not None:
            self._console.print(
                f"API Key for {self._awaiting_api_key}: ", style=_TOOL_STYLE, end=""
            )
            try:
                raw = sys.stdin.readline()
            except (EOFError, KeyboardInterrupt):
                raw = ""
            if raw:
                self._finish_connect_api_key(self._awaiting_api_key, raw.strip())
            return
        self._print_mode_badge()
        self._console.print(" ", end="")
        self._console.print("❯ ", style=_USER_STYLE, end="")
        try:
            raw = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            self._shutdown = True
            return
        if not raw:
            self._shutdown = True
            return
        self._handle_input_text(raw, echo=True)

    def _print_mode_badge(self) -> None:
        """在提示符前输出当前工作模式徽标（不同模式不同背景色，仅标签高亮）。"""
        mode = self._config.ACTIVE_MODE
        style = MODE_RICH_STYLES.get(mode, "bold")
        self._console.print(mode_label(mode), style=style, end="")

    def _handle_input_text(self, raw: str, echo: bool) -> None:
        self._exit_armed = False
        text = raw.strip()
        if self._awaiting_rollback is not None:
            ref = self._awaiting_rollback
            self._awaiting_rollback = None
            if text.lower() in {"y", "yes"}:
                self._execute_rollback(ref)
            else:
                self._console.print("已取消回滚", style=_DIM_STYLE)
            return
        if not text:
            return
        if text.lower() in {"exit", "quit", "q"}:
            self._shutdown = True
            return
        if text.startswith("/"):
            self._run_command(text)
            return
        self._submit(text, echo=echo)

    # ---------- 斜杠命令 ----------

    def _run_command(self, raw: str) -> None:
        self._command_display = None
        parts = raw[1:].strip().split()
        name = parts[0].lower() if parts else ""
        arg = parts[1] if len(parts) > 1 else None
        if name in {"exit", "quit"}:
            self._shutdown = True
        elif name == "help":
            self._show_help()
        elif name == "session":
            self._session_command(arg)
        elif name == "clear":
            self._clear_history()
        elif name == "variants":
            self._variants_command(arg)
        elif name == "models":
            self._models_command(arg)
        elif name == "connect":
            self._connect_command(arg)
        elif name == "mode":
            self._mode_command(arg)
        elif name == "compact":
            keep = int(arg) if arg and arg.isdigit() else None
            self._compact_command(keep)
        elif name == "usage":
            self._usage_command()
        elif name == "init":
            self._init_command(force=arg == "-f")
        elif name == "checkpoints":
            self._checkpoints_command(arg)
        elif name == "rollback":
            self._rollback_command(arg)
        else:
            self._console.print(f"未知命令 /{name}，输入 /help 查看可用命令", style=_TOOL_STYLE)

    def _show_help(self) -> None:
        lines = [("", f"  /{name}  -  {desc}") for name, desc in COMMAND_DESCRIPTIONS.items()]
        self._command_display = ("可用命令", lines)

    # ---------- 供应商 / 模型 / 思考强度 ----------

    def _connect_command(self, name: str | None) -> None:
        if name:
            self._connect_pick(name)
            return
        self._show_connect_picker()

    def _show_connect_picker(self) -> None:
        """展示可连接的供应商：终端下用底部交互选择器，否则静态列表。"""
        providers = self._config.providers()
        presets = self._config.presets()
        if not providers and not presets:
            self._console.print(
                "没有可用的 provider 预设，请在 src/data/providers/preset 中添加",
                style=_ERROR_STYLE,
            )
            return
        items: list[tuple[str, str]] = []
        index = 0
        for name in sorted(providers) + sorted(presets):
            if any(name == existing for existing, _ in items):
                continue
            items.append((name, name))
            if name == self._config.ACTIVE_PROVIDER:
                index = len(items) - 1
        if not self._console.is_terminal:
            lines = [
                ("", f"  {'▸' if i == index else ' '} {label}")
                for i, (_, label) in enumerate(items)
            ]
            lines.append(("class:cmd-hint", "输入 /connect <名称> 使用预设或切换"))
            self._command_display = ("可用供应商", lines)
            return
        self._open_picker("可用供应商（↑↓ 选择 · Enter 确认）", items, index)

    def _connect_pick(self, name: str) -> None:
        """处理选中的供应商：已配置则切换，否则要求输入 API Key。"""
        provider = self._config.get_provider(name)
        if provider is not None:
            self._connect_switch(name)
            return
        if self._config.get_preset(name) is None:
            self._console.print(f"✖ 未知的 provider 或预设：{name}", style=_ERROR_STYLE)
            return
        self._awaiting_api_key = name

    def _connect_switch(self, name: str) -> None:
        try:
            provider = self._config.set_provider(name)
        except ValueError as exc:
            self._console.print(f"✖ {exc}", style=_ERROR_STYLE)
            return
        self._console.print(
            f"已连接到 {provider.name}（{provider.api_url}），"
            f"当前模型：{self._config.active_model()}",
            style=_OK_STYLE,
        )
        self._save_session()

    def _finish_connect_api_key(self, name: str, api_key: str) -> None:
        """收集到 API Key 后用预设生成配置文件并切换。"""
        self._awaiting_api_key = None
        api_key = api_key.strip()
        if not api_key:
            self._console.print(f"✖ 未输入 API Key，{name} 未配置", style=_ERROR_STYLE)
            return
        try:
            provider = self._config.use_preset(name, api_key)
        except ValueError as exc:
            self._console.print(f"✖ {exc}", style=_ERROR_STYLE)
            return
        self._console.print(
            f"已配置并连接到 {provider.name}（{provider.api_url}），"
            f"当前模型：{self._config.active_model()}",
            style=_OK_STYLE,
        )
        self._save_session()

    def _models_command(self, model: str | None) -> None:
        provider = self._config.active_provider()
        if provider is None:
            self._console.print("未配置 API provider，请先 /connect", style=_ERROR_STYLE)
            return
        if model:
            try:
                self._config.set_model(model)
            except ValueError as exc:
                self._console.print(f"✖ {exc}", style=_ERROR_STYLE)
                return
            self._console.print(f"已切换到模型：{model}", style=_OK_STYLE)
            self._save_session()
            return
        current = self._config.active_model()
        if not provider.models:
            self._console.print(
                f"当前 provider {provider.name} 未声明模型列表，当前模型：{current}",
                style=_DIM_STYLE,
            )
            return
        lines = []
        for candidate in provider.models:
            marker = "▸" if candidate == current else " "
            lines.append(("", f"  {marker} {candidate}"))
        lines.append(("class:cmd-hint", "输入 /models <名称> 切换"))
        self._command_display = (f"可用模型（{provider.name}）", lines)

    def _variants_command(self, variant: str | None) -> None:
        if variant:
            try:
                self._config.set_variant(variant)
            except ValueError as exc:
                self._console.print(f"✖ {exc}", style=_ERROR_STYLE)
                return
            self._console.print(f"已切换到思考强度：{variant}", style=_OK_STYLE)
            return
        lines = []
        for name, desc in self._config.variant_descriptions().items():
            marker = "▸" if name == self._config.ACTIVE_VARIANT else " "
            lines.append(("", f"  {marker} {name}  -  {desc}"))
        lines.append(("class:cmd-hint", "输入 /variants <名称> 切换"))
        self._command_display = ("思考强度", lines)

    # ---------- 工作模式 ----------

    def _mode_command(self, mode: str | None) -> None:
        if mode:
            try:
                self._config.set_mode(mode)
            except ValueError as exc:
                self._console.print(f"✖ {exc}", style=_ERROR_STYLE)
                return
            self._console.print(
                f"已切换到模式：{self._config.ACTIVE_MODE}（"
                f"{self._config.mode_descriptions().get(self._config.ACTIVE_MODE, '')}）",
                style=_OK_STYLE,
            )
            return
        lines = []
        for name, desc in self._config.mode_descriptions().items():
            marker = "▸" if name == self._config.ACTIVE_MODE else " "
            lines.append(("", f"  {marker} {name}  -  {desc}"))
        lines.append(("class:cmd-hint", "输入 /mode <名称> 切换"))
        self._command_display = ("工作模式", lines)

    # ---------- 上下文压缩 ----------

    def _compact_command(self, keep: int | None = None) -> None:
        keep = keep if keep and keep > 0 else _DEFAULT_COMPACT_KEEP
        content = [
            (index, message)
            for index, message in enumerate(self._messages)
            if message["role"] in {"user", "assistant"}
        ]
        if len(content) <= keep:
            self._console.print(f"当前共 {len(content)} 条对话消息，无需压缩", style=_DIM_STYLE)
            return
        keep_start = content[-keep][0]
        dropped = [message for index, message in content if index < keep_start]
        marker = (
            f"（上下文已压缩）早期 {len(dropped)} 条消息已移除，"
            f"请基于下方最近的 {keep} 条消息继续对话。"
        )
        self._messages = [{"role": "user", "content": marker}] + self._messages[keep_start:]
        self._save_session()
        self._console.print(
            f"已压缩上下文：移除 {len(dropped)} 条早期消息，保留最近 {keep} 条",
            style=_OK_STYLE,
        )

    # ---------- 用量统计 ----------

    def _usage_command(self) -> None:
        model = self._config.active_model()
        provider = self._config.active_provider()
        provider_name = provider.name if provider else "?"
        window = self._config.context_window(model)
        if self._client is None:
            self._console.print("未连接客户端，无法获取用量", style=_DIM_STYLE)
            return
        usage = self._client.usage_summary()
        input_tokens = usage["input_tokens"]
        output_tokens = usage["output_tokens"]
        total = input_tokens + output_tokens
        last_input = usage["last_input_tokens"]
        current_pct = last_input / window * 100 if window else 0.0
        lines = [
            ("", f"  模型: {model}（{provider_name}）"),
            ("", f"  上下文窗口: {window:,} tokens"),
            ("", f"  当前上下文（最近一次请求输入）: {last_input:,} tokens  ·  {current_pct:.2f}%"),
            ("", ""),
            ("", "  本次会话累计:"),
            ("", f"    - 输入 tokens: {input_tokens:,}"),
            ("", f"    - 输出 tokens: {output_tokens:,}"),
            ("", f"    - 合计: {total:,}  ·  {usage['calls']} 次请求"),
        ]
        self._command_display = ("用量统计", lines)

    # ---------- 初始化 AGENTS.md ----------

    def _init_command(self, force: bool = False) -> None:
        target = self._config.ROOT_DIR / "AGENTS.md"
        if target.exists() and not force:
            self._console.print(
                f"AGENTS.md 已存在（{target}），如需覆盖请使用 /init -f",
                style=_TOOL_STYLE,
            )
            return
        target.write_text(_render_agents_md(self._config.ROOT_DIR), encoding="utf-8")
        self._console.print(f"已生成 {target}", style=_OK_STYLE)

    # ---------- 自动 git ----------

    def _auto_git_start(self) -> None:
        """会话启动：若启用 RP_AUTO_GIT，自动在 ROOT_DIR 初始化 git 仓库。"""
        if not self._config.AUTO_GIT:
            return
        if not ensure_repo(self._config.ROOT_DIR):
            self._git_ready = False
            self._print_tool_line("git: 自动初始化仓库失败（将跳过自动提交与检查点）")
            return
        self._git_ready = True
        self._print_tool_line("git: 工作区仓库已就绪，每轮对话后自动提交（/checkpoints 查看）")

    def _auto_commit(self) -> None:
        """每轮对话结束后提交当前工作区改动（未启用或无改动时跳过）。"""
        if not self._git_ready or not self._config.AUTO_GIT:
            return
        summary = self._last_user_summary()
        round_no = self._round_number()
        message = f"rp: 第 {round_no} 轮对话"
        if summary:
            message += f" - {summary}"
        if commit_changes(
            self._config.ROOT_DIR, message, kind="round", round_no=round_no
        ):
            self._print_tool_line("git: 已提交本轮改动")

    def _round_number(self) -> int:
        """当前已完成（或正在完成）的对话轮次。"""
        return sum(1 for message in self._messages if message["role"] == "assistant")

    def _last_user_summary(self) -> str:
        """最近一条用户消息的摘要（用于提交信息），无则返回空串。"""
        for message in reversed(self._messages):
            if message["role"] == "user":
                return " ".join(str(message["content"]).split())[:40]
        return ""

    # ---------- 检查点与回滚 ----------

    def _checkpoints_command(self, ref: str | None) -> None:
        """查看提交检查点；/checkpoints <hash> 可直接发起回滚。"""
        if not self._git_ready:
            self._console.print(
                "自动 git 未启用（RP_AUTO_GIT=0 或仓库初始化失败）", style=_TOOL_STYLE
            )
            return
        if ref:
            self._confirm_rollback(ref)
            return
        entries = self._checkpoint_store.list()
        if not entries:
            self._console.print("暂无检查点（还没有任何提交）", style=_DIM_STYLE)
            return
        items = [(e["hash"], f"{e['short']}  {e['message']}") for e in entries]
        if not self._console.is_terminal:
            lines = [("", f"  {label}") for _, label in items]
            lines.append(("class:cmd-hint", "输入 /rollback <hash> 回滚到指定检查点"))
            self._command_display = ("提交检查点", lines)
            return
        self._open_picker(
            "检查点（↑↓ 选择 · Enter 确认回滚 · Esc 取消）", items, kind="checkpoints"
        )

    def _rollback_command(self, ref: str | None) -> None:
        """回滚到指定提交：/rollback <hash>（git reset --hard）。"""
        if not self._git_ready:
            self._console.print(
                "自动 git 未启用（RP_AUTO_GIT=0 或仓库初始化失败）", style=_TOOL_STYLE
            )
            return
        if not ref:
            self._console.print("用法：/rollback <hash>（可用 /checkpoints 查看检查点）", style=_TOOL_STYLE)
            return
        self._confirm_rollback(ref)

    def _confirm_rollback(self, ref: str) -> None:
        """发起回滚确认：等待用户输入 y 后执行 git reset --hard。"""
        self._awaiting_rollback = ref
        self._console.print(
            f"⚠ 将执行 git reset --hard {ref}，工作区未提交的改动将丢失。",
            style=_TOOL_STYLE,
        )
        self._console.print("输入 y 确认回滚，其他任意键取消", style=_TOOL_STYLE)

    def _execute_rollback(self, ref: str) -> None:
        ok, message = rollback_to(self._config.ROOT_DIR, ref)
        if ok:
            self._console.print(f"✓ {message}", style=_OK_STYLE)
        else:
            self._console.print(f"✖ 回滚失败：{message}", style=_ERROR_STYLE)

    # ---------- 会话管理 ----------

    def _session_command(self, session_id: str | None) -> None:
        if session_id:
            self._resume_session(session_id)
        else:
            self._list_sessions()

    def _list_sessions(self) -> None:
        sessions = self._session_store.list()
        if not sessions:
            self._console.print("暂无已保存的会话", style=_DIM_STYLE)
            return
        lines = []
        for session in sessions:
            lines.append(
                (
                    "",
                    f"  {session.session_id}  ·  {session.updated_at}"
                    f"  ·  {session.message_count} 条  ·  {session.summary}",
                )
            )
        lines.append(("class:cmd-hint", "输入 /session <id> 恢复指定会话"))
        self._command_display = ("已保存的会话", lines)

    def _resume_session(self, session_id: str) -> None:
        session = self._session_store.load(session_id)
        if session is None:
            self._console.print(
                f"未找到会话 {session_id}，输入 /session 查看列表", style=_ERROR_STYLE
            )
            return
        self._messages = list(session.messages)
        self._system_prompt = session.system_prompt
        if session.mode and session.mode in self._config.MODES:
            self._config.ACTIVE_MODE = session.mode
        self._session = session
        self._console.print(
            f"已恢复会话 {session_id}（{session.message_count} 条消息）",
            style=_OK_STYLE,
        )

    def _save_session(self) -> None:
        if self._single_shot:
            return
        if self._session is None:
            self._session = Session(
                session_id=datetime.now().strftime("%Y%m%d-%H%M%S"),
                model=self._config.active_model(),
                system_prompt=self._system_prompt,
                mode=self._config.ACTIVE_MODE,
            )
        self._session.mode = self._config.ACTIVE_MODE
        self._session.messages = list(self._messages)
        self._session_store.save(self._session)

    def _clear_history(self) -> None:
        self._messages.clear()
        self._current = ""
        self._save_session()
        self._console.print("已清空对话历史", style=_OK_STYLE)

    # ---------- 子 Agent ----------

    def _begin_subagent(self, data: dict) -> None:
        self._close_reply_stream()
        self._flush_current()
        agent_id = data.get("agent_id", "?")
        task = data.get("task", "")
        self._console.print(f"→ 委派给 {agent_id}", style=_TOOL_STYLE)
        if self._can_run_panel():
            try:
                from .subagent_panel import SubAgentPanel

                panel = SubAgentPanel(self._config, self._bus, agent_id, task)
                self._subagent_mode = "panel"
                self._stop_cancel_watcher()
                try:
                    result = panel.run()
                finally:
                    self._subagent_mode = "idle"
                self._start_cancel_watcher()
                self._record_subagent_result(agent_id, result)
                if result:
                    self._console.print()
                    self._console.print(f"  ⇢ {result}", style=_DIM_STYLE)
                return
            except Exception:
                logger.debug("子 Agent 面板不可用，回退到滚动输出", exc_info=True)
        self._subagent_mode = "scroll"
        self._subagent_agent = agent_id
        self._subagent_buffer = ""

    def _can_run_panel(self) -> bool:
        return self._console.is_terminal

    def _handle_subagent_token(self, data: dict) -> None:
        if self._subagent_mode != "scroll":
            return
        text = data.get("text", "")
        self._subagent_buffer += text
        self._console.print(text, style=_DIM_STYLE, end="")

    def _handle_subagent_tool(self, data: dict) -> None:
        if self._subagent_mode != "scroll":
            return
        name = data.get("name", "?")
        arguments = data.get("arguments", "")
        self._last_subagent_tool = name
        self._pending_subagent_read = extract_read_path(arguments) if name == "read" else None
        self._print_tool_line(format_tool_call(name, arguments), indent=4)

    def _handle_subagent_tool_result(self, data: dict) -> None:
        if self._subagent_mode != "scroll":
            return
        result = str(data.get("result", ""))
        last_tool = self._last_subagent_tool
        self._last_subagent_tool = None
        pending = self._pending_subagent_read
        self._pending_subagent_read = None
        if pending is not None and not result.startswith("error:"):
            self._console.print(f"      → 已读取 {pending}", style=_DIM_STYLE)
            return
        if last_tool == "grep":
            status = format_grep_status(result)
            style = _ERROR_STYLE if result.startswith("error:") else _DIM_STYLE
            self._console.print(f"      → {status}", style=style)
            return
        summary = " ".join(result.split())[:_TOOL_RESULT_SUMMARY_LEN]
        suffix = "…" if len(summary) >= _TOOL_RESULT_SUMMARY_LEN else ""
        self._console.print(f"      → {summary}{suffix}", style=_DIM_STYLE)

    def _finish_subagent(self, data: dict) -> None:
        if self._subagent_mode != "scroll":
            return
        self._subagent_mode = "idle"
        self._console.print()
        self._flush_subagent_buffer()
        self._console.print(f"  ⇢ {data.get('result', '')}", style=_DIM_STYLE)

    def _fail_subagent(self, data: dict) -> None:
        if self._subagent_mode != "scroll":
            return
        self._subagent_mode = "idle"
        self._console.print()
        self._console.print(f"✖ 子 Agent 失败: {data.get('error', '')}", style=_ERROR_STYLE)

    def _flush_subagent_buffer(self) -> None:
        if self._subagent_agent and self._subagent_buffer:
            self._messages.append(
                {
                    "role": "tool",
                    "content": (f"子 Agent {self._subagent_agent} 输出: {self._subagent_buffer}"),
                }
            )
        self._subagent_agent = None
        self._subagent_buffer = ""

    def _record_subagent_result(self, agent_id: str, result: str) -> None:
        if result:
            self._messages.append(
                {
                    "role": "tool",
                    "content": f"子 Agent {agent_id} 输出: {result}",
                }
            )

    def _ask(self, question: str) -> None:
        self._console.print(f"? {question}", style=_TOOL_STYLE)
        self._console.print("❯ ", style=_TOOL_STYLE, end="")
        try:
            raw = sys.stdin.readline()
        except (EOFError, KeyboardInterrupt):
            raw = ""
        self._bus.publish(Event(EventTypes.USER_ANSWER, raw.strip()))

    def _provider_problem(self) -> str | None:
        """返回阻止对话的配置问题描述；无问题返回 None。"""
        if self._config.active_provider() is None:
            return "未配置任何 API provider，请先运行 /connect 选择预设并输入 API Key"
        try:
            self._config.validate()
        except ValueError as exc:
            return str(exc)
        return None

    def _submit(self, user_message: str, echo: bool = True) -> None:
        assert self._client is not None, "ChatApp 需要有效的 ChatClient"
        problem = self._provider_problem()
        if problem:
            self._console.print(f"✖ {problem}", style=_ERROR_STYLE)
            if self._single_shot:
                self._shutdown = True
            return
        self._command_display = None
        if echo:
            self._console.print(f"❯ {user_message}", style=_USER_STYLE)
        self._messages.append({"role": "user", "content": user_message})
        self._error = None
        self._interrupted = False
        self._busy = True
        self._start_cancel_watcher()
        self._client.submit(user_message, self._system_prompt, self._history_params())

    # ---------- ESC 双击中断 ----------

    def _start_cancel_watcher(self) -> None:
        if self._cancel_watcher is not None and self._cancel_watcher.is_alive():
            return
        if not (self._console.is_terminal and sys.stdin.isatty()):
            return
        watcher = EscCancelWatcher(self._bus)
        self._cancel_watcher = watcher
        watcher.start()

    def _stop_cancel_watcher(self) -> None:
        watcher = self._cancel_watcher
        if watcher is not None:
            watcher.stop()
            self._cancel_watcher = None

    def _handle_cancel(self) -> None:
        if not self._busy:
            return
        self._interrupted = True
        if self._client is not None:
            self._client.cancel()

    def _history_params(self) -> list[ChatCompletionMessageParam]:
        history: list[ChatCompletionMessageParam] = []
        for message in self._messages:
            role = message["role"]
            content = message["content"]
            if role == "user":
                history.append(ChatCompletionUserMessageParam(role="user", content=content))
            elif role == "assistant":
                history.append(
                    ChatCompletionAssistantMessageParam(role="assistant", content=content)
                )
        return history


def _resolve_color_system(value: str | None) -> _ColorSystemOption | None:
    if not value:
        return None
    if value in {"auto", "standard", "256", "truecolor", "windows"}:
        return value  # type: ignore[return-value]
    return None


def _render_agents_md(root: Path) -> str:
    """根据工作区特征生成 AGENTS.md 模板。"""
    name = root.name
    tech = "Python"
    install = "pip install -r requirements.txt"
    run = "python -m src.main"
    test = "pytest"
    lint = "ruff check ."
    if (root / "package.json").is_file():
        tech = "Node.js"
        install = "npm install"
        run = "npm run dev"
        test = "npm test"
        lint = "npm run lint"
    lines = [
        "# AGENTS.md",
        "",
        "本文件为 AI 编程助手（Agent）在本仓库工作的指引。",
        "",
        "## 项目简介",
        "",
        f"{name}：请在此补充项目描述。",
        "",
        "## 常用命令",
        "",
        f"- 安装依赖：`{install}`",
        f"- 运行项目：`{run}`",
        f"- 运行测试：`{test}`",
        f"- 代码检查：`{lint}`",
        "",
        "## 技术栈",
        "",
        f"- {tech}",
        "",
        "## 代码约定",
        "",
        "- 遵循项目现有的命名与代码风格",
        "- 改动前先阅读相关模块的现有实现",
        "- 完成改动后运行测试与代码检查",
        "",
    ]
    return "\n".join(lines)
