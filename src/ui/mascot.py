from __future__ import annotations

from rich.console import Console
from rich.text import Text

_MASCOT_ART = [
    "     ▬",
    "    ▬▬▬",
    "   ▬▬▬▬▬",
    "  ▮▬▬▬▬▬▮",
    " ▮▮▬▬▬▬▬▮▮",
    "▮▮▮▬▬▬▬▬▮▮▮",
]

_MASCOT_STYLE = "cyan bold"
_VERSION = "0.1.0"
_TAGLINE = "你的编程副驾驶 · Your Programming Co-Pilot"


def mascot_text() -> Text:
    """返回吉祥物字符画（渲染时单色着色）。"""
    return Text("\n".join(_MASCOT_ART), style=_MASCOT_STYLE)


def print_mascot(console: Console) -> None:
    """打印启动吉祥物与欢迎语。"""
    console.print(mascot_text())
    console.print()
    console.print(f"[bold]{_TAGLINE}[/bold]")
    console.print(f"[dim]v{_VERSION}  ·  输入 /help 查看可用命令，exit 退出[/dim]")
    console.print()
