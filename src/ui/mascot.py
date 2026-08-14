from __future__ import annotations

from rich.console import Console
from rich.panel import Panel
from rich.table import Table
from rich.text import Text

_MASCOT_ART = [
    "▬",
    "▬▬▬",
    "▬▬▬▬▬",
    "▮▬▬▬▬▬▮",
    "▮▮▬▬▬▬▬▮▮",
    "▮▮▮▬▬▬▬▬▮▮▮",
]

_MASCOT_STYLE = "cyan bold"
_VERSION = "0.1.0"
_TAGLINE = "你的编程副驾驶 · Your Programming Co-Pilot"

_TIPS = [
    "输入 /help 查看全部命令",
    "/mode 切换 plan / build / auto 模式",
    "/connect 切换供应商 · /models 切换模型",
    "直接输入需求即可开始，exit 退出",
]


def mascot_text() -> Text:
    """返回吉祥物字符画（渲染时单色着色）。"""
    return Text("\n".join(_MASCOT_ART), style=_MASCOT_STYLE)


def _status_lines(model: str, provider: str, cwd: str) -> list[str]:
    lines: list[str] = []
    if model:
        lines.append(f"模型：{model}")
    if provider:
        lines.append(f"供应商：{provider}")
    if cwd:
        lines.append(f"工作目录：{cwd}")
    return lines


def welcome_panel(model: str = "", provider: str = "", cwd: str = "") -> Panel:
    """构建类 Claude Code 的启动欢迎面板（左 logo，右提示与状态）。"""
    left = Table.grid(expand=True)
    left.add_column(justify="center")
    left.add_row(mascot_text())
    left.add_row("")
    left.add_row(Text("欢迎回来！", style="bold"))
    left.add_row(Text(_TAGLINE, style="dim"))

    right = Table.grid(padding=(0, 1))
    right.add_column()
    right.add_row(Text("快速上手", style="bold cyan"))
    for tip in _TIPS:
        right.add_row(Text(f"• {tip}", style="dim"))
    status = _status_lines(model, provider, cwd)
    if status:
        right.add_row("")
        right.add_row(Text("当前状态", style="bold cyan"))
        for line in status:
            right.add_row(Text(line, style="dim"))

    body = Table.grid(expand=True)
    body.add_column(ratio=1, justify="center")
    body.add_column(ratio=2)
    body.add_row(left, right)

    return Panel(body, title=f"rp Co-Pilot v{_VERSION}", border_style="dim")


def print_mascot(
    console: Console,
    model: str = "",
    provider: str = "",
    cwd: str = "",
) -> None:
    """打印启动欢迎面板（类 Claude Code 的欢迎框）。"""
    console.print(welcome_panel(model, provider, cwd))
