from __future__ import annotations

import argparse
import sys

from rich.console import Console

from .api.client import ChatClient
from .api.tools import ToolRegistry
from .config import Config
from .core.event_bus import EventBus
from .core.logger import get_logger
from .core.prompt import get_prompt, list_prompts
from .ui.app import ChatApp

logger = get_logger(__name__)
console = Console()


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="rp",
        description="你的编程副驾驶（Your Programming Co-Pilot）",
    )
    parser.add_argument(
        "-p",
        "--prompt",
        default="SYSTEM_PROMPT.md",
        help="提示词文件名（默认 SYSTEM_PROMPT.md）",
    )
    parser.add_argument(
        "-l",
        "--level",
        default="general",
        help="提示词所在目录（默认 general）",
    )
    parser.add_argument(
        "-m",
        "--message",
        help="单次提问（不传则进入 TUI 交互模式）",
    )
    parser.add_argument(
        "-M",
        "--mode",
        choices=["plan", "build", "auto"],
        default=None,
        help="工作模式（plan=仅规划 / build=实现 / auto=自动规划并实现）",
    )
    parser.add_argument(
        "--list-prompts",
        action="store_true",
        help="列出可用的提示词文件",
    )
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    if args.list_prompts:
        prompts = list_prompts(args.level)
        if not prompts:
            console.print(f"目录 {args.level} 下没有可用提示词")
            return 1
        for path in prompts:
            console.print(path.name)
        return 0

    try:
        system_prompt = get_prompt(args.prompt, args.level)
        Config.validate()
    except (FileNotFoundError, ValueError) as exc:
        console.print(f"[red]错误:[/red] {exc}")
        return 1

    if args.mode:
        Config.ACTIVE_MODE = args.mode

    bus = EventBus()
    tools = ToolRegistry()
    client = ChatClient(Config, bus, tools)
    app = ChatApp(
        Config,
        bus,
        client,
        system_prompt,
        initial_message=args.message,
    )
    return app.run()


if __name__ == "__main__":
    sys.exit(main())
