from __future__ import annotations

from collections.abc import Callable
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
from ..core.git_ops import (
    abort_task_branch,
    finish_task_branch,
    setup_task_branch,
)
from ..core.logger import get_logger

logger = get_logger(__name__)

_FRONTMATTER_MARK = "---"

_ALL_TOOLS = {
    "ask",
    "shell",
    "read",
    "write",
    "edit",
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
    provider: str | None = None
    model: str | None = None
    variant: str | None = None
    mode: str | None = None


class _ConfigOverride:
    """在 with 块内临时覆盖 Config 的活动设置，退出时（含异常）恢复原值。

    任何参数为 None 则该字段保持不动。
    """

    _FIELDS = ("ACTIVE_PROVIDER", "ACTIVE_MODEL", "ACTIVE_VARIANT", "ACTIVE_MODE")

    def __init__(
        self,
        provider: str | None = None,
        model: str | None = None,
        variant: str | None = None,
        mode: str | None = None,
    ) -> None:
        self._overrides = {
            "ACTIVE_PROVIDER": provider,
            "ACTIVE_MODEL": model,
            "ACTIVE_VARIANT": variant,
            "ACTIVE_MODE": mode,
        }
        self._saved: dict[str, object] = {}

    def __enter__(self) -> _ConfigOverride:
        for key, value in self._overrides.items():
            if value is not None:
                self._saved[key] = getattr(Config, key)
                setattr(Config, key, value)
        return self

    def __exit__(self, exc_type, exc, tb) -> None:
        for key, original in self._saved.items():
            setattr(Config, key, original)
        self._saved.clear()


def _parse_overrides(data: dict[str, str], path: Path) -> dict[str, str | None]:
    """校验并规范化 frontmatter 中的可选覆盖字段。

    返回 {"provider": ..., "model": ..., "variant": ..., "mode": ...}，
    任何字段未提供时为 None。校验失败抛 ValueError，错误信息带文件名。
    """
    result: dict[str, str | None] = {
        "provider": None,
        "model": None,
        "variant": None,
        "mode": None,
    }

    raw_provider = data.get("provider")
    if raw_provider is not None:
        provider_name = raw_provider.strip().lower()
        provider = Config.providers().get(provider_name)
        if provider is None:
            available = ", ".join(Config.providers()) or "（无）"
            raise ValueError(f"{path.name}: provider '{raw_provider}' 未配置，可用：{available}")
        result["provider"] = provider.name

    raw_model = data.get("model")
    if raw_model is not None:
        provider_name = result["provider"]
        if provider_name is None:
            active = Config.active_provider()
            provider_name = active.name if active is not None else None
        provider = Config.providers().get(provider_name) if provider_name else None
        if provider is None:
            raise ValueError(f"{path.name}: model '{raw_model}' 缺少对应的 provider 配置")
        if raw_model not in provider.models:
            raise ValueError(
                f"{path.name}: model '{raw_model}' 不在 {provider.name} 的可用列表："
                f"{', '.join(provider.models) or '（空）'}"
            )
        result["model"] = raw_model

    raw_variant = data.get("variant")
    if raw_variant is not None:
        if raw_variant not in Config.VARIANT_PARAMS:
            raise ValueError(
                f"{path.name}: variant '{raw_variant}' 不在已知列表："
                f"{', '.join(Config.VARIANT_PARAMS)}"
            )
        result["variant"] = raw_variant

    raw_mode = data.get("mode")
    if raw_mode is not None:
        if raw_mode not in Config.MODES:
            raise ValueError(
                f"{path.name}: mode '{raw_mode}' 不在已知列表：{', '.join(Config.MODES)}"
            )
        result["mode"] = raw_mode

    return result


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
            overrides = _parse_overrides(data, path)
            agents.append(
                SubAgent(
                    name=data["name"],
                    description=data["description"],
                    tools=_parse_tools(data["tools"], path),
                    prompt=body,
                    path=path,
                    provider=overrides["provider"],
                    model=overrides["model"],
                    variant=overrides["variant"],
                    mode=overrides["mode"],
                )
            )
        except (OSError, ValueError) as exc:
            logger.warning("跳过子 Agent 文件 %s: %s", path.name, exc)
    return agents


class AgentRegistry:
    """子 Agent 注册表：按名称查询。"""

    def __init__(self, agents: list[SubAgent] | None = None) -> None:
        self._agents = {a.name: a for a in (agents if agents is not None else load_agents())}

    def get(self, name: str) -> SubAgent | None:
        return self._agents.get(name)

    def names(self) -> list[str]:
        return sorted(self._agents)

    def all(self) -> list[SubAgent]:
        return list(self._agents.values())


SUBAGENT_RESULT_MAX_CHARS = 8000


def _bus_ask(bus: EventBus, timeout: float | None = None) -> Callable[[str], str]:
    """构造一个通过事件总线向用户提问并等待回答的 ask 函数。"""

    def ask(question: str) -> str:
        bus.publish(Event(EventTypes.ASK_QUESTION, question))
        reply = bus.await_event(EventTypes.USER_ANSWER, timeout=timeout)
        return str(reply.data)

    return ask


class SubAgentRunner:
    """在独立上下文中运行一个子 Agent，返回其最终回复。

    启用自动 git（Config.AUTO_GIT）时，委派任务会先创建独立任务分支执行，
    子 Agent 完成后向用户展示改动统计并请求审核：批准则合并回主分支，
    否则丢弃该分支的改动。
    """

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

    def run(self, ask: Callable[[str], str] | None = None) -> str:
        mode = self._subagent.mode or self._config.ACTIVE_MODE
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
        last_text = ""
        branch_ctx: dict | None = None
        if self._config.AUTO_GIT:
            try:
                branch_ctx = setup_task_branch(self._config.ROOT_DIR, agent_id)
            except Exception:
                logger.debug("任务分支创建失败，子 Agent 将在主分支直接执行", exc_info=True)
                branch_ctx = None
        try:
            with _ConfigOverride(
                provider=self._subagent.provider,
                model=self._subagent.model,
                variant=self._subagent.variant,
            ):
                model = self._config.active_model()
                if not model:
                    # 模型名为空时（如 provider 配置缺失、或配置被 git stash 移出工作区），
                    # 直接给出明确错误，避免向 API 发送空模型名（会得到 400 "you passed ."）。
                    message = (
                        f"子 Agent {agent_id} 无法执行：未解析到有效的 API 模型"
                        "（provider 配置缺失或模型名为空，请检查 src/data/providers/ 配置）"
                    )
                    logger.error("%s", message)
                    self._bus.publish(
                        Event(
                            EventTypes.SUBAGENT_ERROR,
                            {"agent_id": agent_id, "error": message},
                        )
                    )
                    return f"error: {message}"
                self._bus.publish(
                    Event(
                        EventTypes.SUBAGENT_START,
                        {"agent_id": agent_id, "task": self._task},
                    )
                )
                client = make_client(self._config)
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
            abort_task_branch(self._config.ROOT_DIR, branch_ctx)
            self._bus.publish(
                Event(
                    EventTypes.SUBAGENT_ERROR,
                    {"agent_id": agent_id, "error": str(exc)},
                )
            )
            return f"error: 子 Agent {agent_id} 执行失败: {exc}"
        result = last_text or f"（子 Agent {agent_id} 未返回文本）"
        if len(result) > SUBAGENT_RESULT_MAX_CHARS:
            result = result[:SUBAGENT_RESULT_MAX_CHARS] + f"…（已截断，全文共 {len(result)} 字符）"
        self._bus.publish(
            Event(
                EventTypes.SUBAGENT_DONE,
                {"agent_id": agent_id, "result": result},
            )
        )
        if branch_ctx is not None:
            ask_fn = ask if ask is not None else _bus_ask(self._bus)
            try:
                finish_task_branch(self._config.ROOT_DIR, branch_ctx, agent_id, ask_fn)
            except Exception as exc:
                logger.exception("任务分支审核/合并流程失败")
                self._bus.publish(
                    Event(
                        EventTypes.SUBAGENT_ERROR,
                        {"agent_id": agent_id, "error": f"分支审核/合并失败: {exc}"},
                    )
                )
        return result
