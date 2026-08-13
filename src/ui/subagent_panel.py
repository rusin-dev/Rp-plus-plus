from __future__ import annotations

import asyncio

from prompt_toolkit.application import Application
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.layout import Layout, Window
from prompt_toolkit.layout.controls import FormattedTextControl
from prompt_toolkit.layout.dimension import Dimension
from prompt_toolkit.mouse_events import MouseEvent
from prompt_toolkit.widgets import Frame

from ..config import Config
from ..core.event_bus import Event, EventBus, EventTypes

_PUMP_INTERVAL = 0.05
_TOOL_RESULT_SUMMARY_LEN = 200


class SubAgentPanel:
    """子 Agent 执行面板：实时追加输出，标题可鼠标点击折叠/展开。

    面板运行期间会消费总线上的 SUBAGENT_* 事件，直到子 Agent 完成或出错。
    """

    def __init__(
        self, config: type[Config], bus: EventBus, agent_id: str, task: str
    ) -> None:
        self._bus = bus
        self._agent_id = agent_id
        self._task = task
        self._collapsed = False
        self._done = False
        self._lines: list[str] = []
        self._result = ""
        self._error: str | None = None
        self._app: Application | None = None

    # ---------- 事件处理（可单测） ----------

    def handle_event(self, event: Event) -> None:
        data = event.data or {}
        if event.type == EventTypes.SUBAGENT_TOKEN:
            self._append(data.get("text", ""))
        elif event.type == EventTypes.SUBAGENT_TOOL_CALL:
            self._append(f"⎿ {data.get('name')}({data.get('arguments')})")
        elif event.type == EventTypes.SUBAGENT_TOOL_RESULT:
            result = str(data.get("result", ""))
            summary = " ".join(result.split())[:_TOOL_RESULT_SUMMARY_LEN]
            suffix = "…" if len(summary) >= _TOOL_RESULT_SUMMARY_LEN else ""
            self._append(f"→ {summary}{suffix}")
        elif event.type == EventTypes.SUBAGENT_DONE:
            self._result = data.get("result", "")
            self._done = True
            self._append("✓ 子 Agent 完成")
        elif event.type == EventTypes.SUBAGENT_ERROR:
            self._error = data.get("error", "")
            self._done = True
            self._append(f"✖ {self._error}")

    def _append(self, text: str) -> None:
        if not text:
            return
        self._lines.extend(text.splitlines())

    def toggle_collapse(self) -> None:
        self._collapsed = not self._collapsed
        if self._app is not None:
            self._app.invalidate()

    def is_done(self) -> bool:
        return self._done

    def result(self) -> str:
        if self._error is not None:
            return f"error: {self._error}"
        return self._result

    # ---------- 渲染 ----------

    def _fragments(self) -> list[tuple]:
        style = "bold"
        title = f"{'▸' if self._collapsed else '▾'} {self._agent_id}（点击折叠）"
        fragments: list[tuple] = [(style, title, self._on_title_click)]
        if not self._collapsed:
            for line in self._lines:
                fragments.append(("dim", f"  {line}"))
        fragments.append(("", ""))
        return fragments

    def _on_title_click(self, mouse_event: MouseEvent) -> object:
        self.toggle_collapse()
        return None

    def _build_app(self) -> Application:
        kb = KeyBindings()

        @kb.add("c")
        def _toggle(_event: object) -> None:
            self.toggle_collapse()

        control = FormattedTextControl(text=self._fragments, focusable=False)
        body = Window(
            content=control,
            height=Dimension(min=1),
            wrap_lines=True,
            dont_extend_height=True,
        )
        frame = Frame(body, title=f"子 Agent：{self._agent_id}")
        return Application(
            layout=Layout(frame),
            key_bindings=kb,
            mouse_support=True,
            full_screen=True,
            erase_when_done=False,
        )

    # ---------- 运行 ----------

    async def _pump(self) -> None:
        while not self._done:
            for event in self._bus.drain():
                if event.type.startswith("subagent_"):
                    self.handle_event(event)
            if self._app is not None:
                self._app.invalidate()
            if self._done and self._app is not None:
                self._app.exit()
                return
            await asyncio.sleep(_PUMP_INTERVAL)

    async def _run_with_pump(self) -> None:
        pump = asyncio.create_task(self._pump())
        try:
            await self._app.run_async()
        finally:
            pump.cancel()

    def run(self) -> str:
        """阻塞运行面板直到子 Agent 完成/出错，返回结果。"""
        self._app = self._build_app()
        asyncio.run(self._run_with_pump())
        return self.result()
