from __future__ import annotations

import threading
from collections.abc import Callable

from openai import OpenAI
from openai.types.chat import (
    ChatCompletionAssistantMessageParam,
    ChatCompletionMessageParam,
    ChatCompletionMessageToolCallParam,
    ChatCompletionSystemMessageParam,
    ChatCompletionToolMessageParam,
    ChatCompletionUserMessageParam,
)

from ..config import Config
from ..core.event_bus import Event, EventBus, EventTypes
from ..core.logger import get_logger
from .tools import ToolRegistry


def client_credentials(config: type[Config]) -> tuple[str, str]:
    """返回当前活动 provider 的凭证。"""
    provider = config.active_provider()
    if provider is None:
        return "", ""
    return provider.api_key or "", provider.api_url or ""


def make_client(config: type[Config]) -> OpenAI:
    """创建与当前 provider 匹配的 OpenAI 客户端。"""
    api_key, base_url = client_credentials(config)
    return OpenAI(api_key=api_key, base_url=base_url)


def stream_completion(
    config: type[Config],
    bus: EventBus,
    client: OpenAI,
    tools: ToolRegistry,
    messages: list[ChatCompletionMessageParam],
    mode: str,
    agent_id: str | None = None,
    record_usage: Callable[[object], None] | None = None,
    cancel_event: threading.Event | None = None,
) -> tuple[bool, str]:
    """执行一轮流式请求并处理工具调用，返回 (是否发起工具调用, 累积文本)。

    agent_id 为 None 时发布主对话事件（TOKEN/TOOL_CALL/TOOL_RESULT）；
    否则发布带 agent_id 的子 Agent 事件（SUBAGENT_TOKEN/SUBAGENT_TOOL_CALL/
    SUBAGENT_TOOL_RESULT）。record_usage 非空时把 usage 回调给它（用于主对话计数）。
    cancel_event 置位时中止本轮流式与工具执行。
    """
    if cancel_event is not None and cancel_event.is_set():
        return False, ""
    extra_body = config.active_variant_params()
    stream = client.chat.completions.create(
        model=config.active_model(),
        messages=messages,
        tools=tools.schemas_for_mode(mode),
        stream=True,
        extra_body=extra_body or None,
        stream_options={"include_usage": True},
    )

    text = ""
    tool_calls: dict[int, dict[str, str]] = {}
    cancelled = False

    for chunk in stream:
        if cancel_event is not None and cancel_event.is_set():
            cancelled = True
            break
        usage = getattr(chunk, "usage", None)
        if usage is not None and record_usage is not None:
            record_usage(usage)
        if not chunk.choices:
            continue
        delta = chunk.choices[0].delta
        if delta.content:
            text += delta.content
            if agent_id is None:
                bus.publish(Event(EventTypes.TOKEN, delta.content))
            else:
                bus.publish(
                    Event(
                        EventTypes.SUBAGENT_TOKEN,
                        {"agent_id": agent_id, "text": delta.content},
                    )
                )
        for tc in delta.tool_calls or []:
            slot = tool_calls.setdefault(
                tc.index,
                {"id": tc.id or "", "name": "", "arguments": ""},
            )
            if tc.id:
                slot["id"] = tc.id
            if tc.function:
                if tc.function.name:
                    slot["name"] = tc.function.name
                if tc.function.arguments:
                    slot["arguments"] += tc.function.arguments

    if cancelled:
        try:
            stream.close()
        except Exception:
            pass
        if text:
            messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=text))
        return False, text

    if not tool_calls:
        if text:
            messages.append(ChatCompletionAssistantMessageParam(role="assistant", content=text))
        return False, text

    assistant_tool_calls = [
        ChatCompletionMessageToolCallParam(
            id=slot["id"],
            type="function",
            function={"name": slot["name"], "arguments": slot["arguments"]},
        )
        for slot in tool_calls.values()
    ]
    messages.append(
        ChatCompletionAssistantMessageParam(
            role="assistant",
            content=text or None,
            tool_calls=assistant_tool_calls,
        )
    )

    for slot in tool_calls.values():
        if agent_id is None:
            bus.publish(
                Event(
                    EventTypes.TOOL_CALL,
                    {"name": slot["name"], "arguments": slot["arguments"]},
                )
            )
        else:
            bus.publish(
                Event(
                    EventTypes.SUBAGENT_TOOL_CALL,
                    {
                        "agent_id": agent_id,
                        "name": slot["name"],
                        "arguments": slot["arguments"],
                    },
                )
            )
        if slot["name"] in config.mode_tool_exclusions(mode):
            result = f"error: 当前模式（{mode}）禁止使用工具 {slot['name']}"
        else:
            result = tools.execute(slot["name"], slot["arguments"], bus)
        if agent_id is None:
            bus.publish(Event(EventTypes.TOOL_RESULT, result))
        else:
            bus.publish(
                Event(
                    EventTypes.SUBAGENT_TOOL_RESULT,
                    {"agent_id": agent_id, "result": result},
                )
            )
        messages.append(
            ChatCompletionToolMessageParam(
                role="tool",
                tool_call_id=slot["id"],
                content=result,
            )
        )
    return True, text


logger = get_logger(__name__)


class ChatClient:
    """在后台线程中执行 OpenAI 对话请求，通过事件总线推送结果。"""

    def __init__(
        self,
        config: type[Config],
        bus: EventBus,
        tools: ToolRegistry | None = None,
    ) -> None:
        self._config = config
        self._bus = bus
        self._tools = tools or ToolRegistry()
        api_key, base_url = self._client_credentials()
        self._client: OpenAI | None = None
        self._client_params: tuple[str, str] | None = None
        if api_key:
            self._client = make_client(config)
            self._client_params = (api_key, base_url)
        self._usage = {
            "input_tokens": 0,
            "output_tokens": 0,
            "calls": 0,
            "last_input_tokens": 0,
            "last_total_tokens": 0,
        }
        self._cancel = threading.Event()

    def usage_summary(self) -> dict:
        """返回本会话累计的 token 用量快照。"""
        return dict(self._usage)

    def _record_usage(self, usage: object) -> None:
        prompt_tokens = getattr(usage, "prompt_tokens", 0) or 0
        completion_tokens = getattr(usage, "completion_tokens", 0) or 0
        total_tokens = getattr(usage, "total_tokens", 0) or 0
        self._usage["input_tokens"] += prompt_tokens
        self._usage["output_tokens"] += completion_tokens
        self._usage["last_input_tokens"] = prompt_tokens
        self._usage["last_total_tokens"] = total_tokens

    def _client_credentials(self) -> tuple[str, str]:
        """返回当前生效的凭证：优先旧版 CUSTOM_*，否则取活动 provider。"""
        return client_credentials(self._config)

    def _ensure_client(self) -> OpenAI:
        """返回与当前 provider 匹配的 OpenAI 客户端；切换 provider 后自动重建。"""
        api_key, base_url = self._client_credentials()
        if self._client is None or self._client_params != (api_key, base_url):
            self._client = OpenAI(api_key=api_key, base_url=base_url)
            self._client_params = (api_key, base_url)
        return self._client

    def submit(
        self,
        user_message: str,
        system_prompt: str,
        history: list[ChatCompletionMessageParam] | None = None,
    ) -> None:
        """提交一条用户消息，在后台线程中异步执行。"""
        thread = threading.Thread(
            target=self._run,
            args=(user_message, system_prompt, history or []),
            name="chat-worker",
            daemon=True,
        )
        thread.start()

    def cancel(self) -> None:
        """请求中断当前回答；已在流式/工具循环内生效。"""
        self._cancel.set()

    def _run(
        self,
        user_message: str,
        system_prompt: str,
        history: list[ChatCompletionMessageParam],
    ) -> None:
        self._cancel.clear()
        mode = self._config.ACTIVE_MODE
        instructions = self._config.mode_instructions(mode)
        if instructions:
            system_prompt = f"{system_prompt}\n\n{instructions}"
        messages: list[ChatCompletionMessageParam] = [
            ChatCompletionSystemMessageParam(role="system", content=system_prompt),
            *history,
            ChatCompletionUserMessageParam(role="user", content=user_message),
        ]
        try:
            made = True
            while made and not self._cancel.is_set():
                made = self._stream_once(messages, mode)
            self._bus.publish(Event(EventTypes.ASSISTANT_DONE))
        except Exception as exc:
            logger.exception("对话请求失败")
            self._bus.publish(Event(EventTypes.ERROR, str(exc)))

    def _stream_once(self, messages: list[ChatCompletionMessageParam], mode: str) -> bool:
        """执行一轮流式请求；若模型发起了工具调用则执行并返回 True。"""
        self._usage["calls"] += 1
        made, _ = stream_completion(
            self._config,
            self._bus,
            self._ensure_client(),
            self._tools,
            messages,
            mode,
            agent_id=None,
            record_usage=self._record_usage,
            cancel_event=self._cancel,
        )
        return made
