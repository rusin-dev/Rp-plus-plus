from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from openai.types.chat import (
    ChatCompletionMessageParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionUserMessageParam,
)

from ..api.client import make_client, stream_completion
from ..api.tools import ToolRegistry
from ..config import Config
from ..core.event_bus import Event, EventBus, EventTypes
from ..core.logger import get_logger

logger = get_logger(__name__)

_FRONTMATTER_MARK = "---"

_ALL_TOOLS = {
    "ask",
    "shell",
    "read",
    "write",
    "grep",
    "web_search",
    "web_fetch",
    "delegate",
}

_FORBIDDEN_TOOLS = {"ask", "delegate"}


@dataclass(slots=True)
class SubAgent:
    """一个子 Agent 的定义。"""

    name: str
    description: str
    tools: list[str]
    prompt: str
    path: Path


def _parse_frontmatter(text: str, path: Path) -> tuple[dict[str, str], str]:
    """解析 frontmatter 块（--- ... ---）与正文，返回 (字段, 正文)。"""
    lines = text.splitlines()
    if not lines or lines[0].strip() != _FRONTMATTER_MARK:
        raise ValueError(f"{path.name}: 缺少 frontmatter 起始标记")
    closing = None
    for index, line in enumerate(lines[1:], start=1):
        if line.strip() == _FRONTMATTER_MARK:
            closing = index
            break
    if closing is None:
        raise ValueError(f"{path.name}: frontmatter 未闭合")
    data: dict[str, str] = {}
    for line in lines[1:closing]:
        stripped = line.strip()
        if not stripped or stripped.startswith("#"):
            continue
        key, sep, value = stripped.partition(":")
        if not sep:
            raise ValueError(f"{path.name}: frontmatter 行格式错误: {line}")
        data[key.strip()] = value.strip()
    body = "\n".join(lines[closing + 1 :]).strip()
    return data, body


def _parse_tools(raw: str, path: Path) -> list[str]:
    value = raw.strip()
    if not (value.startswith("[") and value.endswith("]")):
        raise ValueError(f"{path.name}: tools 必须是 [a, b] 格式")
    names = [part.strip() for part in value[1:-1].split(",") if part.strip()]
    unknown = [name for name in names if name not in _ALL_TOOLS]
    if unknown:
        raise ValueError(f"{path.name}: 未知工具 {', '.join(unknown)}")
    forbidden = [name for name in names if name in _FORBIDDEN_TOOLS]
    if forbidden:
        raise ValueError(f"{path.name}: 子 Agent 不允许使用工具 {', '.join(forbidden)}")
    return names


def load_agents() -> list[SubAgent]:
    """扫描 data/agents 目录，加载全部子 Agent 定义。"""
    agents_dir = Config.DATA_DIR / "agents"
    if not agents_dir.is_dir():
        return []
    agents: list[SubAgent] = []
    for path in sorted(agents_dir.glob("*.md")):
        try:
            text = path.read_text(encoding="utf-8")
            data, body = _parse_frontmatter(text, path)
            missing = {"name", "description", "tools"} - set(data)
            if missing:
                raise ValueError(f"{path.name}: 缺少字段 {', '.join(sorted(missing))}")
            if not body:
                raise ValueError(f"{path.name}: 缺少提示词正文")
            agents.append(
                SubAgent(
                    name=data["name"],
                    description=data["description"],
                    tools=_parse_tools(data["tools"], path),
                    prompt=body,
                    path=path,
                )
            )
        except (OSError, ValueError) as exc:
            logger.warning("跳过子 Agent 文件 %s: %s", path.name, exc)
    return agents


class AgentRegistry:
    """子 Agent 注册表：按名称查询。"""

    def __init__(self, agents: list[SubAgent] | None = None) -> None:
        self._agents = {
            a.name: a for a in (agents if agents is not None else load_agents())
        }

    def get(self, name: str) -> SubAgent | None:
        return self._agents.get(name)

    def names(self) -> list[str]:
        return sorted(self._agents)

    def all(self) -> list[SubAgent]:
        return list(self._agents.values())


SUBAGENT_RESULT_MAX_CHARS = 8000


class SubAgentRunner:
    """在独立上下文中运行一个子 Agent，返回其最终回复。"""

    def __init__(
        self,
        config: type[Config],
        bus: EventBus,
        tools: ToolRegistry,
        subagent: SubAgent,
        task: str,
        context: str | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._tools = tools
        self._subagent = subagent
        self._task = task
        self._context = context

    def run(self) -> str:
        mode = self._config.ACTIVE_MODE
        system_prompt = self._subagent.prompt
        instructions = self._config.mode_instructions(mode)
        if instructions:
            system_prompt = f"{system_prompt}\n\n{instructions}"
        user_content = self._task
        if self._context:
            user_content = f"## 背景上下文\n{self._context}\n\n## 任务\n{self._task}"
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            ChatCompletionUserMessageParam(role="user", content=user_content),
        ]
        agent_id = self._subagent.name
        self._bus.publish(
            Event(
                EventTypes.SUBAGENT_START,
                {"agent_id": agent_id, "task": self._task},
            )
        )
        client = make_client(self._config)
        last_text = ""
        try:
            while True:
                made, text = stream_completion(
                    self._config,
                    self._bus,
                    client,
                    self._tools,
                    messages,
                    mode,
                    agent_id=agent_id,
                )
                if text:
                    last_text = text
                if not made:
                    break
        except Exception as exc:
            logger.exception("子 Agent %s 执行失败", agent_id)
            self._bus.publish(
                Event(
                    EventTypes.SUBAGENT_ERROR,
                    {"agent_id": agent_id, "error": str(exc)},
                )
            )
            return f"error: 子 Agent {agent_id} 执行失败: {exc}"
        result = last_text or f"（子 Agent {agent_id} 未返回文本）"
        if len(result) > SUBAGENT_RESULT_MAX_CHARS:
            result = (
                result[:SUBAGENT_RESULT_MAX_CHARS]
                + f"…（已截断，全文共 {len(result)} 字符）"
            )
        self._bus.publish(
            Event(
                EventTypes.SUBAGENT_DONE,
                {"agent_id": agent_id, "result": result},
            )
        )
        return result
